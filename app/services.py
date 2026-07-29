"""Service métier de gestion du stock de pièces.

Toute la logique passe par ici — l'interface PySide6 ne doit jamais
manipuler la base de données directement.
"""

from __future__ import annotations

import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Category, Display, Repair, RepairItem, Reseller, StockMovement, Supplier

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


def adjust_stock(
    session: Session,
    display_id: int,
    *,
    change_quantity: int,
    reason: str,
    is_sale: bool = False,
    sale_price_type: SalePriceType | None = None,
    unit_sale_price_cents: int | None = None,
    repair_id: int | None = None,
    supplier_id: int | None = None,
    reseller_id: int | None = None,
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

    if is_sale:
        if unit_sale_price_cents is None:
            unit_sale_price_cents = (
                display.sale_price_wholesale_cents
                if sale_price_type == "wholesale"
                else display.sale_price_retail_cents
            )
    else:
        unit_sale_price_cents = 0

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
            unit_sale_price_cents=unit_sale_price_cents,
            sale_price_type=sale_price_type if is_sale else "",
            repair_id=repair_id,
            supplier_id=supplier_id,
            reseller_id=reseller_id,
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
) -> list[Display]:
    """Applique plusieurs lignes de mouvement de stock en une seule
    transaction atomique — même principe que _apply_repair_parts pour les
    réparations. `lines` est une liste de (display_id, quantity,
    unit_price_cents_override) ; l'override est ignoré si `is_sale` est
    faux. Si une ligne échoue (ex. stock insuffisant), StockError remonte
    et session_scope annule tout le lot : aucun mouvement partiel n'est
    appliqué.
    """
    if not lines:
        raise StockError("Ajoutez au moins un produit.")

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
            supplier_id=supplier_id,
            reseller_id=reseller_id,
        )
        results.append(display)
    return results


def deactivate_display(session: Session, display_id: int) -> None:
    """Supprime logiquement une pièce (jamais de suppression physique)."""
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
        .options(
            selectinload(StockMovement.display),
            selectinload(StockMovement.supplier),
            selectinload(StockMovement.reseller),
        )
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
