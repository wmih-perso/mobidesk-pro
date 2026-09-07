"""Boîte de dialogue d'ajout / modification d'un contact.

Un seul dialog paramétré par type d'entité (revendeur, fournisseur) plutôt
que deux quasi identiques — les deux carnets d'adresses partagent exactement
les mêmes champs et règles côté services (voir app/services.py).
"""

from __future__ import annotations

from typing import Callable, Literal

from PySide6.QtWidgets import (
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
from app.services import (
    StockError,
    create_reseller,
    create_supplier,
    deactivate_reseller,
    deactivate_supplier,
    get_reseller,
    get_supplier,
    update_reseller,
    update_supplier,
)

ContactKind = Literal["reseller", "supplier"]

_TITLES = {
    "reseller": "revendeur",
    "supplier": "fournisseur",
}

_CALLBACKS: dict[ContactKind, tuple[Callable, Callable, Callable, Callable]] = {
    "reseller": (create_reseller, update_reseller, deactivate_reseller, get_reseller),
    "supplier": (create_supplier, update_supplier, deactivate_supplier, get_supplier),
}


class ContactDialog(QDialog):
    """Formulaire d'ajout ou de modification d'un client/revendeur/fournisseur."""

    def __init__(self, kind: ContactKind, contact_id: int | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.contact_id = contact_id
        self._create, self._update, self._deactivate, self._get = _CALLBACKS[kind]
        label = _TITLES[kind]
        self.setWindowTitle(f"Modifier le {label}" if contact_id else f"Ajouter un {label}")
        self.setMinimumWidth(400)
        self.setModal(True)

        self._build_ui()
        if contact_id is not None:
            self._load_contact(contact_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Ex : Karim Benali")
        form.addRow("Nom *", self.name_input)

        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Ex : 0555 12 34 56")
        form.addRow("Téléphone", self.phone_input)

        self.address_input = QLineEdit()
        form.addRow("Adresse", self.address_input)

        self.notes_input = QTextEdit()
        self.notes_input.setFixedHeight(70)
        form.addRow("Notes", self.notes_input)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons_row = QHBoxLayout()

        if self.contact_id is not None:
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

    def _load_contact(self, contact_id: int) -> None:
        with session_scope() as session:
            contact = self._get(session, contact_id)
            self.name_input.setText(contact.name)
            self.phone_input.setText(contact.phone)
            self.address_input.setText(contact.address)
            self.notes_input.setPlainText(contact.notes)

    def _on_save(self) -> None:
        fields = dict(
            name=self.name_input.text(),
            phone=self.phone_input.text(),
            address=self.address_input.text(),
            notes=self.notes_input.toPlainText(),
        )

        try:
            with session_scope() as session:
                if self.contact_id is None:
                    self._create(session, **fields)
                else:
                    self._update(session, self.contact_id, **fields)
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()

    def _on_delete(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Confirmer la suppression",
            f"Voulez-vous vraiment supprimer ce {_TITLES[self.kind]} ? "
            "Il n'apparaîtra plus dans la liste mais son historique est conservé.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            with session_scope() as session:
                self._deactivate(session, self.contact_id)
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()
