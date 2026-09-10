"""Dialog de création d'un retour (reçu ou vers fournisseur)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.services import StockError, create_return, list_displays, list_resellers, list_suppliers
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernSpinBox


class ReturnDialog(FramelessDialog):
    """Déclarer un retour reçu (client/revendeur) ou un retour fournisseur."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(520, 500)
        self.setModal(True)
        self._selected_display_id: int | None = None
        self._display_choices: list[tuple[int, str, str, str]] = []
        self._build_ui()
        self._load_data()

    def _load_data(self) -> None:
        with session_scope() as session:
            self._display_choices = [
                (d.id, d.reference, f"{d.brand} {d.phone_model}".strip(), d.category or "")
                for d in list_displays(session)
            ]
            self._resellers = [(r.id, r.name) for r in list_resellers(session)]
            self._suppliers = [(s.id, s.name) for s in list_suppliers(session)]
        self._update_contact_combo()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        root.addWidget(
            self._make_header(
                "↩  Déclarer un retour",
                gradient="qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1a1a2e,stop:1 #1565c0)",
            )
        )

        # Corps
        body = QWidget()
        body.setStyleSheet("background: white;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Type de retour
        self._add_label(layout, "Type de retour")
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        self.received_btn = QPushButton("📥  Retour reçu (client / revendeur)")
        self.received_btn.setCheckable(True)
        self.received_btn.setChecked(True)
        self.received_btn.clicked.connect(lambda: self._set_type("received"))
        self._style_type_btn(self.received_btn, True)
        type_row.addWidget(self.received_btn)

        self.supplier_btn = QPushButton("📤  Retour fournisseur")
        self.supplier_btn.setCheckable(True)
        self.supplier_btn.clicked.connect(lambda: self._set_type("supplier"))
        self._style_type_btn(self.supplier_btn, False)
        type_row.addWidget(self.supplier_btn)
        layout.addLayout(type_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(sep)

        # Recherche produit
        self._add_label(layout, "Produit")
        self.product_search = QLineEdit()
        self.product_search.setPlaceholderText("Rechercher par référence, modèle...")
        self.product_search.textChanged.connect(self._on_search)
        layout.addWidget(self.product_search)

        self.product_list = QListWidget()
        self.product_list.setFixedHeight(110)
        self.product_list.setStyleSheet(
            "QListWidget { border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; }"
            "QListWidget::item { padding: 7px 12px; border-bottom: 1px solid #f5f5f5; }"
            "QListWidget::item:selected { background: #e0f2f1; color: #1e40af; }"
        )
        self.product_list.itemClicked.connect(self._on_product_selected)
        layout.addWidget(self.product_list)

        self.selected_product_lbl = QLabel("Aucun produit sélectionné")
        self.selected_product_lbl.setStyleSheet(
            "background: #f8faff; border: 1px solid #bfdbfe; border-radius: 6px; "
            "padding: 8px 12px; color: #1e40af; font-weight: 600; font-size: 12px;"
        )
        layout.addWidget(self.selected_product_lbl)

        # Quantité
        self._add_label(layout, "Quantité retournée")
        self.qty_spin = ModernSpinBox()
        self.qty_spin.setRange(1, 10_000)
        self.qty_spin.setFixedWidth(140)
        layout.addWidget(self.qty_spin)

        # Contact (revendeur ou fournisseur)
        self.contact_label = QLabel("Revendeur")
        self.contact_label.setStyleSheet("font-weight: 700; color: #374151; font-size: 13px;")
        layout.addWidget(self.contact_label)
        self.contact_combo = QComboBox()
        layout.addWidget(self.contact_combo)

        # Note
        self._add_label(layout, "Note (optionnel)")
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Ex: défectueux, écran cassé...")
        layout.addWidget(self.note_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626; font-size: 12px;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        root.addWidget(body, stretch=1)

        # Footer
        footer = QWidget()
        footer.setStyleSheet("background: #f8fafc; border-top: 1px solid #e2e8f0;")
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(20, 12, 20, 12)
        f_layout.setSpacing(10)

        cancel_btn = QPushButton("Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        f_layout.addWidget(cancel_btn)

        f_layout.addStretch()

        self.confirm_btn = QPushButton("✔  Confirmer le retour")
        self.confirm_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none; border-radius: 6px; "
            "padding: 10px 20px; font-weight: 700; font-size: 13px; }"
            "QPushButton:hover { background: #1e40af; }"
        )
        self.confirm_btn.clicked.connect(self._on_confirm)
        f_layout.addWidget(self.confirm_btn)

        root.addWidget(footer)

    def _add_label(self, layout: QVBoxLayout, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 700; color: #374151; font-size: 13px;")
        layout.addWidget(lbl)

    def _style_type_btn(self, btn: QPushButton, active: bool) -> None:
        if active:
            btn.setStyleSheet(
                "QPushButton { background: #2563eb; color: white; border: none; border-radius: 6px; "
                "padding: 10px 14px; font-weight: 700; font-size: 13px; }"
                "QPushButton:hover { background: #1d4ed8; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background: #f5f5f5; color: #616161; border: 1px solid #e0e0e0; "
                "border-radius: 6px; padding: 10px 14px; font-weight: 600; font-size: 13px; }"
                "QPushButton:hover { background: #e0f2f1; color: #1e40af; }"
            )

    def _set_type(self, t: str) -> None:
        is_received = t == "received"
        self.received_btn.setChecked(is_received)
        self.supplier_btn.setChecked(not is_received)
        self._style_type_btn(self.received_btn, is_received)
        self._style_type_btn(self.supplier_btn, not is_received)
        self._update_contact_combo()

    def _return_type(self) -> str:
        return "received" if self.received_btn.isChecked() else "supplier"

    def _update_contact_combo(self) -> None:
        is_received = self._return_type() == "received"
        self.contact_label.setText("Revendeur" if is_received else "Fournisseur")
        self.contact_combo.clear()
        if is_received:
            self.contact_combo.addItem("— Sélectionner un revendeur (optionnel) —", None)
            for r_id, name in getattr(self, "_resellers", []):
                self.contact_combo.addItem(name, r_id)
        else:
            self.contact_combo.addItem("— Sélectionner un fournisseur (optionnel) —", None)
            for s_id, name in getattr(self, "_suppliers", []):
                self.contact_combo.addItem(name, s_id)

    def _on_search(self, text: str) -> None:
        self.product_list.clear()
        text = text.strip().lower()
        if not text:
            return
        for d_id, ref, label, cat in self._display_choices:
            if text in ref.lower() or text in label.lower() or text in cat.lower():
                parts = [p for p in [cat, label, ref] if p]
                item = QListWidgetItem("  ·  ".join(parts))
                item.setData(Qt.ItemDataRole.UserRole, (d_id, ref, label))
                self.product_list.addItem(item)

    def _on_product_selected(self, item: QListWidgetItem) -> None:
        d_id, ref, label = item.data(Qt.ItemDataRole.UserRole)
        self._selected_display_id = d_id
        self.selected_product_lbl.setText(f"✔  {ref}  —  {label}")
        self.product_search.clear()
        self.product_list.clear()

    def _on_confirm(self) -> None:
        self.error_label.setText("")
        if self._selected_display_id is None:
            self.error_label.setText("Veuillez sélectionner un produit.")
            return

        r_type = self._return_type()
        contact_id = self.contact_combo.currentData()
        note = self.note_input.text().strip()

        try:
            with session_scope() as session:
                create_return(
                    session,
                    display_id=self._selected_display_id,
                    quantity=self.qty_spin.value(),
                    return_type=r_type,
                    reseller_id=contact_id if r_type == "received" else None,
                    supplier_id=contact_id if r_type == "supplier" else None,
                    note=note,
                )
        except StockError as e:
            self.error_label.setText(str(e))
            return

        self.accept()
