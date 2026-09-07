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


class Sidebar(QWidget):
    page_selected = Signal(str)
    new_sale_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(240)
        self._buttons: dict[str, QPushButton] = {}
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._stock_badge_label: QLabel | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Marque ---
        brand_frame = QFrame()
        brand_frame.setObjectName("SidebarBrand")
        brand_layout = QHBoxLayout(brand_frame)
        brand_layout.setContentsMargins(20, 18, 20, 16)
        brand_layout.setSpacing(10)

        icon_pixmap = _load_icon_pixmap(28)
        if icon_pixmap is not None:
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap)
            brand_layout.addWidget(icon_label)

        brand_texts = QVBoxLayout()
        brand_texts.setSpacing(0)
        brand_name = QLabel("MobiDesk Pro")
        brand_name.setObjectName("SidebarBrandText")
        brand_texts.addWidget(brand_name)
        brand_sub = QLabel("Gestion de stock")
        brand_sub.setObjectName("SidebarUserRole")
        brand_texts.addWidget(brand_sub)
        brand_layout.addLayout(brand_texts)
        brand_layout.addStretch()
        layout.addWidget(brand_frame)

        # --- Navigation principale (plate, sans libellés de section) ---
        nav_container = QWidget()
        nav_container.setObjectName("SidebarNav")
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(12, 16, 12, 12)
        nav_layout.setSpacing(2)

        self._add_nav_page(nav_layout, "dashboard", "home", "Tableau de bord")

        # Item Stock avec badge
        stock_row = QWidget()
        stock_row.setObjectName("SidebarNav")
        stock_row_layout = QHBoxLayout(stock_row)
        stock_row_layout.setContentsMargins(0, 0, 4, 0)
        stock_row_layout.setSpacing(0)

        stock_btn = QPushButton("Stock")
        stock_btn.setObjectName("SidebarButton")
        stock_btn.setIcon(nav_icon("monitor"))
        stock_btn.setIconSize(QSize(17, 17))
        stock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        stock_btn.setCheckable(True)
        stock_btn.clicked.connect(lambda: self.page_selected.emit("stock"))
        self._buttons["stock"] = stock_btn
        self._button_group.addButton(stock_btn)
        stock_row_layout.addWidget(stock_btn, stretch=1)

        self._stock_badge_label = QLabel("")
        self._stock_badge_label.setObjectName("SidebarBadge")
        self._stock_badge_label.setVisible(False)
        self._stock_badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stock_badge_label.setFixedSize(22, 22)
        stock_row_layout.addWidget(self._stock_badge_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        nav_layout.addWidget(stock_row)

        self._add_nav_page(nav_layout, "historique", "list", "Historique")
        self._add_nav_page(nav_layout, "profit", "coin", "Bénéfices")

        nav_layout.addSpacing(6)
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background-color: #1e293b;")
        nav_layout.addWidget(sep2)
        nav_layout.addSpacing(6)

        self._add_nav_page(nav_layout, "resellers", "users", "Revendeurs")
        self._add_nav_page(nav_layout, "suppliers", "download", "Fournisseurs")
        self._add_nav_page(nav_layout, "categories", "tag", "Catégories")
        self._add_nav_page(nav_layout, "settings", "gear", "Paramètres")

        nav_layout.addSpacing(8)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #f1f5f9;")
        nav_layout.addWidget(sep)

        nav_layout.addSpacing(8)

        # Actions secondaires (style plus discret)
        self._add_nav_action(nav_layout, "purchase", "download", "Achat fournisseur")
        self._add_nav_action(nav_layout, "stock_adjust", "list", "Ajustement de stock")

        nav_layout.addSpacing(8)
        nav_layout.addStretch()

        layout.addWidget(nav_container, stretch=1)

        # --- Pied de page ---
        layout.addWidget(self._build_user_footer())

    def _add_nav_page(self, layout: QVBoxLayout, page_key: str, icon: str, label: str) -> None:
        button = QPushButton(label)
        button.setObjectName("SidebarButton")
        button.setIcon(nav_icon(icon))
        button.setIconSize(QSize(17, 17))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setCheckable(True)
        button.clicked.connect(lambda _c, key=page_key: self.page_selected.emit(key))
        self._buttons[page_key] = button
        self._button_group.addButton(button)
        layout.addWidget(button)

    def _add_nav_action(self, layout: QVBoxLayout, page_key: str, icon: str, label: str) -> None:
        button = QPushButton(label)
        button.setObjectName("SidebarActionButton")
        button.setIcon(nav_icon(icon))
        button.setIconSize(QSize(15, 15))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setCheckable(False)
        button.clicked.connect(lambda _c, key=page_key: self.page_selected.emit(key))
        self._buttons[page_key] = button
        layout.addWidget(button)

    def _build_user_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("SidebarFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        avatar = QLabel()
        avatar.setObjectName("SidebarAvatar")
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_pixmap = _load_icon_pixmap(32)
        if avatar_pixmap is not None:
            avatar.setPixmap(avatar_pixmap)
        layout.addWidget(avatar)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        name_label = QLabel("MobiDesk Pro")
        name_label.setObjectName("SidebarUserName")
        texts.addWidget(name_label)
        role_label = QLabel("Administrateur")
        role_label.setObjectName("SidebarUserRole")
        texts.addWidget(role_label)
        layout.addLayout(texts, stretch=1)

        return footer

    def select_page(self, page_key: str) -> None:
        button = self._buttons.get(page_key)
        if button is not None and button.isCheckable():
            button.setChecked(True)

    def set_low_stock_badge(self, count: int) -> None:
        if self._stock_badge_label is None:
            return
        if count > 0:
            self._stock_badge_label.setText(str(count))
            self._stock_badge_label.setVisible(True)
        else:
            self._stock_badge_label.setVisible(False)

    # Kept for backward compat — no longer shown in UI
    def set_stock_value(self, _formatted_value: str) -> None:
        pass
