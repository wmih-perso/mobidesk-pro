"""Boîte de dialogue d'ajout / modification d'un produit."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.database import session_scope
from app.models import Display
from app.money import cents_to_da, da_to_cents
from app.services import (
    StockError,
    create_display,
    deactivate_display,
    list_categories,
    update_display,
)
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox, field_label

QUALITY_CHOICES = ["Original", "Incell", "OLED", "AMOLED", "Compatible", "LCD"]


class DisplayDialog(QDialog):
    """Formulaire d'ajout ou de modification d'un produit.

    En mode modification, la quantité n'est pas éditable ici : elle passe
    obligatoirement par un ajustement de stock tracé (voir StockAdjustDialog).
    """

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__()
        self.display_id = display_id
        self.setWindowTitle(
            "Modifier le produit" if display_id else "Ajouter un produit"
        )
        self.setMinimumWidth(420)
        self.setModal(True)

        self._build_ui()
        if display_id is not None:
            self._load_display(display_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.reference_input = QLineEdit()
        self.reference_input.setPlaceholderText("Ex : AFF-SAMS-A12-001")
        form.addRow("Référence *", self.reference_input)

        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        with session_scope() as session:
            self.category_input.addItems([c.name for c in list_categories(session)])
        form.addRow(field_label("Catégorie"), self.category_input)

        self.brand_input = QLineEdit()
        self.brand_input.setPlaceholderText("Ex : Samsung")
        form.addRow("Marque", self.brand_input)

        self.phone_model_input = QLineEdit()
        self.phone_model_input.setPlaceholderText("Ex : Galaxy A12")
        form.addRow("Modèle compatible", self.phone_model_input)

        self.quality_input = QComboBox()
        self.quality_input.setEditable(True)
        self.quality_input.addItems(QUALITY_CHOICES)
        form.addRow(field_label("Qualité"), self.quality_input)

        self.purchase_price_input = ModernDoubleSpinBox()
        self.purchase_price_input.setRange(0, 10_000_000)
        self.purchase_price_input.setDecimals(0)
        self.purchase_price_input.setSuffix(" DA")
        form.addRow("Prix d'achat", self.purchase_price_input)

        self.sale_price_retail_input = ModernDoubleSpinBox()
        self.sale_price_retail_input.setRange(0, 10_000_000)
        self.sale_price_retail_input.setDecimals(0)
        self.sale_price_retail_input.setSuffix(" DA")
        form.addRow("Prix de vente (détail)", self.sale_price_retail_input)

        self.sale_price_wholesale_input = ModernDoubleSpinBox()
        self.sale_price_wholesale_input.setRange(0, 10_000_000)
        self.sale_price_wholesale_input.setDecimals(0)
        self.sale_price_wholesale_input.setSuffix(" DA")
        form.addRow("Prix de vente (gros)", self.sale_price_wholesale_input)

        if self.display_id is None:
            self.quantity_input = ModernSpinBox()
            self.quantity_input.setRange(0, 1_000_000)
            form.addRow("Quantité initiale", self.quantity_input)
        else:
            self.quantity_input = None

        self.min_stock_input = ModernSpinBox()
        self.min_stock_input.setRange(0, 1_000_000)
        self.min_stock_input.setValue(1)
        form.addRow("Stock minimum", self.min_stock_input)

        self.notes_input = QTextEdit()
        self.notes_input.setFixedHeight(70)
        form.addRow("Notes", self.notes_input)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons_row = QHBoxLayout()

        if self.display_id is not None:
            delete_button = QPushButton("🗑  Supprimer")
            delete_button.setObjectName("DangerButton")
            delete_button.clicked.connect(self._on_delete)
            buttons_row.addWidget(delete_button)

        buttons_row.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("💾  Enregistrer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("✕  Annuler")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName(
            "SecondaryButton"
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

    def _load_display(self, display_id: int) -> None:
        with session_scope() as session:
            display = session.get(Display, display_id)
            self.reference_input.setText(display.reference)
            self.category_input.setCurrentText(display.category)
            self.brand_input.setText(display.brand)
            self.phone_model_input.setText(display.phone_model)
            self.quality_input.setCurrentText(display.quality)
            self.purchase_price_input.setValue(cents_to_da(display.purchase_price_cents))
            self.sale_price_retail_input.setValue(
                cents_to_da(display.sale_price_retail_cents)
            )
            self.sale_price_wholesale_input.setValue(
                cents_to_da(display.sale_price_wholesale_cents)
            )
            self.min_stock_input.setValue(display.min_stock)
            self.notes_input.setPlainText(display.notes)

    def _on_save(self) -> None:
        fields = dict(
            reference=self.reference_input.text(),
            category=self.category_input.currentText(),
            brand=self.brand_input.text(),
            phone_model=self.phone_model_input.text(),
            quality=self.quality_input.currentText(),
            purchase_price_cents=da_to_cents(self.purchase_price_input.value()),
            sale_price_retail_cents=da_to_cents(self.sale_price_retail_input.value()),
            sale_price_wholesale_cents=da_to_cents(
                self.sale_price_wholesale_input.value()
            ),
            min_stock=self.min_stock_input.value(),
            notes=self.notes_input.toPlainText(),
        )

        try:
            with session_scope() as session:
                if self.display_id is None:
                    create_display(
                        session,
                        quantity=self.quantity_input.value(),
                        **fields,
                    )
                else:
                    update_display(session, self.display_id, **fields)
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()

    def _on_delete(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Confirmer la suppression",
            "Voulez-vous vraiment supprimer ce produit ? "
            "Il n'apparaîtra plus dans la liste mais son historique est conservé.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            with session_scope() as session:
                deactivate_display(session, self.display_id)
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()
