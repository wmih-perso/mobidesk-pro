"""Retours — client/revendeur (le produit revient) et fournisseur (il repart).

Un retour part toujours d'une vente ou d'un achat existant : les quantités
retournables, les prix et le coût d'achat sont repris de l'opération
d'origine, jamais des prix actuels du produit. Chaque retour crée un bon
(ReturnDoc) et met à jour d'un seul coup le stock, les lots FIFO et la dette
du contact.

Retour client :
- état « bon » : le produit revient en stock, dans un lot FIFO à son coût
  d'achat d'origine ;
- état « défectueux » : il ne revient pas dans le stock vendable ; il reste
  « en attente » jusqu'à son renvoi au fournisseur ;
- règlement « debt » : la dette du revendeur baisse du montant remboursé ;
  « cash » : remboursement en espèces (obligatoire pour un client comptoir).
- le bénéfice de la vente d'origine est diminué de la marge des produits
  rendus (voir services.sum_return_deduction_cents).

Retour fournisseur :
- depuis un achat : le produit sort du stock (en priorité du lot de cet
  achat) ;
- depuis la liste des défectueux : aucun mouvement de stock (le produit
  n'y était plus) ;
- règlement « debt » (avoir : la dette envers le fournisseur baisse),
  « cash » (remboursement), ou « exchange » (en attente jusqu'à la
  réception du produit de remplacement, qui revient en stock sans être
  payé une seconde fois).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Display, Reseller, ReturnDoc, ReturnLine, StockBatch, StockMovement, Supplier
from app.lot_pricing import refresh_wholesale_price
from app.services import StockError, get_display, restore_to_lots
from app.labels import article_label
from app.search import matches

CUSTOMER_SETTLEMENTS = ("debt", "cash")
SUPPLIER_SETTLEMENTS = ("debt", "cash", "exchange")

SETTLEMENT_LABELS = {
    "debt": "Déduit de la dette",
    "cash": "Espèces",
    "exchange": "Échange",
    "legacy": "—",
}


def _cashier_name() -> str:
    from app import session as _session

    return _session.get_current_cashier_name()


def _designation(display: Display) -> str:
    return f"{display.brand} {display.phone_model}".strip()


# ---------------------------------------------------------------------------
# Lignes retournables
# ---------------------------------------------------------------------------


@dataclass
class ReturnableLine:
    """Une ligne de vente ou d'achat, avec ce qu'il reste à retourner."""

    movement_id: int
    display_id: int
    reference: str
    designation: str
    category: str
    quantity: int  # vendue ou achetée
    returned: int  # déjà retournée
    returnable: int
    unit_price_cents: int  # prix de vente (client) ou d'achat (fournisseur)
    unit_cost_cents: int  # coût d'achat
    label: str = ""  # catégorie + modèle compatible (ex. « AFF P20 LITE »)
    # Achat fournisseur : pièces de ce lot encore en stock selon le logiciel
    # (None si le lot est inconnu). Indicatif : voir LotShortageError.
    lot_remaining: int | None = None


class LotShortageError(StockError):
    """Le logiciel pense qu'il ne reste plus assez de pièces de ce fournisseur
    (la vente suit l'ordre d'achat, pas la pièce réellement prise). L'utilisateur
    peut confirmer s'il voit que la pièce est bien celle de ce fournisseur."""


@dataclass
class ReturnableOperation:
    """Une vente (ou un achat) et ses lignes retournables."""

    batch_id: int
    date: datetime.datetime
    contact_name: str
    lines: list[ReturnableLine]
    contact_id: int | None = None
    invoice_number: str = ""

    @property
    def total_cents(self) -> int:
        return sum(line.unit_price_cents * line.quantity for line in self.lines)

    @property
    def returnable_count(self) -> int:
        return sum(line.returnable for line in self.lines)

    @property
    def piece_count(self) -> int:
        return sum(line.quantity for line in self.lines)


def _returned_by_movement(session: Session, movement_ids: list[int], kind: str) -> dict[int, int]:
    if not movement_ids:
        return {}
    rows = session.execute(
        select(ReturnLine.origin_movement_id, func.sum(ReturnLine.quantity))
        .join(ReturnDoc, ReturnLine.return_id == ReturnDoc.id)
        .where(ReturnDoc.kind == kind, ReturnLine.origin_movement_id.in_(movement_ids))
        .group_by(ReturnLine.origin_movement_id)
    ).all()
    return {movement_id: int(total) for movement_id, total in rows}


def _group_by_batch(movements: list[StockMovement]) -> dict[int, list[StockMovement]]:
    batches: dict[int, list[StockMovement]] = {}
    for m in movements:
        key = m.movement_batch_id if m.movement_batch_id is not None else -m.id
        batches.setdefault(key, []).append(m)
    return batches


def _operation_text(items: list[StockMovement]) -> str:
    return " ".join(
        f"{m.display.reference} {_designation(m.display)} {m.display.category}" for m in items
    )


def list_sales_for_return(
    session: Session,
    *,
    reseller_id: int | None,
    search: str = "",
    ticket: int | None = None,
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
    limit: int = 200,
) -> list[ReturnableOperation]:
    """Ventes d'un revendeur (ou du comptoir si `reseller_id` est None), de
    la plus récente à la plus ancienne. `ticket` filtre sur le numéro de
    ticket (movement_batch_id) ; `search` sur la référence, la désignation
    ou la catégorie d'au moins un produit de la vente."""
    stmt = (
        select(StockMovement)
        .options(selectinload(StockMovement.display), selectinload(StockMovement.reseller))
        .where(StockMovement.is_sale.is_(True))
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    )
    if reseller_id is None:
        stmt = stmt.where(StockMovement.reseller_id.is_(None))
    else:
        stmt = stmt.where(StockMovement.reseller_id == reseller_id)
    if ticket is not None:
        stmt = stmt.where(StockMovement.movement_batch_id == ticket)
    if date_from is not None:
        stmt = stmt.where(StockMovement.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(StockMovement.created_at <= date_to)

    movements = list(session.scalars(stmt).all())
    returned = _returned_by_movement(session, [m.id for m in movements], "customer")
    search = search.strip().lower()

    result: list[ReturnableOperation] = []
    for batch_id, items in _group_by_batch(movements).items():
        if search and not matches(search, _operation_text(items)):
            continue
        lines = []
        for m in sorted(items, key=lambda m: m.id):
            sold = abs(m.change_quantity)
            already = returned.get(m.id, 0)
            lines.append(
                ReturnableLine(
                    movement_id=m.id,
                    display_id=m.display_id,
                    reference=m.display.reference,
                    designation=_designation(m.display),
                    label=article_label(m.display.category, m.display.phone_model, m.display.reference),
                    category=m.display.category,
                    quantity=sold,
                    returned=already,
                    returnable=max(0, sold - already),
                    unit_price_cents=m.unit_sale_price_cents,
                    unit_cost_cents=m.unit_purchase_price_cents,
                )
            )
        first = items[0]
        result.append(
            ReturnableOperation(
                batch_id=batch_id,
                date=first.created_at,
                contact_name=first.reseller.name if first.reseller else "Client comptoir",
                lines=lines,
            )
        )
        if len(result) >= limit:
            break
    return result


def _lot_remaining(session: Session, movements: list[StockMovement]) -> dict[tuple[int, int], int]:
    """Pièces encore en stock dans le lot de chaque achat, par
    (produit, n° d'achat). Un achat sans lot connu (anciennes données) est
    absent du résultat : il n'est alors pas plafonné."""
    keys = {(m.display_id, m.movement_batch_id) for m in movements if m.movement_batch_id is not None}
    if not keys:
        return {}
    rows = session.execute(
        select(StockBatch.display_id, StockBatch.movement_batch_id, StockBatch.quantity_remaining)
        .where(
            StockBatch.display_id.in_({k[0] for k in keys}),
            StockBatch.movement_batch_id.in_({k[1] for k in keys}),
        )
    ).all()
    return {(d, b): q for d, b, q in rows if (d, b) in keys}


def list_purchases_for_return(
    session: Session,
    *,
    supplier_id: int | None,
    search: str = "",
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
    limit: int = 200,
) -> list[ReturnableOperation]:
    """Achats d'un fournisseur (de tous si `supplier_id` est None), avec la
    quantité encore renvoyable par ligne : achetée − déjà renvoyée, et
    jamais plus que le stock actuel. `search` porte sur les produits, le
    n° de facture et le nom du fournisseur."""
    stmt = (
        select(StockMovement)
        .options(selectinload(StockMovement.display), selectinload(StockMovement.supplier))
        .where(
            StockMovement.supplier_id.is_not(None),
            StockMovement.change_quantity > 0,
            StockMovement.is_sale.is_(False),
            StockMovement.return_id.is_(None),
            StockMovement.reason.like("Achat%"),
        )
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    )
    if supplier_id is not None:
        stmt = stmt.where(StockMovement.supplier_id == supplier_id)
    if date_from is not None:
        stmt = stmt.where(StockMovement.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(StockMovement.created_at <= date_to)

    movements = list(session.scalars(stmt).all())
    returned = _returned_by_movement(session, [m.id for m in movements], "supplier")
    lots = _lot_remaining(session, movements)
    search = search.strip().lower()

    result: list[ReturnableOperation] = []
    for batch_id, items in _group_by_batch(movements).items():
        first = items[0]
        supplier_name = first.supplier.name if first.supplier else ""
        if search and not matches(search, first.invoice_number, supplier_name, _operation_text(items)):
            continue
        lines = []
        for m in sorted(items, key=lambda m: m.id):
            bought = m.change_quantity
            already = returned.get(m.id, 0)
            lines.append(
                ReturnableLine(
                    movement_id=m.id,
                    display_id=m.display_id,
                    reference=m.display.reference,
                    designation=_designation(m.display),
                    label=article_label(m.display.category, m.display.phone_model, m.display.reference),
                    category=m.display.category,
                    quantity=bought,
                    returned=already,
                    returnable=max(0, min(bought - already, m.display.quantity)),
                    lot_remaining=lots.get((m.display_id, m.movement_batch_id)),
                    unit_price_cents=m.unit_purchase_price_cents,
                    unit_cost_cents=m.unit_purchase_price_cents,
                )
            )
        result.append(
            ReturnableOperation(
                batch_id=batch_id,
                date=first.created_at,
                contact_name=first.supplier.name if first.supplier else "",
                lines=lines,
                contact_id=first.supplier_id,
                invoice_number=first.invoice_number,
            )
        )
        if len(result) >= limit:
            break
    return result


@dataclass
class PendingDefective:
    """Produit défectueux rendu par un client, pas encore renvoyé."""

    line_id: int
    display_id: int
    reference: str
    designation: str
    category: str
    remaining: int
    unit_cost_cents: int
    customer_name: str
    returned_at: datetime.datetime
    label: str = ""


def list_pending_defectives(session: Session) -> list[PendingDefective]:
    sent_rows = session.execute(
        select(ReturnLine.defective_line_id, func.sum(ReturnLine.quantity))
        .where(ReturnLine.defective_line_id.is_not(None))
        .group_by(ReturnLine.defective_line_id)
    ).all()
    sent = {line_id: int(total) for line_id, total in sent_rows}

    lines = session.scalars(
        select(ReturnLine)
        .options(
            selectinload(ReturnLine.display),
            selectinload(ReturnLine.return_doc).selectinload(ReturnDoc.reseller),
        )
        .join(ReturnDoc, ReturnLine.return_id == ReturnDoc.id)
        .where(ReturnDoc.kind == "customer", ReturnLine.condition == "defective")
        .order_by(ReturnDoc.created_at)
    ).all()

    result = []
    for line in lines:
        remaining = line.quantity - sent.get(line.id, 0)
        if remaining <= 0:
            continue
        doc = line.return_doc
        result.append(
            PendingDefective(
                line_id=line.id,
                display_id=line.display_id,
                reference=line.display.reference,
                designation=_designation(line.display),
                label=article_label(line.display.category, line.display.phone_model, line.display.reference),
                category=line.display.category,
                remaining=remaining,
                unit_cost_cents=line.unit_cost_cents,
                customer_name=doc.reseller.name if doc.reseller else "Client comptoir",
                returned_at=doc.created_at,
            )
        )
    return result


def count_pending_defectives(session: Session) -> int:
    return sum(d.remaining for d in list_pending_defectives(session))


# ---------------------------------------------------------------------------
# Mouvements de stock
# ---------------------------------------------------------------------------


def _stock_in(
    session: Session,
    display: Display,
    *,
    quantity: int,
    unit_cost_cents: int,
    reason: str,
    doc: ReturnDoc,
) -> None:
    """Remet `quantity` en stock dans un lot FIFO à `unit_cost_cents`, sans
    toucher au prix d'achat courant du produit."""
    before = display.quantity
    display.quantity += quantity
    session.add(
        StockBatch(
            display_id=display.id,
            quantity_original=quantity,
            quantity_remaining=quantity,
            unit_purchase_price_cents=unit_cost_cents,
            supplier_id=doc.supplier_id,
            sale_price_wholesale_cents=display.sale_price_wholesale_cents,
        )
    )
    session.add(
        StockMovement(
            display_id=display.id,
            change_quantity=quantity,
            quantity_before=before,
            quantity_after=display.quantity,
            reason=reason,
            unit_purchase_price_cents=unit_cost_cents,
            reseller_id=doc.reseller_id,
            supplier_id=doc.supplier_id,
            linked_batch_id=doc.source_batch_id,
            return_id=doc.id,
        )
    )


def _stock_back_from_sale(
    session: Session,
    display: Display,
    *,
    sale: StockMovement,
    quantity: int,
    reason: str,
    doc: ReturnDoc,
) -> None:
    """Retour client en bon état : la pièce revient dans son lot d'origine
    (même fournisseur, même place dans la file FIFO) — voir restore_to_lots."""
    restore_to_lots(session, sale, quantity)  # le prix gros suit le lot rendu
    before = display.quantity
    display.quantity += quantity
    session.add(
        StockMovement(
            display_id=display.id,
            change_quantity=quantity,
            quantity_before=before,
            quantity_after=display.quantity,
            reason=reason,
            unit_purchase_price_cents=sale.unit_purchase_price_cents,
            reseller_id=doc.reseller_id,
            linked_batch_id=doc.source_batch_id,
            return_id=doc.id,
        )
    )


def _stock_out(
    session: Session,
    display: Display,
    *,
    quantity: int,
    unit_cost_cents: int,
    reason: str,
    doc: ReturnDoc,
    preferred_batch_id: int | None,
) -> None:
    """Sort `quantity` du stock, en consommant d'abord le lot FIFO de
    l'achat d'origine (`preferred_batch_id`), puis les plus anciens."""
    if display.quantity < quantity:
        raise StockError(
            f"Stock insuffisant pour {display.reference} : "
            f"{display.quantity} disponible(s), {quantity} demandé(s)."
        )
    batches = list(
        session.scalars(
            select(StockBatch)
            .where(StockBatch.display_id == display.id, StockBatch.quantity_remaining > 0)
            .order_by(StockBatch.created_at, StockBatch.id)
        ).all()
    )
    batches.sort(key=lambda b: b.movement_batch_id != preferred_batch_id)
    remaining = quantity
    for batch in batches:
        if remaining == 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        remaining -= take

    before = display.quantity
    display.quantity -= quantity
    refresh_wholesale_price(session, display)
    session.add(
        StockMovement(
            display_id=display.id,
            change_quantity=-quantity,
            quantity_before=before,
            quantity_after=display.quantity,
            reason=reason,
            unit_purchase_price_cents=unit_cost_cents,
            supplier_id=doc.supplier_id,
            linked_batch_id=doc.source_batch_id,
            return_id=doc.id,
        )
    )


# ---------------------------------------------------------------------------
# Retour client / revendeur
# ---------------------------------------------------------------------------


def create_customer_return(
    session: Session,
    *,
    lines: list[tuple[int, int, str]],
    settlement: str,
    note: str = "",
) -> ReturnDoc:
    """Enregistre un retour client à partir d'une vente.

    `lines` : (id du mouvement de vente, quantité rendue, état "good" ou
    "defective"). Toutes les lignes doivent venir de la même vente.
    """
    lines = [(mid, qty, cond) for mid, qty, cond in lines if qty > 0]
    if not lines:
        raise StockError("Indiquez au moins une quantité à retourner.")
    if settlement not in CUSTOMER_SETTLEMENTS:
        raise StockError("Mode de remboursement invalide.")

    sales = {m.id: m for m in session.scalars(
        select(StockMovement).where(StockMovement.id.in_([mid for mid, _q, _c in lines]))
    )}
    returned = _returned_by_movement(session, list(sales), "customer")

    batch_ids = set()
    for movement_id, qty, condition in lines:
        sale = sales.get(movement_id)
        if sale is None or not sale.is_sale:
            raise StockError("Ligne de vente introuvable.")
        if condition not in ("good", "defective"):
            raise StockError("État du produit invalide.")
        returnable = abs(sale.change_quantity) - returned.get(movement_id, 0)
        if qty > returnable:
            raise StockError(
                f"{sale.display.reference} : {returnable} pièce(s) retournable(s) au maximum."
            )
        batch_ids.add(sale.movement_batch_id)
    if len(batch_ids) != 1:
        raise StockError("Un retour ne peut concerner qu'une seule vente.")

    first_sale = sales[lines[0][0]]
    reseller = session.get(Reseller, first_sale.reseller_id) if first_sale.reseller_id else None
    if reseller is None and settlement != "cash":
        raise StockError("Un client comptoir est remboursé en espèces.")

    amount = sum(sales[mid].unit_sale_price_cents * qty for mid, qty, _c in lines)
    balance_before = reseller.balance_cents if reseller else 0
    balance_after = balance_before - amount if settlement == "debt" else balance_before

    doc = ReturnDoc(
        kind="customer",
        reseller_id=reseller.id if reseller else None,
        source_batch_id=first_sale.movement_batch_id,
        settlement=settlement,
        status="done",
        amount_cents=amount,
        balance_before_cents=balance_before,
        balance_after_cents=balance_after,
        note=note.strip(),
        cashier_name=_cashier_name(),
    )
    session.add(doc)
    session.flush()

    for movement_id, qty, condition in lines:
        sale = sales[movement_id]
        session.add(
            ReturnLine(
                return_id=doc.id,
                display_id=sale.display_id,
                quantity=qty,
                unit_price_cents=sale.unit_sale_price_cents,
                unit_cost_cents=sale.unit_purchase_price_cents,
                condition=condition,
                origin_movement_id=sale.id,
            )
        )
        if condition == "good":
            _stock_back_from_sale(
                session,
                get_display(session, sale.display_id),
                sale=sale,
                quantity=qty,
                reason=f"Retour client — bon R{doc.id}",
                doc=doc,
            )

    if reseller is not None:
        reseller.balance_cents = balance_after
    session.flush()
    return doc


# ---------------------------------------------------------------------------
# Retour fournisseur
# ---------------------------------------------------------------------------


def set_customer_return_settlement(session: Session, return_id: int, settlement: str) -> ReturnDoc:
    """Corrige le règlement d'un bon de retour client d'un revendeur :
    « debt » (le montant est déduit de sa dette) ou « cash » (remboursé en
    espèces, sa dette ne bouge pas). Le stock ne change pas."""
    if settlement not in CUSTOMER_SETTLEMENTS:
        raise StockError("Mode de remboursement invalide.")
    doc = session.get(ReturnDoc, return_id)
    if doc is None or doc.kind != "customer":
        raise StockError("Bon de retour client introuvable.")
    if doc.reseller_id is None:
        raise StockError("Un client comptoir est toujours remboursé en espèces.")
    if doc.settlement == settlement:
        raise StockError("Ce bon a déjà ce mode de règlement.")
    if doc.settlement not in CUSTOMER_SETTLEMENTS:
        raise StockError("Ce bon ne peut pas être modifié.")
    reseller = session.get(Reseller, doc.reseller_id)
    if reseller is None:
        raise StockError("Revendeur introuvable.")
    if settlement == "debt":
        reseller.balance_cents -= doc.amount_cents
        doc.balance_after_cents = doc.balance_before_cents - doc.amount_cents
    else:
        reseller.balance_cents += doc.amount_cents
        doc.balance_after_cents = doc.balance_before_cents
    doc.settlement = settlement
    session.flush()
    return doc


def create_supplier_return(
    session: Session,
    *,
    supplier_id: int,
    purchase_lines: list[tuple[int, int]] = (),
    defective_lines: list[tuple[int, int]] = (),
    settlement: str,
    note: str = "",
    confirm_lot_shortage: bool = False,
) -> ReturnDoc:
    """Enregistre un renvoi au fournisseur.

    Si le lot de cet achat est épuisé selon le logiciel, lève LotShortageError
    tant que `confirm_lot_shortage` n'est pas vrai.

    `purchase_lines` : (id du mouvement d'achat, quantité) — le produit
    sort du stock. `defective_lines` : (id de la ligne client défectueuse,
    quantité) — aucun mouvement de stock.
    """
    purchase_lines = [(mid, qty) for mid, qty in purchase_lines if qty > 0]
    defective_lines = [(lid, qty) for lid, qty in defective_lines if qty > 0]
    if not purchase_lines and not defective_lines:
        raise StockError("Indiquez au moins une quantité à renvoyer.")
    if settlement not in SUPPLIER_SETTLEMENTS:
        raise StockError("Mode de règlement invalide.")
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise StockError("Fournisseur introuvable.")

    purchases = {m.id: m for m in session.scalars(
        select(StockMovement).where(StockMovement.id.in_([mid for mid, _q in purchase_lines]))
    )}
    returned = _returned_by_movement(session, list(purchases), "supplier")
    lots = _lot_remaining(session, list(purchases.values()))
    for movement_id, qty in purchase_lines:
        purchase = purchases.get(movement_id)
        if purchase is None or purchase.change_quantity <= 0 or purchase.supplier_id != supplier_id:
            raise StockError("Ligne d'achat introuvable pour ce fournisseur.")
        returnable = purchase.change_quantity - returned.get(movement_id, 0)
        if qty > returnable:
            raise StockError(
                f"{purchase.display.reference} : {returnable} pièce(s) renvoyable(s) au maximum."
            )
        in_lot = lots.get((purchase.display_id, purchase.movement_batch_id))
        if in_lot is not None and qty > in_lot and not confirm_lot_shortage:
            raise LotShortageError(
                f"{purchase.display.reference} : selon le logiciel, il reste {in_lot} pièce(s) "
                f"de ce fournisseur en stock (les autres ont été vendues).\n"
                f"Renvoyer {qty} pièce(s) quand même ?"
            )

    pending = {d.line_id: d for d in list_pending_defectives(session)}
    for line_id, qty in defective_lines:
        defective = pending.get(line_id)
        if defective is None:
            raise StockError("Produit défectueux introuvable ou déjà renvoyé.")
        if qty > defective.remaining:
            raise StockError(
                f"{defective.reference} : {defective.remaining} pièce(s) en attente au maximum."
            )

    amount = sum(purchases[mid].unit_purchase_price_cents * qty for mid, qty in purchase_lines)
    amount += sum(pending[lid].unit_cost_cents * qty for lid, qty in defective_lines)
    balance_before = supplier.balance_cents
    balance_after = balance_before - amount if settlement == "debt" else balance_before
    source_batches = {purchases[mid].movement_batch_id for mid, _q in purchase_lines}

    doc = ReturnDoc(
        kind="supplier",
        supplier_id=supplier_id,
        source_batch_id=source_batches.pop() if len(source_batches) == 1 and not defective_lines else None,
        settlement=settlement,
        status="pending" if settlement == "exchange" else "done",
        amount_cents=amount,
        balance_before_cents=balance_before,
        balance_after_cents=balance_after,
        note=note.strip(),
        cashier_name=_cashier_name(),
    )
    session.add(doc)
    session.flush()

    for movement_id, qty in purchase_lines:
        purchase = purchases[movement_id]
        cost = purchase.unit_purchase_price_cents
        session.add(
            ReturnLine(
                return_id=doc.id,
                display_id=purchase.display_id,
                quantity=qty,
                unit_price_cents=cost,
                unit_cost_cents=cost,
                origin_movement_id=purchase.id,
            )
        )
        _stock_out(
            session,
            get_display(session, purchase.display_id),
            quantity=qty,
            unit_cost_cents=cost,
            reason=f"Retour fournisseur — bon R{doc.id}",
            doc=doc,
            preferred_batch_id=purchase.movement_batch_id,
        )

    for line_id, qty in defective_lines:
        defective = pending[line_id]
        session.add(
            ReturnLine(
                return_id=doc.id,
                display_id=defective.display_id,
                quantity=qty,
                unit_price_cents=defective.unit_cost_cents,
                unit_cost_cents=defective.unit_cost_cents,
                defective_line_id=line_id,
            )
        )

    supplier.balance_cents = balance_after
    session.flush()
    return doc


def _get_pending_exchange(session: Session, return_id: int) -> ReturnDoc:
    doc = session.get(ReturnDoc, return_id)
    if doc is None or doc.kind != "supplier":
        raise StockError("Bon de retour fournisseur introuvable.")
    if doc.status != "pending":
        raise StockError("Ce retour est déjà réglé.")
    return doc


def receive_exchange(session: Session, return_id: int) -> ReturnDoc:
    """Le fournisseur a remplacé les produits renvoyés : ils reviennent en
    stock au même coût, sans nouveau paiement ni changement de dette."""
    doc = _get_pending_exchange(session, return_id)
    for line in doc.lines:
        display = get_display(session, line.display_id)
        _stock_in(
            session,
            display,
            quantity=line.quantity,
            unit_cost_cents=line.unit_cost_cents or display.purchase_price_cents,
            reason=f"Échange fournisseur reçu — bon R{doc.id}",
            doc=doc,
        )
    doc.status = "done"
    session.flush()
    return doc


def settle_pending_exchange(session: Session, return_id: int, *, settlement: str) -> ReturnDoc:
    """Le fournisseur rembourse finalement au lieu d'échanger : avoir sur
    la dette ("debt") ou espèces ("cash")."""
    if settlement not in ("debt", "cash"):
        raise StockError("Mode de règlement invalide.")
    doc = _get_pending_exchange(session, return_id)
    supplier = session.get(Supplier, doc.supplier_id) if doc.supplier_id else None
    if settlement == "debt" and supplier is None:
        raise StockError("Ce retour n'est lié à aucun fournisseur.")
    if doc.amount_cents == 0:
        doc.amount_cents = sum(
            line.quantity * (line.unit_cost_cents or line.display.purchase_price_cents)
            for line in doc.lines
        )
    doc.balance_before_cents = supplier.balance_cents if supplier else 0
    if settlement == "debt":
        supplier.balance_cents -= doc.amount_cents
    doc.balance_after_cents = supplier.balance_cents if supplier else 0
    doc.settlement = settlement
    doc.status = "done"
    session.flush()
    return doc


# ---------------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------------


def get_return(session: Session, return_id: int) -> ReturnDoc:
    doc = session.scalar(
        select(ReturnDoc)
        .options(
            selectinload(ReturnDoc.lines).selectinload(ReturnLine.display),
            selectinload(ReturnDoc.reseller),
            selectinload(ReturnDoc.supplier),
        )
        .where(ReturnDoc.id == return_id)
    )
    if doc is None:
        raise StockError("Bon de retour introuvable.")
    return doc


def list_return_docs(
    session: Session,
    *,
    kind: str | None = None,
    search: str = "",
    pending_only: bool = False,
) -> list[ReturnDoc]:
    stmt = (
        select(ReturnDoc)
        .options(
            selectinload(ReturnDoc.lines).selectinload(ReturnLine.display),
            selectinload(ReturnDoc.reseller),
            selectinload(ReturnDoc.supplier),
        )
        .order_by(ReturnDoc.created_at.desc(), ReturnDoc.id.desc())
    )
    if kind is not None:
        stmt = stmt.where(ReturnDoc.kind == kind)
    if pending_only:
        stmt = stmt.where(ReturnDoc.status == "pending")
    docs = list(session.scalars(stmt).all())

    search = search.strip().lower()
    if search:
        def _text(doc: ReturnDoc) -> str:
            contact = doc.reseller.name if doc.reseller else doc.supplier.name if doc.supplier else ""
            products = " ".join(
                f"{line.display.reference} {_designation(line.display)}" for line in doc.lines
            )
            return f"r{doc.id} {doc.source_batch_id or ''} {contact} {products} {doc.note}".lower()

        docs = [doc for doc in docs if matches(search, _text(doc))]
    return docs


def contact_name(doc: ReturnDoc) -> str:
    if doc.kind == "customer":
        return doc.reseller.name if doc.reseller else "Client comptoir"
    return doc.supplier.name if doc.supplier else "—"


def returned_qty_for_batch(session: Session, batch_id: int) -> dict[int, int]:
    """Quantité déjà rendue par mouvement de vente d'un ticket."""
    movement_ids = list(session.scalars(
        select(StockMovement.id).where(StockMovement.movement_batch_id == batch_id)
    ).all())
    return _returned_by_movement(session, movement_ids, "customer")


def list_returns_for_batch(session: Session, batch_id: int, kind: str) -> list[ReturnDoc]:
    """Bons de retour faits sur une vente (kind="customer") ou un achat
    (kind="supplier") donné."""
    stmt = (
        select(ReturnDoc)
        .options(selectinload(ReturnDoc.lines).selectinload(ReturnLine.display))
        .where(ReturnDoc.kind == kind)
        .order_by(ReturnDoc.created_at)
    )
    if kind == "customer":
        stmt = stmt.where(ReturnDoc.source_batch_id == batch_id)
    else:
        purchase_ids = select(StockMovement.id).where(StockMovement.movement_batch_id == batch_id)
        stmt = stmt.where(
            ReturnDoc.id.in_(
                select(ReturnLine.return_id).where(ReturnLine.origin_movement_id.in_(purchase_ids))
            )
        )
    return list(session.scalars(stmt).all())
