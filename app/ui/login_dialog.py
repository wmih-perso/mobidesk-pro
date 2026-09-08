"""Boîte de dialogue de connexion multi-comptes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app import session as _session
from app.services import verify_user_password
from app.ui.frameless_dialog import FramelessDialog


class LoginDialog(FramelessDialog):
    """Demande username + mot de passe avant d'ouvrir le tableau de bord."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(400)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        root.addWidget(
            self._make_header(
                "🔒  MobiDesk Pro",
                gradient="qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1a1a2e,stop:1 #16213e)",
                height=64,
            )
        )

        body = QWidget()
        body.setStyleSheet("background: white;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        sub = QLabel("Entrez vos identifiants pour continuer.")
        sub.setStyleSheet("color: #6b7280; font-size: 13px;")
        layout.addWidget(sub)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def _lbl(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: 600; color: #374151; font-size: 13px;")
            return lbl

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Nom d'utilisateur")
        self.username_input.returnPressed.connect(self._on_login)
        form.addRow(_lbl("Utilisateur"), self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Mot de passe")
        self.password_input.returnPressed.connect(self._on_login)
        form.addRow(_lbl("Mot de passe"), self.password_input)

        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            "color: #dc2626; font-size: 12px; background: #fef2f2;"
            " border: 1px solid #fca5a5; border-radius: 6px; padding: 8px 12px;"
        )
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        login_btn = QPushButton("Se connecter")
        login_btn.setFixedHeight(48)
        login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        login_btn.setStyleSheet(
            "QPushButton { background: #009688; color: white; border: none;"
            " border-radius: 10px; font-weight: 700; font-size: 14px; }"
            "QPushButton:hover { background: #00796b; }"
            "QPushButton:pressed { background: #00695c; }"
        )
        login_btn.clicked.connect(self._on_login)
        layout.addWidget(login_btn)

        root.addWidget(body)
        self.username_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_login(self) -> None:
        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username:
            self._show_error("Veuillez entrer votre nom d'utilisateur.")
            return

        with session_scope() as db:
            user = verify_user_password(db, username, password)

        if user is None:
            self._show_error("Identifiants incorrects ou compte désactivé.")
            self.password_input.clear()
            self.password_input.setFocus(Qt.FocusReason.OtherFocusReason)
            return

        _session.set_current_user(
            _session.UserSession(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                role=user.role,
            )
        )
        self.accept()

    def _show_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.show()
