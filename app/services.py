"""Service métier de gestion du stock de pièces.

Toute la logique passe par ici — l'interface PySide6 ne doit jamais
manipuler la base de données directement.
"""

from __future__ import annotations

import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.locations import normalize_location
from app.lot_pricing import refresh_wholesale_price, set_current_lot_wholesale
from app.models import (
    Category, Display, Expense, LotAllocation, Repair, RepairItem, Reseller, ResellerPayment,
    StockBatch, StockMovement, Supplier, SupplierPayment, User,
)

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
    brand: str | None = None,
    stock_state: str | None = None,
) -> list[Display]:
    """`stock_state` : "low" (au minimum ou en dessous), "out" (rupture,
    0 pièce) ou "in" (au moins une pièce) ; None = tous."""
    stmt = select(Display)

    if only_active:
        stmt = stmt.where(Display.is_active.is_(True))

    if only_low_stock:
        stmt = stmt.where(Display.quantity <= Display.min_stock)

    if category:
        stmt = stmt.where(Display.category == category)

    if brand:
        stmt = stmt.where(Display.brand == brand)

    if stock_state == "low":
        stmt = stmt.where(Display.quantity <= Display.min_stock)
    elif stock_state == "out":
        stmt = stmt.where(Display.quantity <= 0)
    elif stock_state == "in":
        stmt = stmt.where(Display.quantity > 0)

    stmt = stmt.order_by(Display.brand, Display.phone_model)
    displays = list(session.scalars(stmt).all())
    if search.strip():
        # Recherche par mots (app/search.py) : tous les mots, n'importe où.
        from app.search import brand_words, filter_and_rank

        displays = filter_and_rank(
            search, displays,
            lambda d: (d.reference, d.category, d.brand, d.phone_model, d.quality, d.aliases),
            optional_words=brand_words(list_brands(session)),
        )
    return displays


def list_brands(session: Session) -> list[str]:
    """Marques des produits actifs, triées (pour le filtre du stock)."""
    rows = session.scalars(
        select(Display.brand).where(Display.is_active.is_(True), Display.brand != "").distinct()
    ).all()
    return sorted({brand.strip() for brand in rows if brand and brand.strip()}, key=str.lower)


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
    location: str = "",
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
        location_number=normalize_location(location),
    )
    session.add(display)
    session.flush()

    if quantity > 0:
        # Le stock de départ est un achat comme un autre : lot FIFO et coût
        # sur le mouvement (bénéfice et capital justes).
        session.add(
            StockBatch(
                display_id=display.id,
                quantity_original=quantity,
                quantity_remaining=quantity,
                unit_purchase_price_cents=purchase_price_cents,
                sale_price_wholesale_cents=sale_price_wholesale_cents,
            )
        )
        session.add(
            StockMovement(
                display_id=display.id,
                change_quantity=quantity,
                quantity_before=0,
                quantity_after=quantity,
                reason="Stock initial",
                unit_purchase_price_cents=purchase_price_cents,
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
    location: str | None = None,
) -> Display:
    """Modifie les informations d'une pièce (hors quantité — voir adjust_stock).
    Emplacement inchangé si `location` vaut None."""
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
    if sale_price_wholesale_cents != display.sale_price_wholesale_cents:
        set_current_lot_wholesale(session, display, sale_price_wholesale_cents)
    display.sale_price_wholesale_cents = sale_price_wholesale_cents
    display.min_stock = min_stock
    display.notes = notes.strip()
    if location is not None:
        display.location_number = normalize_location(location)
        display.location_kind = ""  # ancien type de rangement (v2.4.19), plus utilisé
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
    invoice_number: str = "",
    model_label: str = "",
    lot_wholesale_cents: int | None = None,
    unit_list_price_cents: int | None = None,
) -> Display:
    """Ajoute ou retire une quantité du stock d'une pièce.

    Entrée : `lot_wholesale_cents` = prix gros du nouveau lot (par défaut le
    prix gros actuel du produit). Après chaque mouvement, le prix gros du
    produit suit le lot le plus ancien en stock (app/lot_pricing.py).

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
            sale_price_wholesale_cents=(
                lot_wholesale_cents if lot_wholesale_cents is not None
                else display.sale_price_wholesale_cents
            ),
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
        allocations: list[tuple[StockBatch, int]] = []
        for batch in batches:
            if consumed >= qty_needed:
                break
            take = min(batch.quantity_remaining, qty_needed - consumed)
            total_cost += take * batch.unit_purchase_price_cents
            batch.quantity_remaining -= take
            consumed += take
            allocations.append((batch, take))

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
    movement = StockMovement(
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
        invoice_number=invoice_number.strip(),
        model_label=model_label.strip(),
        # Prix avant remise (ticket) ; sans remise, c'est le prix de vente.
        unit_list_price_cents=(
            (unit_list_price_cents if unit_list_price_cents is not None else unit_sale_price_cents)
            if is_sale else 0
        ),
    )
    session.add(movement)
    session.flush()
    if change_quantity < 0:
        _record_allocations(session, movement, allocations)
    refresh_wholesale_price(session, display)
    return display


def _record_allocations(
    session: Session, movement: StockMovement, allocations: list[tuple[StockBatch, int]]
) -> None:
    """Mémorise quels lots une sortie de stock a consommés (voir LotAllocation)."""
    for batch, take in allocations:
        session.add(LotAllocation(
            movement_id=movement.id,
            batch_id=batch.id,
            quantity=take,
            unit_cost_cents=batch.unit_purchase_price_cents,
        ))
    session.flush()


def restore_to_lots(session: Session, movement: StockMovement, quantity: int) -> None:
    """Remet `quantity` pièces d'une sortie de stock (vente, ajustement)
    dans leurs lots. Avec la trace des lots (sorties depuis la v2.4.21),
    chaque pièce retourne exactement dans son lot, le dernier consommé
    d'abord. Sans trace (anciennes ventes), elle retourne dans le plus
    ancien lot déjà entamé, au coût de la vente. Ne touche pas à
    `display.quantity` (à l'appelant)."""
    remaining = quantity
    allocations = session.scalars(
        select(LotAllocation)
        .where(LotAllocation.movement_id == movement.id)
        .order_by(LotAllocation.id.desc())
    ).all()
    for allocation in allocations:
        if remaining == 0:
            break
        take = min(allocation.quantity, remaining)
        batch = session.get(StockBatch, allocation.batch_id)
        if batch is not None:
            batch.quantity_remaining += take
        else:
            _add_restored_batch(session, movement, take, allocation.unit_cost_cents)
        allocation.quantity -= take
        if allocation.quantity == 0:
            session.delete(allocation)
        remaining -= take
    if remaining > 0:
        _restore_without_trace(session, movement, remaining)
    session.flush()
    refresh_wholesale_price(session, get_display(session, movement.display_id))


def _restore_without_trace(session: Session, movement: StockMovement, quantity: int) -> None:
    cost = movement.unit_purchase_price_cents
    started = session.scalars(
        select(StockBatch)
        .where(
            StockBatch.display_id == movement.display_id,
            StockBatch.quantity_remaining < StockBatch.quantity_original,
        )
        .order_by(StockBatch.created_at, StockBatch.id)
    ).all()
    for batch in started:
        if quantity == 0:
            return
        if batch.unit_purchase_price_cents == cost:
            take = min(quantity, batch.quantity_original - batch.quantity_remaining)
            batch.quantity_remaining += take
            quantity -= take
    if quantity > 0:
        oldest = started[0] if started else None
        _add_restored_batch(
            session, movement, quantity, cost,
            created_at=oldest.created_at if oldest else movement.created_at,
            supplier_id=oldest.supplier_id if oldest else None,
        )


def _add_restored_batch(
    session: Session, movement: StockMovement, quantity: int, cost: int,
    created_at: datetime.datetime | None = None, supplier_id: int | None = None,
) -> None:
    session.add(StockBatch(
        display_id=movement.display_id,
        quantity_original=quantity,
        quantity_remaining=quantity,
        unit_purchase_price_cents=cost,
        supplier_id=supplier_id,
        created_at=created_at or movement.created_at,
    ))


def _consume_lots(session: Session, display: Display, quantity: int) -> list[tuple[StockBatch, int]]:
    """Prend `quantity` pièces dans les lots, le plus ancien d'abord (FIFO)."""
    batches = session.scalars(
        select(StockBatch)
        .where(StockBatch.display_id == display.id, StockBatch.quantity_remaining > 0)
        .order_by(StockBatch.created_at, StockBatch.id)
    ).all()
    taken: list[tuple[StockBatch, int]] = []
    for batch in batches:
        if quantity == 0:
            break
        take = min(batch.quantity_remaining, quantity)
        batch.quantity_remaining -= take
        quantity -= take
        taken.append((batch, take))
    return taken


def apply_stock_batch(
    session: Session,
    *,
    direction: Literal[1, -1],
    reason: str,
    lines: list[tuple],
    is_sale: bool = False,
    sale_price_type: SalePriceType | None = None,
    supplier_id: int | None = None,
    reseller_id: int | None = None,
    versement_cents: int | None = None,
    remise_cents: int = 0,
    invoice_number: str = "",
) -> tuple[list[Display], int]:
    """Applique plusieurs lignes de mouvement de stock en une seule
    transaction atomique. Si `versement_cents` est fourni pour une vente
    revendeur, met à jour le solde du revendeur et annote les mouvements.
    Chaque ligne est (display_id, quantité, prix) ou, en option,
    (display_id, quantité, prix, modèle choisi[, prix avant remise]) pour une vente, et
    (display_id, quantité, prix d'achat, prix gros du lot) pour une entrée.
    """
    if not lines:
        raise StockError("Ajoutez au moins un produit.")

    batch_id = _next_movement_batch_id(session)
    results = []
    for display_id, quantity, unit_price_override, *extra in lines:
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
            invoice_number=invoice_number,
            model_label=(extra[0] if extra else "") if is_sale else "",
            lot_wholesale_cents=(extra[0] if extra and not is_sale else None),
            unit_list_price_cents=(extra[1] if is_sale and len(extra) > 1 else None),
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


def _ensure_no_returns(session: Session, movements: list[StockMovement]) -> None:
    """Refuse de modifier une opération liée à un bon de retour : le bon
    pointe vers ces mouvements (quantités et prix d'origine)."""
    from app.models import ReturnLine

    if any(m.return_id is not None for m in movements):
        raise StockError(
            "Ce mouvement provient d'un bon de retour : il ne peut pas être modifié."
        )
    has_returns = session.scalar(
        select(ReturnLine.id)
        .where(ReturnLine.origin_movement_id.in_([m.id for m in movements]))
        .limit(1)
    )
    if has_returns is not None:
        raise StockError(
            "Des retours ont déjà été faits sur cette opération : elle ne peut plus être modifiée."
        )


def _batch_movements(
    session: Session, batch_id: int | None, movement_id: int | None
) -> list[StockMovement]:
    if batch_id is not None:
        movements = list(session.scalars(
            select(StockMovement).where(StockMovement.movement_batch_id == batch_id)
            .order_by(StockMovement.id)
        ).all())
    elif movement_id is not None:
        movements = list(session.scalars(
            select(StockMovement).where(StockMovement.id == movement_id)
        ).all())
    else:
        raise StockError("batch_id ou movement_id requis.")
    if not movements:
        raise StockError("Mouvement introuvable.")
    return movements


def _entry_lot(session: Session, movement: StockMovement) -> StockBatch:
    """Lot FIFO créé par une entrée de stock (achat, ajustement, stock initial)."""
    if movement.movement_batch_id is not None:
        lot = session.scalar(
            select(StockBatch).where(
                StockBatch.movement_batch_id == movement.movement_batch_id,
                StockBatch.display_id == movement.display_id,
            )
        )
    else:
        # Stock initial / anciennes entrées sans n° d'opération : le lot de
        # même quantité et même prix, créé au même moment.
        candidates = session.scalars(
            select(StockBatch).where(
                StockBatch.display_id == movement.display_id,
                StockBatch.movement_batch_id.is_(None),
                StockBatch.quantity_original == movement.change_quantity,
                StockBatch.unit_purchase_price_cents == movement.unit_purchase_price_cents,
            )
        ).all()
        lot = min(
            candidates,
            key=lambda b: abs((b.created_at - movement.created_at).total_seconds()),
            default=None,
        )
    if lot is None:
        raise StockError(
            f"Le lot de cette entrée de stock est introuvable : elle ne peut pas être modifiée."
        )
    return lot


def edit_movement_batch(
    session: Session,
    *,
    batch_id: int | None = None,
    movement_id: int | None = None,
    lines: list[tuple],
) -> None:
    """Modifie EN PLACE une opération (achat, vente ou ajustement).

    `lines` : (display_id, quantité, prix unitaire) — prix d'achat pour une
    entrée, prix de vente pour une vente. Un produit absent de `lines` est
    retiré de l'opération ; `lines` vide annule toute l'opération.

    Conservés : date, n° d'opération (= n° de ticket), caissier, facture,
    versement, modèle choisi, place des lots dans la file FIFO. La dette du
    revendeur / fournisseur est corrigée de la différence de total.

    Achat dont des pièces sont déjà vendues : quantité jamais en dessous du
    nombre de pièces vendues ; un nouveau prix d'achat ne vaut que pour les
    pièces encore en stock (les ventes passées gardent leur coût).
    """
    movements = _batch_movements(session, batch_id, movement_id)
    _ensure_no_returns(session, movements)
    first = movements[0]
    is_entry = first.change_quantity > 0
    wanted: dict[int, tuple[int, int]] = {}
    list_prices: dict[int, int] = {}
    for display_id, quantity, price, *extra in lines:
        if extra and extra[0] is not None:
            list_prices[display_id] = extra[0]
        if quantity <= 0:
            raise StockError("La quantité de chaque produit doit être positive.")
        if price < 0:
            raise StockError("Le prix ne peut pas être négatif.")
        if display_id in wanted:
            raise StockError("Un même produit apparaît deux fois dans l'opération.")
        wanted[display_id] = (quantity, price)

    def line_total(m: StockMovement) -> int:
        price = m.unit_purchase_price_cents if is_entry else m.unit_sale_price_cents
        return (price or 0) * abs(m.change_quantity)

    old_total = sum(line_total(m) for m in movements)
    new_total = 0

    for m in movements:
        new_qty, new_price = wanted.pop(m.display_id, (0, None))
        if is_entry:
            _edit_entry(session, m, new_qty, new_price)
        else:
            _edit_exit(session, m, new_qty, new_price)
            if new_qty and m.is_sale and m.display_id in list_prices:
                m.unit_list_price_cents = list_prices[m.display_id]
        if new_qty:
            new_total += line_total(m)

    for display_id in {m.display_id for m in movements}:
        refresh_wholesale_price(session, get_display(session, display_id))

    # Produits ajoutés à l'opération : même date, même n°, même contact.
    if wanted and first.movement_batch_id is None:
        raise StockError("Impossible d'ajouter un produit à cette opération (stock initial).")
    for display_id, (quantity, price) in wanted.items():
        adjust_stock(
            session,
            display_id,
            change_quantity=quantity if is_entry else -quantity,
            reason=first.reason,
            is_sale=first.is_sale,
            sale_price_type=first.sale_price_type or None,
            unit_sale_price_cents=None if is_entry else price,
            unit_purchase_price_cents=price if is_entry else None,
            supplier_id=first.supplier_id,
            reseller_id=first.reseller_id,
            movement_batch_id=first.movement_batch_id,
            invoice_number=first.invoice_number,
            unit_list_price_cents=list_prices.get(display_id),
        )
        added = session.scalars(
            select(StockMovement).where(StockMovement.display_id == display_id)
            .order_by(StockMovement.id.desc()).limit(1)
        ).one()
        added.created_at = first.created_at
        added.cashier_name = first.cashier_name
        added.versement_cents = first.versement_cents
        added.remise_cents = first.remise_cents
        added.reseller_balance_before_cents = first.reseller_balance_before_cents
        added.supplier_versement_cents = first.supplier_versement_cents
        added.supplier_balance_before_cents = first.supplier_balance_before_cents
        if is_entry:
            lot = _entry_lot(session, added)
            lot.created_at = first.created_at  # même place dans la file FIFO
        new_total += line_total(added)

    delta = new_total - old_total
    if delta:
        if first.is_sale and first.reseller_id is not None:
            reseller = session.get(Reseller, first.reseller_id)
            if reseller is not None:
                reseller.balance_cents += delta
        elif is_entry and first.supplier_id is not None:
            supplier = session.get(Supplier, first.supplier_id)
            if supplier is not None:
                supplier.balance_cents += delta
    session.flush()


def _edit_entry(session: Session, m: StockMovement, new_qty: int, new_price: int | None) -> None:
    lot = _entry_lot(session, m)
    display = get_display(session, m.display_id)
    old_qty = m.change_quantity
    gone = lot.quantity_original - lot.quantity_remaining  # pièces déjà sorties de ce lot
    if new_qty < gone:
        label = display.reference
        raise StockError(
            f"{label} : {gone} pièce(s) de cet achat déjà vendue(s) ou sortie(s) — "
            f"quantité minimum {gone}. Pour rendre des pièces au fournisseur, "
            "utilisez Retour fournisseur."
        )
    delta = new_qty - old_qty
    if display.quantity + delta < 0:
        raise StockError(f"{display.reference} : le stock deviendrait négatif.")
    display.quantity += delta
    if new_qty == 0:
        session.delete(lot)
        session.delete(m)
        return
    lot.quantity_original = new_qty
    lot.quantity_remaining += delta
    m.change_quantity = new_qty
    m.quantity_after = m.quantity_before + new_qty
    if new_price is not None and new_price != m.unit_purchase_price_cents:
        # 1a : le nouveau prix vaut pour les pièces encore en stock ; les
        # ventes déjà faites gardent le coût figé sur leur ligne.
        lot.unit_purchase_price_cents = new_price
        m.unit_purchase_price_cents = new_price
        latest = session.scalar(
            select(StockBatch).where(StockBatch.display_id == display.id)
            .order_by(StockBatch.created_at.desc(), StockBatch.id.desc()).limit(1)
        )
        if latest is lot:
            display.purchase_price_cents = new_price


def _edit_exit(session: Session, m: StockMovement, new_qty: int, new_price: int | None) -> None:
    display = get_display(session, m.display_id)
    old_qty = abs(m.change_quantity)
    if new_qty < old_qty:
        restore_to_lots(session, m, old_qty - new_qty)
        display.quantity += old_qty - new_qty
        if new_qty > 0:
            left = session.scalars(
                select(LotAllocation).where(LotAllocation.movement_id == m.id)
            ).all()
            if left and sum(a.quantity for a in left) == new_qty:
                m.unit_purchase_price_cents = sum(a.quantity * a.unit_cost_cents for a in left) // new_qty
    elif new_qty > old_qty:
        extra = new_qty - old_qty
        if display.quantity < extra:
            raise StockError(
                f"Stock insuffisant pour {display.reference} : {display.quantity} disponible(s)."
            )
        taken = _consume_lots(session, display, extra)
        _record_allocations(session, m, taken)
        extra_cost = sum(t * b.unit_purchase_price_cents for b, t in taken)
        m.unit_purchase_price_cents = (m.unit_purchase_price_cents * old_qty + extra_cost) // new_qty
        display.quantity -= extra
    if new_qty == 0:
        for allocation in session.scalars(
            select(LotAllocation).where(LotAllocation.movement_id == m.id)
        ).all():
            session.delete(allocation)
        session.delete(m)
        return
    m.change_quantity = -new_qty
    m.quantity_after = m.quantity_before - new_qty
    if m.is_sale and new_price is not None:
        m.unit_sale_price_cents = new_price


def cancel_movement_batch(session: Session, batch_id: int | None = None, movement_id: int | None = None) -> None:
    """Annule toute une opération (voir edit_movement_batch) : stock remis
    dans les lots, dettes corrigées. Refusé pour un achat dont des pièces
    sont déjà vendues."""
    edit_movement_batch(session, batch_id=batch_id, movement_id=movement_id, lines=[])


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
        cashier_name = next((m.cashier_name for m in items if m.cashier_name), "")

        # Filtre texte : produit (référence ou désignation), revendeur ou caissier
        if search:
            from app.search import matches

            products = " ".join(
                f"{m.display.reference} {m.display.category} {m.display.brand} {m.display.phone_model}"
                for m in items
            )
            if not matches(search, reseller_name, cashier_name, products):
                continue

        result.append({
            "batch_id": batch_id,
            "date": first.created_at,
            "sale_type": first.sale_price_type,
            "nb_items": nb_items,
            "total_cents": total_cents,
            "reseller": reseller_name,
            "cashier": cashier_name,
            "movements": items,
        })

    return result


def sum_return_deduction_cents(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> int:
    """Bénéfice annulé par les retours clients sur la période : pour chaque
    produit rendu, la marge de la vente d'origine (prix de vente moins coût
    d'achat figés sur le bon de retour — voir app/returns.py)."""
    from app.models import ReturnDoc, ReturnLine

    stmt = (
        select(
            func.sum(
                (ReturnLine.unit_price_cents - ReturnLine.unit_cost_cents) * ReturnLine.quantity
            )
        )
        .join(ReturnDoc, ReturnLine.return_id == ReturnDoc.id)
        .where(ReturnDoc.kind == "customer")
    )
    if start is not None:
        stmt = stmt.where(ReturnDoc.created_at >= start)
    if end is not None:
        stmt = stmt.where(ReturnDoc.created_at <= end)
    return session.scalar(stmt) or 0


EXPENSE_KINDS = ("Livraison", "Transport", "Emballage", "Autre")


def _expense_bounds(stmt, start, end):
    if start is not None:
        stmt = stmt.where(Expense.expense_date >= start)
    if end is not None:
        stmt = stmt.where(Expense.expense_date <= end)
    return stmt


def list_expenses(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> list[Expense]:
    """Frais de la période, du plus récent au plus ancien."""
    stmt = _expense_bounds(select(Expense), start, end)
    return list(session.scalars(stmt.order_by(Expense.expense_date.desc(), Expense.id.desc())).all())


def sum_expenses_cents(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> int:
    stmt = _expense_bounds(select(func.sum(Expense.amount_cents)), start, end)
    return session.scalar(stmt) or 0


def _check_expense(kind: str, amount_cents: int) -> str:
    kind = (kind or "").strip() or "Autre"
    if amount_cents <= 0:
        raise StockError("Le montant du frais doit être supérieur à zéro.")
    return kind


def create_expense(
    session: Session,
    *,
    expense_date: datetime.datetime,
    kind: str,
    amount_cents: int,
    note: str = "",
) -> Expense:
    kind = _check_expense(kind, amount_cents)
    from app import session as _session

    expense = Expense(
        expense_date=expense_date,
        kind=kind,
        amount_cents=amount_cents,
        note=(note or "").strip(),
        cashier_name=_session.get_current_cashier_name(),
    )
    session.add(expense)
    session.flush()
    return expense


def update_expense(
    session: Session,
    expense_id: int,
    *,
    expense_date: datetime.datetime,
    kind: str,
    amount_cents: int,
    note: str = "",
) -> Expense:
    kind = _check_expense(kind, amount_cents)
    expense = session.get(Expense, expense_id)
    if expense is None:
        raise StockError("Ce frais n'existe plus.")
    expense.expense_date = expense_date
    expense.kind = kind
    expense.amount_cents = amount_cents
    expense.note = (note or "").strip()
    session.flush()
    return expense


def delete_expense(session: Session, expense_id: int) -> None:
    expense = session.get(Expense, expense_id)
    if expense is None:
        raise StockError("Ce frais n'existe plus.")
    session.delete(expense)
    session.flush()


def sum_profit_cents(
    session: Session,
    *,
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
) -> int:
    """Bénéfice net (en centimes) : ventes réelles moins retours reçus liés
    et moins les frais de la période."""
    gross = sum(m.profit_cents for m in list_sales(session, start=start, end=end))
    deduction = sum_return_deduction_cents(session, start=start, end=end)
    expenses = sum_expenses_cents(session, start=start, end=end)
    return gross - deduction - expenses


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


def record_supplier_payment(
    session: Session, supplier_id: int, amount_cents: int, note: str = ""
) -> SupplierPayment:
    """Versement à un fournisseur : réduit ce qu'on lui doit."""
    if amount_cents <= 0:
        raise StockError("Le montant du versement doit être positif.")
    supplier = get_supplier(session, supplier_id)
    return _add_supplier_payment(
        session, supplier, "payment", amount_cents, supplier.balance_cents - amount_cents, note
    )


def set_supplier_balance(
    session: Session, supplier_id: int, balance_cents: int, note: str = ""
) -> SupplierPayment:
    """Ajustement manuel (admin) : fixe directement ce qu'on doit au
    fournisseur. L'écart est gardé dans l'historique des versements."""
    supplier = get_supplier(session, supplier_id)
    if balance_cents == supplier.balance_cents:
        raise StockError("Le solde est déjà à ce montant.")
    return _add_supplier_payment(
        session, supplier, "adjust", supplier.balance_cents - balance_cents, balance_cents,
        note or "Ajustement du solde",
    )


def _add_supplier_payment(
    session: Session, supplier: Supplier, kind: str, amount_cents: int,
    balance_after: int, note: str,
) -> SupplierPayment:
    from app import session as _session

    payment = SupplierPayment(
        supplier_id=supplier.id,
        kind=kind,
        amount_cents=amount_cents,
        balance_before_cents=supplier.balance_cents,
        balance_after_cents=balance_after,
        note=note.strip(),
        cashier_name=_session.get_current_cashier_name(),
    )
    supplier.balance_cents = balance_after
    session.add(payment)
    session.flush()
    return payment


def list_supplier_payments(session: Session, supplier_id: int) -> list[SupplierPayment]:
    """Historique des versements à un fournisseur, du plus récent au plus ancien."""
    stmt = (
        select(SupplierPayment)
        .where(SupplierPayment.supplier_id == supplier_id)
        .order_by(SupplierPayment.created_at.desc())
    )
    return list(session.scalars(stmt).all())


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


# Palette des badges de catégorie : 10 couleurs foncées bien distinctes,
# lisibles en texte blanc. Une nouvelle catégorie reçoit la première
# couleur pas encore utilisée.
CATEGORY_PALETTE = [
    "#7c3aed",  # violet
    "#ea580c",  # orange
    "#0891b2",  # cyan
    "#db2777",  # rose
    "#16a34a",  # vert
    "#2563eb",  # bleu
    "#ca8a04",  # jaune moutarde
    "#dc2626",  # rouge
    "#0d9488",  # turquoise
    "#4b5563",  # gris
]

# Couleur des produits dont la catégorie n'est pas (ou plus) gérée.
UNKNOWN_CATEGORY_COLOR = "#9ca3af"


def _next_free_category_color(session: Session) -> str:
    used = set(session.scalars(select(Category.color)).all())
    for color in CATEGORY_PALETTE:
        if color not in used:
            return color
    count = session.scalar(select(func.count()).select_from(Category)) or 0
    return CATEGORY_PALETTE[count % len(CATEGORY_PALETTE)]


def _validate_category_color(color: str) -> str:
    color = color.strip().lower()
    if len(color) != 7 or not color.startswith("#"):
        raise StockError("Couleur de catégorie invalide.")
    try:
        int(color[1:], 16)
    except ValueError:
        raise StockError("Couleur de catégorie invalide.") from None
    return color


def category_colors(session: Session) -> dict[str, str]:
    """Nom de catégorie -> couleur du badge, pour l'affichage des produits."""
    return {c.name: c.color or UNKNOWN_CATEGORY_COLOR for c in list_categories(session)}


def list_categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.name)).all())


def get_category(session: Session, category_id: int) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise StockError("Cette catégorie n'existe pas ou a été supprimée.")
    return category


def create_category(session: Session, *, name: str, color: str | None = None) -> Category:
    name = name.strip()
    if not name:
        raise StockError("Le nom de la catégorie est obligatoire.")

    existing = session.scalar(select(Category).where(Category.name == name))
    if existing is not None:
        raise StockError(f"La catégorie « {name} » existe déjà.")

    color = _validate_category_color(color) if color else _next_free_category_color(session)
    category = Category(name=name, color=color)
    session.add(category)
    session.flush()
    return category


def update_category(
    session: Session, category_id: int, *, name: str, color: str | None = None
) -> Category:
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
    if color:
        category.color = _validate_category_color(color)
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
