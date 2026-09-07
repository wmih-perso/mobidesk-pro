"""Onglet « Contacts » — carnets d'adresses revendeurs et fournisseurs."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.services import list_resellers, list_suppliers
from app.ui.contact_dialog import ContactDialog, ContactKind
from app.ui.widgets import EmptyState, apply_card_shadow

CONTACT_COLUMNS = ["Nom", "Téléphone", "Adresse", "Notes"]


class ContactListPanel(QWidget):
    """Liste d'un seul carnet d'adresses (revendeur ou fournisseur),
    avec ajout/édition via ContactDialog — calqué sur RepairsTab."""

    def __init__(
        self,
        kind: ContactKind,
        *,
        add_label: str,
        empty_icon: str,
        empty_title: str,
        empty_subtitle: str,
        list_func: Callable,
    ) -> None:
        super().__init__()
        self.kind = kind
        self._list_func = list_func
        self._build_ui(add_label, empty_icon, empty_title, empty_subtitle)
        self.refresh()

    def _build_ui(self, add_label: str, empty_icon: str, empty_title: str, empty_subtitle: str) -> None:
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
        add_button = QPushButton(add_label)
        add_button.clicked.connect(self._on_add)
        actions_row.addWidget(add_button)
        table_layout.addLayout(actions_row)

        self.table = QTableWidget(0, len(CONTACT_COLUMNS))
        self.table.setHorizontalHeaderLabels(CONTACT_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._on_edit)
        table_layout.addWidget(self.table, stretch=1)

        self.empty_state = EmptyState(empty_icon, empty_title, empty_subtitle)
        table_layout.addWidget(self.empty_state, stretch=1)

        outer.addWidget(table_card, stretch=1)

    def _on_add(self) -> None:
        dialog = ContactDialog(self.kind)
        if dialog.exec() == ContactDialog.DialogCode.Accepted:
            self.refresh()

    def _selected_contact_id(self) -> int | None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_edit(self) -> None:
        contact_id = self._selected_contact_id()
        if contact_id is None:
            return
        dialog = ContactDialog(self.kind, contact_id)
        if dialog.exec() == ContactDialog.DialogCode.Accepted:
            self.refresh()

    def refresh(self) -> None:
        with session_scope() as session:
            contacts = self._list_func(session)
            rows = [
                (c.id, c.name, c.phone, c.address, c.notes) for c in contacts
            ]

        self._render_table(rows)

    def _render_table(self, rows: list[tuple]) -> None:
        has_rows = len(rows) > 0
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)

        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            contact_id, *values = row
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, contact_id)
                self.table.setItem(row_index, column_index, item)


class ContactsTab(QWidget):
    """Regroupe les trois carnets d'adresses (clients, revendeurs,
    fournisseurs) dans un seul onglet à sous-onglets."""

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.sub_tabs = QTabWidget()
        outer.addWidget(self.sub_tabs)

        self.resellers_panel = ContactListPanel(
            "reseller",
            add_label="+  Nouveau revendeur",
            empty_icon="🛒",
            empty_title="Aucun revendeur enregistré",
            empty_subtitle="Cliquez sur « Nouveau revendeur » pour en ajouter un.",
            list_func=list_resellers,
        )
        self.sub_tabs.addTab(self.resellers_panel, "Revendeurs")

        self.suppliers_panel = ContactListPanel(
            "supplier",
            add_label="+  Nouveau fournisseur",
            empty_icon="🚚",
            empty_title="Aucun fournisseur enregistré",
            empty_subtitle="Cliquez sur « Nouveau fournisseur » pour en ajouter un.",
            list_func=list_suppliers,
        )
        self.sub_tabs.addTab(self.suppliers_panel, "Fournisseurs")

    def refresh(self) -> None:
        self.resellers_panel.refresh()
        self.suppliers_panel.refresh()
