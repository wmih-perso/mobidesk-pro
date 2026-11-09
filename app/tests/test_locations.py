from app.locations import location_label, location_sort_key, suggested_kind
from app.merge import merge_displays
from app.services import create_display, list_displays, update_display


def _make(db, ref, model, kind="", number="", category="Afficheur"):
    return create_display(
        db, reference=ref, category=category, brand="Samsung", phone_model=model,
        quality="", purchase_price_cents=100, sale_price_retail_cents=300,
        sale_price_wholesale_cents=200, quantity=0, min_stock=1,
        location_kind=kind, location_number=number,
    )


def test_label_sort_and_suggestion():
    assert location_label("Boîte", "7") == "Boîte 7"
    assert location_label("Compartiment", "3") == "Comp. 3"
    assert location_label("", "") == ""
    keys = sorted(
        [("Tiroir", "1"), ("", ""), ("Boîte", "10"), ("Boîte", "2"), ("Compartiment", "1")],
        key=lambda k: location_sort_key(*k),
    )
    assert keys == [("Boîte", "2"), ("Boîte", "10"), ("Compartiment", "1"), ("Tiroir", "1"), ("", "")]
    assert suggested_kind("Afficheur") == "Boîte"
    assert suggested_kind("Batterie") == "Compartiment"
    assert suggested_kind("NAPPE DE CHARGE") == "Tiroir"
    assert suggested_kind("COV") == ""


def test_search_ignores_the_location(db_session):
    a22 = _make(db_session, "IMP-1", "A22", "Boîte", "A22")
    other = _make(db_session, "IMP-2", "A10", "Boîte", "A22")
    assert [d.id for d in list_displays(db_session, search="A22")] == [a22.id]
    assert other.location_number == "A22"


def test_update_keeps_location_unless_given_and_merge_fills_it(db_session):
    a = _make(db_session, "A", "C51")
    b = _make(db_session, "B", "C51", "Boîte", "4")
    update_display(
        db_session, b.id, reference="B", category="Afficheur", brand="Samsung",
        phone_model="C51", quality="", purchase_price_cents=100,
        sale_price_retail_cents=300, sale_price_wholesale_cents=200, min_stock=1,
    )
    assert (b.location_kind, b.location_number) == ("Boîte", "4")
    keep = merge_displays(db_session, a.id, b.id)
    assert location_label(keep.location_kind, keep.location_number) == "Boîte 4"
