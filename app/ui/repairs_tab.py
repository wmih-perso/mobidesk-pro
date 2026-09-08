"""Onglet « Réparations » — historique des réparations facturées."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import format_da
from app.services import list_repairs
from app.ui.repair_dialog import RepairDialog
from app.ui.widgets import EmptyState, apply_card_shadow

REPAIR_COLUMNS = [
    "Date",
    "Client",
    "Téléphone",
    "Appareil",
    "Remise",
    "Prix facturé",
    "Pièces utilisées",
]


class RepairsTab(QWidget):
    """Liste des réparations enregistrées, avec ajout via RepairDialog."""

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        table_card = QFrame()
        table_card.setObjectName("Card")
        apply_card_shadow(table_card)
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 14, 16, 12)
        table_layout.setSpacing(8)

        actions_row = QHBoxLayout()
        actions_row.addStretch()
        add_button = QPushButton("+  Nouvelle réparation")
        add_button.clicked.connect(self._on_add)
        actions_row.addWidget(add_button)
        table_layout.addLayout(actions_row)

        self.repairs_table = QTableWidget(0, len(REPAIR_COLUMNS))
        self.repairs_table.setHorizontalHeaderLabels(REPAIR_COLUMNS)
        self.repairs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.repairs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.repairs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.repairs_table.setAlternatingRowColors(True)
        self.repairs_table.setShowGrid(False)
        self.repairs_table.verticalHeader().setVisible(False)
        self.repairs_table.verticalHeader().setDefaultSectionSize(40)
        self.repairs_table.setFrameShape(QFrame.Shape.NoFrame)
        self.repairs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.repairs_table.horizontalHeader().setStretchLastSection(True)
        self.repairs_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.repairs_table.doubleClicked.connect(self._on_edit)
        table_layout.addWidget(self.repairs_table, stretch=1)

        self.repairs_empty_state = EmptyState(
            "🔧",
            "Aucune réparation enregistrée",
            "Cliquez sur « Nouvelle réparation » pour en ajouter une.",
        )
        table_layout.addWidget(self.repairs_empty_state, stretch=1)

        outer.addWidget(table_card, stretch=1)

    def _on_add(self) -> None:
        dialog = RepairDialog()
        if dialog.exec() == RepairDialog.DialogCode.Accepted:
            self.refresh()

    def _selected_repair_id(self) -> int | None:
        selected_rows = self.repairs_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        item = self.repairs_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_edit(self) -> None:
        repair_id = self._selected_repair_id()
        if repair_id is None:
            return
        dialog = RepairDialog(repair_id)
        if dialog.exec() == RepairDialog.DialogCode.Accepted:
            self.refresh()

    def refresh(self) -> None:
        with session_scope() as session:
            repairs = list_repairs(session)
            rows = [
                (
                    r.id,
                    r.created_at.strftime("%d/%m/%Y %H:%M"),
                    r.client_name,
                    r.client_phone,
                    f"{r.phone_brand} {r.phone_model}".strip(),
                    format_da(r.discount_cents),
                    format_da(r.charged_price_cents),
                    ", ".join(
                        f"{item.display.reference} x{item.quantity}" for item in r.items
                    ),
                )
                for r in repairs
            ]

        self._render_table(rows)

    def _render_table(self, rows: list[tuple]) -> None:
        has_rows = len(rows) > 0
        self.repairs_table.setVisible(has_rows)
        self.repairs_empty_state.setVisible(not has_rows)

        self.repairs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            repair_id, *values = row
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, repair_id)
                self.repairs_table.setItem(row_index, column_index, item)
