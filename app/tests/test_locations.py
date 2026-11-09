from app.locations import location_label, location_sort_key
from app.merge import merge_displays
from app.services import create_display, list_displays, update_display


def _make(db, ref, model, location=""):
    return create_display(
        db, reference=ref, category="Afficheur", brand="Samsung", phone_model=model,
        quality="", purchase_price_cents=100, sale_price_retail_cents=300,
        sale_price_wholesale_cents=200, quantity=0, min_stock=1, location=location,
    )


def test_label_and_natural_sort():
    assert location_label(" b32 ") == "B32"
    assert location_label("") == ""
    codes = ["T2", "", "B32", "C5", "B10", "b2", "C1"]
    assert [location_label(c) for c in sorted(codes, key=location_sort_key)] == [
        "B2", "B10", "B32", "C1", "C5", "T2", "",
    ]


def test_location_saved_in_capitals_and_ignored_by_search(db_session):
    a22 = _make(db_session, "IMP-1", "A22", "b32")
    _make(db_session, "IMP-2", "A10", "A22")
    assert a22.location_number == "B32"
    assert [d.id for d in list_displays(db_session, search="A22")] == [a22.id]
    assert list_displays(db_session, search="B32") == []


def test_update_keeps_location_unless_given_and_merge_fills_it(db_session):
    a = _make(db_session, "A", "C51")
    b = _make(db_session, "B", "C51", "B4")
    update_display(
        db_session, b.id, reference="B", category="Afficheur", brand="Samsung",
        phone_model="C51", quality="", purchase_price_cents=100,
        sale_price_retail_cents=300, sale_price_wholesale_cents=200, min_stock=1,
    )
    assert b.location_number == "B4"
    keep = merge_displays(db_session, a.id, b.id)
    assert keep.location_number == "B4"
