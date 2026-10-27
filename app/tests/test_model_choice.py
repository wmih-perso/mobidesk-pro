from app.labels import article_label, split_models
from app.services import apply_stock_batch, create_display, list_movements


def test_split_models():
    assert split_models("9A / 9C, 10 ; 9a") == ["9A", "9C", "10"]
    assert split_models("P20 LITE") == ["P20 LITE"]
    assert split_models("") == []


def test_article_label_with_chosen_model():
    assert article_label("Afficheur", "9A / 9C", "R1", "9A") == "AFF | 9A"
    # sans modèle choisi : ancien libellé
    assert article_label("Afficheur", "P20 LITE", "R1") == "AFF P20 LITE"


def test_sale_stores_chosen_model(db_session):
    display = create_display(
        db_session, reference="AFF-9", category="Afficheur", brand="Infinix",
        phone_model="9A / 9C", quality="", purchase_price_cents=100,
        sale_price_retail_cents=200, sale_price_wholesale_cents=150,
        quantity=5, min_stock=1,
    )
    apply_stock_batch(
        db_session, direction=-1, reason="Vente", lines=[(display.id, 1, 200, "9A")],
        is_sale=True, sale_price_type="retail",
    )
    sale = [m for m in list_movements(db_session, display_id=display.id) if m.is_sale][0]
    assert sale.model_label == "9A"
