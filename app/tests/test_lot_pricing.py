"""Prix gros par lot : le prix du produit suit le lot le plus ancien en stock."""

import datetime

from sqlalchemy import select

from app.lot_pricing import LotInfo, fifo_cost_for_quantity, wholesale_for_quantity
from app.models import StockBatch, StockMovement
from app.returns import create_customer_return
from app.services import (
    apply_stock_batch, create_display, create_reseller, edit_movement_batch, update_display,
)

DA = 100


def _setup(db):
    d = create_display(
        db, reference="AFF-A15", category="Afficheur", brand="Samsung", phone_model="A15",
        quality="", purchase_price_cents=1500 * DA, sale_price_retail_cents=2500 * DA,
        sale_price_wholesale_cents=1800 * DA, quantity=1, min_stock=1,
    )
    first = db.scalars(select(StockBatch).where(StockBatch.display_id == d.id)).one()
    first.created_at = datetime.datetime(2026, 11, 1)
    # 2 pièces à 1 800, prix gros 2 200 pour ce lot
    apply_stock_batch(db, direction=1, reason="Achat fournisseur",
                      lines=[(d.id, 2, 1800 * DA, 2200 * DA)])
    return d


def _sell(db, d, qty, price=None, reseller=None):
    _, bid = apply_stock_batch(
        db, direction=-1, reason="Vente", lines=[(d.id, qty, (price or 2200) * DA)], is_sale=True,
        sale_price_type="wholesale", reseller_id=reseller.id if reseller else None,
        versement_cents=0 if reseller else None,
    )
    return bid


def test_wholesale_price_switches_when_first_lot_is_sold(db_session):
    d = _setup(db_session)
    assert d.sale_price_wholesale_cents == 1800 * DA  # lot 1 encore en stock
    _sell(db_session, d, 1, 1800)
    assert d.sale_price_wholesale_cents == 2200 * DA  # lot 1 épuisé → lot 2


def test_price_goes_back_when_piece_returns_to_old_lot(db_session):
    d = _setup(db_session)
    reseller = create_reseller(db_session, name="Rezki")
    bid = _sell(db_session, d, 1, 1800, reseller)
    assert d.sale_price_wholesale_cents == 2200 * DA
    sale = db_session.scalars(select(StockMovement).where(StockMovement.movement_batch_id == bid)).one()
    create_customer_return(db_session, lines=[(sale.id, 1, "good")], settlement="debt")
    assert d.sale_price_wholesale_cents == 1800 * DA
    # même chose en retirant la pièce de la vente (✏ Modifier)
    bid = _sell(db_session, d, 1, 1800)
    edit_movement_batch(db_session, batch_id=bid, lines=[])
    assert d.sale_price_wholesale_cents == 1800 * DA


def test_manual_price_in_product_sheet_applies_to_current_lot(db_session):
    d = _setup(db_session)
    update_display(
        db_session, d.id, reference=d.reference, category=d.category, brand=d.brand,
        phone_model=d.phone_model, quality="", purchase_price_cents=d.purchase_price_cents,
        sale_price_retail_cents=d.sale_price_retail_cents, sale_price_wholesale_cents=1900 * DA,
        min_stock=1,
    )
    assert d.sale_price_wholesale_cents == 1900 * DA
    apply_stock_batch(db_session, direction=1, reason="Ajustement", lines=[(d.id, 1, 1500 * DA)])
    assert d.sale_price_wholesale_cents == 1900 * DA  # pas remplacé au mouvement suivant
    _sell(db_session, d, 1, 1900)
    assert d.sale_price_wholesale_cents == 2200 * DA


def test_old_lots_without_price_keep_the_product_price(db_session):
    d = _setup(db_session)
    for lot in db_session.scalars(select(StockBatch)):
        lot.sale_price_wholesale_cents = None  # lots d'avant la mise à jour
    d.sale_price_wholesale_cents = 2000 * DA
    _sell(db_session, d, 1, 2000)
    assert d.sale_price_wholesale_cents == 2000 * DA


def test_average_price_and_cost_over_two_lots():
    lots = [LotInfo(1, 1500 * DA, 1800 * DA), LotInfo(2, 1800 * DA, 2200 * DA)]
    assert wholesale_for_quantity(lots, 1, 0) == 1800 * DA
    assert wholesale_for_quantity(lots, 2, 0) == 2000 * DA  # (1 800 + 2 200) / 2
    assert wholesale_for_quantity([LotInfo(1, 0, None)], 1, 1700 * DA) == 1700 * DA
    assert fifo_cost_for_quantity(lots, 2, 0) == (1500 + 1800) * DA
    assert fifo_cost_for_quantity(lots, 4, 1900 * DA) == (1500 + 3600 + 1900) * DA
