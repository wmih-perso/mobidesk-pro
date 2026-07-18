import pytest

from app.services import (
    StockError,
    count_repairs,
    create_repair,
    list_movements,
    list_repairs,
    update_repair,
)
from app.tests.test_services import _create_sample


def _create_repair_sample(db_session, **overrides):
    fields = dict(
        client_name="Karim Benali",
        client_phone="0555123456",
        phone_brand="Samsung",
        phone_model="Galaxy A12",
        description="Écran cassé",
        discount_cents=0,
        parts=[],
        notes="",
    )
    fields.update(overrides)
    return create_repair(db_session, **fields)


def test_create_repair_decrements_stock_for_each_part(db_session):
    display_a = _create_sample(db_session, reference="AFF-001", quantity=5)
    display_b = _create_sample(db_session, reference="BAT-001", quantity=10)

    _create_repair_sample(db_session, parts=[(display_a.id, 1), (display_b.id, 2)])

    db_session.refresh(display_a)
    db_session.refresh(display_b)
    assert display_a.quantity == 4
    assert display_b.quantity == 8


def test_create_repair_creates_repair_items_with_price_snapshot(db_session):
    display = _create_sample(
        db_session,
        reference="AFF-001",
        purchase_price_cents=150000,
        sale_price_retail_cents=250000,
        quantity=5,
    )

    repair = _create_repair_sample(db_session, parts=[(display.id, 2)])

    [item] = repair.items
    assert item.display_id == display.id
    assert item.quantity == 2
    assert item.unit_purchase_price_cents == 150000
    assert item.unit_sale_price_cents == 250000


def test_create_repair_computes_charged_price_from_parts_retail_price(db_session):
    display_a = _create_sample(
        db_session, reference="AFF-001", sale_price_retail_cents=250000, quantity=5
    )
    display_b = _create_sample(
        db_session, reference="BAT-001", sale_price_retail_cents=90000, quantity=5
    )

    repair = _create_repair_sample(
        db_session, parts=[(display_a.id, 1), (display_b.id, 2)]
    )

    # 250000 + 2*90000 = 430000
    assert repair.charged_price_cents == 430000


def test_create_repair_applies_discount_to_charged_price(db_session):
    display = _create_sample(
        db_session, reference="AFF-001", sale_price_retail_cents=250000, quantity=5
    )

    repair = _create_repair_sample(
        db_session, parts=[(display.id, 1)], discount_cents=30000
    )

    assert repair.discount_cents == 30000
    assert repair.charged_price_cents == 220000


def test_create_repair_rejects_discount_larger_than_parts_total(db_session):
    display = _create_sample(
        db_session, reference="AFF-001", sale_price_retail_cents=100000, quantity=5
    )

    with pytest.raises(StockError):
        _create_repair_sample(
            db_session, parts=[(display.id, 1)], discount_cents=200000
        )


def test_create_repair_links_stock_movements_via_repair_id(db_session):
    display = _create_sample(db_session, reference="AFF-001", quantity=5)

    repair = _create_repair_sample(db_session, parts=[(display.id, 1)])

    movements = list_movements(db_session, display_id=display.id)
    linked = [m for m in movements if m.reason == "Utilisation en réparation"]
    assert len(linked) == 1
    assert linked[0].repair_id == repair.id


def test_create_repair_rejects_missing_client_name(db_session):
    display = _create_sample(db_session, reference="AFF-001", quantity=5)

    with pytest.raises(StockError):
        _create_repair_sample(db_session, client_name="", parts=[(display.id, 1)])


def test_create_repair_rejects_empty_parts_list(db_session):
    with pytest.raises(StockError):
        _create_repair_sample(db_session, parts=[])


def test_create_repair_rejects_negative_discount(db_session):
    display = _create_sample(db_session, reference="AFF-001", quantity=5)

    with pytest.raises(StockError):
        _create_repair_sample(
            db_session, discount_cents=-1, parts=[(display.id, 1)]
        )


def test_create_repair_rolls_back_entirely_if_one_part_has_insufficient_stock(db_session):
    display_a = _create_sample(db_session, reference="AFF-001", quantity=5)
    display_b = _create_sample(db_session, reference="BAT-001", quantity=1)
    db_session.commit()

    with pytest.raises(StockError):
        _create_repair_sample(
            db_session, parts=[(display_a.id, 1), (display_b.id, 5)]
        )
    # Simule le rollback que session_scope() applique automatiquement en
    # production sur toute exception — la fixture db_session ne le fait
    # pas elle-même (contrairement à session_scope), donc les tests
    # doivent le déclencher explicitement pour vérifier l'atomicité.
    db_session.rollback()

    db_session.refresh(display_a)
    db_session.refresh(display_b)
    assert display_a.quantity == 5
    assert display_b.quantity == 1
    assert list_repairs(db_session) == []


def test_list_repairs_orders_most_recent_first(db_session):
    display = _create_sample(db_session, reference="AFF-001", quantity=10)

    first = _create_repair_sample(db_session, client_name="Client A", parts=[(display.id, 1)])
    second = _create_repair_sample(db_session, client_name="Client B", parts=[(display.id, 1)])

    repairs = list_repairs(db_session)
    assert [r.id for r in repairs] == [second.id, first.id]
    assert count_repairs(db_session) == 2


def _update_repair_sample(db_session, repair_id, **overrides):
    fields = dict(
        client_name="Karim Benali",
        client_phone="0555123456",
        phone_brand="Samsung",
        phone_model="Galaxy A12",
        description="Écran cassé",
        discount_cents=0,
        parts=[],
        notes="",
    )
    fields.update(overrides)
    return update_repair(db_session, repair_id, **fields)


def test_update_repair_restocks_old_parts_and_consumes_new_ones(db_session):
    display_a = _create_sample(db_session, reference="AFF-001", quantity=5)
    display_b = _create_sample(db_session, reference="BAT-001", quantity=5)

    repair = _create_repair_sample(db_session, parts=[(display_a.id, 2)])
    db_session.refresh(display_a)
    assert display_a.quantity == 3

    _update_repair_sample(db_session, repair.id, parts=[(display_b.id, 1)])

    db_session.refresh(display_a)
    db_session.refresh(display_b)
    assert display_a.quantity == 5  # remise en stock
    assert display_b.quantity == 4  # nouvellement consommée


def test_update_repair_recomputes_charged_price(db_session):
    display_a = _create_sample(
        db_session, reference="AFF-001", sale_price_retail_cents=250000, quantity=5
    )
    display_b = _create_sample(
        db_session, reference="BAT-001", sale_price_retail_cents=90000, quantity=5
    )

    repair = _create_repair_sample(db_session, parts=[(display_a.id, 1)])
    assert repair.charged_price_cents == 250000

    _update_repair_sample(
        db_session, repair.id, parts=[(display_b.id, 2)], discount_cents=10000
    )

    assert repair.charged_price_cents == 90000 * 2 - 10000
    assert repair.discount_cents == 10000


def test_update_repair_removes_old_stock_movements(db_session):
    display_a = _create_sample(db_session, reference="AFF-001", quantity=5)
    display_b = _create_sample(db_session, reference="BAT-001", quantity=5)

    repair = _create_repair_sample(db_session, parts=[(display_a.id, 1)])
    _update_repair_sample(db_session, repair.id, parts=[(display_b.id, 1)])

    movements_a = list_movements(db_session, display_id=display_a.id)
    linked_a = [m for m in movements_a if m.repair_id == repair.id]
    assert linked_a == []

    movements_b = list_movements(db_session, display_id=display_b.id)
    linked_b = [m for m in movements_b if m.repair_id == repair.id]
    assert len(linked_b) == 1


def test_update_repair_rolls_back_if_new_part_has_insufficient_stock(db_session):
    display_a = _create_sample(db_session, reference="AFF-001", quantity=5)
    display_b = _create_sample(db_session, reference="BAT-001", quantity=1)

    repair = _create_repair_sample(db_session, parts=[(display_a.id, 2)])
    db_session.commit()

    with pytest.raises(StockError):
        _update_repair_sample(db_session, repair.id, parts=[(display_b.id, 5)])
    db_session.rollback()

    db_session.refresh(display_a)
    db_session.refresh(display_b)
    assert display_a.quantity == 3  # état après la réparation d'origine, inchangé
    assert display_b.quantity == 1
    [reloaded] = list_repairs(db_session)
    assert [item.display_id for item in reloaded.items] == [display_a.id]
