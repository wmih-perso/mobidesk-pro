"""Modification en place d'une opération (E1, E2, E3 de l'audit) et retour
client dans le lot d'origine (E4)."""

import datetime

import pytest
from sqlalchemy import select

from app.models import LotAllocation, StockBatch, StockMovement
from app.returns import create_customer_return
from app.services import (
    StockError, apply_stock_batch, cancel_movement_batch, create_display, create_reseller,
    create_supplier, edit_movement_batch,
)

DA = 100


@pytest.fixture()
def setup(db_session):
    """L'exemple de l'audit : 1 pièce à 1 500 (Salam), puis 2 à 1 900 (Zaim)."""
    salam = create_supplier(db_session, name="Salam")
    zaim = create_supplier(db_session, name="Zaim")
    d = create_display(
        db_session, reference="AFF-A15", category="Afficheur", brand="Samsung",
        phone_model="A15", quality="", purchase_price_cents=0,
        sale_price_retail_cents=2500 * DA, sale_price_wholesale_cents=1800 * DA,
        quantity=0, min_stock=1,
    )
    _, b1 = apply_stock_batch(db_session, direction=1, reason="Achat fournisseur",
                              lines=[(d.id, 1, 1500 * DA)], supplier_id=salam.id, versement_cents=0)
    _, b2 = apply_stock_batch(db_session, direction=1, reason="Achat fournisseur",
                              lines=[(d.id, 2, 1900 * DA)], supplier_id=zaim.id, versement_cents=0,
                              invoice_number="F-2")
    # dates distinctes pour l'ordre FIFO
    for i, bid in enumerate((b1, b2)):
        for obj in db_session.scalars(select(StockBatch).where(StockBatch.movement_batch_id == bid)):
            obj.created_at = datetime.datetime(2026, 11, 1 + i, 10)
        for obj in db_session.scalars(select(StockMovement).where(StockMovement.movement_batch_id == bid)):
            obj.created_at = datetime.datetime(2026, 11, 1 + i, 10)
    db_session.flush()
    return db_session, d, salam, zaim, b1, b2


def _lots(db, d):
    return [(b.unit_purchase_price_cents // DA, b.quantity_remaining, b.quantity_original)
            for b in db.scalars(select(StockBatch).where(StockBatch.display_id == d.id)
                                .order_by(StockBatch.created_at, StockBatch.id))]


def _sell(db, d, qty, price, reseller=None):
    _, bid = apply_stock_batch(
        db, direction=-1, reason="Vente", lines=[(d.id, qty, price * DA, "A15")], is_sale=True,
        sale_price_type="wholesale", reseller_id=reseller.id if reseller else None,
        versement_cents=0 if reseller else None,
    )
    sale = db.scalars(select(StockMovement).where(StockMovement.movement_batch_id == bid)).one()
    return bid, sale


def _stock_matches_lots(db, d):
    return d.quantity == sum(b[1] for b in _lots(db, d))


# --- E1 / E3 : achats ------------------------------------------------------


def test_purchase_partly_sold_cannot_go_below_sold_quantity(setup):
    db, d, _s, zaim, _b1, b2 = setup
    _sell(db, d, 2, 2200)  # lot 1 vide, 1 pièce du lot 2 vendue
    with pytest.raises(StockError, match="minimum 1"):
        edit_movement_batch(db, batch_id=b2, lines=[])  # produit retiré de l'achat
    with pytest.raises(StockError, match="minimum 1"):
        cancel_movement_batch(db, batch_id=b2)
    edit_movement_batch(db, batch_id=b2, lines=[(d.id, 1, 1900 * DA)])  # = pièces vendues : accepté
    assert _lots(db, d) == [(1500, 0, 1), (1900, 0, 1)]
    assert d.quantity == 0 and _stock_matches_lots(db, d)
    assert zaim.balance_cents == 1900 * DA  # dette : 2 pièces → 1 pièce


def test_purchase_price_fix_applies_to_remaining_pieces_only(setup):
    db, d, _s, zaim, _b1, b2 = setup
    _, sale = _sell(db, d, 2, 2200)  # coût figé 1 700
    zaim.balance_cents = 3800 * DA
    edit_movement_batch(db, batch_id=b2, lines=[(d.id, 2, 2000 * DA)])
    assert _lots(db, d) == [(1500, 0, 1), (2000, 1, 2)]
    assert sale.unit_purchase_price_cents == 1700 * DA  # vente passée inchangée
    assert zaim.balance_cents == 4000 * DA  # dette +200
    purchase = db.scalars(select(StockMovement).where(StockMovement.movement_batch_id == b2)).one()
    assert purchase.invoice_number == "F-2"
    assert purchase.created_at == datetime.datetime(2026, 11, 2, 10)
    assert _stock_matches_lots(db, d)


def test_purchase_quantity_up_and_down_keeps_lot_and_date(setup):
    db, d, _s, zaim, _b1, b2 = setup
    edit_movement_batch(db, batch_id=b2, lines=[(d.id, 5, 1900 * DA)])
    assert d.quantity == 6 and _lots(db, d) == [(1500, 1, 1), (1900, 5, 5)]
    assert zaim.balance_cents == (2 + 3) * 1900 * DA  # +3 pièces non payées
    edit_movement_batch(db, batch_id=b2, lines=[(d.id, 1, 1900 * DA)])
    assert d.quantity == 2 and _lots(db, d) == [(1500, 1, 1), (1900, 1, 1)]
    assert _stock_matches_lots(db, d)


def test_unsold_purchase_can_be_cancelled(setup):
    db, d, _s, zaim, _b1, b2 = setup
    cancel_movement_batch(db, batch_id=b2)
    assert d.quantity == 1 and _lots(db, d) == [(1500, 1, 1)]
    assert zaim.balance_cents == 0  # l'achat (non payé) est retiré de la dette


# --- E2 : ventes -----------------------------------------------------------


def test_sale_price_edit_keeps_date_ticket_cost_and_fixes_debt(setup):
    db, d, *_ = setup
    reseller = create_reseller(db, name="Kader")
    bid, sale = _sell(db, d, 1, 2200, reseller)
    sale.created_at = datetime.datetime(2026, 11, 3, 15)
    assert reseller.balance_cents == 2200 * DA
    edit_movement_batch(db, batch_id=bid, lines=[(d.id, 1, 2000 * DA)])
    assert sale.movement_batch_id == bid  # même n° de ticket
    assert sale.created_at == datetime.datetime(2026, 11, 3, 15)
    assert sale.unit_purchase_price_cents == 1500 * DA  # coût d'origine
    assert sale.profit_cents == 500 * DA
    assert sale.model_label == "A15"
    assert reseller.balance_cents == 2000 * DA


def test_sale_quantity_down_returns_piece_to_its_own_lot(setup):
    db, d, *_ = setup
    bid, sale = _sell(db, d, 2, 2200)  # 1 pièce lot 1 + 1 pièce lot 2
    edit_movement_batch(db, batch_id=bid, lines=[(d.id, 1, 2200 * DA)])
    # la dernière pièce prise (lot 2, 1 900) revient dans le lot 2
    assert _lots(db, d) == [(1500, 0, 1), (1900, 2, 2)]
    assert sale.unit_purchase_price_cents == 1500 * DA
    assert sale.profit_cents == 700 * DA
    assert _stock_matches_lots(db, d)


def test_sale_quantity_up_takes_next_lot(setup):
    db, d, *_ = setup
    bid, sale = _sell(db, d, 1, 2200)
    edit_movement_batch(db, batch_id=bid, lines=[(d.id, 3, 2200 * DA)])
    assert _lots(db, d) == [(1500, 0, 1), (1900, 0, 2)]
    assert d.quantity == 0
    assert sale.unit_purchase_price_cents == (1500 + 2 * 1900) * DA // 3
    with pytest.raises(StockError, match="Stock insuffisant"):
        edit_movement_batch(db, batch_id=bid, lines=[(d.id, 4, 2200 * DA)])


def test_cancelled_sale_goes_back_to_original_lots(setup):
    db, d, *_ = setup
    reseller = create_reseller(db, name="Rezki")
    bid, _sale = _sell(db, d, 2, 2200, reseller)
    cancel_movement_batch(db, batch_id=bid)
    assert _lots(db, d) == [(1500, 1, 1), (1900, 2, 2)]
    assert reseller.balance_cents == 0
    assert db.scalars(select(LotAllocation)).all() == []


def test_old_sale_without_trace_returns_to_oldest_started_lot(setup):
    db, d, *_ = setup
    bid, sale = _sell(db, d, 1, 2200)
    for a in db.scalars(select(LotAllocation)).all():  # vente « d'avant la mise à jour »
        db.delete(a)
    db.flush()
    edit_movement_batch(db, batch_id=bid, lines=[])
    assert _lots(db, d) == [(1500, 1, 1), (1900, 2, 2)]


# --- E4 : retour client --------------------------------------------------


def test_customer_return_goes_back_to_original_lot_and_supplier(setup):
    db, d, salam, *_ = setup
    reseller = create_reseller(db, name="Rezki")
    _bid, sale = _sell(db, d, 2, 2200, reseller)
    create_customer_return(db, lines=[(sale.id, 1, "good")], settlement="debt")
    assert _lots(db, d) == [(1500, 0, 1), (1900, 2, 2)]  # retour dans le lot 2 (Zaim)
    assert d.quantity == 2 and _stock_matches_lots(db, d)
    assert db.scalars(select(StockBatch).where(StockBatch.supplier_id.is_(None))).all() == []
