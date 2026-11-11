"""Réponses du bot Telegram (telegram_bot/queries.py), sur une base de test."""

import datetime

from app.services import (
    apply_stock_batch, create_display, create_reseller, create_supplier, record_reseller_payment,
)
from telegram_bot import queries

DA = 100


def _setup(db):
    d = create_display(
        db, reference="IMP-AFF-040", category="Afficheur", brand="REALME",
        phone_model="8I 9I A76 A96", quality="", purchase_price_cents=1680 * DA,
        sale_price_retail_cents=3500 * DA, sale_price_wholesale_cents=1900 * DA,
        quantity=3, min_stock=1, location="b32",
    )
    create_display(
        db, reference="IMP-BAT-1", category="Batterie", brand="REDMI", phone_model="9A",
        quality="", purchase_price_cents=500 * DA, sale_price_retail_cents=900 * DA,
        sale_price_wholesale_cents=800 * DA, quantity=0, min_stock=1,
    )
    r = create_reseller(db, name="rezki phone", phone="0541515385")
    s = create_supplier(db, name="SALAM STAR ORAN")
    s.balance_cents = 219500 * DA
    apply_stock_batch(db, direction=-1, reason="Vente", lines=[(d.id, 2, 2000 * DA)], is_sale=True,
                      sale_price_type="wholesale", reseller_id=r.id, versement_cents=1000 * DA)
    record_reseller_payment(db, r.id, 500 * DA)
    return d, r


def test_stock_hides_purchase_price_from_employees(db_session):
    _setup(db_session)
    admin = queries.stock(db_session, "a76", admin=True)
    employee = queries.stock(db_session, "a76", admin=False)
    assert "<b>1</b> pcs" in admin and "📦 B32" in admin and "gros 1 900 DA" in admin
    assert "achat 1 680 DA" in admin and "achat" not in employee
    assert "Aucun produit" in queries.stock(db_session, "zzz", admin=False)


def test_sales_today_profit_only_for_admin(db_session):
    _setup(db_session)
    admin = queries.sales(db_session, "", admin=True)
    employee = queries.sales(db_session, "", admin=False)
    assert "Tickets : <b>1</b>" in admin and "4 000 DA" in admin
    assert "Bénéfice brut : <b>640 DA</b>" in admin  # 2 × (2 000 − 1 680)
    assert "Bénéfice" not in employee
    assert "Période inconnue" in queries.sales(db_session, "demain", admin=True)


def test_low_stock_debts_reseller_and_cash(db_session):
    _setup(db_session)
    assert "BAT 9A REDMI — rupture" in queries.low_stock(db_session, admin=False)
    debts = queries.debts(db_session)
    assert "rezki phone : 2 500 DA" in debts and "SALAM STAR ORAN : 219 500 DA" in debts
    card = queries.reseller(db_session, "rezki", admin=False)
    assert "doit <b>2 500 DA</b>" in card and "4 000 DA" in card and "500 DA" in card
    cash = queries.cash(db_session, "")
    assert "Encaissé sur les ventes : 1 000 DA" in cash
    assert "Versements reçus : 500 DA" in cash
    assert "Solde espèces : <b>1 500 DA</b>" in cash


def test_period_bounds_use_local_time():
    now = datetime.datetime(2026, 11, 11, 10, 0)  # heure d'Alger (UTC+1)
    start, end, local_start, _le, label = queries.period_bounds("", now)
    assert label == "aujourd'hui"
    assert local_start == datetime.datetime(2026, 11, 11, 0, 0)
    assert start == datetime.datetime(2026, 11, 10, 23, 0)  # minuit local = 23 h UTC
    assert queries.period_bounds("hier", now)[2] == datetime.datetime(2026, 11, 10)
    assert queries.period_bounds("semaine", now)[2] == datetime.datetime(2026, 11, 9)  # lundi
