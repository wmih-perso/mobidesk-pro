"""Barre de navigation supérieure horizontale."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

APP_ICON_PATH = Path(__file__).resolve().parent / "resources" / "app_icon.png"


def _load_icon_pixmap(size: int) -> QPixmap | None:
    if not APP_ICON_PATH.exists():
        return None
    pixmap = QPixmap(str(APP_ICON_PATH))
    return pixmap.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def _make_logo_badge(size: int = 36) -> QPixmap:
    """Logo rond teal avec les initiales 'M' en blanc."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2563eb"))
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(QColor("#ffffff"))
    from PySide6.QtGui import QFont
    font = QFont("Segoe UI", int(size * 0.42), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "M")
    painter.end()
    return pixmap


class Sidebar(QWidget):
    """Barre de navigation horizontale."""

    page_selected = Signal(str)
    new_sale_requested = Signal()
    add_product_requested = Signal()
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TopNav")
        self.setFixedHeight(56)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._buttons: dict[str, QPushButton] = {}
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._stock_badge_label: QLabel | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        # Badge logo rond + Nom
        logo_label = QLabel()
        logo_label.setPixmap(_make_logo_badge(34))
        logo_label.setFixedSize(34, 34)
        layout.addWidget(logo_label)

        layout.addSpacing(10)

        brand = QLabel("MobiDesk Pro")
        brand.setObjectName("NavBrand")
        layout.addWidget(brand)

        layout.addSpacing(20)

        # Séparateur vertical
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background-color: rgba(255,255,255,0.15); border: none;")
        sep.setFixedSize(1, 30)
        layout.addWidget(sep)

        layout.addSpacing(4)

        # Boutons de navigation
        self._add_nav(layout, "stock", "Stock")
        self._add_nav(layout, "ventes", "Ventes")
        self._add_nav(layout, "historique", "Historique")
        self._add_nav(layout, "profit", "Bénéfices")
        self._add_nav(layout, "resellers", "Revendeurs")
        self._add_nav(layout, "suppliers", "Fournisseurs")
        self._add_nav(layout, "categories", "Catégories")
        self._add_nav(layout, "settings", "Paramètres")

        # Bouton "Comptes" — visible seulement pour l'admin (mis à jour après login)
        self._accounts_btn = QPushButton("👥  Comptes")
        self._accounts_btn.setObjectName("NavButton")
        self._accounts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._accounts_btn.setCheckable(True)
        self._accounts_btn.clicked.connect(lambda _c: self.page_selected.emit("accounts"))
        self._accounts_btn.setVisible(False)
        self._button_group.addButton(self._accounts_btn)
        self._buttons["accounts"] = self._accounts_btn
        layout.addWidget(self._accounts_btn)

        layout.addSpacing(8)

        refresh_btn = QPushButton("🔄")
        refresh_btn.setToolTip("Actualiser")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setStyleSheet(
            "QPushButton { border: 1px solid rgba(255,255,255,0.15); border-radius: 6px;"
            " background: rgba(255,255,255,0.08); color: white; font-size: 15px; padding: 0; }"
            "QPushButton:hover { background: rgba(255,255,255,0.18); border-color: rgba(255,255,255,0.35); }"
            "QPushButton:pressed { background: rgba(255,255,255,0.28); }"
        )
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        layout.addWidget(refresh_btn)

        layout.addStretch()

        # Séparateur
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("background-color: rgba(255,255,255,0.15); border: none;")
        sep2.setFixedSize(1, 30)
        layout.addWidget(sep2)

        layout.addSpacing(8)

        # Boutons d'action rapide à droite
        sale_btn = QPushButton("🛒  Nouvelle vente")
        sale_btn.setObjectName("NavSaleButton")
        sale_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sale_btn.clicked.connect(self.new_sale_requested.emit)
        layout.addWidget(sale_btn)

        layout.addSpacing(6)

        add_btn = QPushButton("+  Produit")
        add_btn.setObjectName("NavAddButton")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self.add_product_requested.emit)
        layout.addWidget(add_btn)

        layout.addSpacing(28)

        # Séparateur
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("background-color: rgba(255,255,255,0.15); border: none;")
        sep3.setFixedSize(1, 30)
        layout.addWidget(sep3)

        layout.addSpacing(14)

        # Boutons fenêtre
        min_btn = QPushButton("—")
        min_btn.setObjectName("WinMinBtn")
        min_btn.setFixedSize(28, 26)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.setToolTip("Réduire")
        min_btn.clicked.connect(lambda: self.window().showMinimized())
        layout.addWidget(min_btn)

        layout.addSpacing(4)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("WinCloseBtn")
        close_btn.setFixedSize(28, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Fermer")
        close_btn.clicked.connect(lambda: self.window().close())
        layout.addWidget(close_btn)

    def _add_nav(self, layout: QHBoxLayout, page_key: str, label: str) -> None:
        btn = QPushButton(label)
        btn.setObjectName("NavButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _c, key=page_key: self.page_selected.emit(key))
        self._buttons[page_key] = btn
        self._button_group.addButton(btn)
        layout.addWidget(btn)

    def select_page(self, page_key: str) -> None:
        btn = self._buttons.get(page_key)
        if btn is not None and btn.isCheckable():
            btn.setChecked(True)

    def set_current_user(self, display_name: str, role: str) -> None:
        """Montre/cache le bouton Comptes selon le rôle."""
        self._accounts_btn.setVisible(role == "admin")

    def set_low_stock_badge(self, count: int) -> None:
        """Met à jour le badge de stock faible — visible dans la barre de statut."""
        pass

    # Backward compat
    def set_stock_value(self, _formatted_value: str) -> None:
        pass
