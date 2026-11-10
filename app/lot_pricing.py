"""Prix gros par lot et coût FIFO d'une quantité.

Chaque lot (StockBatch) peut porter son propre prix gros. Le prix gros du
produit est celui du lot le plus ancien encore en stock (la prochaine
pièce vendue) : quand ce lot est épuisé, le prix passe automatiquement à
celui du lot suivant. Un lot sans prix (créé avant la v2.4.22) laisse le
prix de la fiche produit tel quel.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Display, StockBatch


@dataclass(frozen=True)
class LotInfo:
    remaining: int
    unit_cost_cents: int
    wholesale_cents: int | None


def _fifo_order(stmt):
    return stmt.order_by(StockBatch.created_at, StockBatch.id)


def refresh_wholesale_price(session: Session, display: Display) -> None:
    """Prix gros du produit = prix du lot le plus ancien encore en stock."""
    session.flush()
    first = session.scalar(
        _fifo_order(
            select(StockBatch).where(
                StockBatch.display_id == display.id, StockBatch.quantity_remaining > 0
            )
        ).limit(1)
    )
    if first is not None and first.sale_price_wholesale_cents is not None:
        display.sale_price_wholesale_cents = first.sale_price_wholesale_cents


def set_current_lot_wholesale(session: Session, display: Display, price_cents: int) -> None:
    """Prix gros changé à la main dans la fiche produit : il vaut pour le
    lot en cours (sinon il serait remplacé au prochain mouvement)."""
    first = session.scalar(
        _fifo_order(
            select(StockBatch).where(
                StockBatch.display_id == display.id, StockBatch.quantity_remaining > 0
            )
        ).limit(1)
    )
    if first is not None:
        first.sale_price_wholesale_cents = price_cents


def lots_by_display(session: Session, display_ids=None) -> dict[int, list[LotInfo]]:
    """Lots encore en stock, dans l'ordre FIFO, par produit."""
    stmt = _fifo_order(select(StockBatch).where(StockBatch.quantity_remaining > 0))
    if display_ids is not None:
        stmt = stmt.where(StockBatch.display_id.in_(list(display_ids)))
    result: dict[int, list[LotInfo]] = defaultdict(list)
    for batch in session.scalars(stmt):
        result[batch.display_id].append(
            LotInfo(batch.quantity_remaining, batch.unit_purchase_price_cents,
                    batch.sale_price_wholesale_cents)
        )
    return dict(result)


def _take(lots: list[LotInfo], quantity: int):
    """(pièces, lot) pour les `quantity` prochaines pièces vendues (FIFO)."""
    for lot in lots:
        if quantity <= 0:
            return
        take = min(lot.remaining, quantity)
        yield take, lot
        quantity -= take


def wholesale_for_quantity(lots: list[LotInfo], quantity: int, default_cents: int) -> int:
    """Prix gros unitaire proposé pour `quantity` pièces : moyenne des prix
    des lots qui seront consommés (option « a »). Lot sans prix → prix de
    la fiche produit."""
    if quantity <= 0:
        return default_cents
    total = covered = 0
    for take, lot in _take(lots, quantity):
        price = lot.wholesale_cents if lot.wholesale_cents is not None else default_cents
        total += take * price
        covered += take
    total += (quantity - covered) * default_cents  # au-delà du stock connu
    return round(total / quantity)


def fifo_cost_for_quantity(lots: list[LotInfo], quantity: int, default_cents: int) -> int:
    """Coût d'achat total des `quantity` prochaines pièces vendues (FIFO)."""
    total = covered = 0
    for take, lot in _take(lots, quantity):
        total += take * lot.unit_cost_cents
        covered += take
    return total + (quantity - covered) * default_cents
