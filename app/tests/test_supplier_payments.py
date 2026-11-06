import pytest

from app.services import (
    StockError, apply_stock_batch, create_display, create_supplier,
    list_supplier_payments, record_supplier_payment, set_supplier_balance,
)


def _display(db):
    return create_display(
        db, reference="IMP-1", category="Afficheur", brand="Samsung", phone_model="A15",
        quality="", purchase_price_cents=100, sale_price_retail_cents=300,
        sale_price_wholesale_cents=200, quantity=0, min_stock=1,
    )


def test_unpaid_purchase_then_payment(db_session):
    salam = create_supplier(db_session, name="Salam")
    display = _display(db_session)
    apply_stock_batch(
        db_session, direction=1, reason="Achat fournisseur",
        lines=[(display.id, 4, 50000)], supplier_id=salam.id, versement_cents=50000,
    )
    assert salam.balance_cents == 150000  # 2000 DA achetés, 500 DA payés

    record_supplier_payment(db_session, salam.id, 150000, "espèces")
    assert salam.balance_cents == 0
    [payment] = list_supplier_payments(db_session, salam.id)
    assert (payment.balance_before_cents, payment.balance_after_cents) == (150000, 0)

    with pytest.raises(StockError):
        record_supplier_payment(db_session, salam.id, 0)


def test_adjust_supplier_balance(db_session):
    zaim = create_supplier(db_session, name="Zaim")
    zaim.balance_cents = 39000
    adjust = set_supplier_balance(db_session, zaim.id, 0, "écart corrigé")
    assert zaim.balance_cents == 0
    assert adjust.kind == "adjust" and adjust.amount_cents == 39000
    with pytest.raises(StockError):
        set_supplier_balance(db_session, zaim.id, 0)
