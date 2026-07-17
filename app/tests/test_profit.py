import pytest

from app.services import (
    StockError,
    adjust_stock,
    list_sales,
    sum_profit_cents,
    update_display,
)
from app.tests.test_services import _create_sample


def test_sale_records_price_snapshot(db_session):
    display = _create_sample(
        db_session, purchase_price_cents=100000, sale_price_cents=180000, quantity=5
    )

    adjust_stock(db_session, display.id, change_quantity=-1, reason="Vente", is_sale=True)

    [sale] = list_sales(db_session)
    assert sale.is_sale is True
    assert sale.unit_purchase_price_cents == 100000
    assert sale.unit_sale_price_cents == 180000


def test_non_sale_reason_leaves_prices_at_zero(db_session):
    display = _create_sample(db_session, quantity=5)

    adjust_stock(
        db_session, display.id, change_quantity=-1, reason="Produit endommagé", is_sale=False
    )

    assert list_sales(db_session) == []


def test_rejects_is_sale_on_positive_change(db_session):
    display = _create_sample(db_session, quantity=5)

    with pytest.raises(StockError):
        adjust_stock(
            db_session, display.id, change_quantity=2, reason="Retour client", is_sale=True
        )


def test_price_snapshot_survives_later_price_change(db_session):
    display = _create_sample(
        db_session, purchase_price_cents=100000, sale_price_cents=180000, quantity=5
    )

    adjust_stock(db_session, display.id, change_quantity=-1, reason="Vente", is_sale=True)

    update_display(
        db_session,
        display.id,
        reference=display.reference,
        brand=display.brand,
        phone_model=display.phone_model,
        quality=display.quality,
        color=display.color,
        purchase_price_cents=999999,
        sale_price_cents=999999,
        min_stock=display.min_stock,
    )

    [sale] = list_sales(db_session)
    assert sale.unit_purchase_price_cents == 100000
    assert sale.unit_sale_price_cents == 180000


def test_movement_profit_cents_property(db_session):
    display = _create_sample(
        db_session, purchase_price_cents=100000, sale_price_cents=180000, quantity=5
    )

    adjust_stock(db_session, display.id, change_quantity=-2, reason="Vente", is_sale=True)
    adjust_stock(
        db_session, display.id, change_quantity=-1, reason="Produit perdu", is_sale=False
    )

    [sale] = list_sales(db_session)
    assert sale.profit_cents == 80000 * 2


def test_list_sales_filters_by_period(db_session):
    import datetime

    display = _create_sample(db_session, quantity=5)
    adjust_stock(db_session, display.id, change_quantity=-1, reason="Vente", is_sale=True)

    [sale] = list_sales(db_session)
    sale.created_at = datetime.datetime(2020, 1, 1)
    db_session.flush()

    assert list_sales(db_session, start=datetime.datetime(2021, 1, 1)) == []
    assert list_sales(db_session, end=datetime.datetime(2021, 1, 1)) == [sale]


def test_sum_profit_cents_aggregates_and_excludes_non_sales(db_session):
    display_a = _create_sample(
        db_session,
        reference="AFF-001",
        purchase_price_cents=100000,
        sale_price_cents=150000,
        quantity=5,
    )
    display_b = _create_sample(
        db_session,
        reference="AFF-002",
        purchase_price_cents=50000,
        sale_price_cents=90000,
        quantity=5,
    )

    adjust_stock(db_session, display_a.id, change_quantity=-1, reason="Vente", is_sale=True)
    adjust_stock(db_session, display_b.id, change_quantity=-2, reason="Vente", is_sale=True)
    adjust_stock(
        db_session, display_a.id, change_quantity=-1, reason="Produit endommagé", is_sale=False
    )
    adjust_stock(db_session, display_a.id, change_quantity=3, reason="Achat fournisseur")

    total = sum_profit_cents(db_session)

    assert total == 50000 + 2 * 40000
