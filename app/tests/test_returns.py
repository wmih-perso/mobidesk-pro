import pytest
from sqlalchemy import create_engine, select

from app.models import Display, ReturnDoc, StockBatch, StockMovement
from app.returns import (
    count_pending_defectives,
    create_customer_return,
    create_supplier_return,
    list_pending_defectives,
    list_purchases_for_return,
    list_sales_for_return,
    receive_exchange,
    settle_pending_exchange,
)
from app.services import (
    StockError,
    apply_stock_batch,
    cancel_movement_batch,
    create_reseller,
    create_supplier,
    sum_profit_cents,
    sum_return_deduction_cents,
)
from app.tests.test_services import _create_sample


def _wholesale_sale(db_session, display, reseller, quantity=3, unit_price=200000, versement=0):
    _, batch_id = apply_stock_batch(
        db_session,
        direction=-1,
        reason="Vente",
        lines=[(display.id, quantity, unit_price)],
        is_sale=True,
        sale_price_type="wholesale",
        reseller_id=reseller.id,
        versement_cents=versement,
    )
    return batch_id


def _sale_line(db_session, reseller_id):
    [sale] = list_sales_for_return(db_session, reseller_id=reseller_id)
    return sale, sale.lines[0]


def test_reseller_returns_one_of_three_displays(db_session):
    # L'exemple du cahier des charges : 3 afficheurs vendus à 2 000 DA,
    # le revendeur en rend 1 en bon état.
    display = _create_sample(db_session, purchase_price_cents=150000, quantity=10)
    reseller = create_reseller(db_session, name="Boutique Amine")
    _wholesale_sale(db_session, display, reseller)
    assert reseller.balance_cents == 600000
    profit_before = sum_profit_cents(db_session)

    sale, line = _sale_line(db_session, reseller.id)
    assert (line.quantity, line.returnable) == (3, 3)

    doc = create_customer_return(db_session, lines=[(line.movement_id, 1, "good")], settlement="debt")

    assert doc.amount_cents == 200000
    assert (doc.balance_before_cents, doc.balance_after_cents) == (600000, 400000)
    assert reseller.balance_cents == 400000
    assert display.quantity == 8
    assert sum_profit_cents(db_session) == profit_before - (200000 - 150000)
    _, line = _sale_line(db_session, reseller.id)
    assert (line.returned, line.returnable) == (1, 2)


def test_returned_stock_comes_back_at_original_cost(db_session):
    display = _create_sample(db_session, purchase_price_cents=150000, quantity=3)
    reseller = create_reseller(db_session, name="R")
    _wholesale_sale(db_session, display, reseller)
    display.purchase_price_cents = 999999  # le prix actuel ne doit pas servir

    _, line = _sale_line(db_session, reseller.id)
    create_customer_return(db_session, lines=[(line.movement_id, 1, "good")], settlement="debt")

    batches = db_session.scalars(
        select(StockBatch).where(StockBatch.display_id == display.id, StockBatch.quantity_remaining > 0)
    ).all()
    assert [(b.quantity_remaining, b.unit_purchase_price_cents) for b in batches] == [(1, 150000)]
    assert display.purchase_price_cents == 999999


def test_cannot_return_more_than_sold(db_session):
    display = _create_sample(db_session)
    reseller = create_reseller(db_session, name="R")
    _wholesale_sale(db_session, display, reseller)
    _, line = _sale_line(db_session, reseller.id)

    create_customer_return(db_session, lines=[(line.movement_id, 2, "good")], settlement="debt")
    with pytest.raises(StockError):
        create_customer_return(db_session, lines=[(line.movement_id, 2, "good")], settlement="debt")


def test_cash_refund_leaves_debt_unchanged(db_session):
    display = _create_sample(db_session)
    reseller = create_reseller(db_session, name="R")
    _wholesale_sale(db_session, display, reseller)
    _, line = _sale_line(db_session, reseller.id)

    create_customer_return(db_session, lines=[(line.movement_id, 1, "good")], settlement="cash")

    assert reseller.balance_cents == 600000


def test_counter_customer_return_requires_cash_and_finds_ticket(db_session):
    display = _create_sample(db_session, quantity=5)
    _, batch_id = apply_stock_batch(
        db_session, direction=-1, reason="Vente", lines=[(display.id, 2, 250000)],
        is_sale=True, sale_price_type="retail",
    )

    [sale] = list_sales_for_return(db_session, reseller_id=None, ticket=batch_id)
    assert sale.contact_name == "Client comptoir"
    assert list_sales_for_return(db_session, reseller_id=None, ticket=batch_id + 99) == []
    line = sale.lines[0]

    with pytest.raises(StockError):
        create_customer_return(db_session, lines=[(line.movement_id, 1, "good")], settlement="debt")
    doc = create_customer_return(db_session, lines=[(line.movement_id, 1, "good")], settlement="cash")
    assert doc.amount_cents == 250000
    assert display.quantity == 4


def test_defective_return_stays_out_of_stock_until_sent_to_supplier(db_session):
    display = _create_sample(db_session, purchase_price_cents=150000, quantity=10)
    reseller = create_reseller(db_session, name="R")
    supplier = create_supplier(db_session, name="F")
    supplier.balance_cents = 500000
    _wholesale_sale(db_session, display, reseller)
    _, line = _sale_line(db_session, reseller.id)

    create_customer_return(db_session, lines=[(line.movement_id, 1, "defective")], settlement="debt")

    assert display.quantity == 7  # pas remis en stock vendable
    assert reseller.balance_cents == 400000
    [pending] = list_pending_defectives(db_session)
    assert (pending.remaining, pending.unit_cost_cents) == (1, 150000)

    doc = create_supplier_return(
        db_session, supplier_id=supplier.id, defective_lines=[(pending.line_id, 1)], settlement="debt"
    )

    assert doc.amount_cents == 150000
    assert supplier.balance_cents == 350000
    assert display.quantity == 7
    assert count_pending_defectives(db_session) == 0


def _purchase(db_session, display, supplier, quantity=4, unit_cost=140000):
    apply_stock_batch(
        db_session, direction=1, reason="Achat fournisseur",
        lines=[(display.id, quantity, unit_cost)], supplier_id=supplier.id, versement_cents=0,
    )
    [purchase] = list_purchases_for_return(db_session, supplier_id=supplier.id)
    return purchase.lines[0]


def test_supplier_return_from_purchase_reduces_stock_and_debt(db_session):
    display = _create_sample(db_session, purchase_price_cents=150000, quantity=2)
    supplier = create_supplier(db_session, name="F")
    line = _purchase(db_session, display, supplier)
    assert supplier.balance_cents == 560000
    assert (line.quantity, line.returnable, line.unit_price_cents) == (4, 4, 140000)

    doc = create_supplier_return(
        db_session, supplier_id=supplier.id, purchase_lines=[(line.movement_id, 1)], settlement="debt"
    )

    assert doc.amount_cents == 140000
    assert supplier.balance_cents == 420000
    assert display.quantity == 5
    # Le lot de cet achat est celui qui est consommé.
    [batch] = db_session.scalars(
        select(StockBatch).where(StockBatch.display_id == display.id, StockBatch.unit_purchase_price_cents == 140000)
    ).all()
    assert batch.quantity_remaining == 3


def test_supplier_return_is_limited_by_purchase_and_stock(db_session):
    display = _create_sample(db_session, quantity=0)
    supplier = create_supplier(db_session, name="F")
    line = _purchase(db_session, display, supplier, quantity=2)

    with pytest.raises(StockError):
        create_supplier_return(
            db_session, supplier_id=supplier.id, purchase_lines=[(line.movement_id, 3)], settlement="cash"
        )


def test_supplier_exchange_then_reception(db_session):
    display = _create_sample(db_session, quantity=0)
    supplier = create_supplier(db_session, name="F")
    line = _purchase(db_session, display, supplier, quantity=2, unit_cost=140000)
    debt = supplier.balance_cents

    doc = create_supplier_return(
        db_session, supplier_id=supplier.id, purchase_lines=[(line.movement_id, 1)], settlement="exchange"
    )
    assert doc.status == "pending"
    assert display.quantity == 1

    receive_exchange(db_session, doc.id)

    assert doc.status == "done"
    assert display.quantity == 2
    assert supplier.balance_cents == debt
    with pytest.raises(StockError):
        receive_exchange(db_session, doc.id)


def test_pending_exchange_settled_as_credit(db_session):
    display = _create_sample(db_session, quantity=0)
    supplier = create_supplier(db_session, name="F")
    line = _purchase(db_session, display, supplier, quantity=2, unit_cost=140000)
    debt = supplier.balance_cents
    doc = create_supplier_return(
        db_session, supplier_id=supplier.id, purchase_lines=[(line.movement_id, 1)], settlement="exchange"
    )

    settle_pending_exchange(db_session, doc.id, settlement="debt")

    assert supplier.balance_cents == debt - 140000
    assert (doc.status, doc.settlement) == ("done", "debt")


def test_sale_with_returns_cannot_be_edited(db_session):
    display = _create_sample(db_session)
    reseller = create_reseller(db_session, name="R")
    batch_id = _wholesale_sale(db_session, display, reseller)
    _, line = _sale_line(db_session, reseller.id)
    create_customer_return(db_session, lines=[(line.movement_id, 1, "good")], settlement="debt")

    with pytest.raises(StockError):
        cancel_movement_batch(db_session, batch_id=batch_id)


def test_legacy_returns_are_converted_with_same_profit(tmp_path):
    from app.database import _migrate_legacy_returns, Base
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        display = _create_sample(session, purchase_price_cents=150000, quantity=10)
        reseller = create_reseller(session, name="R")
        batch_id = _wholesale_sale(session, display, reseller)
        display_id = display.id
        # Ancien retour reçu lié à la vente + ancien retour fournisseur non résolu.
        for change, reason, link in ((1, "Retour reçu", batch_id), (-1, "Retour fournisseur", None)):
            session.add(StockMovement(
                display_id=display.id, change_quantity=change, quantity_before=0, quantity_after=0,
                reason=reason, reseller_id=reseller.id if change > 0 else None, linked_batch_id=link,
            ))
        session.commit()

    _migrate_legacy_returns(engine)
    _migrate_legacy_returns(engine)  # idempotente

    with Session(engine) as session:
        docs = session.scalars(select(ReturnDoc).order_by(ReturnDoc.id)).all()
        assert [(d.kind, d.status) for d in docs] == [("customer", "done"), ("supplier", "pending")]
        assert sum_return_deduction_cents(session) == 200000 - 150000
        assert session.get(Display, display_id).quantity == 7  # stock non retouché
