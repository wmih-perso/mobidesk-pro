"""Service métier de gestion du stock de pièces.

Toute la logique passe par ici — l'interface PySide6 ne doit jamais
manipuler la base de données directement.
"""

from __future__ import annotations

import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Category, Display, Repair, RepairItem, Reseller, ResellerPayment, StockBatch, StockMovement, Supplier, User

SalePriceType = Literal["retail", "wholesale"]


class StockError(Exception):
    """Erreur métier destinée à être affichée à l'utilisateur (en français)."""


def list_displays(
    session: Session,
    *,
    search: str = "",
    only_active: bool = True,
    only_low_stock: bool = False,
    category: str | None = None,
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

    if category:
        stmt = stmt.where(Display.category == category)

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
        raise StockError("Cette pièce n'existe pas ou a été supprimée.")
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
    category: str,
    brand: str,
    phone_model: str,
    quality: str,
    purchase_price_cents: int,
    sale_price_retail_cents: int,
    sale_price_wholesale_cents: int,
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
        category=category.strip(),
        brand=brand.strip(),
        phone_model=phone_model.strip(),
        quality=quality.strip(),
        purchase_price_cents=purchase_price_cents,
        sale_price_retail_cents=sale_price_retail_cents,
        sale_price_wholesale_cents=sale_price_wholesale_cents,
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
    category: str,
    brand: str,
    phone_model: str,
    quality: str,
    purchase_price_cents: int,
    sale_price_retail_cents: int,
    sale_price_wholesale_cents: int,
    min_stock: int,
    notes: str = "",
) -> Display:
    """Modifie les informations d'une pièce (hors quantité — voir adjust_stock)."""
    display = get_display(session, display_id)
    reference = reference.strip()
    _validate_display_fields(reference, display.quantity, min_stock)

    existing = session.scalar(
        select(Display).where(Display.reference == reference, Display.id != display_id)
    )
    if existing is not None:
        raise StockError(f"La référence « {reference} » existe déjà.")

    display.reference = reference
    display.category = category.strip()
    display.brand = brand.strip()
    display.phone_model = phone_model.strip()
    display.quality = quality.strip()
    display.purchase_price_cents = purchase_price_cents
    display.sale_price_retail_cents = sale_price_retail_cents
    display.sale_price_wholesale_cents = sale_price_wholesale_cents
    display.min_stock = min_stock
    display.notes = notes.strip()
    session.flush()
    return display


def _next_movement_batch_id(session: Session) -> int:
    from sqlalchemy import func
    result = session.execute(
        __import__("sqlalchemy").select(func.max(StockMovement.movement_batch_id))
    ).scalar()
    return (result or 0) + 1


def adjust_stock(
    session: Session,
    display_id: int,
    *,
    change_quantity: int,
    reason: str,
    is_sale: bool = False,
    sale_price_type: SalePriceType | None = None,
    unit_sale_price_cents: int | None = None,
    unit_purchase_price_cents: int | None = None,
    repair_id: int | None = None,
    supplier_id: int | None = None,
    reseller_id: int | None = None,
    movement_batch_id: int | None = None,
) -> Display:
    """Ajoute ou retire une quantité du stock d'une pièce.

    Le stock ne doit jamais devenir négatif. Si `is_sale` est vrai,
    `sale_price_type` ("retail" ou "wholesale") est obligatoire — il indique
    le prix de référence utilisé (tracé sur le mouvement pour analyse), et
    sert de valeur par défaut du prix figé. `unit_sale_price_cents` permet
    de surcharger ce prix par défaut (ex. une remise ponctuelle) : c'est
    cette valeur qui est figée sur le mouvement et qui compte pour le
    bénéfice, pour que celui-ci reste stable même si les prix de la pièce
    changent ensuite. `repair_id` relie ce mouvement à une réparation
    (voir create_repair) — laissé à None pour une vente ou un ajustement
    normal. `supplier_id` trace le fournisseur d'un achat (entrée de stock),
    `reseller_id` trace le revendeur d'une vente en gros (sortie de stock) —
    tous deux facultatifs et indépendants du motif texte libre.
    """
    if change_quantity == 0:
        raise StockError("La quantité à ajuster doit être différente de zéro.")
    if not reason or not reason.strip():
        raise StockError("Un motif est obligatoire pour ajuster le stock.")
    if is_sale and change_quantity >= 0:
        raise StockError("Une vente doit correspondre à une sortie de stock.")
    if is_sale and sale_price_type not in ("retail", "wholesale"):
        raise StockError("Le type de prix (détail ou gros) est obligatoire pour une vente.")
    if is_sale and unit_sale_price_cents is not None and unit_sale_price_cents < 0:
        raise StockError("Le prix de vente ne peut pas être négatif.")

    display = get_display(session, display_id)
    quantity_before = display.quantity
    quantity_after = quantity_before + change_quantity

    if quantity_after < 0:
        raise StockError(
            f"Stock insuffisant : quantité actuelle {quantity_before}, "
            f"impossible de retirer {abs(change_quantity)}."
        )

    if change_quantity > 0:
        # --- Entrée de stock : créer un lot FIFO ---
        batch_price = (
            unit_purchase_price_cents
            if unit_purchase_price_cents is not None and unit_purchase_price_cents >= 0
            else display.purchase_price_cents
        )
        session.add(StockBatch(
            display_id=display.id,
            quantity_original=change_quantity,
            quantity_remaining=change_quantity,
            unit_purchase_price_cents=batch_price,
            supplier_id=supplier_id,
            movement_batch_id=movement_batch_id,
        ))
        if unit_purchase_price_cents is not None and unit_purchase_price_cents >= 0:
            display.purchase_price_cents = unit_purchase_price_cents
        fifo_purchase_price = batch_price
        unit_sale_price_cents = 0

    else:
        # --- Sortie de stock : consommer les lots FIFO (plus ancien en premier) ---
        qty_needed = abs(change_quantity)
        batches = (
            session.query(StockBatch)
            .filter(
                StockBatch.display_id == display.id,
                StockBatch.quantity_remaining > 0,
            )
            .order_by(StockBatch.created_at, StockBatch.id)
            .all()
        )
        total_cost = 0
        consumed = 0
        for batch in batches:
            if consumed >= qty_needed:
                break
            take = min(batch.quantity_remaining, qty_needed - consumed)
            total_cost += take * batch.unit_purchase_price_cents
            batch.quantity_remaining -= take
            consumed += take

        fifo_purchase_price = (total_cost // consumed) if consumed > 0 else display.purchase_price_cents

        if is_sale:
            if unit_sale_price_cents is None:
                unit_sale_price_cents = (
                    display.sale_price_wholesale_cents
                    if sale_price_type == "wholesale"
                    else display.sale_price_retail_cents
                )
        else:
            unit_sale_price_cents = 0

    from app import session as _session
    cashier_name = _session.get_current_cashier_name() if is_sale else ""

    display.quantity = quantity_after
    session.add(
        StockMovement(
            display_id=display.id,
            change_quantity=change_quantity,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            reason=reason.strip(),
            is_sale=is_sale,
            unit_purchase_price_cents=fifo_purchase_price,
            unit_sale_price_cents=unit_sale_price_cents,
            sale_price_type=sale_price_type if is_sale else "",
            repair_id=repair_id,
            supplier_id=supplier_id,
            reseller_id=reseller_id,
            movement_batch_id=movement_batch_id,
            cashier_name=cashier_name,
        )
    )
    session.flush()
    return display


def apply_stock_batch(
    session: Session,
    *,
    direction: Literal[1, -1],
    reason: str,
    lines: list[tuple[int, int, int | None]],
    is_sale: bool = False,
    sale_price_type: SalePriceType | None = None,
    supplier_id: int | None = None,
    reseller_id: int | None = None,
    versement_cents: int | None = None,
    remise_cents: int = 0,
) -> tuple[list[Display], int]:
    """Applique plusieurs lignes de mouvement de stock en une seule
    transaction atomique. Si `versement_cents` est fourni pour une vente
    revendeur, met à jour le solde du revendeur et annote les mouvements.
    """
    if not lines:
        raise StockError("Ajoutez au moins un produit.")

    batch_id = _next_movement_batch_id(session)
    results = []
    for display_id, quantity, unit_price_override in lines:
        display = adjust_stock(
            session,
            display_id,
            change_quantity=direction * quantity,
            reason=reason,
            is_sale=is_sale,
            sale_price_type=sale_price_type,
            unit_sale_price_cents=unit_price_override if is_sale else None,
            unit_purchase_price_cents=unit_price_override if not is_sale else None,
            supplier_id=supplier_id,
            reseller_id=reseller_id,
            movement_batch_id=batch_id,
        )
        results.append(display)

    # Mise à jour du solde fournisseur lors d'un achat
    if not is_sale and supplier_id is not None and versement_cents is not None:
        from app.models import Supplier
        supplier = session.get(Supplier, supplier_id)
        balance_before = supplier.balance_cents if supplier else 0
        batch_movements = (
            session.query(StockMovement)
            .filter(StockMovement.movement_batch_id == batch_id)
            .all()
        )
        total_purchase = sum(
            (m.unit_purchase_price_cents or 0) * abs(m.change_quantity) for m in batch_movements
        )
        new_balance = balance_before + total_purchase - versement_cents
        if supplier:
            supplier.balance_cents = new_balance
        for m in batch_movements:
            m.supplier_versement_cents = versement_cents
            m.supplier_balance_before_cents = balance_before
        session.flush()

    # Mise à jour du solde revendeur et annotation des mouvements
    if is_sale and reseller_id is not None and versement_cents is not None:
        reseller = session.get(Reseller, reseller_id)
        balance_before = reseller.balance_cents if reseller else 0
        batch_movements = (
            session.query(StockMovement)
            .filter(StockMovement.movement_batch_id == batch_id)
            .all()
        )
        total_after_remise = sum(
            m.unit_sale_price_cents * abs(m.change_quantity) for m in batch_movements
        )
        new_balance = balance_before + total_after_remise - versement_cents
        if reseller:
            reseller.balance_cents = new_balance
        for m in batch_movements:
            m.versement_cents = versement_cents
            m.remise_cents = remise_cents
            m.reseller_balance_before_cents = balance_before
        session.flush()

    return results, batch_id


def cancel_movement_batch(session: Session, batch_id: int | None = None, movement_id: int | None = None) -> None:
    """Annule tous les mouvements d'un lot (movement_batch_id).

    Pour chaque mouvement :
    - Entrée (achat) : supprime le lot FIFO correspondant et réduit le stock.
    - Sortie (vente/ajustement) : restitue la quantité en créant un lot FIFO
      au prix d'achat d'origine, et augmente le stock.
    Les mouvements originaux sont supprimés (pas de trace d'annulation —
    l'édition recréera de nouveaux mouvements corrects).
    """
    if batch_id is not None:
        movements = (
            session.query(StockMovement)
            .filter(StockMovement.movement_batch_id == batch_id)
            .all()
        )
    elif movement_id is not None:
        movements = (
            session.query(StockMovement)
            .filter(StockMovement.id == movement_id)
            .all()
        )
    else:
        raise StockError("batch_id ou movement_id requis.")
    if not movements:
        raise StockError("Mouvement introuvable.")

    for mvt in movements:
        display = get_display(session, mvt.display_id)

        if mvt.change_quantity > 0:
            # Annulation d'une entrée : supprimer le lot FIFO créé par cet achat
            batch = (
                session.query(StockBatch)
                .filter(StockBatch.movement_batch_id == batch_id,
                        StockBatch.display_id == mvt.display_id)
                .first()
            )
            if batch:
                session.delete(batch)
            display.quantity -= mvt.change_quantity
        else:
            # Annulation d'une sortie : remettre la quantité en stock via un nouveau lot
            qty = abs(mvt.change_quantity)
            display.quantity += qty
            session.add(StockBatch(
                display_id=display.id,
                quantity_original=qty,
                quantity_remaining=qty,
                unit_purchase_price_cents=mvt.unit_purchase_price_cents,
                movement_batch_id=None,
            ))

        session.delete(mvt)

    session.flush()


def deactivate_display(session: Session, display_id: int) -> None:
    """Supprime logiquement une pièce (jamais de suppression physique)."""
    display = get_display(session, display_id)
    display.is_active = False
    session.flush()


MovementTypeFilter = Literal["all", "sales", "entries", "exits"]


def _apply_movement_filters(
    stmt,
    *,
    display_id: int | None,
    supplier_id: int | None,
    reseller_id: int | None,
    movement_type: MovementTypeFilter,
):
    if display_id is not None:
        stmt = stmt.where(StockMovement.display_id == display_id)
    if supplier_id is not None:
        stmt = stmt.where(StockMovement.supplier_id == supplier_id)
    if reseller_id is not None:
        stmt = stmt.where(StockMovement.reseller_id == reseller_id)
    if movement_type == "sales":
        stmt = stmt.where(StockMovement.is_sale.is_(True))
    elif movement_type == "entries":
        stmt = stmt.where(StockMovement.change_quantity > 0)
    elif movement_type == "exits":
        stmt = stmt.where(
            StockMovement.is_sale.is_(False),
            StockMovement.change_quantity < 0,
        )
    return stmt


def list_movements(
    session: Session,
    *,
    display_id: int | None = None,
    supplier_id: int | None = None,
    reseller_id: int | None = None,
    movement_type: MovementTypeFilter = "all",
    limit: int | None = None,
    offset: int | None = None,
) -> list[StockMovement]:
    stmt = (
        select(StockMovement)
        .options(
            selectinload(StockMovement.display),
            selectinload(StockMovement.supplier),
            selectinload(StockMovement.reseller),
        )
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    )
    stmt = _apply_movement_filters(
        stmt,
        display_id=display_id,
        supplier_id=supplier_id,
        reseller_id=reseller_id,
        movement_type=movement_type,
    )
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def count_movements(
    session: Session,
    *,
    display_id: int | None = None,
    supplier_id: int | None = None,
    reseller_id: int | None = None,
    movement_type: MovementTypeFilter = "all",
) -> int:
    stmt = select(func.count()).select_from(StockMovement)
    stmt = _apply_movement_filters(
        stmt,
        display_id=display_id,
        supplier_id=supplier_id,
        reseller_id=reseller_id,
        movement_type=movement_type,
    )
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


def create_return(
    session: Session,
    *,
    display_id: int,
    quantity: int,
    return_type: str,
    reseller_id: int | None = None,
    supplier_id: int | None = None,
    note: str = "",
    linked_batch_id: int | None = None,
) -> StockMovement:
    """Crée un mouvement de retour.

    return_type='received'  → retour reçu du revendeur/client (stock +)
    return_type='supplier'  → retour envoyé au fournisseur (stock -)
    """
    if quantity <= 0:
        raise StockError("La quantité doit être supérieure à 0.")

    display = get_display(session, display_id)

    if return_type == "received":
        change = +quantity
        reason = "Retour reçu" + (f" — {note}" if note else "")
    else:
        change = -quantity
        reason = "Retour fournisseur" + (f" — {note}" if note else "")
        if display.quantity < quantity:
            raise StockError(
                f"Stock insuffisant : {display.quantity} disponible(s), "
                f"{quantity} demandé(s)."
            )

    before = display.quantity
    display.quantity += change
    after = display.quantity

    mvt = StockMovement(
        display_id=display_id,
        change_quantity=change,
        quantity_before=before,
        quantity_after=after,
        reason=reason,
        is_sale=False,
        reseller_id=reseller_id if return_type == "received" else None,
        supplier_id=supplier_id if return_type == "supplier" else None,
        linked_batch_id=linked_batch_id,
    )
    session.add(mvt)
    session.flush()
    return mvt


def create_replacement(
    session: Session,
    *,
    display_id: int,
    quantity: int,
    supplier_id: int | None = None,
    linked_return_batch_id: int | None = None,
) -> StockMovement:
    """Crée une entrée stock de remplacement fournisseur (échange sans paiement).

    Le lot FIFO est créé au prix d'achat courant du produit — le remplacement
    est comptabilisé au même coût que l'achat d'origine pour préserver les
    marges. Le prix d'achat du produit n'est PAS mis à jour (pas de nouveau
    paiement).
    """
    if quantity <= 0:
        raise StockError("La quantité doit être supérieure à 0.")
    display = get_display(session, display_id)
    before = display.quantity
    display.quantity += quantity
    after = display.quantity

    batch_price = display.purchase_price_cents
    next_batch_id = (session.scalar(select(func.max(StockMovement.movement_batch_id))) or 0) + 1

    session.add(StockBatch(
        display_id=display_id,
        quantity_original=quantity,
        quantity_remaining=quantity,
        unit_purchase_price_cents=batch_price,
        supplier_id=supplier_id,
        movement_batch_id=next_batch_id,
    ))

    mvt = StockMovement(
        display_id=display_id,
        change_quantity=quantity,
        quantity_before=before,
        quantity_after=after,
        reason="Remplacement fournisseur",
        is_sale=False,
        supplier_id=supplier_id,
        movement_batch_id=next_batch_id,
        linked_batch_id=linked_return_batch_id,
    )
    session.add(mvt)
    session.flush()
    return mvt


def has_replacement(session: Session, return_batch_id: int) -> bool:
    """Indique si le retour fournisseur a été résolu (remplacement ou remboursement)."""
    stmt = select(StockMovement.id).where(
        StockMovement.linked_batch_id == return_batch_id,
        StockMovement.reason.like("Remplacement%") | StockMovement.reason.like("Remboursement%"),
    ).limit(1)
    return session.scalar(stmt) is not None


def create_refund_resolution(
    session: Session,
    *,
    display_id: int,
    linked_return_batch_id: int,
    supplier_id: int | None = None,
    note: str = "",
) -> StockMovement:
    """Enregistre un remboursement fournisseur (sans mouvement de stock).

    Un mouvement à change_quantity=0 sert de marqueur de résolution —
    le stock n'est pas modifié car le produit a déjà quitté le stock
    lors du retour fournisseur.
    """
    display = get_display(session, display_id)
    mvt = StockMovement(
        display_id=display_id,
        change_quantity=0,
        quantity_before=display.quantity,
        quantity_after=display.quantity,
        reason="Remboursement fournisseur" + (f" — {note}" if note else ""),
        is_sale=False,
        supplier_id=supplier_id,
        linked_batch_id=linked_return_batch_id,
    )
    session.add(mvt)
    session.flush()
    return mvt


def list_returns_for_batch(
    session: Session,
    batch_id: int,
) -> list[StockMovement]:
    """Retours liés à un lot de vente ou d'achat (via linked_batch_id)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    stmt = (
        select(StockMovement)
        .options(selectinload(StockMovement.display))
        .where(StockMovement.linked_batch_id == batch_id)
        .order_by(StockMovement.created_at)
    )
    return list(session.scalars(stmt).all())


def list_returns(
    session: Session,
    *,
    search: str = "",
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
    return_type_filter: str = "all",
) -> list[StockMovement]:
    """Mouvements de retour (motif commence par 'Retour')."""
    stmt = (
        select(StockMovement)
        .options(
            selectinload(StockMovement.display),
            selectinload(StockMovement.reseller),
            selectinload(StockMovement.supplier),
        )
        .where(StockMovement.reason.like("Retour%"))
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    )
    if date_from:
        stmt = stmt.where(StockMovement.created_at >= date_from)
    if date_to:
        stmt = stmt.where(StockMovement.created_at <= date_to)
    if return_type_filter == "received":
        stmt = stmt.where(StockMovement.change_quantity > 0)
    elif return_type_filter == "supplier":
        stmt = stmt.where(StockMovement.change_quantity < 0)

    movements = list(session.scalars(stmt).all())

    if search:
        s = search.lower()
        movements = [
            m for m in movements
            if s in (m.display.reference or "").lower()
            or s in f"{m.display.brand} {m.display.phone_model}".lower()
            or s in (m.reseller.name if m.reseller else "").lower()
            or s in (m.supplier.name if m.supplier else "").lower()
        ]

    return movements


def list_sale_batches(
    session: Session,
    *,
    search: str = "",
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
    sale_type_filter: str = "all",
) -> list[dict]:
    """Retourne les ventes groupées par movement_batch_id (1 entrée = 1 vente)."""
    stmt = (
        select(StockMovement)
        .options(
            selectinload(StockMovement.display),
            selectinload(StockMovement.reseller),
        )
        .where(StockMovement.is_sale.is_(True))
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    )
    if date_from is not None:
        stmt = stmt.where(StockMovement.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(StockMovement.created_at <= date_to)
    if sale_type_filter == "retail":
        stmt = stmt.where(StockMovement.sale_price_type == "retail")
    elif sale_type_filter == "wholesale":
        stmt = stmt.where(StockMovement.sale_price_type == "wholesale")

    movements = list(session.scalars(stmt).all())

    from collections import defaultdict, OrderedDict
    batches: dict[int, list[StockMovement]] = OrderedDict()
    for m in movements:
        key = m.movement_batch_id if m.movement_batch_id is not None else -m.id
        if key not in batches:
            batches[key] = []
        batches[key].append(m)

    result = []
    for batch_id, items in batches.items():
        first = items[0]
        total_cents = sum(
            m.unit_sale_price_cents * abs(m.change_quantity) for m in items
        )
        nb_items = len(items)
        reseller_name = first.reseller.name if first.reseller else ""

        # Filtre texte : sur référence ou désignation de n'importe quel produit du lot
        if search:
            s = search.lower()
            matched = any(
                s in (m.display.reference or "").lower()
                or s in f"{m.display.brand} {m.display.phone_model}".lower()
                for m in items
            )
            if not matched:
                continue

        result.append({
            "batch_id": batch_id,
            "date": first.created_at,
            "sale_type": first.sale_price_type,
            "nb_items": nb_items,
            "total_cents": total_cents,
            "reseller": reseller_name,
            "movements": items,
        })

    return result


def sum_return_deduction_cents(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> int:
    """Bénéfice déduit par les retours reçus sur la période.

    Pour chaque retour lié à une vente (linked_batch_id non nul), on retrouve
    la vente originale afin d'utiliser les prix figés à l'époque de la vente
    (pas les prix actuels du produit). Un retour sans linked_batch_id est ignoré
    car on ne peut pas déterminer le prix de vente d'origine.
    """
    stmt = (
        select(StockMovement)
        .where(
            StockMovement.reason.like("Retour reçu%"),
            StockMovement.linked_batch_id.is_not(None),
            StockMovement.change_quantity > 0,
        )
    )
    if start is not None:
        stmt = stmt.where(StockMovement.created_at >= start)
    if end is not None:
        stmt = stmt.where(StockMovement.created_at <= end)

    returns = list(session.scalars(stmt).all())
    total = 0
    for ret in returns:
        orig = session.scalar(
            select(StockMovement)
            .where(
                StockMovement.movement_batch_id == ret.linked_batch_id,
                StockMovement.display_id == ret.display_id,
                StockMovement.is_sale.is_(True),
            )
            .limit(1)
        )
        if orig:
            unit_profit = orig.unit_sale_price_cents - orig.unit_purchase_price_cents
            total += unit_profit * abs(ret.change_quantity)
    return total


def sum_profit_cents(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> int:
    """Bénéfice net (en centimes) : ventes réelles moins retours reçus liés."""
    gross = sum(m.profit_cents for m in list_sales(session, start=start, end=end))
    deduction = sum_return_deduction_cents(session, start=start, end=end)
    return gross - deduction


def get_repair(session: Session, repair_id: int) -> Repair:
    repair = session.get(Repair, repair_id)
    if repair is None:
        raise StockError("Cette réparation n'existe pas ou a été supprimée.")
    return repair


def _apply_repair_parts(
    session: Session, repair: Repair, parts: list[tuple[int, int]]
) -> int:
    """Consomme les pièces `parts` pour `repair` (déjà flush avec un id) :
    décrémente le stock, crée un StockMovement tracé et un RepairItem par
    pièce. Retourne le total en centimes (prix détail des pièces) avant
    remise. Partagé par create_repair et update_repair.
    """
    parts_total_cents = 0
    for display_id, quantity in parts:
        if quantity <= 0:
            raise StockError("La quantité de chaque pièce doit être positive.")
        display = get_display(session, display_id)
        adjust_stock(
            session,
            display_id,
            change_quantity=-quantity,
            reason="Utilisation en réparation",
            is_sale=False,
            repair_id=repair.id,
        )
        session.add(
            RepairItem(
                repair_id=repair.id,
                display_id=display_id,
                quantity=quantity,
                unit_purchase_price_cents=display.purchase_price_cents,
                unit_sale_price_cents=display.sale_price_retail_cents,
            )
        )
        parts_total_cents += display.sale_price_retail_cents * quantity
    return parts_total_cents


def create_repair(
    session: Session,
    *,
    client_name: str,
    client_phone: str,
    phone_brand: str,
    phone_model: str,
    description: str,
    discount_cents: int,
    parts: list[tuple[int, int]],
    notes: str = "",
) -> Repair:
    """Enregistre une réparation et décrémente le stock des pièces utilisées.

    `parts` est une liste de (display_id, quantity). Toute la transaction
    est atomique : si une pièce n'a pas assez de stock, StockError remonte
    et rien n'est appliqué (voir session_scope, qui annule tout sur
    exception).

    Le prix détail de chaque pièce inclut déjà la pose/main d'œuvre —
    charged_price_cents est donc calculé (somme des prix détail des pièces
    consommées moins discount_cents), jamais saisi directement.
    """
    if not client_name or not client_name.strip():
        raise StockError("Le nom du client est obligatoire.")
    if discount_cents < 0:
        raise StockError("La remise ne peut pas être négative.")
    if not parts:
        raise StockError("Une réparation doit consommer au moins une pièce.")

    repair = Repair(
        client_name=client_name.strip(),
        client_phone=client_phone.strip(),
        phone_brand=phone_brand.strip(),
        phone_model=phone_model.strip(),
        description=description.strip(),
        discount_cents=discount_cents,
        notes=notes.strip(),
    )
    session.add(repair)
    session.flush()

    parts_total_cents = _apply_repair_parts(session, repair, parts)

    if discount_cents > parts_total_cents:
        raise StockError("La remise ne peut pas dépasser le total des pièces.")

    repair.charged_price_cents = parts_total_cents - discount_cents

    session.flush()
    return repair


def update_repair(
    session: Session,
    repair_id: int,
    *,
    client_name: str,
    client_phone: str,
    phone_brand: str,
    phone_model: str,
    description: str,
    discount_cents: int,
    parts: list[tuple[int, int]],
    notes: str = "",
) -> Repair:
    """Modifie une réparation existante.

    Remet en stock les pièces consommées par l'ancienne version, supprime
    les mouvements de stock et lignes de pièces qu'elle avait générés,
    puis réapplique la nouvelle liste de pièces — l'historique des
    mouvements ne garde que la version finale. Transaction atomique : si
    une nouvelle pièce n'a pas assez de stock, StockError remonte et
    session_scope annule tout, restaurant l'état d'avant modification.
    """
    repair = get_repair(session, repair_id)

    old_movements = session.scalars(
        select(StockMovement).where(StockMovement.repair_id == repair_id)
    ).all()
    for movement in old_movements:
        display = get_display(session, movement.display_id)
        display.quantity += abs(movement.change_quantity)
        session.delete(movement)

    for item in list(repair.items):
        session.delete(item)
    session.flush()

    if not client_name or not client_name.strip():
        raise StockError("Le nom du client est obligatoire.")
    if discount_cents < 0:
        raise StockError("La remise ne peut pas être négative.")
    if not parts:
        raise StockError("Une réparation doit consommer au moins une pièce.")

    repair.client_name = client_name.strip()
    repair.client_phone = client_phone.strip()
    repair.phone_brand = phone_brand.strip()
    repair.phone_model = phone_model.strip()
    repair.description = description.strip()
    repair.discount_cents = discount_cents
    repair.notes = notes.strip()

    parts_total_cents = _apply_repair_parts(session, repair, parts)

    if discount_cents > parts_total_cents:
        raise StockError("La remise ne peut pas dépasser le total des pièces.")

    repair.charged_price_cents = parts_total_cents - discount_cents

    session.flush()
    return repair


def list_repairs(
    session: Session,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> list[Repair]:
    stmt = (
        select(Repair)
        .options(selectinload(Repair.items).selectinload(RepairItem.display))
        .order_by(Repair.created_at.desc())
    )
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def count_repairs(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Repair)) or 0


# ----------------------------------------------------------------------
# Carnets d'adresses (revendeurs, fournisseurs)
#
# Les clients ne sont pas ici : ils sont saisis directement sur chaque
# réparation (Repair.client_name/client_phone, voir create_repair) plutôt
# que dans un carnet séparé, pour éviter la double saisie.
#
# Les deux modèles partagent exactement les mêmes champs et règles — la
# logique commune est factorisée ici, mais chaque entité garde ses propres
# fonctions publiques (create_reseller, create_supplier, ...) pour que
# l'appelant n'ait jamais à manipuler la classe ORM directement.
# ----------------------------------------------------------------------

_ContactModel = Reseller | Supplier


def _list_contacts(
    session: Session,
    model: type[_ContactModel],
    *,
    search: str = "",
    only_active: bool = True,
) -> list[_ContactModel]:
    stmt = select(model)
    if only_active:
        stmt = stmt.where(model.is_active.is_(True))
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(model.name.ilike(pattern) | model.phone.ilike(pattern))
    stmt = stmt.order_by(model.name)
    return list(session.scalars(stmt).all())


def _get_contact(
    session: Session, model: type[_ContactModel], contact_id: int, *, not_found_message: str
) -> _ContactModel:
    contact = session.get(model, contact_id)
    if contact is None:
        raise StockError(not_found_message)
    return contact


def _create_contact(
    session: Session,
    model: type[_ContactModel],
    *,
    name: str,
    phone: str,
    address: str,
    notes: str,
) -> _ContactModel:
    if not name or not name.strip():
        raise StockError("Le nom est obligatoire.")
    contact = model(
        name=name.strip(),
        phone=phone.strip(),
        address=address.strip(),
        notes=notes.strip(),
    )
    session.add(contact)
    session.flush()
    return contact


def _update_contact(
    contact: _ContactModel, *, name: str, phone: str, address: str, notes: str
) -> _ContactModel:
    if not name or not name.strip():
        raise StockError("Le nom est obligatoire.")
    contact.name = name.strip()
    contact.phone = phone.strip()
    contact.address = address.strip()
    contact.notes = notes.strip()
    return contact


def list_resellers(
    session: Session, *, search: str = "", only_active: bool = True
) -> list[Reseller]:
    return _list_contacts(session, Reseller, search=search, only_active=only_active)


def get_reseller(session: Session, reseller_id: int) -> Reseller:
    return _get_contact(
        session, Reseller, reseller_id, not_found_message="Ce revendeur n'existe pas ou a été supprimé."
    )


def create_reseller(
    session: Session, *, name: str, phone: str = "", address: str = "", notes: str = ""
) -> Reseller:
    return _create_contact(session, Reseller, name=name, phone=phone, address=address, notes=notes)


def update_reseller(
    session: Session, reseller_id: int, *, name: str, phone: str, address: str, notes: str = ""
) -> Reseller:
    reseller = get_reseller(session, reseller_id)
    _update_contact(reseller, name=name, phone=phone, address=address, notes=notes)
    session.flush()
    return reseller


def deactivate_reseller(session: Session, reseller_id: int) -> None:
    reseller = get_reseller(session, reseller_id)
    reseller.is_active = False
    session.flush()


def record_reseller_payment(
    session: Session,
    reseller_id: int,
    amount_cents: int,
    note: str = "",
) -> ResellerPayment:
    """Enregistre un versement du revendeur et met à jour son solde."""
    if amount_cents <= 0:
        raise StockError("Le montant du versement doit être positif.")
    reseller = get_reseller(session, reseller_id)
    balance_before = reseller.balance_cents
    balance_after = balance_before - amount_cents
    reseller.balance_cents = balance_after

    from app import session as _session
    cashier_name = _session.get_current_cashier_name()

    payment = ResellerPayment(
        reseller_id=reseller_id,
        amount_cents=amount_cents,
        balance_before_cents=balance_before,
        balance_after_cents=balance_after,
        note=note.strip(),
        cashier_name=cashier_name,
    )
    session.add(payment)
    session.flush()
    return payment


def list_reseller_payments(
    session: Session,
    reseller_id: int,
) -> list[ResellerPayment]:
    """Historique des versements d'un revendeur, du plus récent au plus ancien."""
    stmt = (
        select(ResellerPayment)
        .where(ResellerPayment.reseller_id == reseller_id)
        .order_by(ResellerPayment.created_at.desc())
    )
    return list(session.scalars(stmt).all())


def list_suppliers(
    session: Session, *, search: str = "", only_active: bool = True
) -> list[Supplier]:
    return _list_contacts(session, Supplier, search=search, only_active=only_active)


def get_supplier(session: Session, supplier_id: int) -> Supplier:
    return _get_contact(
        session, Supplier, supplier_id, not_found_message="Ce fournisseur n'existe pas ou a été supprimé."
    )


def create_supplier(
    session: Session, *, name: str, phone: str = "", address: str = "", notes: str = ""
) -> Supplier:
    return _create_contact(session, Supplier, name=name, phone=phone, address=address, notes=notes)


def update_supplier(
    session: Session, supplier_id: int, *, name: str, phone: str, address: str, notes: str = ""
) -> Supplier:
    supplier = get_supplier(session, supplier_id)
    _update_contact(supplier, name=name, phone=phone, address=address, notes=notes)
    session.flush()
    return supplier


def deactivate_supplier(session: Session, supplier_id: int) -> None:
    supplier = get_supplier(session, supplier_id)
    supplier.is_active = False
    session.flush()


# ----------------------------------------------------------------------
# Catégories de produits
#
# Gérées par l'utilisateur (Paramètres > Catégories) plutôt que codées en
# dur — toute catégorie créée ici apparaît automatiquement dans le
# formulaire d'ajout/modification de produit (voir app/ui/display_dialog.py).
# ----------------------------------------------------------------------


def list_categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.name)).all())


def get_category(session: Session, category_id: int) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise StockError("Cette catégorie n'existe pas ou a été supprimée.")
    return category


def create_category(session: Session, *, name: str) -> Category:
    name = name.strip()
    if not name:
        raise StockError("Le nom de la catégorie est obligatoire.")

    existing = session.scalar(select(Category).where(Category.name == name))
    if existing is not None:
        raise StockError(f"La catégorie « {name} » existe déjà.")

    category = Category(name=name)
    session.add(category)
    session.flush()
    return category


def update_category(session: Session, category_id: int, *, name: str) -> Category:
    category = get_category(session, category_id)
    name = name.strip()
    if not name:
        raise StockError("Le nom de la catégorie est obligatoire.")

    existing = session.scalar(
        select(Category).where(Category.name == name, Category.id != category_id)
    )
    if existing is not None:
        raise StockError(f"La catégorie « {name} » existe déjà.")

    old_name = category.name
    category.name = name
    session.flush()

    if old_name != name:
        for display in session.scalars(
            select(Display).where(Display.category == old_name)
        ):
            display.category = name
        session.flush()

    return category


def delete_category(session: Session, category_id: int) -> None:
    """Supprime définitivement une catégorie (pas de suppression logique —
    contrairement aux produits/contacts, une catégorie n'a pas d'historique
    propre). Les produits qui l'utilisaient gardent leur valeur texte
    actuelle : elle redevient simplement une catégorie libre, non gérée."""
    category = get_category(session, category_id)
    session.delete(category)
    session.flush()


# ---------------------------------------------------------------------------
# Gestion des comptes utilisateurs
# ---------------------------------------------------------------------------

import hashlib as _hashlib
import secrets as _secrets


def _hash_user_password(password: str, salt: str) -> str:
    return _hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def verify_user_password(session: Session, username: str, password: str) -> User | None:
    """Retourne l'utilisateur si username + password sont corrects, sinon None."""
    user = session.scalar(
        select(User).where(User.username == username, User.is_active.is_(True))
    )
    if user is None:
        return None
    if _hash_user_password(password, user.password_salt) == user.password_hash:
        return user
    return None


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.created_at)).all())


def create_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str = "cashier",
) -> User:
    username = username.strip()
    if not username:
        raise StockError("Le nom d'utilisateur ne peut pas être vide.")
    if not password:
        raise StockError("Le mot de passe ne peut pas être vide.")
    if role not in ("admin", "cashier"):
        raise StockError("Rôle invalide.")
    existing = session.scalar(select(User).where(User.username == username))
    if existing:
        raise StockError(f"L'utilisateur « {username} » existe déjà.")
    salt = _secrets.token_hex(16)
    user = User(
        username=username,
        display_name=display_name.strip() or username,
        password_hash=_hash_user_password(password, salt),
        password_salt=salt,
        role=role,
    )
    session.add(user)
    session.flush()
    return user


def update_user(
    session: Session,
    user_id: int,
    *,
    display_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise StockError("Utilisateur introuvable.")
    if display_name is not None:
        user.display_name = display_name.strip() or user.username
    if role is not None:
        if role not in ("admin", "cashier"):
            raise StockError("Rôle invalide.")
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    session.flush()
    return user


def change_user_password(session: Session, user_id: int, new_password: str) -> None:
    if not new_password:
        raise StockError("Le mot de passe ne peut pas être vide.")
    user = session.get(User, user_id)
    if user is None:
        raise StockError("Utilisateur introuvable.")
    salt = _secrets.token_hex(16)
    user.password_hash = _hash_user_password(new_password, salt)
    user.password_salt = salt
    session.flush()


def delete_user(session: Session, user_id: int) -> None:
    user = session.get(User, user_id)
    if user is None:
        raise StockError("Utilisateur introuvable.")
    admins_count = session.scalar(
        select(func.count()).where(User.role == "admin", User.is_active.is_(True))
    )
    if user.role == "admin" and admins_count <= 1:
        raise StockError("Impossible de supprimer le dernier compte administrateur.")
    session.delete(user)
    session.flush()
