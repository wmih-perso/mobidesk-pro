"""Menu latéral de navigation."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import nav_icon

APP_ICON_PATH = Path(__file__).resolve().parent / "resources" / "app_icon.png"


def _load_icon_pixmap(size: int) -> QPixmap | None:
    if not APP_ICON_PATH.exists():
        return None
    pixmap = QPixmap(str(APP_ICON_PATH))
    return pixmap.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )

# (clé de page, icône, libellé, action ponctuelle plutôt qu'une page)
NAV_SECTIONS: list[tuple[str, list[tuple[str, str, str, bool]]]] = [
    ("", [("dashboard", "home", "Tableau de bord", False)]),
    (
        "Gestion du stock",
        [
            ("displays", "monitor", "Produits", False),
            ("movements", "list", "Mouvements de stock", False),
            ("alerts", "bell", "Alertes de stock", False),
            ("profit", "coin", "Bénéfices", False),
            ("repairs", "wrench", "Réparations", False),
            ("contacts", "users", "Contacts", False),
        ],
    ),
    (
        "Actions rapides",
        [
            ("purchase", "download", "Achat fournisseur", True),
            ("sale_wholesale", "upload", "Vente en gros", True),
            ("stock_adjust", "list", "Ajustement de stock", True),
        ],
    ),
    ("", [("settings", "gear", "Paramètres", False)]),
]


class Sidebar(QWidget):
    page_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(270)
        self._buttons: dict[str, QPushButton] = {}
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand_frame = QFrame()
        brand_frame.setObjectName("SidebarBrand")
        brand_layout = QHBoxLayout(brand_frame)
        brand_layout.setContentsMargins(22, 22, 22, 18)
        brand_layout.setSpacing(10)

        icon_pixmap = _load_icon_pixmap(28)
        if icon_pixmap is not None:
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap)
            brand_layout.addWidget(icon_label)

        brand_text = QLabel("MobiDesk Pro")
        brand_text.setObjectName("SidebarBrandText")
        brand_layout.addWidget(brand_text)
        brand_layout.addStretch()

        layout.addWidget(brand_frame)

        nav_container = QWidget()
        nav_container.setObjectName("SidebarNav")
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(12, 12, 12, 12)
        nav_layout.setSpacing(2)

        for section_title, items in NAV_SECTIONS:
            if section_title:
                section_label = QLabel(section_title.upper())
                section_label.setObjectName("SidebarSectionLabel")
                nav_layout.addSpacing(14)
                nav_layout.addWidget(section_label)

            for page_key, icon, label, is_action in items:
                button = QPushButton(label)
                button.setObjectName("SidebarButton")
                button.setIcon(nav_icon(icon))
                button.setIconSize(QSize(19, 19))
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(
                    lambda _checked, key=page_key: self.page_selected.emit(key)
                )
                if is_action:
                    # Une action ponctuelle (ouvre une boîte de dialogue) ne doit
                    # pas rester "sélectionnée" comme le ferait une page.
                    button.setCheckable(False)
                else:
                    button.setCheckable(True)
                    self._button_group.addButton(button)
                self._buttons[page_key] = button
                nav_layout.addWidget(button)

        nav_layout.addStretch()
        layout.addWidget(nav_container, stretch=1)

        layout.addWidget(self._build_summary_card())
        layout.addWidget(self._build_user_footer())

    def _build_summary_card(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("SidebarNav")
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(16, 8, 16, 16)

        card = QFrame()
        card.setObjectName("SidebarSummaryCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 16)
        card_layout.setSpacing(2)

        title = QLabel("Résumé rapide")
        title.setObjectName("SidebarSummaryTitle")
        card_layout.addWidget(title)

        label = QLabel("Valeur totale du stock")
        label.setObjectName("SidebarSummaryLabel")
        card_layout.addWidget(label)

        self.summary_value_label = QLabel("0 DA")
        self.summary_value_label.setObjectName("SidebarSummaryValue")
        card_layout.addWidget(self.summary_value_label)

        wrapper_layout.addWidget(card)
        return wrapper

    def _build_user_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("SidebarFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        avatar = QLabel()
        avatar.setObjectName("SidebarAvatar")
        avatar.setFixedSize(36, 36)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_pixmap = _load_icon_pixmap(36)
        if avatar_pixmap is not None:
            avatar.setPixmap(avatar_pixmap)
        layout.addWidget(avatar)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        name_label = QLabel("MobiDesk Pro")
        name_label.setObjectName("SidebarUserName")
        texts.addWidget(name_label)
        role_label = QLabel("Gestion de stock")
        role_label.setObjectName("SidebarUserRole")
        texts.addWidget(role_label)
        layout.addLayout(texts, stretch=1)

        return footer

    def select_page(self, page_key: str) -> None:
        button = self._buttons.get(page_key)
        if button is not None:
            button.setChecked(True)

    def set_stock_value(self, formatted_value: str) -> None:
        self.summary_value_label.setText(formatted_value)
