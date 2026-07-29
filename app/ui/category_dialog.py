"""Boîte de dialogue d'ajout / modification d'une catégorie de produit."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.database import session_scope
from app.services import StockError, create_category, delete_category, get_category, update_category


class CategoryDialog(QDialog):
    """Formulaire d'ajout ou de modification d'une catégorie de produit."""

    def __init__(self, category_id: int | None = None) -> None:
        super().__init__()
        self.category_id = category_id
        self.setWindowTitle(
            "Modifier la catégorie" if category_id else "Ajouter une catégorie"
        )
        self.setMinimumWidth(360)
        self.setModal(True)

        self._build_ui()
        if category_id is not None:
            self._load_category(category_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Ex : Coque")
        form.addRow("Nom *", self.name_input)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons_row = QHBoxLayout()

        if self.category_id is not None:
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
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("SecondaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

    def _load_category(self, category_id: int) -> None:
        with session_scope() as session:
            category = get_category(session, category_id)
            self.name_input.setText(category.name)

    def _on_save(self) -> None:
        name = self.name_input.text()
        try:
            with session_scope() as session:
                if self.category_id is None:
                    create_category(session, name=name)
                else:
                    update_category(session, self.category_id, name=name)
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()

    def _on_delete(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Confirmer la suppression",
            "Voulez-vous vraiment supprimer cette catégorie ? "
            "Les produits qui l'utilisaient garderont leur catégorie actuelle "
            "(non gérée), mais elle n'apparaîtra plus dans la liste proposée.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            with session_scope() as session:
                delete_category(session, self.category_id)
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()
