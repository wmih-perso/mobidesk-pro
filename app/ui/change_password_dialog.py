"""Boîte de dialogue de changement du mot de passe."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from app.auth import set_password, verify_password


class ChangePasswordDialog(QDialog):
    """Change le mot de passe partagé, après vérification de l'actuel."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Changer le mot de passe")
        self.setMinimumWidth(380)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.current_password_input = QLineEdit()
        self.current_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Mot de passe actuel", self.current_password_input)

        self.new_password_input = QLineEdit()
        self.new_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Nouveau mot de passe", self.new_password_input)

        self.confirm_password_input = QLineEdit()
        self.confirm_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Confirmer le nouveau mot de passe", self.confirm_password_input)

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

    def _on_save(self) -> None:
        if not verify_password(self.current_password_input.text()):
            self.error_label.setText("Mot de passe actuel incorrect.")
            return

        new_password = self.new_password_input.text()
        if not new_password:
            self.error_label.setText("Le nouveau mot de passe ne peut pas être vide.")
            return
        if new_password != self.confirm_password_input.text():
            self.error_label.setText("Les deux mots de passe ne correspondent pas.")
            return

        set_password(new_password)
        self.accept()
