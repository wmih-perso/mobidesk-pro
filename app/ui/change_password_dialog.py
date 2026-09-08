"""Boîte de dialogue de changement du mot de passe."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.auth import set_password, verify_password
from app.ui.frameless_dialog import FramelessDialog


class ChangePasswordDialog(FramelessDialog):
    """Change le mot de passe partagé, après vérification de l'actuel."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(400)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(
            "🔐  Changer le mot de passe",
            gradient="qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #374151,stop:1 #1565c0)",
        ))

        body = QWidget()
        body.setStyleSheet("background: white;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 24)
        body_layout.setSpacing(12)
        root.addWidget(body)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def _lbl(t):
            lbl = QLabel(t)
            lbl.setStyleSheet("font-weight: 600; color: #374151; font-size: 13px;")
            return lbl

        self.current_password_input = QLineEdit()
        self.current_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_lbl("Mot de passe actuel"), self.current_password_input)

        self.new_password_input = QLineEdit()
        self.new_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_lbl("Nouveau mot de passe"), self.new_password_input)

        self.confirm_password_input = QLineEdit()
        self.confirm_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_input.returnPressed.connect(self._on_save)
        form.addRow(_lbl("Confirmer"), self.confirm_password_input)

        body_layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            "color: #dc2626; font-size: 12px; background: #fef2f2;"
            " border: 1px solid #fca5a5; border-radius: 6px; padding: 8px 12px;"
        )
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        body_layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedHeight(44)
        save_btn.setMinimumWidth(160)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border: none;"
            " border-radius: 8px; font-weight: 700; font-size: 13px; padding: 0 16px; }"
            "QPushButton:hover { background: #0d47a1; }"
        )
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        body_layout.addLayout(btn_row)

    def _on_save(self) -> None:
        if not verify_password(self.current_password_input.text()):
            self._show_error("Mot de passe actuel incorrect.")
            return
        new_password = self.new_password_input.text()
        if not new_password:
            self._show_error("Le nouveau mot de passe ne peut pas être vide.")
            return
        if new_password != self.confirm_password_input.text():
            self._show_error("Les deux mots de passe ne correspondent pas.")
            return
        set_password(new_password)
        self.accept()

    def _show_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.show()
