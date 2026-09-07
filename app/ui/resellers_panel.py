"""Panneau de gestion des revendeurs (Paramètres > Revendeurs)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.services import StockError, create_reseller, deactivate_reseller, list_resellers
from app.ui.widgets import EmptyState


class ResellersPanel(QWidget):
    """Liste des revendeurs avec ajout et suppression."""

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        actions_row = QHBoxLayout()
        actions_row.addStretch()
        add_button = QPushButton("+  Nouveau revendeur")
        add_button.clicked.connect(self._on_add)
        actions_row.addWidget(add_button)
        layout.addLayout(actions_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Nom", "Téléphone", ""])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setFixedHeight(160)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 36)
        layout.addWidget(self.table)

        self.empty_state = EmptyState(
            "👥",
            "Aucun revendeur enregistré",
            "Ajoutez vos revendeurs pour les associer aux ventes en gros.",
            "+  Nouveau revendeur",
        )
        self.empty_state.action_button.clicked.connect(self._on_add)
        layout.addWidget(self.empty_state)

    def refresh(self) -> None:
        with session_scope() as session:
            resellers = list_resellers(session)

        has_rows = bool(resellers)
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)

        self.table.setRowCount(len(resellers))
        for i, r in enumerate(resellers):
            self.table.setItem(i, 0, QTableWidgetItem(r.name))
            self.table.setItem(i, 1, QTableWidgetItem(r.phone or ""))

            btn = QPushButton("×")
            btn.setObjectName("IconButton")
            btn.setFixedSize(24, 24)
            btn.setStyleSheet("padding: 0; color: #dc2626; font-weight: 700;")
            btn.setToolTip("Supprimer")
            btn.clicked.connect(lambda _checked, rid=r.id: self._on_delete(rid))
            self.table.setCellWidget(i, 2, btn)

    def _on_add(self) -> None:
        dialog = _ResellerDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                with session_scope() as session:
                    create_reseller(session, name=dialog.reseller_name, phone=dialog.reseller_phone)
                self.refresh()
            except StockError as error:
                QMessageBox.warning(self, "Erreur", str(error))

    def _on_delete(self, reseller_id: int) -> None:
        confirm = QMessageBox.question(
            self,
            "Supprimer le revendeur",
            "Ce revendeur sera retiré de la liste. L'historique des ventes reste intact.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        with session_scope() as session:
            deactivate_reseller(session, reseller_id)
        self.refresh()


class _ResellerDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouveau revendeur")
        self.setModal(True)
        self.reseller_name = ""
        self.reseller_phone = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Ex : Boutique Algiers")
        form.addRow("Nom *", self._name_input)

        self._phone_input = QLineEdit()
        self._phone_input.setPlaceholderText("Ex : 0555 12 34 56")
        form.addRow("Téléphone", self._phone_input)

        layout.addLayout(form)

        self._error_label = QLabel("")
        self._error_label.setObjectName("ErrorLabel")
        layout.addWidget(self._error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("SecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        name = self._name_input.text().strip()
        if not name:
            self._error_label.setText("Le nom est obligatoire.")
            return
        self.reseller_name = name
        self.reseller_phone = self._phone_input.text().strip()
        self.accept()
