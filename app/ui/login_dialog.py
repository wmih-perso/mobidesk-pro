"""Boîte de dialogue de connexion (mot de passe unique)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from app.auth import verify_password


class LoginDialog(QDialog):
    """Demande le mot de passe avant d'ouvrir le tableau de bord.

    Un mot de passe correct accepte la boîte de dialogue ; fermer la
    fenêtre (croix, Échap) la rejette — l'appelant doit alors quitter
    l'application sans jamais construire la fenêtre principale.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Connexion — MobiDesk Pro")
        self.setMinimumWidth(360)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        title = QLabel("🔒  MobiDesk Pro")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel("Entrez le mot de passe pour continuer.")
        subtitle.setStyleSheet("color: #8991ac;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.returnPressed.connect(self._on_login)
        form.addRow("Mot de passe", self.password_input)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Se connecter")
        buttons.accepted.connect(self._on_login)
        layout.addWidget(buttons)

        self.password_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_login(self) -> None:
        if verify_password(self.password_input.text()):
            self.accept()
        else:
            self.error_label.setText("Mot de passe incorrect.")
            self.password_input.clear()
            self.password_input.setFocus(Qt.FocusReason.OtherFocusReason)
