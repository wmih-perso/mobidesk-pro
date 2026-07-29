import pytest

from app.services import (
    StockError,
    adjust_stock,
    apply_stock_batch,
    count_low_stock_displays,
    count_movements,
    create_category,
    create_display,
    create_reseller,
    create_supplier,
    deactivate_display,
    delete_category,
    list_categories,
    list_displays,
    list_movements,
    update_category,
    update_display,
)


def _create_sample(db_session, **overrides):
    fields = dict(
        reference="AFF-001",
        category="Afficheur",
        brand="Samsung",
        phone_model="Galaxy A12",
        quality="Incell",
        purchase_price_cents=150000,
        sale_price_retail_cents=250000,
        sale_price_wholesale_cents=220000,
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
        category="Afficheur",
        brand="Samsung",
        phone_model="Galaxy A13",
        quality="Original",
        purchase_price_cents=160000,
        sale_price_retail_cents=260000,
        sale_price_wholesale_cents=230000,
        min_stock=2,
    )

    assert display.reference == "AFF-001-B"
    assert display.phone_model == "Galaxy A13"
    assert display.quantity == 7  # inchangée


def test_adjust_stock_persists_supplier_id_on_purchase(db_session):
    supplier = create_supplier(db_session, name="ACME Pièces")
    display = _create_sample(db_session, quantity=5)

    adjust_stock(
        db_session,
        display.id,
        change_quantity=3,
        reason="Achat fournisseur",
        supplier_id=supplier.id,
    )

    [movement] = list_movements(db_session, display_id=display.id, limit=1)
    assert movement.supplier_id == supplier.id
    assert movement.supplier.name == "ACME Pièces"
    assert movement.reseller_id is None


def test_adjust_stock_persists_reseller_id_on_wholesale_sale(db_session):
    reseller = create_reseller(db_session, name="Revendeur Test")
    display = _create_sample(db_session, quantity=5)

    adjust_stock(
        db_session,
        display.id,
        change_quantity=-2,
        reason="Vente",
        is_sale=True,
        sale_price_type="wholesale",
        reseller_id=reseller.id,
    )

    [movement] = list_movements(db_session, display_id=display.id, limit=1)
    assert movement.reseller_id == reseller.id
    assert movement.reseller.name == "Revendeur Test"
    assert movement.supplier_id is None


def test_apply_stock_batch_requires_at_least_one_line(db_session):
    with pytest.raises(StockError):
        apply_stock_batch(db_session, direction=1, reason="Achat fournisseur", lines=[])


def test_apply_stock_batch_applies_all_lines_atomically(db_session):
    supplier = create_supplier(db_session, name="ACME Pièces")
    display_a = _create_sample(db_session, reference="AFF-001", quantity=5)
    display_b = _create_sample(db_session, reference="AFF-002", quantity=2)

    apply_stock_batch(
        db_session,
        direction=1,
        reason="Achat fournisseur",
        lines=[(display_a.id, 3, None), (display_b.id, 10, None)],
        supplier_id=supplier.id,
    )

    db_session.refresh(display_a)
    db_session.refresh(display_b)
    assert display_a.quantity == 8
    assert display_b.quantity == 12

    movements_a = list_movements(db_session, display_id=display_a.id)
    movements_b = list_movements(db_session, display_id=display_b.id)
    assert movements_a[0].supplier_id == supplier.id
    assert movements_b[0].supplier_id == supplier.id


def test_apply_stock_batch_rolls_back_whole_batch_on_failure(db_session):
    display_a = _create_sample(db_session, reference="AFF-001", quantity=5)
    display_b = _create_sample(db_session, reference="AFF-002", quantity=2)
    db_session.commit()

    with pytest.raises(StockError):
        apply_stock_batch(
            db_session,
            direction=-1,
            reason="Vente",
            lines=[(display_a.id, 3, None), (display_b.id, 999, None)],
            is_sale=True,
            sale_price_type="retail",
        )

    db_session.rollback()
    db_session.refresh(display_a)
    db_session.refresh(display_b)
    assert display_a.quantity == 5  # rien n'a bougé, y compris la ligne valide avant l'échec
    assert display_b.quantity == 2


def test_apply_stock_batch_uses_price_override_only_for_sales(db_session):
    display = _create_sample(
        db_session, sale_price_retail_cents=250000, sale_price_wholesale_cents=220000, quantity=5
    )

    apply_stock_batch(
        db_session,
        direction=-1,
        reason="Vente",
        lines=[(display.id, 1, 200000)],
        is_sale=True,
        sale_price_type="retail",
    )

    [movement] = list_movements(db_session, display_id=display.id, limit=1)
    assert movement.unit_sale_price_cents == 200000


def test_deactivate_display_hides_it_from_default_listing(db_session):
    display = _create_sample(db_session)

    deactivate_display(db_session, display.id)

    assert list_displays(db_session) == []
    assert list_displays(db_session, only_active=False) != []


def test_count_low_stock_displays_matches_property(db_session):
    _create_sample(db_session, reference="AFF-001", quantity=1, min_stock=5)
    _create_sample(db_session, reference="AFF-002", quantity=20, min_stock=5)
    _create_sample(db_session, reference="AFF-003", quantity=3, min_stock=3)

    assert count_low_stock_displays(db_session) == 2


def test_count_low_stock_displays_excludes_inactive(db_session):
    display = _create_sample(db_session, reference="AFF-001", quantity=1, min_stock=5)
    deactivate_display(db_session, display.id)

    assert count_low_stock_displays(db_session) == 0


def test_list_movements_pagination_orders_most_recent_first(db_session):
    display = _create_sample(db_session, quantity=0)
    for i in range(5):
        adjust_stock(db_session, display.id, change_quantity=1, reason=f"Entrée {i}")

    first_page = list_movements(db_session, limit=2, offset=0)
    second_page = list_movements(db_session, limit=2, offset=2)

    assert [m.reason for m in first_page] == ["Entrée 4", "Entrée 3"]
    assert [m.reason for m in second_page] == ["Entrée 2", "Entrée 1"]


def test_count_movements_returns_total_regardless_of_pagination(db_session):
    display = _create_sample(db_session, quantity=0)
    for i in range(3):
        adjust_stock(db_session, display.id, change_quantity=1, reason=f"Entrée {i}")

    assert count_movements(db_session) == 3
    assert len(list_movements(db_session, limit=1)) == 1


def test_adjust_stock_sale_requires_price_type(db_session):
    display = _create_sample(db_session, quantity=5)

    with pytest.raises(StockError):
        adjust_stock(db_session, display.id, change_quantity=-1, reason="Vente", is_sale=True)


def test_adjust_stock_sale_uses_retail_price(db_session):
    display = _create_sample(
        db_session, sale_price_retail_cents=250000, sale_price_wholesale_cents=220000, quantity=5
    )

    adjust_stock(
        db_session,
        display.id,
        change_quantity=-1,
        reason="Vente",
        is_sale=True,
        sale_price_type="retail",
    )

    [movement] = list_movements(db_session, display_id=display.id, limit=1)
    assert movement.unit_sale_price_cents == 250000
    assert movement.sale_price_type == "retail"


def test_adjust_stock_sale_uses_wholesale_price(db_session):
    display = _create_sample(
        db_session, sale_price_retail_cents=250000, sale_price_wholesale_cents=220000, quantity=5
    )

    adjust_stock(
        db_session,
        display.id,
        change_quantity=-1,
        reason="Vente",
        is_sale=True,
        sale_price_type="wholesale",
    )

    [movement] = list_movements(db_session, display_id=display.id, limit=1)
    assert movement.unit_sale_price_cents == 220000
    assert movement.sale_price_type == "wholesale"


def test_adjust_stock_sale_allows_custom_price_override(db_session):
    display = _create_sample(
        db_session, sale_price_retail_cents=250000, sale_price_wholesale_cents=220000, quantity=5
    )

    adjust_stock(
        db_session,
        display.id,
        change_quantity=-1,
        reason="Vente",
        is_sale=True,
        sale_price_type="retail",
        unit_sale_price_cents=200000,
    )

    [movement] = list_movements(db_session, display_id=display.id, limit=1)
    assert movement.unit_sale_price_cents == 200000
    assert movement.sale_price_type == "retail"


def test_list_displays_filters_by_category(db_session):
    _create_sample(db_session, reference="AFF-001", category="Afficheur")
    _create_sample(db_session, reference="BAT-001", category="Batterie")

    results = list_displays(db_session, category="Batterie")

    assert [d.reference for d in results] == ["BAT-001"]


def test_list_categories_returns_them_sorted_by_name(db_session):
    create_category(db_session, name="Batterie")
    create_category(db_session, name="Afficheur")

    names = [c.name for c in list_categories(db_session)]

    assert names == ["Afficheur", "Batterie"]


def test_create_category_rejects_duplicate_name(db_session):
    create_category(db_session, name="Afficheur")

    with pytest.raises(StockError):
        create_category(db_session, name="Afficheur")


def test_create_category_adds_new_choice(db_session):
    category = create_category(db_session, name="Coque")

    names = [c.name for c in list_categories(db_session)]
    assert category.name == "Coque"
    assert "Coque" in names


def test_update_category_renames_and_updates_existing_displays(db_session):
    category = create_category(db_session, name="Coque")
    _create_sample(db_session, reference="COQ-001", category="Coque")

    update_category(db_session, category.id, name="Coques")

    names = [c.name for c in list_categories(db_session)]
    assert "Coques" in names
    assert "Coque" not in names
    [display] = list_displays(db_session, category="Coques")
    assert display.reference == "COQ-001"


def test_delete_category_removes_it_from_the_list(db_session):
    category = create_category(db_session, name="Coque")

    delete_category(db_session, category.id)

    names = [c.name for c in list_categories(db_session)]
    assert "Coque" not in names
