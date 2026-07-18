"""Icônes vectorielles dessinées à la main pour le menu latéral.

Les émojis dépendent des polices couleur installées sur le système et de la
façon dont le moteur de rendu les compose avec le reste du texte : le
résultat est imprévisible (icône manquante, en noir et blanc, mal alignée).
Ces icônes sont tracées directement avec QPainter — elles rendent donc à
l'identique sur n'importe quelle machine Windows, sans dépendance externe.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap

_ICON_CACHE: dict[tuple[str, int, str], QIcon] = {}


def nav_icon(kind: str, *, size: int = 20, color: str = "#eaecf9") -> QIcon:
    cache_key = (kind, size, color)
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(size * 0.09)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    draw = _DRAWERS.get(kind)
    if draw is not None:
        draw(painter, size, color)

    painter.end()

    icon = QIcon(pixmap)
    _ICON_CACHE[cache_key] = icon
    return icon


def _p(size: int, x: float, y: float) -> QPointF:
    return QPointF(size * x, size * y)


def _draw_home(painter: QPainter, size: int, color: str) -> None:
    path = QPainterPath()
    path.moveTo(_p(size, 0.14, 0.52))
    path.lineTo(_p(size, 0.5, 0.18))
    path.lineTo(_p(size, 0.86, 0.52))
    painter.drawPath(path)
    painter.drawLine(_p(size, 0.26, 0.46), _p(size, 0.26, 0.84))
    painter.drawLine(_p(size, 0.26, 0.84), _p(size, 0.74, 0.84))
    painter.drawLine(_p(size, 0.74, 0.84), _p(size, 0.74, 0.46))


def _draw_monitor(painter: QPainter, size: int, color: str) -> None:
    rect = QRectF(_p(size, 0.14, 0.18), _p(size, 0.86, 0.68))
    painter.drawRoundedRect(rect, size * 0.06, size * 0.06)
    painter.drawLine(_p(size, 0.5, 0.68), _p(size, 0.5, 0.82))
    painter.drawLine(_p(size, 0.32, 0.82), _p(size, 0.68, 0.82))


def _draw_list(painter: QPainter, size: int, color: str) -> None:
    for y in (0.28, 0.5, 0.72):
        painter.drawLine(_p(size, 0.16, y), _p(size, 0.84, y))


def _draw_tray_arrow(painter: QPainter, size: int, color: str, *, down: bool) -> None:
    top, bottom = (0.16, 0.58) if down else (0.58, 0.16)
    painter.drawLine(_p(size, 0.5, top), _p(size, 0.5, bottom))
    dy = 0.12 if down else -0.12
    painter.drawLine(_p(size, 0.5, bottom), _p(size, 0.5 - 0.16, bottom - dy))
    painter.drawLine(_p(size, 0.5, bottom), _p(size, 0.5 + 0.16, bottom - dy))
    painter.drawLine(_p(size, 0.16, 0.82), _p(size, 0.84, 0.82))


def _draw_download(painter: QPainter, size: int, color: str) -> None:
    _draw_tray_arrow(painter, size, color, down=True)


def _draw_upload(painter: QPainter, size: int, color: str) -> None:
    _draw_tray_arrow(painter, size, color, down=False)


def _draw_bell(painter: QPainter, size: int, color: str) -> None:
    path = QPainterPath()
    path.moveTo(_p(size, 0.26, 0.62))
    path.lineTo(_p(size, 0.26, 0.42))
    path.arcTo(QRectF(_p(size, 0.26, 0.16), _p(size, 0.74, 0.42)), 180, -180)
    path.lineTo(_p(size, 0.74, 0.62))
    path.lineTo(_p(size, 0.82, 0.7))
    path.lineTo(_p(size, 0.18, 0.7))
    path.closeSubpath()
    painter.drawPath(path)

    dot_center = _p(size, 0.5, 0.82)
    dot_radius = size * 0.07
    painter.setBrush(color)
    painter.drawEllipse(dot_center, dot_radius, dot_radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw_gear(painter: QPainter, size: int, color: str) -> None:
    center = _p(size, 0.5, 0.5)
    outer_radius = size * 0.3
    inner_radius = size * 0.13
    painter.drawEllipse(center, outer_radius, outer_radius)
    painter.drawEllipse(center, inner_radius, inner_radius)

    import math

    for i in range(6):
        angle = math.radians(i * 60)
        x1 = center.x() + math.cos(angle) * outer_radius
        y1 = center.y() + math.sin(angle) * outer_radius
        x2 = center.x() + math.cos(angle) * (outer_radius + size * 0.12)
        y2 = center.y() + math.sin(angle) * (outer_radius + size * 0.12)
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _draw_coin(painter: QPainter, size: int, color: str) -> None:
    center = _p(size, 0.5, 0.5)
    radius = size * 0.32
    painter.drawEllipse(center, radius, radius)

    painter.drawLine(_p(size, 0.5, 0.28), _p(size, 0.5, 0.72))

    curve = QPainterPath()
    curve.moveTo(_p(size, 0.62, 0.38))
    curve.lineTo(_p(size, 0.42, 0.38))
    curve.arcTo(QRectF(_p(size, 0.36, 0.36), _p(size, 0.56, 0.5)), 90, 180)
    curve.lineTo(_p(size, 0.58, 0.5))
    curve.arcTo(QRectF(_p(size, 0.36, 0.5), _p(size, 0.56, 0.64)), 90, -180)
    curve.lineTo(_p(size, 0.38, 0.62))
    painter.drawPath(curve)


def _draw_tag(painter: QPainter, size: int, color: str) -> None:
    path = QPainterPath()
    path.moveTo(_p(size, 0.18, 0.22))
    path.lineTo(_p(size, 0.52, 0.22))
    path.lineTo(_p(size, 0.84, 0.54))
    path.lineTo(_p(size, 0.5, 0.88))
    path.lineTo(_p(size, 0.18, 0.56))
    path.closeSubpath()
    painter.drawPath(path)

    dot_center = _p(size, 0.36, 0.36)
    dot_radius = size * 0.055
    painter.setBrush(color)
    painter.drawEllipse(dot_center, dot_radius, dot_radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw_wrench(painter: QPainter, size: int, color: str) -> None:
    path = QPainterPath()
    path.moveTo(_p(size, 0.30, 0.70))
    path.lineTo(_p(size, 0.62, 0.38))
    painter.drawPath(path)
    painter.drawLine(_p(size, 0.18, 0.82), _p(size, 0.30, 0.70))

    head_center = _p(size, 0.72, 0.28)
    head_radius = size * 0.16
    painter.drawArc(
        QRectF(
            head_center.x() - head_radius,
            head_center.y() - head_radius,
            head_radius * 2,
            head_radius * 2,
        ),
        30 * 16,
        300 * 16,
    )


def _draw_users(painter: QPainter, size: int, color: str) -> None:
    left_center = _p(size, 0.36, 0.38)
    left_radius = size * 0.14
    painter.drawEllipse(left_center, left_radius, left_radius)
    left_body = QPainterPath()
    left_body.moveTo(_p(size, 0.14, 0.82))
    left_body.arcTo(QRectF(_p(size, 0.14, 0.52), _p(size, 0.58, 0.82)), 180, 180)
    painter.drawPath(left_body)

    right_center = _p(size, 0.66, 0.32)
    right_radius = size * 0.11
    painter.drawEllipse(right_center, right_radius, right_radius)
    right_body = QPainterPath()
    right_body.moveTo(_p(size, 0.58, 0.66))
    right_body.arcTo(QRectF(_p(size, 0.55, 0.44), _p(size, 0.87, 0.66)), 180, 145)
    painter.drawPath(right_body)


_DRAWERS = {
    "home": _draw_home,
    "monitor": _draw_monitor,
    "list": _draw_list,
    "download": _draw_download,
    "upload": _draw_upload,
    "bell": _draw_bell,
    "gear": _draw_gear,
    "tag": _draw_tag,
    "coin": _draw_coin,
    "wrench": _draw_wrench,
    "users": _draw_users,
}
