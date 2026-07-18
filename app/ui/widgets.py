"""Petits composants réutilisables pour une présentation professionnelle."""

from __future__ import annotations

import math

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import nav_icon

_STEPPER_BUTTON_STYLE = """
    QPushButton {
        background-color: #f4f7f5;
        border: none;
        color: #3d4a43;
        font-size: 15px;
        font-weight: 700;
        padding: 0;
    }
    QPushButton:hover {
        background-color: #e0efe8;
        color: #0f5c46;
    }
    QPushButton:pressed {
        background-color: #c5e3d3;
    }
    QPushButton:disabled {
        color: #a9ddc9;
        background-color: #f4f7f5;
    }
"""


class _ModernStepperMixin:
    """Ajoute deux boutons − / + côte à côte, en overlay du champ natif.

    Qt ne permet pas de repositionner les sous-contrôles up/down d'un
    QSpinBox côte à côte via QSS (ce sont des rectangles fixes en haut/bas
    à droite) — on masque donc les boutons natifs et on superpose deux
    QPushButton réels, ce qui reste un vrai QSpinBox/QDoubleSpinBox pour le
    reste du code (value(), setRange(), valueChanged, ...).
    """

    _STEPPER_WIDTH = 26

    def _init_stepper(self) -> None:
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setStyleSheet(
            f"padding-right: {2 * self._STEPPER_WIDTH + 6}px;"
        )

        self._decrement_button = QPushButton("−", self)
        self._increment_button = QPushButton("+", self)
        for button in (self._decrement_button, self._increment_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_STEPPER_BUTTON_STYLE)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._decrement_button.clicked.connect(self.stepDown)
        self._increment_button.clicked.connect(self.stepUp)

    def focusInEvent(self, event) -> None:  # noqa: N802 — signature imposée par Qt
        super().focusInEvent(event)
        # Sélectionne la valeur existante (ex. « 0 ») pour qu'un premier
        # caractère tapé la remplace directement, sans effacer à la main —
        # le suffixe (" DA") n'est jamais inclus dans la sélection du champ.
        QTimer.singleShot(0, self.selectAll)

    def resizeEvent(self, event) -> None:  # noqa: N802 — signature imposée par Qt
        super().resizeEvent(event)
        height = self.height() - 4
        self._decrement_button.setGeometry(
            self.width() - 2 * self._STEPPER_WIDTH - 4, 2, self._STEPPER_WIDTH, height
        )
        self._increment_button.setGeometry(
            self.width() - self._STEPPER_WIDTH - 2, 2, self._STEPPER_WIDTH, height
        )
        self._decrement_button.setStyleSheet(
            _STEPPER_BUTTON_STYLE + "QPushButton { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }"
        )
        self._increment_button.setStyleSheet(
            _STEPPER_BUTTON_STYLE + "QPushButton { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }"
        )


class ModernSpinBox(_ModernStepperMixin, QSpinBox):
    """QSpinBox avec des boutons −/+ modernes côte à côte."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._init_stepper()


class ModernDoubleSpinBox(_ModernStepperMixin, QDoubleSpinBox):
    """QDoubleSpinBox avec des boutons −/+ modernes côte à côte."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._init_stepper()


def field_label(text: str, *, icon: str = "tag", color: str = "#4b5170") -> QWidget:
    """Construit un libellé de formulaire avec une petite icône devant le
    texte — utilisé pour les champs de sélection (listes déroulantes) afin
    de les distinguer visuellement des champs de saisie libre."""
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    icon_label = QLabel()
    icon_label.setPixmap(nav_icon(icon, size=16, color=color).pixmap(QSize(16, 16)))
    layout.addWidget(icon_label)

    text_label = QLabel(text)
    layout.addWidget(text_label)
    layout.addStretch()

    return wrapper


def apply_card_shadow(widget: QWidget, *, blur: int = 24, y_offset: int = 4) -> None:
    """Ajoute une ombre portée légère pour donner de la profondeur aux cartes."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(y_offset)
    effect.setColor(QColor(30, 36, 51, 35))
    widget.setGraphicsEffect(effect)


class Sparkline(QWidget):
    """Mini-graphique décoratif accompagnant une carte statistique.

    Purement illustratif (aucune série temporelle n'est suivie par
    l'application) — la courbe est générée une fois à partir d'une graine
    fixe pour donner un aspect vivant au tableau de bord.
    """

    def __init__(self, color: str, *, seed: int = 0) -> None:
        super().__init__()
        self._color = QColor(color)
        self.setFixedHeight(36)
        self.setMinimumWidth(90)
        points = 12
        self._values = [
            0.5 + 0.4 * math.sin(seed + i * 0.9) + 0.15 * math.sin(seed * 2 + i * 2.3)
            for i in range(points)
        ]

    def paintEvent(self, event) -> None:  # noqa: N802 — signature imposée par Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        margin = 3
        usable_height = height - 2 * margin
        step = width / (len(self._values) - 1)

        path = QPainterPath()
        for index, value in enumerate(self._values):
            x = index * step
            y = margin + (1 - value) * usable_height
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        fill_path = QPainterPath(path)
        fill_path.lineTo(width, height)
        fill_path.lineTo(0, height)
        fill_path.closeSubpath()

        fill_color = QColor(self._color)
        fill_color.setAlpha(35)
        painter.fillPath(fill_path, fill_color)

        pen = QPen(self._color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)


class IconStatCard(QFrame):
    """Carte statistique avec icône, valeur, libellé et mini-graphique."""

    def __init__(
        self,
        icon: str,
        title: str,
        value: str,
        subtitle: str,
        *,
        accent: str,
        accent_bg: str,
        sparkline_seed: int = 0,
    ) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._accent = accent

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)

        icon_badge = QLabel(icon)
        icon_badge.setFixedSize(44, 44)
        icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_badge.setStyleSheet(
            f"background-color: {accent_bg}; border-radius: 12px; font-size: 19px; border: none;"
        )
        header.addWidget(icon_badge)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("font-size: 22px; font-weight: 700; border: none;")
        texts.addWidget(self.value_label)

        title_label = QLabel(title)
        title_label.setStyleSheet("color: #6b7290; font-weight: 600; border: none;")
        texts.addWidget(title_label)

        header.addLayout(texts, stretch=1)
        outer.addLayout(header)

        footer = QHBoxLayout()
        subtitle_label = QLabel(subtitle)
        subtitle_label.setStyleSheet("color: #a0a5bd; font-size: 11px; border: none;")
        footer.addWidget(subtitle_label)
        footer.addStretch()
        footer.addWidget(Sparkline(accent, seed=sparkline_seed))
        outer.addLayout(footer)

        apply_card_shadow(self)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


# Kept as a thin alias for backward compatibility with earlier simpler cards.
StatCard = IconStatCard


class EmptyState(QWidget):
    """État vide affiché quand un tableau ne contient aucune ligne."""

    def __init__(self, icon: str, title: str, subtitle: str, action_text: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-size: 46px; border: none;")
        layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: 700; border: none;")
        layout.addWidget(title_label)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label.setStyleSheet("color: #8991ac; border: none;")
        layout.addWidget(subtitle_label)

        self.action_button: QPushButton | None = None
        if action_text:
            layout.addSpacing(6)
            self.action_button = QPushButton(action_text)
            self.action_button.setFixedWidth(220)
            layout.addWidget(self.action_button, alignment=Qt.AlignmentFlag.AlignCenter)
