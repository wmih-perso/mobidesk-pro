import pytest

from app.merge import list_lots, merge_displays, suggest_merges
from app.returns import LotShortageError, create_supplier_return, list_purchases_for_return
from app.models import Display
from app.services import (
    StockError, apply_stock_batch, create_display, create_supplier, list_displays,
)


def _make(db, ref, model, qty=0, price=100, brand="Realme", category="Afficheur"):
    return create_display(
        db, reference=ref, category=category, brand=brand, phone_model=model,
        quality="", purchase_price_cents=price, sale_price_retail_cents=300,
        sale_price_wholesale_cents=200, quantity=qty, min_stock=1,
    )


def _buy(db, display, supplier, qty, price):
    apply_stock_batch(
        db, direction=1, reason="Achat fournisseur", lines=[(display.id, qty, price)],
        supplier_id=supplier.id,
    )


def test_suggest_merges_finds_c51_but_not_s20_vs_s20plus(db_session):
    _make(db_session, "IMP-AFF-044", "C51 C53 NOTE")
    _make(db_session, "ZT-1", "C53 / C51 / NOTE 50")
    _make(db_session, "IMP-COV-042", "S20", category="COV")
    _make(db_session, "IMP-COV-043", "S20+", category="COV")
    _make(db_session, "X", "C51 C53 NOTE", brand="Samsung")  # autre marque
    pairs = suggest_merges(db_session)
    assert [(p.a.reference, p.b.reference) for p in pairs] == [("IMP-AFF-044", "ZT-1")]


def test_merge_keeps_lots_and_moves_everything(db_session):
    salam = create_supplier(db_session, name="Salam")
    zaim = create_supplier(db_session, name="Zaim")
    a = _make(db_session, "IMP-AFF-044", "C51 C53 NOTE")
    b = _make(db_session, "ZT-1", "C53 / C51 / NOTE 50")
    _buy(db_session, a, salam, 1, 148000)
    _buy(db_session, b, zaim, 6, 155500)

    keep = merge_displays(db_session, a.id, b.id)

    assert keep.quantity == 7
    lots = list_lots(db_session, keep.id)
    assert [(lot.supplier_name, lot.unit_price_cents, lot.quantity_remaining) for lot in lots] == [
        ("Salam", 148000, 1), ("Zaim", 155500, 6),
    ]
    assert keep.purchase_price_cents == round((148000 + 6 * 155500) / 7)
    assert "ZT-1" in keep.aliases
    assert not db_session.get(Display, b.id).is_active
    # la recherche retrouve l'ancienne référence
    assert [d.id for d in list_displays(db_session, search="ZT-1")] == [a.id]
    with pytest.raises(StockError):
        merge_displays(db_session, a.id, b.id)  # déjà fusionné


def test_supplier_return_warns_when_lot_is_empty(db_session):
    salam = create_supplier(db_session, name="Salam")
    zaim = create_supplier(db_session, name="Zaim")
    a = _make(db_session, "A", "C51")
    _buy(db_session, a, salam, 2, 100)
    _buy(db_session, a, zaim, 3, 120)
    # 2 pièces vendues : FIFO consomme le lot de Salam
    apply_stock_batch(
        db_session, direction=-1, reason="Vente", lines=[(a.id, 2, 300)],
        is_sale=True, sale_price_type="retail",
    )
    ops = list_purchases_for_return(db_session, supplier_id=salam.id)
    assert ops[0].lines[0].lot_remaining == 0
    purchase_id = ops[0].lines[0].movement_id
    # avertissement : le logiciel pense que le lot de Salam est épuisé
    with pytest.raises(LotShortageError):
        create_supplier_return(
            db_session, supplier_id=salam.id, purchase_lines=[(purchase_id, 1)], settlement="debt",
        )
    # …mais l'utilisateur peut confirmer (il voit l'autocollant sur la pièce)
    doc = create_supplier_return(
        db_session, supplier_id=salam.id, purchase_lines=[(purchase_id, 1)],
        settlement="debt", confirm_lot_shortage=True,
    )
    assert doc.amount_cents == 100
    # le lot de Salam étant vide, la pièce sort du stock restant (lot de Zaim)
    ops = list_purchases_for_return(db_session, supplier_id=zaim.id)
    assert ops[0].lines[0].lot_remaining == 2


def test_suggest_ignores_pro_vs_pro_max(db_session):
    _make(db_session, "A", "12 PRO ORIGINAL")
    _make(db_session, "B", "12 PRO MAX ORIGINAL")
    assert suggest_merges(db_session) == []
