"""Suivi du capital (onglet Capital, admin).

Le capital de départ est une photo prise à une date : stock au prix d'achat
+ espèces en caisse. Ensuite, sans compter le bénéfice, l'argent ne fait que
changer de forme — un achat transforme des espèces en stock, une vente
transforme du stock en espèces (au prix d'achat) — et le total doit rester
égal au capital de départ :

    stock + espèces du capital + défectueux en attente + échanges en attente
        = capital de départ  (± écart : pièces perdues, cassées, corrigées)

Le bénéfice (prix de vente − prix d'achat) est calculé à part : c'est ce que
l'on peut retirer de la caisse.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import CapitalStart, Display, Reseller, ReturnDoc, ReturnLine, StockBatch, StockMovement
from app.services import StockError, sum_profit_cents


def current_stock_value_cents(session: Session) -> int:
    """Valeur du stock au prix d'achat réel (lots FIFO restants)."""
    value = session.scalar(
        select(func.sum(StockBatch.quantity_remaining * StockBatch.unit_purchase_price_cents))
        .join(Display, StockBatch.display_id == Display.id)
        .where(Display.is_active.is_(True), StockBatch.quantity_remaining > 0)
    )
    return value or 0


def get_capital_start(session: Session) -> CapitalStart | None:
    return session.scalar(select(CapitalStart).order_by(CapitalStart.id.desc()).limit(1))


def start_capital_tracking(session: Session, *, cash_cents: int, created_by: str = "") -> CapitalStart:
    """Démarre (ou redémarre) le suivi : photo du stock actuel + espèces."""
    if cash_cents < 0:
        raise StockError("Le montant des espèces ne peut pas être négatif.")
    start = CapitalStart(
        stock_value_cents=current_stock_value_cents(session),
        cash_cents=cash_cents,
        created_by=created_by,
    )
    session.add(start)
    session.flush()
    return start


@dataclass
class CapitalReport:
    start: CapitalStart
    stock_cents: int  # stock actuel au prix d'achat
    cash_cents: int  # espèces du capital (sans bénéfice)
    defective_cents: int  # défectueux rendus par des clients, pas encore renvoyés
    exchange_cents: int  # renvoyés au fournisseur, échange attendu
    purchases_cents: int
    sold_cost_cents: int
    customer_returns_cost_cents: int
    supplier_refunds_cents: int
    profit_cents: int  # bénéfice depuis le départ (à part)
    reseller_debts_cents: int  # dettes des revendeurs aujourd'hui

    @property
    def total_cents(self) -> int:
        return self.stock_cents + self.cash_cents + self.defective_cents + self.exchange_cents

    @property
    def gap_cents(self) -> int:
        """Total − capital de départ : négatif = pièces perdues / cassées /
        corrigées à la baisse."""
        return self.total_cents - self.start.capital_cents


def _sum_movements(session: Session, since: datetime.datetime, *conditions) -> int:
    value = session.scalar(
        select(func.sum(StockMovement.unit_purchase_price_cents * func.abs(StockMovement.change_quantity)))
        .where(StockMovement.created_at >= since, *conditions)
    )
    return value or 0


def _sum_return_lines(session: Session, since: datetime.datetime, *conditions) -> int:
    value = session.scalar(
        select(func.sum(ReturnLine.unit_cost_cents * ReturnLine.quantity))
        .join(ReturnDoc, ReturnLine.return_id == ReturnDoc.id)
        .where(ReturnDoc.created_at >= since, *conditions)
    )
    return value or 0


def compute_capital(session: Session) -> CapitalReport | None:
    start = get_capital_start(session)
    if start is None:
        return None
    since = start.started_at

    purchases = _sum_movements(
        session, since,
        StockMovement.change_quantity > 0,
        StockMovement.return_id.is_(None),
        or_(StockMovement.reason.like("Achat%"), StockMovement.reason == "Stock initial"),
    )
    sold_cost = _sum_movements(session, since, StockMovement.is_sale.is_(True))
    customer_returns = _sum_return_lines(session, since, ReturnDoc.kind == "customer")
    # Retours fournisseur réglés (avoir ou espèces) : le capital redevient de l'argent.
    supplier_refunds = _sum_return_lines(
        session, since, ReturnDoc.kind == "supplier", ReturnDoc.status == "done",
        ReturnDoc.settlement.in_(("debt", "cash")),
    )
    exchanges = _sum_return_lines(
        session, since, ReturnDoc.kind == "supplier", ReturnDoc.status == "pending",
    )

    # Défectueux rendus depuis le départ et pas encore renvoyés.
    from app.returns import list_pending_defectives

    defective = sum(
        d.remaining * d.unit_cost_cents
        for d in list_pending_defectives(session)
        if d.returned_at >= since
    )

    cash = start.cash_cents - purchases + sold_cost - customer_returns + supplier_refunds
    debts = session.scalar(
        select(func.sum(Reseller.balance_cents)).where(Reseller.balance_cents > 0)
    ) or 0

    return CapitalReport(
        start=start,
        stock_cents=current_stock_value_cents(session),
        cash_cents=cash,
        defective_cents=defective,
        exchange_cents=exchanges,
        purchases_cents=purchases,
        sold_cost_cents=sold_cost,
        customer_returns_cost_cents=customer_returns,
        supplier_refunds_cents=supplier_refunds,
        profit_cents=sum_profit_cents(session, start=since),
        reseller_debts_cents=debts,
    )
