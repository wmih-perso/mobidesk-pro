"""Boîte de dialogue d'ajout / modification d'une catégorie de produit."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.services import StockError, create_category, delete_category, get_category, update_category
from app.ui.frameless_dialog import FramelessDialog


class CategoryDialog(FramelessDialog):
    """Formulaire d'ajout ou de modification d'une catégorie de produit."""

    def __init__(self, category_id: int | None = None) -> None:
        super().__init__()
        self.category_id = category_id
        self.setMinimumWidth(380)
        self.setModal(True)
        self._build_ui()
        if category_id is not None:
            self._load_category(category_id)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = "Modifier la catégorie" if self.category_id else "Ajouter une catégorie"
        root.addWidget(self._make_header(title))

        # Body
        body = QWidget()
        body.setStyleSheet("background: white;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 20, 28, 12)
        body_layout.setSpacing(8)

        name_lbl = QLabel("Nom *")
        name_lbl.setStyleSheet("font-weight: 600; color: #374151; font-size: 13px;")
        body_layout.addWidget(name_lbl)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Ex : Coque")
        self.name_input.setMinimumHeight(38)
        self.name_input.setStyleSheet(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 6px;"
            " padding: 7px 10px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #0097a7; }"
        )
        self.name_input.returnPressed.connect(self._on_save)
        body_layout.addWidget(self.name_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            "color: #dc2626; font-size: 12px; background: #fef2f2;"
            " border: 1px solid #fca5a5; border-radius: 6px; padding: 8px 12px;"
        )
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        body_layout.addWidget(self.error_label)

        body_layout.addStretch()
        root.addWidget(body, stretch=1)

        # Footer
        footer = QFrame()
        footer.setFixedHeight(68)
        footer.setStyleSheet("QFrame { background: #f0f9ff; border-top: 1px solid #bae6fd; }")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 0, 20, 0)
        footer_layout.setSpacing(10)

        if self.category_id is not None:
            del_btn = QPushButton("🗑  Supprimer")
            del_btn.setFixedHeight(44)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setStyleSheet(
                "QPushButton { background: #fee2e2; color: #dc2626; border: 1px solid #fca5a5;"
                " border-radius: 8px; font-weight: 700; font-size: 13px; padding: 0 14px; }"
                "QPushButton:hover { background: #dc2626; color: white; border-color: #dc2626; }"
            )
            del_btn.clicked.connect(self._on_delete)
            footer_layout.addWidget(del_btn)

        footer_layout.addStretch()

        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedHeight(44)
        save_btn.setMinimumWidth(150)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border: none;"
            " border-radius: 8px; font-weight: 700; font-size: 13px; padding: 0 18px; }"
            "QPushButton:hover { background: #0d47a1; }"
            "QPushButton:pressed { background: #0a2f6e; }"
        )
        save_btn.clicked.connect(self._on_save)
        footer_layout.addWidget(save_btn)

        root.addWidget(footer)

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
            self.error_label.show()
            return
        self.accept()

    def _on_delete(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Confirmer la suppression",
            "Voulez-vous vraiment supprimer cette catégorie ?\n"
            "Les produits qui l'utilisaient garderont leur catégorie actuelle.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            with session_scope() as session:
                delete_category(session, self.category_id)
        except StockError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.accept()
