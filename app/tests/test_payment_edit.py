import datetime

import pytest
from sqlalchemy import select

from app.models import ResellerPayment, StockMovement
from app.services import (
    StockError, apply_stock_batch, create_display, create_reseller, create_supplier,
    delete_payment, record_reseller_payment, record_supplier_payment, set_supplier_balance,
    update_payment,
)

DA = 100


def _display(db):
    return create_display(
        db, reference="AFF-1", category="Afficheur", brand="Samsung", phone_model="A15",
        quality="", purchase_price_cents=1000 * DA, sale_price_retail_cents=3000 * DA,
        sale_price_wholesale_cents=2000 * DA, quantity=10, min_stock=1,
    )


def _at(db, obj, day):
    obj.created_at = datetime.datetime(2026, 11, day, 10)
    db.flush()


def test_edit_reseller_payment_amount_fixes_debt_and_later_snapshots(db_session):
    db = db_session
    d = _display(db)
    r = create_reseller(db, name="Rezki")
    r.balance_cents = 10000 * DA
    p1 = record_reseller_payment(db, r.id, 2000 * DA, "espèces")       # 10 000 → 8 000
    _at(db, p1, 5)
    _, bid = apply_stock_batch(db, direction=-1, reason="Vente", lines=[(d.id, 1, 2000 * DA)],
                               is_sale=True, sale_price_type="wholesale", reseller_id=r.id,
                               versement_cents=0)                    # 8 000 → 10 000
    sale = db.scalars(select(StockMovement).where(StockMovement.movement_batch_id == bid)).one()
    _at(db, sale, 6)
    p2 = record_reseller_payment(db, r.id, 1000 * DA)                   # 10 000 → 9 000
    _at(db, p2, 7)
    assert r.balance_cents == 9000 * DA

    update_payment(db, "resellers", p1.id, amount_cents=2500 * DA, created_at=p1.created_at,
                   note="espèces")
    assert r.balance_cents == 8500 * DA
    assert (p1.balance_before_cents, p1.balance_after_cents) == (10000 * DA, 7500 * DA)
    db.refresh(sale); db.refresh(p2)
    assert sale.reseller_balance_before_cents == 7500 * DA   # ticket réimprimé juste
    assert (p2.balance_before_cents, p2.balance_after_cents) == (9500 * DA, 8500 * DA)
    assert "2 000 DA → 2 500 DA" in p1.note and p1.note.startswith("espèces")


def test_change_date_only_and_delete(db_session):
    db = db_session
    r = create_reseller(db, name="Kader")
    r.balance_cents = 5000 * DA
    p = record_reseller_payment(db, r.id, 1000 * DA)
    new_date = datetime.datetime(2026, 11, 1, 9, 30)
    update_payment(db, "resellers", p.id, amount_cents=1000 * DA, created_at=new_date, note="")
    assert p.created_at == new_date and r.balance_cents == 4000 * DA and "date" in p.note
    delete_payment(db, "resellers", p.id)
    assert r.balance_cents == 5000 * DA
    assert db.scalars(select(ResellerPayment)).all() == []


def test_supplier_payment_edit_and_adjustment_is_protected(db_session):
    db = db_session
    s = create_supplier(db, name="Salam")
    s.balance_cents = 219500 * DA
    p = record_supplier_payment(db, s.id, 100000 * DA)
    update_payment(db, "suppliers", p.id, amount_cents=120000 * DA, created_at=p.created_at)
    assert s.balance_cents == 99500 * DA
    adj = set_supplier_balance(db, s.id, 0)
    with pytest.raises(StockError, match="ajustement"):
        update_payment(db, "suppliers", adj.id, amount_cents=1, created_at=adj.created_at)
    with pytest.raises(StockError):
        update_payment(db, "suppliers", p.id, amount_cents=0, created_at=p.created_at)
