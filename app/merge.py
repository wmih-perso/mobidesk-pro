"""Fusion de produits en double (un même article créé par plusieurs
fournisseurs) et lots restants par fournisseur."""

from __future__ import annotations

import datetime
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Display, ReturnLine, RepairItem, StockBatch, StockMovement, Supplier
from app.services import StockError

# Seuil de ressemblance (part de mots du modèle en commun) pour suggérer
# deux produits comme doublons probables.
SUGGEST_THRESHOLD = 0.7

# Mots qui distinguent deux versions d'un même modèle : si l'un des deux
# produits en a un que l'autre n'a pas, ce ne sont pas des doublons.
VARIANT_WORDS = frozenset({"MAX", "PRO", "PLUS", "LITE", "MINI", "ULTRA", "NEO", "FE", "+"})


@dataclass
class ProductInfo:
    id: int
    reference: str
    category: str
    brand: str
    phone_model: str
    quantity: int
    purchase_price_cents: int


@dataclass
class MergeSuggestion:
    a: ProductInfo
    b: ProductInfo
    score: float  # 1.0 = mêmes mots dans le modèle


@dataclass
class Lot:
    supplier_name: str
    date: datetime.datetime
    unit_price_cents: int
    quantity_original: int
    quantity_remaining: int


def _tokens(model: str) -> frozenset[str]:
    # « + » compte : S20 et S20+ sont deux produits différents.
    return frozenset(re.findall(r"[A-Z0-9+]+", (model or "").upper()))


def _info(d: Display) -> ProductInfo:
    return ProductInfo(
        d.id, d.reference, d.category or "", d.brand or "", d.phone_model or "",
        d.quantity, d.purchase_price_cents,
    )


def suggest_merges(session: Session, threshold: float = SUGGEST_THRESHOLD) -> list[MergeSuggestion]:
    """Paires de produits actifs de même catégorie et même marque dont les
    modèles se ressemblent, du plus probable au moins probable."""
    displays = list(session.scalars(select(Display).where(Display.is_active.is_(True))).all())
    groups: dict[tuple[str, str], list[Display]] = {}
    for d in displays:
        groups.setdefault(((d.category or "").casefold(), (d.brand or "").casefold()), []).append(d)

    suggestions: list[MergeSuggestion] = []
    for members in groups.values():
        toks = {d.id: _tokens(d.phone_model) for d in members}
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                ta, tb = toks[a.id], toks[b.id]
                if not ta or not tb:
                    continue
                if (ta ^ tb) & VARIANT_WORDS:
                    continue  # « 12 PRO » et « 12 PRO MAX » sont deux téléphones
                score = len(ta & tb) / len(ta | tb)
                if score >= threshold:
                    suggestions.append(MergeSuggestion(_info(a), _info(b), score))
    suggestions.sort(key=lambda s: (-s.score, s.a.phone_model))
    return suggestions


def list_lots(session: Session, display_id: int) -> list[Lot]:
    """Lots encore en stock d'un produit (le plus ancien d'abord), avec le
    fournisseur qui les a livrés."""
    rows = session.execute(
        select(StockBatch, Supplier.name)
        .outerjoin(Supplier, StockBatch.supplier_id == Supplier.id)
        .where(StockBatch.display_id == display_id, StockBatch.quantity_remaining > 0)
        .order_by(StockBatch.created_at, StockBatch.id)
    ).all()
    return [
        Lot(name or "—", b.created_at, b.unit_purchase_price_cents,
            b.quantity_original, b.quantity_remaining)
        for b, name in rows
    ]


def _append_lines(existing: str, *values: str) -> str:
    lines = [line for line in (existing or "").splitlines() if line.strip()]
    for value in values:
        value = (value or "").strip()
        if value and value.casefold() not in {line.casefold() for line in lines}:
            lines.append(value)
    return "\n".join(lines)


def merge_displays(session: Session, keep_id: int, drop_id: int) -> Display:
    """Fusionne `drop_id` dans `keep_id` : stock, lots (fournisseur et prix
    conservés), historique, retours et réparations passent sur le produit
    gardé. L'ancien produit est désactivé (sa référence reste réservée) et
    sa référence/son modèle deviennent des alias de recherche."""
    if keep_id == drop_id:
        raise StockError("Choisissez deux produits différents.")
    keep = session.get(Display, keep_id)
    drop = session.get(Display, drop_id)
    if keep is None or drop is None:
        raise StockError("Un des deux produits n'existe plus.")
    if not keep.is_active or not drop.is_active:
        raise StockError("Un des deux produits est déjà désactivé ou fusionné.")

    for model in (StockMovement, StockBatch, ReturnLine, RepairItem):
        session.execute(update(model).where(model.display_id == drop.id).values(display_id=keep.id))

    keep.quantity += drop.quantity
    keep.min_stock = max(keep.min_stock, drop.min_stock)
    keep.aliases = _append_lines(
        keep.aliases, drop.reference, drop.phone_model, *(drop.aliases or "").splitlines()
    )
    drop.quantity = 0
    drop.is_active = False
    drop.notes = _append_lines(drop.notes, f"Fusionné dans {keep.reference}")
    session.flush()
    session.expire(keep)
    session.expire(drop)

    # Prix d'achat du produit = moyenne pondérée des lots restants.
    lots = session.scalars(
        select(StockBatch).where(StockBatch.display_id == keep.id, StockBatch.quantity_remaining > 0)
    ).all()
    pieces = sum(b.quantity_remaining for b in lots)
    if pieces > 0:
        keep.purchase_price_cents = round(
            sum(b.quantity_remaining * b.unit_purchase_price_cents for b in lots) / pieces
        )
    session.flush()
    return keep


def backup_database_file(db_path: Path, backup_dir: Path) -> Path:
    """Copie cohérente de la base (API de sauvegarde SQLite) avant une fusion."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"stock_avant_fusion_{datetime.datetime.now():%Y%m%d_%H%M%S}.db"
    source = sqlite3.connect(str(db_path))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    # On ne garde que les 10 dernières copies.
    for old in sorted(backup_dir.glob("stock_avant_fusion_*.db"))[:-10]:
        old.unlink(missing_ok=True)
    return target
