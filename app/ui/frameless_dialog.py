"""Mixin pour dialogues sans barre de titre Windows — header personnalisé draggable."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
)


class FramelessDialog(QDialog):
    """QDialog sans barre de titre Windows.

    Sous-classer et appeler `_make_header(title, gradient_css)` pour obtenir
    un header draggable avec bouton ✕ intégré.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # Ombre portée légère sur la fenêtre
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.setGraphicsEffect(shadow)

        self._drag_pos: QPoint | None = None
        self._drag_header: QFrame | None = None

    # ------------------------------------------------------------------
    # Header factory
    # ------------------------------------------------------------------

    def _make_header(
        self,
        title: str,
        gradient: str = "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1565c0,stop:1 #0097a7)",
        height: int = 60,
        extra_widget=None,
    ) -> QFrame:
        """Retourne un QFrame header coloré, draggable, avec bouton ✕."""
        header = QFrame()
        header.setStyleSheet(f"background: {gradient};")
        header.setFixedHeight(height)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 0, 12, 0)
        layout.setSpacing(8)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color: white; font-size: 18px; font-weight: 700;"
            " letter-spacing: 0.5px; background: transparent;"
        )
        layout.addWidget(title_lbl)
        layout.addStretch()

        if extra_widget is not None:
            layout.addWidget(extra_widget)
            layout.addSpacing(8)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("FramelessCloseBtn")
        close_btn.setFixedSize(38, 38)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        # Rend le header draggable
        header.mousePressEvent = self._header_mouse_press
        header.mouseMoveEvent = self._header_mouse_move
        header.mouseReleaseEvent = self._header_mouse_release
        self._drag_header = header

        return header

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def _header_mouse_press(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _header_mouse_move(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _header_mouse_release(self, _event) -> None:
        self._drag_pos = None
