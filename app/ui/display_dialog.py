"""Boîte de dialogue d'ajout / modification d'un produit — style professionnel."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
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
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #374151; font-weight: 600; font-size: 13px;")
    return lbl


class DisplayDialog(FramelessDialog):
    """Formulaire d'ajout ou de modification d'un produit.

    En mode modification, la quantité n'est pas éditable ici : elle passe
    obligatoirement par un ajustement de stock tracé.
    """

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__()
        self.display_id = display_id
        self.setMinimumWidth(760)
        self.setMinimumHeight(560)
        self.setModal(True)
        self._build_ui()
        if display_id is not None:
            self._load_display(display_id)

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = "Modifier le produit" if self.display_id else "Nouveau Produit"
        extra = self._build_delete_btn() if self.display_id is not None else None
        root.addWidget(self._make_header(title, extra_widget=extra))
        root.addWidget(self._build_body(), stretch=1)
        root.addWidget(self._build_error_bar())
        root.addWidget(self._build_footer())

    def _build_delete_btn(self) -> QPushButton:
        btn = QPushButton("🗑  Supprimer")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.15); color: white;"
            " border: 1px solid rgba(255,255,255,0.4); border-radius: 6px;"
            " padding: 6px 14px; font-weight: 600; font-size: 12px; }"
            "QPushButton:hover { background: rgba(220,38,38,0.7); border-color: #dc2626; }"
        )
        btn.clicked.connect(self._on_delete)
        return btn

    def _build_body(self) -> QWidget:
        body = QWidget()
        body.setStyleSheet("background: white;")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(28, 20, 28, 12)
        outer.setSpacing(0)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnMinimumWidth(1, 32)

        row = 0

        grid.addWidget(_field_label("Référence produit"), row, 0)
        grid.addWidget(_field_label("Désignation (modèle compatible)"), row, 2)
        row += 1

        self.reference_input = QLineEdit()
        self.reference_input.setPlaceholderText("Ex : AFF-SAMS-A12-001")
        self._style_input(self.reference_input)
        grid.addWidget(self.reference_input, row, 0)

        self.phone_model_input = QLineEdit()
        self.phone_model_input.setPlaceholderText("Ex : Galaxy A12")
        self._style_input(self.phone_model_input)
        grid.addWidget(self.phone_model_input, row, 2)
        row += 1

        grid.addWidget(_field_label("Marque"), row, 0)
        grid.addWidget(_field_label("Catégorie (Famille)"), row, 2)
        row += 1

        self.brand_input = QLineEdit()
        self.brand_input.setPlaceholderText("Ex : Samsung")
        self._style_input(self.brand_input)
        grid.addWidget(self.brand_input, row, 0)

        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self._style_combo(self.category_input)
        with session_scope() as session:
            self.category_input.addItems([c.name for c in list_categories(session)])
        grid.addWidget(self.category_input, row, 2)
        row += 1

        grid.addWidget(_field_label("Qualité"), row, 0)
        if self.display_id is None:
            grid.addWidget(_field_label("Qte initiale *"), row, 2)
        else:
            grid.addWidget(_field_label("Qte initiale"), row, 2)
        row += 1

        self.quality_input = QComboBox()
        self.quality_input.setEditable(True)
        self.quality_input.addItems(["Original", "Incell", "OLED", "AMOLED", "Compatible", "LCD", ""])
        self._style_combo(self.quality_input)
        grid.addWidget(self.quality_input, row, 0)

        if self.display_id is None:
            self.quantity_input = ModernSpinBox()
            self.quantity_input.setRange(0, 1_000_000)
            grid.addWidget(self.quantity_input, row, 2)
        else:
            self.quantity_input = None
            qty_lbl = QLabel("Modifier via Achat fournisseur ou Ajustement")
            qty_lbl.setStyleSheet("color: #9ca3af; font-size: 11px; font-style: italic;")
            grid.addWidget(qty_lbl, row, 2)
        row += 1

        grid.addWidget(_field_label("Qte alerté (stock minimum)"), row, 0)
        grid.addWidget(_field_label("Prix d'achat *"), row, 2)
        row += 1

        self.min_stock_input = ModernSpinBox()
        self.min_stock_input.setRange(0, 1_000_000)
        self.min_stock_input.setValue(1)
        grid.addWidget(self.min_stock_input, row, 0)

        self.purchase_price_input = ModernDoubleSpinBox()
        self.purchase_price_input.setRange(0, 10_000_000)
        self.purchase_price_input.setDecimals(0)
        self.purchase_price_input.setSuffix(" DA")
        grid.addWidget(self.purchase_price_input, row, 2)
        row += 1

        grid.addWidget(_field_label("Prix vente détail *"), row, 0)
        grid.addWidget(_field_label("Prix de gros"), row, 2)
        row += 1

        self.sale_price_retail_input = ModernDoubleSpinBox()
        self.sale_price_retail_input.setRange(0, 10_000_000)
        self.sale_price_retail_input.setDecimals(0)
        self.sale_price_retail_input.setSuffix(" DA")
        grid.addWidget(self.sale_price_retail_input, row, 0)

        self.sale_price_wholesale_input = ModernDoubleSpinBox()
        self.sale_price_wholesale_input.setRange(0, 10_000_000)
        self.sale_price_wholesale_input.setDecimals(0)
        self.sale_price_wholesale_input.setSuffix(" DA")
        grid.addWidget(self.sale_price_wholesale_input, row, 2)
        row += 1

        grid.addWidget(_field_label("Notes"), row, 0, 1, 3)
        row += 1

        self.notes_input = QTextEdit()
        self.notes_input.setFixedHeight(62)
        self.notes_input.setStyleSheet(
            "QTextEdit { border: 1px solid #d1d5db; border-radius: 6px;"
            " padding: 6px; font-size: 13px; background: white; }"
            "QTextEdit:focus { border-color: #2563eb; }"
        )
        grid.addWidget(self.notes_input, row, 0, 1, 3)

        outer.addWidget(grid_widget, stretch=1)
        return body

    def _build_error_bar(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: white;")
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(28, 0, 28, 4)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            "color: #dc2626; font-size: 12px; background: #fef2f2;"
            " border: 1px solid #fca5a5; border-radius: 6px; padding: 8px 12px;"
        )
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        return wrapper

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setFixedHeight(76)
        footer.setStyleSheet(
            "QFrame { background: #f0f9ff; border-top: 1px solid #bae6fd; }"
        )
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(12)

        close_btn = QPushButton("✕  Fermer")
        close_btn.setFixedHeight(52)
        close_btn.setMinimumWidth(150)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background: #6b7280; color: white; border: none;"
            " border-radius: 10px; font-weight: 700; font-size: 14px; padding: 0 20px; }"
            "QPushButton:hover { background: #4b5563; }"
            "QPushButton:pressed { background: #374151; }"
        )
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        layout.addStretch()

        if self.display_id is None:
            stay_btn = QPushButton("💾  Enregistrer et Rester")
            stay_btn.setFixedHeight(52)
            stay_btn.setMinimumWidth(220)
            stay_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            stay_btn.setStyleSheet(
                "QPushButton { background: #2563eb; color: white; border: none;"
                " border-radius: 10px; font-weight: 700; font-size: 14px; padding: 0 20px; }"
                "QPushButton:hover { background: #1d4ed8; }"
                "QPushButton:pressed { background: #1e40af; }"
            )
            stay_btn.clicked.connect(self._on_save_and_stay)
            layout.addWidget(stay_btn)

        save_btn = QPushButton("💾  Enregistrer et Quitter")
        save_btn.setFixedHeight(52)
        save_btn.setMinimumWidth(220)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border: none;"
            " border-radius: 10px; font-weight: 700; font-size: 14px; padding: 0 20px; }"
            "QPushButton:hover { background: #0d47a1; }"
            "QPushButton:pressed { background: #0a2f6e; }"
        )
        save_btn.clicked.connect(self._on_save_and_quit)
        layout.addWidget(save_btn)

        return footer

    # ------------------------------------------------------------------
    # Styles helpers
    # ------------------------------------------------------------------

    def _style_input(self, widget: QLineEdit) -> None:
        widget.setStyleSheet(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 6px;"
            " padding: 7px 10px; font-size: 13px; background: white; }"
            "QLineEdit:focus { border-color: #2563eb; }"
        )
        widget.setMinimumHeight(36)

    def _style_combo(self, widget: QComboBox) -> None:
        widget.setStyleSheet(
            "QComboBox { border: 1px solid #d1d5db; border-radius: 6px;"
            " padding: 6px 10px; font-size: 13px; background: white; min-height: 36px; }"
            "QComboBox:focus { border-color: #2563eb; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
        )

    # ------------------------------------------------------------------
    # Chargement
    # ------------------------------------------------------------------

    def _load_display(self, display_id: int) -> None:
        with session_scope() as session:
            display = session.get(Display, display_id)
            self.reference_input.setText(display.reference)
            self.category_input.setCurrentText(display.category)
            self.brand_input.setText(display.brand)
            self.phone_model_input.setText(display.phone_model)
            self.quality_input.setCurrentText(display.quality)
            self.purchase_price_input.setValue(cents_to_da(display.purchase_price_cents))
            self.sale_price_retail_input.setValue(cents_to_da(display.sale_price_retail_cents))
            self.sale_price_wholesale_input.setValue(cents_to_da(display.sale_price_wholesale_cents))
            self.min_stock_input.setValue(display.min_stock)
            self.notes_input.setPlainText(display.notes)

    # ------------------------------------------------------------------
    # Sauvegarde
    # ------------------------------------------------------------------

    def _fields(self) -> dict:
        return dict(
            reference=self.reference_input.text(),
            category=self.category_input.currentText(),
            brand=self.brand_input.text(),
            phone_model=self.phone_model_input.text(),
            quality=self.quality_input.currentText(),
            purchase_price_cents=da_to_cents(self.purchase_price_input.value()),
            sale_price_retail_cents=da_to_cents(self.sale_price_retail_input.value()),
            sale_price_wholesale_cents=da_to_cents(self.sale_price_wholesale_input.value()),
            min_stock=self.min_stock_input.value(),
            notes=self.notes_input.toPlainText(),
        )

    def _save(self) -> bool:
        self.error_label.hide()
        try:
            with session_scope() as session:
                if self.display_id is None:
                    create_display(
                        session,
                        quantity=self.quantity_input.value(),
                        **self._fields(),
                    )
                else:
                    update_display(session, self.display_id, **self._fields())
        except StockError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return False
        return True

    def _on_save_and_quit(self) -> None:
        if self._save():
            self.accept()

    def _on_save_and_stay(self) -> None:
        if self._save():
            self._reset_form()

    def _reset_form(self) -> None:
        self.reference_input.clear()
        self.brand_input.clear()
        self.phone_model_input.clear()
        self.quality_input.setCurrentIndex(0)
        self.purchase_price_input.setValue(0)
        self.sale_price_retail_input.setValue(0)
        self.sale_price_wholesale_input.setValue(0)
        self.min_stock_input.setValue(1)
        self.notes_input.clear()
        if self.quantity_input is not None:
            self.quantity_input.setValue(0)
        self.reference_input.setFocus()
        current = self.category_input.currentText()
        self.category_input.clear()
        with session_scope() as session:
            self.category_input.addItems([c.name for c in list_categories(session)])
        self.category_input.setCurrentText(current)

    # ------------------------------------------------------------------
    # Suppression
    # ------------------------------------------------------------------

    def _on_delete(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Confirmer la suppression",
            "Voulez-vous vraiment supprimer ce produit ?\n"
            "Il n'apparaîtra plus dans la liste mais son historique est conservé.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            with session_scope() as session:
                deactivate_display(session, self.display_id)
        except StockError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.accept()
