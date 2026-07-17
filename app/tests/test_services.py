import pytest

from app.services import (
    StockError,
    adjust_stock,
    create_display,
    deactivate_display,
    list_displays,
    list_movements,
    update_display,
)


def _create_sample(db_session, **overrides):
    fields = dict(
        reference="AFF-001",
        brand="Samsung",
        phone_model="Galaxy A12",
        quality="Incell",
        color="Noir",
        purchase_price_cents=150000,
        sale_price_cents=250000,
        quantity=10,
        min_stock=3,
    )
    fields.update(overrides)
    return create_display(db_session, **fields)


def test_create_display_records_initial_stock_movement(db_session):
    display = _create_sample(db_session)

    movements = list_movements(db_session, display_id=display.id)

    assert display.quantity == 10
    assert len(movements) == 1
    assert movements[0].change_quantity == 10
    assert movements[0].reason == "Stock initial"


def test_create_display_rejects_duplicate_reference(db_session):
    _create_sample(db_session)

    with pytest.raises(StockError):
        _create_sample(db_session)


def test_create_display_rejects_negative_quantity(db_session):
    with pytest.raises(StockError):
        _create_sample(db_session, quantity=-1)


def test_adjust_stock_increases_quantity(db_session):
    display = _create_sample(db_session, quantity=5)

    updated = adjust_stock(db_session, display.id, change_quantity=3, reason="Achat fournisseur")

    assert updated.quantity == 8


def test_adjust_stock_decreases_quantity(db_session):
    display = _create_sample(db_session, quantity=5)

    updated = adjust_stock(db_session, display.id, change_quantity=-2, reason="Vente")

    assert updated.quantity == 3


def test_adjust_stock_never_goes_negative(db_session):
    display = _create_sample(db_session, quantity=2)

    with pytest.raises(StockError):
        adjust_stock(db_session, display.id, change_quantity=-5, reason="Vente")

    db_session.refresh(display)
    assert display.quantity == 2


def test_adjust_stock_requires_a_reason(db_session):
    display = _create_sample(db_session, quantity=5)

    with pytest.raises(StockError):
        adjust_stock(db_session, display.id, change_quantity=1, reason="")


def test_is_low_stock_flag(db_session):
    display = _create_sample(db_session, quantity=2, min_stock=3)
    assert display.is_low_stock is True

    display.quantity = 5
    assert display.is_low_stock is False


def test_list_displays_filters_low_stock_only(db_session):
    _create_sample(db_session, reference="AFF-001", quantity=1, min_stock=5)
    _create_sample(db_session, reference="AFF-002", quantity=20, min_stock=5)

    low_stock = list_displays(db_session, only_low_stock=True)

    assert [d.reference for d in low_stock] == ["AFF-001"]


def test_list_displays_search_by_brand(db_session):
    _create_sample(db_session, reference="AFF-001", brand="Samsung")
    _create_sample(db_session, reference="AFF-002", brand="Apple")

    results = list_displays(db_session, search="samsung")

    assert [d.reference for d in results] == ["AFF-001"]


def test_update_display_changes_fields_without_touching_quantity(db_session):
    display = _create_sample(db_session, quantity=7)

    update_display(
        db_session,
        display.id,
        reference="AFF-001-B",
        brand="Samsung",
        phone_model="Galaxy A13",
        quality="Original",
        color="Bleu",
        purchase_price_cents=160000,
        sale_price_cents=260000,
        min_stock=2,
    )

    assert display.reference == "AFF-001-B"
    assert display.phone_model == "Galaxy A13"
    assert display.quantity == 7  # inchangée


def test_deactivate_display_hides_it_from_default_listing(db_session):
    display = _create_sample(db_session)

    deactivate_display(db_session, display.id)

    assert list_displays(db_session) == []
    assert list_displays(db_session, only_active=False) != []
