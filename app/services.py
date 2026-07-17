"""Service métier de gestion du stock d'afficheurs.

Toute la logique passe par ici — l'interface PySide6 ne doit jamais
manipuler la base de données directement.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Display, StockMovement


class StockError(Exception):
    """Erreur métier destinée à être affichée à l'utilisateur (en français)."""


def list_displays(
    session: Session,
    *,
    search: str = "",
    only_active: bool = True,
    only_low_stock: bool = False,
) -> list[Display]:
    stmt = select(Display)

    if only_active:
        stmt = stmt.where(Display.is_active.is_(True))

    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            Display.reference.ilike(pattern)
            | Display.brand.ilike(pattern)
            | Display.phone_model.ilike(pattern)
        )

    if only_low_stock:
        stmt = stmt.where(Display.quantity <= Display.min_stock)

    stmt = stmt.order_by(Display.brand, Display.phone_model)
    return list(session.scalars(stmt).all())


def count_low_stock_displays(session: Session) -> int:
    """Nombre d'afficheurs actifs en alerte de stock bas — sans charger
    les lignes elles-mêmes (utilisé pour la carte statistique)."""
    stmt = (
        select(func.count())
        .select_from(Display)
        .where(Display.is_active.is_(True), Display.quantity <= Display.min_stock)
    )
    return session.scalar(stmt) or 0


def get_display(session: Session, display_id: int) -> Display:
    display = session.get(Display, display_id)
    if display is None:
        raise StockError("Cet afficheur n'existe pas ou a été supprimé.")
    return display


def _validate_display_fields(reference: str, quantity: int, min_stock: int) -> None:
    if not reference or not reference.strip():
        raise StockError("La référence est obligatoire.")
    if quantity < 0:
        raise StockError("La quantité ne peut pas être négative.")
    if min_stock < 0:
        raise StockError("Le stock minimum ne peut pas être négatif.")


def create_display(
    session: Session,
    *,
    reference: str,
    brand: str,
    phone_model: str,
    quality: str,
    color: str,
    purchase_price_cents: int,
    sale_price_cents: int,
    quantity: int,
    min_stock: int,
    notes: str = "",
) -> Display:
    reference = reference.strip()
    _validate_display_fields(reference, quantity, min_stock)

    existing = session.scalar(select(Display).where(Display.reference == reference))
    if existing is not None:
        raise StockError(f"La référence « {reference} » existe déjà.")

    display = Display(
        reference=reference,
        brand=brand.strip(),
        phone_model=phone_model.strip(),
        quality=quality.strip(),
        color=color.strip(),
        purchase_price_cents=purchase_price_cents,
        sale_price_cents=sale_price_cents,
        quantity=quantity,
        min_stock=min_stock,
        notes=notes.strip(),
    )
    session.add(display)
    session.flush()

    if quantity > 0:
        session.add(
            StockMovement(
                display_id=display.id,
                change_quantity=quantity,
                quantity_before=0,
                quantity_after=quantity,
                reason="Stock initial",
            )
        )

    return display


def update_display(
    session: Session,
    display_id: int,
    *,
    reference: str,
    brand: str,
    phone_model: str,
    quality: str,
    color: str,
    purchase_price_cents: int,
    sale_price_cents: int,
    min_stock: int,
    notes: str = "",
) -> Display:
    """Modifie les informations d'un afficheur (hors quantité — voir adjust_stock)."""
    display = get_display(session, display_id)
    reference = reference.strip()
    _validate_display_fields(reference, display.quantity, min_stock)

    existing = session.scalar(
        select(Display).where(Display.reference == reference, Display.id != display_id)
    )
    if existing is not None:
        raise StockError(f"La référence « {reference} » existe déjà.")

    display.reference = reference
    display.brand = brand.strip()
    display.phone_model = phone_model.strip()
    display.quality = quality.strip()
    display.color = color.strip()
    display.purchase_price_cents = purchase_price_cents
    display.sale_price_cents = sale_price_cents
    display.min_stock = min_stock
    display.notes = notes.strip()
    session.flush()
    return display


def adjust_stock(
    session: Session,
    display_id: int,
    *,
    change_quantity: int,
    reason: str,
    is_sale: bool = False,
) -> Display:
    """Ajoute ou retire une quantité du stock d'un afficheur.

    Le stock ne doit jamais devenir négatif. Si `is_sale` est vrai, les prix
    d'achat et de vente courants de l'afficheur sont figés sur le mouvement
    créé, pour que le bénéfice de cette vente reste stable même si les prix
    de l'afficheur changent ensuite.
    """
    if change_quantity == 0:
        raise StockError("La quantité à ajuster doit être différente de zéro.")
    if not reason or not reason.strip():
        raise StockError("Un motif est obligatoire pour ajuster le stock.")
    if is_sale and change_quantity >= 0:
        raise StockError("Une vente doit correspondre à une sortie de stock.")

    display = get_display(session, display_id)
    quantity_before = display.quantity
    quantity_after = quantity_before + change_quantity

    if quantity_after < 0:
        raise StockError(
            f"Stock insuffisant : quantité actuelle {quantity_before}, "
            f"impossible de retirer {abs(change_quantity)}."
        )

    display.quantity = quantity_after
    session.add(
        StockMovement(
            display_id=display.id,
            change_quantity=change_quantity,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            reason=reason.strip(),
            is_sale=is_sale,
            unit_purchase_price_cents=display.purchase_price_cents if is_sale else 0,
            unit_sale_price_cents=display.sale_price_cents if is_sale else 0,
        )
    )
    session.flush()
    return display


def deactivate_display(session: Session, display_id: int) -> None:
    """Supprime logiquement un afficheur (jamais de suppression physique)."""
    display = get_display(session, display_id)
    display.is_active = False
    session.flush()


def list_movements(
    session: Session,
    *,
    display_id: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[StockMovement]:
    stmt = (
        select(StockMovement)
        .options(selectinload(StockMovement.display))
        .order_by(StockMovement.created_at.desc())
    )
    if display_id is not None:
        stmt = stmt.where(StockMovement.display_id == display_id)
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def count_movements(session: Session, *, display_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(StockMovement)
    if display_id is not None:
        stmt = stmt.where(StockMovement.display_id == display_id)
    return session.scalar(stmt) or 0


def list_sales(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> list[StockMovement]:
    """Mouvements de vente (is_sale=True) dans la période donnée, du plus
    récent au plus ancien. `start`/`end` sont inclusifs si fournis."""
    stmt = (
        select(StockMovement)
        .options(selectinload(StockMovement.display))
        .where(StockMovement.is_sale.is_(True))
    )
    if start is not None:
        stmt = stmt.where(StockMovement.created_at >= start)
    if end is not None:
        stmt = stmt.where(StockMovement.created_at <= end)
    stmt = stmt.order_by(StockMovement.created_at.desc())
    return list(session.scalars(stmt).all())


def sum_profit_cents(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> int:
    """Bénéfice total (en centimes) des ventes réelles sur la période."""
    return sum(m.profit_cents for m in list_sales(session, start=start, end=end))
