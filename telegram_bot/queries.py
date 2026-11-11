"""Réponses du bot : lecture seule de la base MobiDesk (copie de la dernière
sauvegarde). Réutilise les calculs de l'application (app/services.py) pour
que les chiffres soient identiques à ceux de MobiDesk.

Chaque fonction reçoit une session SQLAlchemy et `admin` (rôle de la
personne) et renvoie un texte HTML pour Telegram. Les employés ne voient
ni bénéfices, ni prix d'achat, ni dettes, ni caisse.
"""

from __future__ import annotations

import datetime
import html
import os
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.labels import article_label
from app.locations import location_label
from app.models import Expense, Reseller, ResellerPayment, ReturnDoc, StockMovement, Supplier, SupplierPayment
from app.search import filter_and_rank
from app.services import (
    list_displays, list_reseller_payments, list_resellers, list_sale_batches, list_sales,
    list_suppliers, sum_expenses_cents, sum_profit_cents, sum_return_deduction_cents,
)

TZ = ZoneInfo(os.environ.get("BOT_TIMEZONE", "Africa/Algiers"))
MAX_LINES = 25


def da(cents: int) -> str:
    return f"{round(cents / 100):,}".replace(",", " ") + " DA"


def esc(text) -> str:
    return html.escape(str(text or ""))


# ---------------------------------------------------------------------------
# Dates : la base est en UTC, l'affichage et les périodes en heure locale
# ---------------------------------------------------------------------------


def to_local(utc_naive: datetime.datetime) -> datetime.datetime:
    return utc_naive.replace(tzinfo=datetime.timezone.utc).astimezone(TZ).replace(tzinfo=None)


def _local_to_utc(local_naive: datetime.datetime) -> datetime.datetime:
    return local_naive.replace(tzinfo=TZ).astimezone(datetime.timezone.utc).replace(tzinfo=None)


PERIODS = {
    "": "aujourd'hui", "jour": "aujourd'hui", "aujourd'hui": "aujourd'hui", "aujourdhui": "aujourd'hui",
    "hier": "hier", "semaine": "cette semaine", "mois": "ce mois-ci",
}


def period_bounds(word: str, now_local: datetime.datetime | None = None):
    """(début UTC, fin UTC, début local, fin local, libellé) d'une période."""
    now_local = now_local or datetime.datetime.now(TZ).replace(tzinfo=None)
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    label = PERIODS.get(word.strip().lower())
    if label is None:
        raise ValueError(word)
    if label == "hier":
        start, end = today - datetime.timedelta(days=1), today - datetime.timedelta(microseconds=1)
    elif label == "cette semaine":
        start, end = today - datetime.timedelta(days=today.weekday()), now_local
    elif label == "ce mois-ci":
        start, end = today.replace(day=1), now_local
    else:
        start, end = today, now_local
    return _local_to_utc(start), _local_to_utc(end), start, end, label


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


def stock(session: Session, query: str, admin: bool) -> str:
    query = query.strip()
    if not query:
        return "Tapez le produit à chercher, par exemple : <code>/stock a15</code>"
    found = list_displays(session, search=query)
    if not found:
        return f"Aucun produit trouvé pour « {esc(query)} »."
    lines = [f"🔍 <b>{len(found)} produit(s)</b> pour « {esc(query)} »\n"]
    for d in found[:MAX_LINES]:
        name = f"{article_label(d.category, d.phone_model, d.reference)} — {d.brand}".strip(" —")
        qty = f"<b>{d.quantity}</b> pcs" if d.quantity > 0 else "<b>rupture</b>"
        parts = [qty]
        location = location_label(d.location_number)
        if location:
            parts.append(f"📦 {esc(location)}")
        parts.append(f"gros {da(d.sale_price_wholesale_cents)}")
        if admin:
            parts.append(f"achat {da(d.purchase_price_cents)}")
        lines.append(f"• {esc(name)}\n   {' · '.join(parts)}")
    if len(found) > MAX_LINES:
        lines.append(f"\n… et {len(found) - MAX_LINES} autre(s). Précisez la recherche.")
    return "\n".join(lines)


def low_stock(session: Session, admin: bool) -> str:
    items = sorted(list_displays(session, stock_state="low"), key=lambda d: (d.quantity, d.category))
    if not items:
        return "✅ Aucun produit en stock bas."
    out = [f"⚠️ <b>{len(items)} produit(s) en stock bas</b>\n"]
    for d in items[:60]:
        name = f"{article_label(d.category, d.phone_model, d.reference)} {d.brand}".strip()
        state = "rupture" if d.quantity <= 0 else f"{d.quantity} (min {d.min_stock})"
        out.append(f"• {esc(name)} — {state}")
    if len(items) > 60:
        out.append(f"… et {len(items) - 60} autre(s).")
    return "\n".join(out)


def sales(session: Session, word: str, admin: bool) -> str:
    try:
        start, end, local_start, local_end, label = period_bounds(word)
    except ValueError:
        return "Période inconnue. Exemples : <code>/ventes</code>, <code>/ventes hier</code>, <code>/ventes semaine</code>, <code>/ventes mois</code>"
    # Les calculs de MobiDesk prennent des bornes en heure locale.
    moves = list_sales(session, start=local_start, end=local_end)
    tickets = {m.movement_batch_id or -m.id for m in moves}
    revenue = sum(m.unit_sale_price_cents * abs(m.change_quantity) for m in moves)
    pieces = sum(abs(m.change_quantity) for m in moves)
    out = [
        f"🧾 <b>Ventes {label}</b>",
        f"Tickets : <b>{len(tickets)}</b> · Pièces : <b>{pieces}</b>",
        f"Chiffre d'affaires : <b>{da(revenue)}</b>",
    ]
    if admin:
        gross = sum(m.profit_cents for m in moves)
        returns = sum_return_deduction_cents(session, start=local_start, end=local_end)
        expenses = sum_expenses_cents(session, start=local_start, end=local_end)
        net = sum_profit_cents(session, start=local_start, end=local_end)
        out.append(f"Bénéfice brut : <b>{da(gross)}</b>")
        if returns:
            out.append(f"− Retours : {da(returns)}")
        if expenses:
            out.append(f"− Frais : {da(expenses)}")
        if returns or expenses:
            out.append(f"Bénéfice net : <b>{da(net)}</b>")
    if moves:
        by_product: dict[str, int] = {}
        for m in moves:
            d = m.display
            key = article_label(d.category, d.phone_model, d.reference, m.model_label) if d else "?"
            by_product[key] = by_product.get(key, 0) + abs(m.change_quantity)
        top = sorted(by_product.items(), key=lambda kv: -kv[1])[:5]
        out.append("\n<b>Les plus vendus</b>")
        out += [f"• {esc(name)} × {qty}" for name, qty in top]
    return "\n".join(out)


def debts(session: Session) -> str:
    resellers = list_resellers(session)
    owe_us = sorted((r for r in resellers if r.balance_cents > 0), key=lambda r: -r.balance_cents)
    we_owe_resellers = [r for r in resellers if r.balance_cents < 0]
    suppliers = sorted((s for s in list_suppliers(session) if s.balance_cents > 0),
                       key=lambda s: -s.balance_cents)
    out = ["💳 <b>Dettes</b>\n", "<b>Revendeurs qui vous doivent</b>"]
    out += [f"• {esc(r.name)} : {da(r.balance_cents)}" for r in owe_us] or ["• aucun"]
    if owe_us:
        out.append(f"Total : <b>{da(sum(r.balance_cents for r in owe_us))}</b>")
    if we_owe_resellers:
        out.append("\n<b>Vous devez à des revendeurs (avoir)</b>")
        out += [f"• {esc(r.name)} : {da(-r.balance_cents)}" for r in we_owe_resellers]
    out.append("\n<b>À payer aux fournisseurs</b>")
    out += [f"• {esc(s.name)} : {da(s.balance_cents)}" for s in suppliers] or ["• rien"]
    if suppliers:
        out.append(f"Total : <b>{da(sum(s.balance_cents for s in suppliers))}</b>")
    return "\n".join(out)


def reseller(session: Session, query: str, admin: bool) -> str:
    query = query.strip()
    resellers = list_resellers(session)
    if not query:
        names = ", ".join(esc(r.name) for r in resellers)
        return f"Tapez le nom, par exemple : <code>/revendeur rezki</code>\n\nRevendeurs : {names}"
    found = filter_and_rank(query, resellers, lambda r: (r.name, r.phone or ""))
    if not found:
        return f"Aucun revendeur trouvé pour « {esc(query)} »."
    if len(found) > 1 and found[0].name.lower() != query.lower():
        return "Plusieurs revendeurs correspondent :\n" + "\n".join(f"• {esc(r.name)}" for r in found[:15])
    r = found[0]
    if r.balance_cents > 0:
        balance = f"doit <b>{da(r.balance_cents)}</b>"
    elif r.balance_cents < 0:
        balance = f"avoir de <b>{da(-r.balance_cents)}</b>"
    else:
        balance = "<b>soldé</b> ✅"
    out = [f"👤 <b>{esc(r.name)}</b>" + (f"  ☎ {esc(r.phone)}" if r.phone else ""), f"Solde : {balance}"]
    batches = [b for b in list_sale_batches(session) if b["movements"][0].reseller_id == r.id][:5]
    out.append("\n<b>Dernières ventes</b>")
    if batches:
        for b in batches:
            out.append(f"• {to_local(b['date']):%d/%m %H:%M} — ticket #{b['batch_id']} — "
                       f"{b['nb_items']} produit(s) — {da(b['total_cents'])}")
    else:
        out.append("• aucune")
    payments = list_reseller_payments(session, r.id)[:5]
    out.append("\n<b>Derniers versements</b>")
    if payments:
        for p in payments:
            what = "ajustement" if getattr(p, "kind", "payment") == "adjust" else da(p.amount_cents)
            out.append(f"• {to_local(p.created_at):%d/%m %H:%M} — {what}")
    else:
        out.append("• aucun")
    return "\n".join(out)


def cash(session: Session, word: str = "") -> str:
    """Espèces de la période : encaissé sur les ventes + versements reçus −
    remboursements en espèces − frais − paiements aux fournisseurs."""
    try:
        start, end, local_start, local_end, label = period_bounds(word)
    except ValueError:
        return "Période inconnue. Exemples : <code>/caisse</code>, <code>/caisse hier</code>"
    sale_batches = {}
    for m in list_sales(session, start=local_start, end=local_end):
        key = m.movement_batch_id or -m.id
        info = sale_batches.setdefault(key, {"total": 0, "versement": m.versement_cents, "reseller": m.reseller_id})
        info["total"] += m.unit_sale_price_cents * abs(m.change_quantity)
    on_sales = sum(b["versement"] if b["reseller"] else b["total"] for b in sale_batches.values())
    payments = session.scalars(
        select(ResellerPayment).where(ResellerPayment.created_at >= start, ResellerPayment.created_at <= end)
    ).all()
    received = sum(p.amount_cents for p in payments if getattr(p, "kind", "payment") != "adjust")
    refunds = sum(
        d.amount_cents for d in session.scalars(
            select(ReturnDoc).where(ReturnDoc.kind == "customer", ReturnDoc.settlement == "cash",
                                    ReturnDoc.created_at >= start, ReturnDoc.created_at <= end)
        )
    )
    expenses = sum_expenses_cents(session, start=local_start, end=local_end)
    supplier_paid = sum(
        p.amount_cents for p in session.scalars(
            select(SupplierPayment).where(SupplierPayment.created_at >= start, SupplierPayment.created_at <= end,
                                          SupplierPayment.kind == "payment")
        )
    )
    purchase_batches = {}
    for m in session.scalars(
        select(StockMovement).where(StockMovement.change_quantity > 0, StockMovement.supplier_id.is_not(None),
                                    StockMovement.return_id.is_(None),
                                    StockMovement.created_at >= start, StockMovement.created_at <= end)
    ):
        purchase_batches[m.movement_batch_id or -m.id] = m.supplier_versement_cents
    supplier_paid += sum(purchase_batches.values())
    total = on_sales + received - refunds - expenses - supplier_paid
    out = [
        f"💰 <b>Caisse {label}</b>",
        f"Encaissé sur les ventes : {da(on_sales)}",
        f"Versements reçus : {da(received)}",
    ]
    if refunds:
        out.append(f"− Remboursements (retours) : {da(refunds)}")
    if expenses:
        out.append(f"− Frais : {da(expenses)}")
    if supplier_paid:
        out.append(f"− Payé aux fournisseurs : {da(supplier_paid)}")
    out.append(f"\nSolde espèces : <b>{da(total)}</b>")
    return "\n".join(out)
