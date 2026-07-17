"""Modèles ORM — stock des afficheurs uniquement."""

from __future__ import annotations

import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Display(Base):
    """Un afficheur en stock (écran de téléphone)."""

    __tablename__ = "displays"

    id: Mapped[int] = mapped_column(primary_key=True)

    reference: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    brand: Mapped[str] = mapped_column(String(100), default="")
    phone_model: Mapped[str] = mapped_column(String(150), default="")
    quality: Mapped[str] = mapped_column(String(50), default="")
    color: Mapped[str] = mapped_column(String(50), default="")

    # Montants stockés en centimes de dinar pour éviter les erreurs
    # d'arrondi liées aux nombres flottants (1 DA = 100 centimes).
    purchase_price_cents: Mapped[int] = mapped_column(default=0)
    sale_price_cents: Mapped[int] = mapped_column(default=0)

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
    """Historique des entrées/sorties de stock d'un afficheur."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)

    display_id: Mapped[int] = mapped_column(ForeignKey("displays.id"))
    display: Mapped["Display"] = relationship(back_populates="movements")

    change_quantity: Mapped[int]  # positif = entrée, négatif = sortie
    quantity_before: Mapped[int]
    quantity_after: Mapped[int]
    reason: Mapped[str] = mapped_column(String(255), default="")

    # Renseignés uniquement pour une sortie de motif "Vente" — les prix sont
    # figés au moment de la vente pour que le bénéfice historique ne bouge
    # jamais si le prix d'achat ou de vente de l'afficheur change ensuite.
    is_sale: Mapped[bool] = mapped_column(default=False)
    unit_purchase_price_cents: Mapped[int] = mapped_column(default=0)
    unit_sale_price_cents: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)

    @property
    def profit_cents(self) -> int:
        """Bénéfice total de cette ligne de mouvement (0 si non-vente)."""
        if not self.is_sale:
            return 0
        unit_profit = self.unit_sale_price_cents - self.unit_purchase_price_cents
        return unit_profit * abs(self.change_quantity)

    def __repr__(self) -> str:
        return f"<StockMovement display_id={self.display_id} change={self.change_quantity}>"
