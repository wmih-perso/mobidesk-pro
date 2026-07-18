"""Modèles ORM — stock des pièces (afficheurs, batteries, autres)."""

from __future__ import annotations

import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Display(Base):
    """Une pièce en stock (afficheur, batterie ou autre — voir `category`)."""

    __tablename__ = "displays"

    id: Mapped[int] = mapped_column(primary_key=True)

    reference: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(50), default="Afficheur", index=True)
    brand: Mapped[str] = mapped_column(String(100), default="", index=True)
    phone_model: Mapped[str] = mapped_column(String(150), default="", index=True)
    quality: Mapped[str] = mapped_column(String(50), default="")

    # Montants stockés en centimes de dinar pour éviter les erreurs
    # d'arrondi liées aux nombres flottants (1 DA = 100 centimes).
    purchase_price_cents: Mapped[int] = mapped_column(default=0)
    # Deux prix de vente fixes : détail (unité) et gros (revendeurs) —
    # le choix appliqué à chaque vente est tracé sur le StockMovement.
    sale_price_retail_cents: Mapped[int] = mapped_column(default=0)
    sale_price_wholesale_cents: Mapped[int] = mapped_column(default=0)

    quantity: Mapped[int] = mapped_column(default=0)
    min_stock: Mapped[int] = mapped_column(default=1)

    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(default=_now, onupdate=_now)

    movements: Mapped[list["StockMovement"]] = relationship(
        back_populates="display", cascade="all, delete-orphan"
    )

    @property
    def is_low_stock(self) -> bool:
        return self.quantity <= self.min_stock

    def __repr__(self) -> str:
        return f"<Display {self.reference}>"


class StockMovement(Base):
    """Historique des entrées/sorties de stock d'une pièce."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)

    display_id: Mapped[int] = mapped_column(ForeignKey("displays.id"), index=True)
    display: Mapped["Display"] = relationship(back_populates="movements")

    change_quantity: Mapped[int]  # positif = entrée, négatif = sortie
    quantity_before: Mapped[int]
    quantity_after: Mapped[int]
    reason: Mapped[str] = mapped_column(String(255), default="")

    # Renseignés uniquement pour une sortie de motif "Vente" — les prix sont
    # figés au moment de la vente pour que le bénéfice historique ne bouge
    # jamais si le prix d'achat ou de vente de l'afficheur change ensuite.
    is_sale: Mapped[bool] = mapped_column(default=False, index=True)
    unit_purchase_price_cents: Mapped[int] = mapped_column(default=0)
    unit_sale_price_cents: Mapped[int] = mapped_column(default=0)
    # Purement informatif : quel prix (détail/gros) a été appliqué à cette
    # vente. N'entre pas dans le calcul de profit_cents ci-dessous, qui se
    # base uniquement sur unit_sale_price_cents déjà figé.
    sale_price_type: Mapped[str] = mapped_column(String(10), default="")

    # Renseigné uniquement si ce mouvement a été généré par une réparation
    # (consommation de pièce) — permet de remonter du mouvement de stock à
    # la réparation exacte qui l'a causé.
    repair_id: Mapped[int | None] = mapped_column(
        ForeignKey("repairs.id"), default=None, index=True
    )

    # Renseigné uniquement pour une entrée de motif "Achat fournisseur" —
    # trace qui a livré cette pièce, sans figer un fournisseur unique sur
    # le produit lui-même (un produit reçoit des livraisons de fournisseurs
    # différents dans le temps).
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id"), default=None, index=True
    )
    supplier: Mapped["Supplier | None"] = relationship()

    # Renseigné uniquement pour une sortie en "Prix gros" — trace à quel
    # revendeur cette vente a été faite.
    reseller_id: Mapped[int | None] = mapped_column(
        ForeignKey("resellers.id"), default=None, index=True
    )
    reseller: Mapped["Reseller | None"] = relationship()

    created_at: Mapped[datetime.datetime] = mapped_column(default=_now, index=True)

    @property
    def profit_cents(self) -> int:
        """Bénéfice total de cette ligne de mouvement (0 si non-vente)."""
        if not self.is_sale:
            return 0
        unit_profit = self.unit_sale_price_cents - self.unit_purchase_price_cents
        return unit_profit * abs(self.change_quantity)

    def __repr__(self) -> str:
        return f"<StockMovement display_id={self.display_id} change={self.change_quantity}>"


class Repair(Base):
    """Une réparation facturée à un client, consommant des pièces du stock."""

    __tablename__ = "repairs"

    id: Mapped[int] = mapped_column(primary_key=True)

    client_name: Mapped[str] = mapped_column(String(150), default="")
    client_phone: Mapped[str] = mapped_column(String(30), default="")
    phone_brand: Mapped[str] = mapped_column(String(100), default="")
    phone_model: Mapped[str] = mapped_column(String(150), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    # Le prix détail de chaque pièce inclut déjà la pose/main d'œuvre —
    # charged_price_cents est donc calculé (somme des prix détail des
    # pièces consommées moins discount_cents), jamais saisi directement.
    discount_cents: Mapped[int] = mapped_column(default=0)
    charged_price_cents: Mapped[int] = mapped_column(default=0)

    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now, index=True)

    items: Mapped[list["RepairItem"]] = relationship(
        back_populates="repair", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Repair id={self.id} client={self.client_name}>"


class RepairItem(Base):
    """Une pièce consommée par une réparation (quantité + coût figé)."""

    __tablename__ = "repair_items"

    id: Mapped[int] = mapped_column(primary_key=True)

    repair_id: Mapped[int] = mapped_column(ForeignKey("repairs.id"), index=True)
    repair: Mapped["Repair"] = relationship(back_populates="items")

    display_id: Mapped[int] = mapped_column(ForeignKey("displays.id"), index=True)
    display: Mapped["Display"] = relationship()

    quantity: Mapped[int]
    # Prix d'achat figé au moment de la réparation, pour cohérence avec
    # StockMovement.unit_purchase_price_cents (même logique de snapshot).
    unit_purchase_price_cents: Mapped[int] = mapped_column(default=0)
    # Prix détail figé (pose/main d'œuvre incluse) — sert de base au calcul
    # de Repair.charged_price_cents, avant application de la remise globale.
    unit_sale_price_cents: Mapped[int] = mapped_column(default=0)

    def __repr__(self) -> str:
        return f"<RepairItem repair_id={self.repair_id} display_id={self.display_id}>"


class Reseller(Base):
    """Un revendeur du carnet d'adresses (achats en gros)."""

    __tablename__ = "resellers"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(150), index=True)
    phone: Mapped[str] = mapped_column(String(30), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<Reseller {self.name}>"


class Supplier(Base):
    """Un fournisseur du carnet d'adresses (origine des pièces en stock)."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(150), index=True)
    phone: Mapped[str] = mapped_column(String(30), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<Supplier {self.name}>"
