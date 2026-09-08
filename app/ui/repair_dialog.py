"""Boîte de dialogue d'ajout d'une réparation (consomme des pièces du stock)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import cents_to_da, da_to_cents, format_da
from app.services import StockError, create_repair, get_repair, list_displays, update_repair
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox

PARTS_COLUMNS = ["Pièce", "Quantité", "Prix détail", ""]


class RepairDialog(FramelessDialog):
    """Formulaire d'ajout ou de modification d'une réparation.

    En mode modification, sauvegarder remplace entièrement les pièces
    consommées par la nouvelle sélection (voir update_repair) : le stock
    est réajusté automatiquement (pièces retirées remises en stock,
    nouvelles pièces décrémentées).
    """

    def __init__(self, repair_id: int | None = None) -> None:
        super().__init__()
        self.repair_id = repair_id
        self.setMinimumWidth(540)
        self.setModal(True)
        self._part_rows: list[tuple[int, int, ModernSpinBox]] = []
        self._build_ui()
        if repair_id is not None:
            self._load_repair(repair_id)

    def _build_ui(self) -> None:
        title = "Modifier la réparation" if self.repair_id else "Nouvelle réparation"
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(
            title,
            gradient="qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1565c0,stop:1 #00897b)",
        ))

        body = QWidget()
        body.setStyleSheet("background: white;")
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(20, 16, 20, 16)
        body_v.setSpacing(10)
        root.addWidget(body)
        layout = body_v

        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.client_name_input = QLineEdit()
        self.client_name_input.setPlaceholderText("Ex : Karim Benali")
        form.addRow("Client *", self.client_name_input)

        self.client_phone_input = QLineEdit()
        self.client_phone_input.setPlaceholderText("Ex : 0555 12 34 56")
        form.addRow("Téléphone client", self.client_phone_input)

        self.phone_brand_input = QLineEdit()
        self.phone_brand_input.setPlaceholderText("Ex : Samsung")
        form.addRow("Marque du téléphone", self.phone_brand_input)

        self.phone_model_input = QLineEdit()
        self.phone_model_input.setPlaceholderText("Ex : Galaxy A12")
        form.addRow("Modèle du téléphone", self.phone_model_input)

        self.description_input = QTextEdit()
        self.description_input.setFixedHeight(60)
        self.description_input.setPlaceholderText("Panne constatée...")
        form.addRow("Description", self.description_input)

        parts_label = QLabel("Pièces utilisées *")
        parts_label.setStyleSheet("font-weight: 700; margin-top: 6px;")
        layout.addWidget(parts_label)

        with session_scope() as session:
            self._display_choices = [
                (
                    d.id,
                    f"{d.reference} — {d.brand} {d.phone_model} (stock : {d.quantity})".strip(),
                    d.sale_price_retail_cents,
                )
                for d in list_displays(session)
            ]

        self.part_search_input = QLineEdit()
        self.part_search_input.setPlaceholderText(
            "Rechercher un produit (référence, marque, modèle)..."
        )
        self.part_search_input.textChanged.connect(self._on_part_search_changed)
        layout.addWidget(self.part_search_input)

        self.part_search_results = QListWidget()
        self.part_search_results.setFixedHeight(110)
        self.part_search_results.setVisible(False)
        self.part_search_results.itemClicked.connect(self._on_part_search_result_clicked)
        layout.addWidget(self.part_search_results)

        self.parts_table = QTableWidget(0, len(PARTS_COLUMNS))
        self.parts_table.setHorizontalHeaderLabels(PARTS_COLUMNS)
        header = self.parts_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.parts_table.setColumnWidth(0, 260)
        self.parts_table.setColumnWidth(1, 120)
        self.parts_table.setColumnWidth(2, 110)
        self.parts_table.setColumnWidth(3, 36)
        self.parts_table.verticalHeader().setVisible(False)
        self.parts_table.verticalHeader().setDefaultSectionSize(44)
        self.parts_table.setFixedHeight(160)
        layout.addWidget(self.parts_table)

        totals_row = QHBoxLayout()
        totals_row.addStretch()
        totals_row.addWidget(QLabel("Total pièces :"))
        self.parts_total_label = QLabel("0 DA")
        self.parts_total_label.setStyleSheet("font-weight: 700;")
        totals_row.addWidget(self.parts_total_label)
        layout.addLayout(totals_row)

        form_discount = QFormLayout()
        self.discount_input = ModernDoubleSpinBox()
        self.discount_input.setRange(0, 10_000_000)
        self.discount_input.setDecimals(0)
        self.discount_input.setSuffix(" DA")
        self.discount_input.valueChanged.connect(self._update_charged_price_label)
        form_discount.addRow("Remise", self.discount_input)
        layout.addLayout(form_discount)

        charged_row = QHBoxLayout()
        charged_row.addStretch()
        charged_row.addWidget(QLabel("Prix facturé :"))
        self.charged_price_label = QLabel("0 DA")
        self.charged_price_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #059669;")
        charged_row.addWidget(self.charged_price_label)
        layout.addLayout(charged_row)

        self.notes_input = QTextEdit()
        self.notes_input.setFixedHeight(60)
        form2 = QFormLayout()
        form2.addRow("Notes", self.notes_input)
        layout.addLayout(form2)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

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
        layout.addWidget(buttons)

    def _load_repair(self, repair_id: int) -> None:
        with session_scope() as session:
            repair = get_repair(session, repair_id)
            self.client_name_input.setText(repair.client_name)
            self.client_phone_input.setText(repair.client_phone)
            self.phone_brand_input.setText(repair.phone_brand)
            self.phone_model_input.setText(repair.phone_model)
            self.description_input.setPlainText(repair.description)
            self.notes_input.setPlainText(repair.notes)

            for item in repair.items:
                display = item.display
                label = (
                    f"{display.reference} — {display.brand} {display.phone_model} "
                    f"(stock : {display.quantity})".strip()
                )
                self._add_part_row(display.id, label, item.unit_sale_price_cents)
                self._part_rows[-1][2].setValue(item.quantity)

            self.discount_input.setValue(cents_to_da(repair.discount_cents))

        self._update_totals()

    def _on_part_search_changed(self, text: str) -> None:
        text = text.strip().lower()
        self.part_search_results.clear()

        if not text:
            self.part_search_results.setVisible(False)
            return

        already_added = {display_id for display_id, _price_cents, _spin in self._part_rows}
        matches = [
            (display_id, label, price_cents)
            for display_id, label, price_cents in self._display_choices
            if text in label.lower() and display_id not in already_added
        ]

        if not matches:
            self.part_search_results.setVisible(False)
            return

        for display_id, label, price_cents in matches:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (display_id, price_cents))
            self.part_search_results.addItem(item)
        self.part_search_results.setVisible(True)

    def _on_part_search_result_clicked(self, item: QListWidgetItem) -> None:
        display_id, price_cents = item.data(Qt.ItemDataRole.UserRole)
        label = item.text()
        self._add_part_row(display_id, label, price_cents)

        self.part_search_input.clear()
        self.part_search_results.clear()
        self.part_search_results.setVisible(False)

    def _add_part_row(self, display_id: int, label: str, price_cents: int) -> None:
        row_index = self.parts_table.rowCount()
        self.parts_table.insertRow(row_index)

        name_item = QTableWidgetItem(label)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.parts_table.setItem(row_index, 0, name_item)

        quantity_spin = ModernSpinBox()
        quantity_spin.setRange(1, 1_000_000)
        quantity_spin.setMinimumHeight(32)
        quantity_spin.valueChanged.connect(self._update_totals)
        self.parts_table.setCellWidget(row_index, 1, quantity_spin)

        price_item = QTableWidgetItem("")
        price_item.setFlags(price_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.parts_table.setItem(row_index, 2, price_item)

        remove_button = QPushButton("🗑")
        remove_button.setObjectName("IconButton")
        remove_button.clicked.connect(lambda: self._remove_part_row(quantity_spin))
        self.parts_table.setCellWidget(row_index, 3, remove_button)

        self._part_rows.append((display_id, price_cents, quantity_spin))
        self._update_totals()

    def _remove_part_row(self, quantity_spin: ModernSpinBox) -> None:
        for row_index, (_display_id, _price_cents, spin) in enumerate(self._part_rows):
            if spin is quantity_spin:
                self.parts_table.removeRow(row_index)
                self._part_rows.pop(row_index)
                break
        self._update_totals()

    def _collect_parts(self) -> list[tuple[int, int]]:
        return [
            (display_id, spin.value())
            for display_id, _price_cents, spin in self._part_rows
        ]

    def _parts_total_cents(self) -> int:
        total = 0
        for row_index, (_display_id, price_cents, spin) in enumerate(self._part_rows):
            quantity = spin.value()
            total += price_cents * quantity
            price_item = self.parts_table.item(row_index, 2)
            if price_item is not None:
                price_item.setText(format_da(price_cents * quantity))
        return total

    def _update_totals(self) -> None:
        total_cents = self._parts_total_cents()
        self.parts_total_label.setText(format_da(total_cents))
        self.discount_input.setRange(0, max(0, cents_to_da(total_cents)))
        self._update_charged_price_label()

    def _update_charged_price_label(self) -> None:
        total_cents = self._parts_total_cents()
        discount_cents = da_to_cents(self.discount_input.value())
        charged_cents = max(0, total_cents - discount_cents)
        self.charged_price_label.setText(format_da(charged_cents))

    def _on_save(self) -> None:
        fields = dict(
            client_name=self.client_name_input.text(),
            client_phone=self.client_phone_input.text(),
            phone_brand=self.phone_brand_input.text(),
            phone_model=self.phone_model_input.text(),
            description=self.description_input.toPlainText(),
            discount_cents=da_to_cents(self.discount_input.value()),
            parts=self._collect_parts(),
            notes=self.notes_input.toPlainText(),
        )

        try:
            with session_scope() as session:
                if self.repair_id is None:
                    create_repair(session, **fields)
                else:
                    update_repair(session, self.repair_id, **fields)
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()
