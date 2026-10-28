import datetime

import pytest

from app.services import (
    StockError,
    create_display,
    create_expense,
    delete_expense,
    list_expenses,
    sum_expenses_cents,
    sum_profit_cents,
    update_expense,
    apply_stock_batch,
)

D = datetime.datetime


def test_expenses_sum_and_period_filter(db_session):
    create_expense(db_session, expense_date=D(2026, 10, 1), kind="Livraison", amount_cents=100000)
    create_expense(db_session, expense_date=D(2026, 10, 20), kind="Transport", amount_cents=50000)
    create_expense(db_session, expense_date=D(2026, 9, 5), kind="Autre", amount_cents=7000)

    assert sum_expenses_cents(db_session) == 157000
    oct_start, oct_end = D(2026, 10, 1), D(2026, 10, 31, 23, 59)
    assert sum_expenses_cents(db_session, start=oct_start, end=oct_end) == 150000
    assert [e.kind for e in list_expenses(db_session, start=oct_start, end=oct_end)] == [
        "Transport", "Livraison",
    ]


def test_expense_update_and_delete(db_session):
    e = create_expense(db_session, expense_date=D(2026, 10, 1), kind="Livraison", amount_cents=100)
    update_expense(db_session, e.id, expense_date=D(2026, 10, 2), kind="", amount_cents=250, note=" x ")
    assert (e.kind, e.amount_cents, e.note) == ("Autre", 250, "x")
    delete_expense(db_session, e.id)
    assert sum_expenses_cents(db_session) == 0
    with pytest.raises(StockError):
        delete_expense(db_session, e.id)


def test_expense_amount_must_be_positive(db_session):
    with pytest.raises(StockError):
        create_expense(db_session, expense_date=D(2026, 10, 1), kind="Livraison", amount_cents=0)


def test_expenses_reduce_net_profit(db_session):
    display = create_display(
        db_session, reference="A", category="Afficheur", brand="", phone_model="9A",
        quality="", purchase_price_cents=100, sale_price_retail_cents=300,
        sale_price_wholesale_cents=200, quantity=5, min_stock=1,
    )
    apply_stock_batch(
        db_session, direction=-1, reason="Vente", lines=[(display.id, 1, 300)],
        is_sale=True, sale_price_type="retail",
    )
    before = sum_profit_cents(db_session)
    assert before == 200
    create_expense(db_session, expense_date=D.now(), kind="Livraison", amount_cents=50)
    assert sum_profit_cents(db_session) == 150
