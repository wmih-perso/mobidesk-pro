"""Panneau de gestion des catégories de produits (Paramètres > Catégories).

Toute catégorie ajoutée ici apparaît automatiquement dans le formulaire
d'ajout/modification de produit (voir app/ui/display_dialog.py).
"""

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
from app.services import list_categories
from app.ui.category_dialog import CategoryDialog
from app.ui.widgets import EmptyState

CATEGORY_COLUMNS = ["Nom"]


class CategoryPanel(QWidget):
    """Liste des catégories de produits, avec ajout/édition via CategoryDialog."""

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        actions_row = QHBoxLayout()
        actions_row.addStretch()
        add_button = QPushButton("+  Nouvelle catégorie")
        add_button.clicked.connect(self._on_add)
        actions_row.addWidget(add_button)
        outer.addLayout(actions_row)

        self.table = QTableWidget(0, len(CATEGORY_COLUMNS))
        self.table.setHorizontalHeaderLabels(CATEGORY_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setMaximumHeight(220)
        self.table.doubleClicked.connect(self._on_edit)
        outer.addWidget(self.table)

        self.empty_state = EmptyState(
            "🏷️",
            "Aucune catégorie enregistrée",
            "Cliquez sur « Nouvelle catégorie » pour en ajouter une.",
        )
        outer.addWidget(self.empty_state)

    def _on_add(self) -> None:
        dialog = CategoryDialog()
        if dialog.exec() == CategoryDialog.DialogCode.Accepted:
            self.refresh()

    def _selected_category_id(self) -> int | None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_edit(self) -> None:
        category_id = self._selected_category_id()
        if category_id is None:
            return
        dialog = CategoryDialog(category_id)
        if dialog.exec() == CategoryDialog.DialogCode.Accepted:
            self.refresh()

    def refresh(self) -> None:
        with session_scope() as session:
            categories = list_categories(session)
            rows = [(c.id, c.name) for c in categories]

        has_rows = len(rows) > 0
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)

        self.table.setRowCount(len(rows))
        for row_index, (category_id, name) in enumerate(rows):
            item = QTableWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, category_id)
            self.table.setItem(row_index, 0, item)
