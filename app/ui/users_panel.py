"""Panel de gestion des comptes utilisateurs (admin uniquement)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from app.database import session_scope
from app.ui.frameless_dialog import FramelessDialog
from app.services import (
    StockError,
    change_user_password,
    create_user,
    delete_user,
    list_users,
    update_user,
)
from app.ui.widgets import apply_card_shadow


class _UserDialog(FramelessDialog):
    """Création ou édition d'un compte utilisateur."""

    def __init__(self, user_id: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self._user_id = user_id
        self.setMinimumWidth(420)
        self.setModal(True)
        self._build_ui()
        if user_id:
            self._load(user_id)

    def _build_ui(self) -> None:
        title = "Modifier le compte" if self._user_id else "Nouveau compte"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._make_header(
            title,
            gradient="qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #374151,stop:1 #1565c0)",
        ))

        body = QWidget()
        body.setStyleSheet("background: white;")
        root = QVBoxLayout(body)
        root.setSpacing(14)
        root.setContentsMargins(24, 20, 24, 20)
        outer.addWidget(body)

        form = QFormLayout()
        form.setSpacing(10)

        def _lbl(t):
            l = QLabel(t)
            l.setStyleSheet("font-weight: 600; color: #374151;")
            return l

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("ex: caisse1")
        form.addRow(_lbl("Nom d'utilisateur *"), self.username_input)

        self.display_name_input = QLineEdit()
        self.display_name_input.setPlaceholderText("ex: Ahmed")
        form.addRow(_lbl("Prénom / Nom affiché *"), self.display_name_input)

        self.role_combo = QComboBox()
        self.role_combo.addItem("Caissier", "cashier")
        self.role_combo.addItem("Administrateur", "admin")
        form.addRow(_lbl("Rôle"), self.role_combo)

        root.addLayout(form)

        # Mot de passe (obligatoire à la création, optionnel en édition)
        pwd_title = QLabel("Mot de passe" + ("" if self._user_id else " *"))
        pwd_title.setStyleSheet("font-weight: 700; color: #374151; margin-top: 6px;")
        root.addWidget(pwd_title)

        if self._user_id:
            hint = QLabel("Laisser vide pour ne pas changer le mot de passe.")
            hint.setStyleSheet("color: #9ca3af; font-size: 12px;")
            root.addWidget(hint)

        pwd_form = QFormLayout()
        pwd_form.setSpacing(8)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Mot de passe")
        pwd_form.addRow(_lbl("Mot de passe"), self.password_input)

        self.confirm_input = QLineEdit()
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_input.setPlaceholderText("Confirmer")
        pwd_form.addRow(_lbl("Confirmer"), self.confirm_input)

        root.addLayout(pwd_form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626; font-size: 12px;")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        cancel = QPushButton("Annuler")
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()

        save = QPushButton("💾  Enregistrer")
        save.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " border-radius: 6px; padding: 9px 20px; font-weight: 700; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        save.clicked.connect(self._on_save)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    def _load(self, user_id: int) -> None:
        with session_scope() as session:
            from app.models import User
            user = session.get(User, user_id)
            if user:
                self.username_input.setText(user.username)
                self.username_input.setReadOnly(True)
                self.display_name_input.setText(user.display_name)
                idx = self.role_combo.findData(user.role)
                if idx >= 0:
                    self.role_combo.setCurrentIndex(idx)

    def _on_save(self) -> None:
        self.error_label.setText("")
        username = self.username_input.text().strip()
        display_name = self.display_name_input.text().strip()
        role = self.role_combo.currentData()
        password = self.password_input.text()
        confirm = self.confirm_input.text()

        if not display_name:
            self.error_label.setText("Le prénom/nom affiché est obligatoire.")
            return

        if password or not self._user_id:
            if not password:
                self.error_label.setText("Le mot de passe est obligatoire.")
                return
            if password != confirm:
                self.error_label.setText("Les deux mots de passe ne correspondent pas.")
                return

        try:
            with session_scope() as session:
                if self._user_id:
                    update_user(session, self._user_id, display_name=display_name, role=role)
                    if password:
                        change_user_password(session, self._user_id, password)
                else:
                    create_user(
                        session,
                        username=username,
                        display_name=display_name,
                        password=password,
                        role=role,
                    )
        except StockError as e:
            self.error_label.setText(str(e))
            return

        self.accept()


class UsersPanel(QWidget):
    """Page de gestion des comptes — accessible aux administrateurs uniquement."""

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 24)
        outer.setSpacing(16)

        header_row = QHBoxLayout()
        title = QLabel("👥  Gestion des comptes")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        header_row.addWidget(title)
        header_row.addStretch()

        add_btn = QPushButton("+  Nouveau compte")
        add_btn.clicked.connect(self._on_add)
        header_row.addWidget(add_btn)
        outer.addLayout(header_row)

        hint = QLabel(
            "Les caissiers ont accès à toutes les fonctions sauf la gestion des comptes. "
            "L'administrateur peut créer, modifier et supprimer des comptes."
        )
        hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 12)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Utilisateur", "Nom affiché", "Rôle", "Statut", ""])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setFrameShape(QFrame.Shape.NoFrame)

        from PySide6.QtWidgets import QHeaderView
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 140)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        card_layout.addWidget(self.table)
        outer.addWidget(card, stretch=1)

    def refresh(self) -> None:
        with session_scope() as session:
            users = list_users(session)
            rows = [
                (u.id, u.username, u.display_name, u.role, u.is_active)
                for u in users
            ]

        self.table.setRowCount(len(rows))
        for i, (uid, username, display_name, role, is_active) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(username))
            self.table.setItem(i, 1, QTableWidgetItem(display_name))

            role_lbl = "Administrateur" if role == "admin" else "Caissier"
            role_item = QTableWidgetItem(role_lbl)
            role_item.setForeground(
                Qt.GlobalColor.darkMagenta if role == "admin" else Qt.GlobalColor.darkCyan
            )
            self.table.setItem(i, 2, role_item)

            status = "Actif" if is_active else "Désactivé"
            status_item = QTableWidgetItem(status)
            status_item.setForeground(
                Qt.GlobalColor.darkGreen if is_active else Qt.GlobalColor.red
            )
            self.table.setItem(i, 3, status_item)

            # Actions
            wrapper = QWidget()
            row_layout = QHBoxLayout(wrapper)
            row_layout.setContentsMargins(4, 2, 4, 2)
            row_layout.setSpacing(4)

            edit_btn = QPushButton("✏")
            edit_btn.setObjectName("IconButton")
            edit_btn.setFixedSize(28, 28)
            edit_btn.setToolTip("Modifier")
            edit_btn.clicked.connect(lambda _c, u_id=uid: self._on_edit(u_id))
            row_layout.addWidget(edit_btn)

            toggle_btn = QPushButton("🔴" if is_active else "🟢")
            toggle_btn.setObjectName("IconButton")
            toggle_btn.setFixedSize(28, 28)
            toggle_btn.setToolTip("Désactiver" if is_active else "Activer")
            toggle_btn.clicked.connect(
                lambda _c, u_id=uid, active=is_active: self._on_toggle(u_id, active)
            )
            row_layout.addWidget(toggle_btn)

            del_btn = QPushButton("×")
            del_btn.setObjectName("IconButton")
            del_btn.setFixedSize(28, 28)
            del_btn.setStyleSheet(
                "QPushButton { color: #dc2626; font-weight: 700; font-size: 15px; }"
            )
            del_btn.setToolTip("Supprimer")
            del_btn.clicked.connect(lambda _c, u_id=uid, name=display_name: self._on_delete(u_id, name))
            row_layout.addWidget(del_btn)

            row_layout.addStretch()
            self.table.setCellWidget(i, 4, wrapper)

    def _on_add(self) -> None:
        dlg = _UserDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_edit(self, user_id: int) -> None:
        dlg = _UserDialog(user_id=user_id, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_toggle(self, user_id: int, currently_active: bool) -> None:
        action = "désactiver" if currently_active else "activer"
        confirm = QMessageBox.question(
            self, "Confirmer", f"Voulez-vous {action} ce compte ?"
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            with session_scope() as session:
                update_user(session, user_id, is_active=not currently_active)
        except StockError as e:
            QMessageBox.warning(self, "Erreur", str(e))
            return
        self.refresh()

    def _on_delete(self, user_id: int, name: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Supprimer le compte",
            f"Supprimer définitivement le compte « {name} » ?\nCette action est irréversible.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            with session_scope() as session:
                delete_user(session, user_id)
        except StockError as e:
            QMessageBox.warning(self, "Erreur", str(e))
            return
        self.refresh()
