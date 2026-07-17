"""Boîtes de dialogue d'entrée et de sortie de stock (mouvements tracés).

Plutôt qu'un unique formulaire générique où l'utilisateur doit choisir à la
fois un sens et un motif libre, chaque bouton de la fenêtre principale ouvre
un formulaire dédié dont le sens est déjà fixé : cela correspond aux gestes
réels de la boutique — un achat fournisseur fait toujours entrer du stock,
une vente ou une utilisation en réparation en fait toujours sortir.

Le formulaire peut être ouvert avec un afficheur déjà sélectionné (depuis le
tableau) ou vide (depuis le menu latéral) — dans ce second cas, un sélecteur
d'afficheur apparaît en haut du formulaire.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from app.database import session_scope
from app.models import Display
from app.money import cents_to_da
from app.services import StockError, adjust_stock, list_displays
from app.ui.widgets import ModernSpinBox, field_label

# Motifs d'entrée de stock — typiquement un achat fournisseur ou un retour
# client. Le sens est toujours positif.
IN_REASONS = [
    "Achat fournisseur",
    "Retour client",
    "Correction d'inventaire",
    "Autre entrée",
]

# Motifs de sortie de stock — vente, utilisation en réparation, perte...
# Le sens est toujours négatif.
OUT_REASONS = [
    "Vente",
    "Utilisation en réparation",
    "Produit endommagé",
    "Produit perdu",
    "Correction d'inventaire",
    "Autre sortie",
]


class _BaseStockMovementDialog(QDialog):
    """Base commune aux formulaires d'entrée et de sortie de stock."""

    direction: int  # +1 pour une entrée, -1 pour une sortie
    reasons: list[str]

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__()
        self.display_id = display_id
        self.setMinimumWidth(420)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)

        self.display_picker: QComboBox | None = None
        if self.display_id is None:
            self.display_picker = QComboBox()
            with session_scope() as session:
                for display in list_displays(session):
                    label = f"{display.reference} — {display.brand} {display.phone_model} (stock : {display.quantity})".strip()
                    self.display_picker.addItem(label, display.id)
            self.display_picker.currentIndexChanged.connect(self._on_display_changed)
            form.addRow(field_label("Afficheur *", icon="monitor"), self.display_picker)
        else:
            info_label = QLabel("")
            info_label.setStyleSheet("font-weight: 700; font-size: 14px;")
            layout.addWidget(info_label)
            self._info_label = info_label

        self.stock_label = QLabel("")
        self.stock_label.setStyleSheet("color: #6b7290;")
        layout.addWidget(self.stock_label)

        layout.addSpacing(4)
        layout.addLayout(form)

        self.quantity_input = ModernSpinBox()
        self.quantity_input.setRange(1, 1_000_000)
        self.quantity_input.valueChanged.connect(self._update_stock_label)
        form.addRow("Quantité *", self.quantity_input)

        self.reason_input = QComboBox()
        self.reason_input.setEditable(True)
        self.reason_input.addItems(self.reasons)
        self.reason_input.currentTextChanged.connect(self._update_stock_label)
        form.addRow(field_label("Motif *"), self.reason_input)

        self.revenue_label = QLabel("")
        self.revenue_label.setStyleSheet("color: #059669; font-weight: 600;")
        layout.addWidget(self.revenue_label)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("✔  Valider")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("✕  Annuler")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName(
            "SecondaryButton"
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_selected_display()

    def _current_display_id(self) -> int | None:
        if self.display_picker is not None:
            return self.display_picker.currentData()
        return self.display_id

    def _on_display_changed(self) -> None:
        self._load_selected_display()

    def _load_selected_display(self) -> None:
        display_id = self._current_display_id()
        if display_id is None:
            self.stock_label.setText("Aucun afficheur disponible.")
            self._current_quantity = 0
            self._sale_price_cents = 0
            return

        with session_scope() as session:
            display = session.get(Display, display_id)
            self._current_quantity = display.quantity
            self._sale_price_cents = display.sale_price_cents
            if self.display_picker is None:
                description = f"{display.brand} {display.phone_model}".strip()
                self._info_label.setText(
                    display.reference + (f" — {description}" if description else "")
                )

        self._update_stock_label()

    def _update_stock_label(self) -> None:
        quantity = self.quantity_input.value()
        projected = self._current_quantity + self.direction * quantity
        self.stock_label.setText(
            f"Quantité actuelle : {self._current_quantity}  →  après validation : {projected}"
        )

        if self.direction < 0 and self.reason_input.currentText() == "Vente":
            revenue = cents_to_da(self._sale_price_cents) * quantity
            self.revenue_label.setText(
                f"Montant estimé de la vente : {round(revenue):,} DA".replace(",", " ")
            )
        else:
            self.revenue_label.setText("")

    def _on_save(self) -> None:
        display_id = self._current_display_id()
        if display_id is None:
            self.error_label.setText("Veuillez sélectionner un afficheur.")
            return

        quantity = self.direction * self.quantity_input.value()
        is_sale = self.direction < 0 and self.reason_input.currentText().strip() == "Vente"

        try:
            with session_scope() as session:
                adjust_stock(
                    session,
                    display_id,
                    change_quantity=quantity,
                    reason=self.reason_input.currentText(),
                    is_sale=is_sale,
                )
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()


class StockInDialog(_BaseStockMovementDialog):
    """Enregistre une entrée de stock (achat fournisseur, retour client...)."""

    direction = 1
    reasons = IN_REASONS

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__(display_id)
        self.setWindowTitle("Entrée de stock")


class StockOutDialog(_BaseStockMovementDialog):
    """Enregistre une sortie de stock (vente, utilisation en réparation...)."""

    direction = -1
    reasons = OUT_REASONS

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__(display_id)
        self.setWindowTitle("Sortie de stock")
