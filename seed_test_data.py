"""Script de données test — ajoute un fournisseur de test + mouvements liés.

Usage :
    python seed_test_data.py
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import init_engine, session_scope
from app.models import Display, StockMovement, Supplier

init_engine()


def _now(days_ago: int = 0) -> datetime.datetime:
    return datetime.datetime.now() - datetime.timedelta(days=days_ago)


with session_scope() as session:
    # 1. Fournisseur de test
    supplier = session.query(Supplier).filter_by(name="Fournisseur Test").first()
    if supplier is None:
        supplier = Supplier(
            name="Fournisseur Test",
            phone="0550 123 456",
            address="Alger Centre",
            notes="Fournisseur ajouté par le script de test",
        )
        session.add(supplier)
        session.flush()
        print(f"  Fournisseur créé : id={supplier.id}")
    else:
        print(f"  Fournisseur existant : id={supplier.id}")

    # 2. Prend les 3 premiers produits actifs
    displays = session.query(Display).filter_by(is_active=True).limit(3).all()
    if not displays:
        print("  Aucun produit en stock — lancez l'app et ajoutez des produits d'abord.")
        sys.exit(1)

    # 3. Ajoute 3 achats fournisseur (entrées de stock)
    for i, display in enumerate(displays):
        qty = 5 + i * 2
        before = display.quantity
        display.quantity += qty
        mvt = StockMovement(
            display_id=display.id,
            change_quantity=qty,
            quantity_before=before,
            quantity_after=display.quantity,
            reason="Achat fournisseur",
            is_sale=False,
            supplier_id=supplier.id,
            created_at=_now(days_ago=10 - i * 3),
        )
        session.add(mvt)
        print(f"  Achat : {display.reference}  +{qty}  (fournisseur={supplier.id})")

    # 4. Ajoute 2 ventes (sorties) pour le même produit, sans fournisseur
    for i, display in enumerate(displays[:2]):
        qty = 2
        before = display.quantity
        display.quantity -= qty
        mvt = StockMovement(
            display_id=display.id,
            change_quantity=-qty,
            quantity_before=before,
            quantity_after=display.quantity,
            reason="Vente",
            is_sale=True,
            unit_purchase_price_cents=display.purchase_price_cents,
            unit_sale_price_cents=display.sale_price_retail_cents,
            sale_price_type="retail",
            created_at=_now(days_ago=i + 1),
        )
        session.add(mvt)
        print(f"  Vente   : {display.reference}  -{qty}")

print("\nDone. Relancez l'application pour voir les données.")
