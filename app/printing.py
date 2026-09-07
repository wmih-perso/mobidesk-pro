"""Impression de tickets de caisse sur imprimante thermique 58mm."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QMarginsF, QRect, QSizeF, Qt
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPen
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QMessageBox

from app.database import session_scope
from app.models import Display, StockMovement

STORE_NAME = "MOBISHOP"
STORE_PHONE = "0558 57 19 32"
STORE_TAGLINE = "Pièces & Réparation Téléphones"


def _da(cents: int) -> str:
    """Centimes → chaîne DA sans suffixe (ex: 250000 → '2 500')."""
    return f"{round(cents / 100):,}".replace(",", " ")


def _collect_ticket_data(batch_id: int | None, movement_id: int | None) -> dict | None:
    with session_scope() as session:
        if batch_id is not None:
            movements = (
                session.query(StockMovement)
                .filter(StockMovement.movement_batch_id == batch_id)
                .order_by(StockMovement.id)
                .all()
            )
        elif movement_id is not None:
            m = session.get(StockMovement, movement_id)
            movements = [m] if m else []
        else:
            return None

        if not movements:
            return None

        lines = []
        ticket_date = movements[0].created_at
        ticket_num = movements[0].movement_batch_id or movements[0].id

        for m in movements:
            display = session.get(Display, m.display_id)
            label = (
                f"{display.brand} {display.phone_model}".strip() if display else "?"
            )
            qty = abs(m.change_quantity)
            unit_cents = m.unit_sale_price_cents or 0
            lines.append({
                "label": label,
                "qty": qty,
                "unit_cents": unit_cents,
                "total_cents": unit_cents * qty,
            })

        return {
            "ticket_num": ticket_num,
            "date_str": (
                ticket_date.strftime("%d/%m/%Y  %H:%M")
                if ticket_date
                else datetime.now().strftime("%d/%m/%Y  %H:%M")
            ),
            "lines": lines,
            "total_cents": sum(l["total_cents"] for l in lines),
            "nb_articles": len(lines),
            "nb_pieces": sum(l["qty"] for l in lines),
        }


def print_sale_ticket(
    batch_id: int | None = None,
    movement_id: int | None = None,
    parent=None,
) -> None:
    """Ouvre le dialogue d'imprimante et imprime le ticket."""
    data = _collect_ticket_data(batch_id, movement_id)
    if not data:
        QMessageBox.warning(parent, "Impression", "Aucun mouvement trouvé pour ce ticket.")
        return

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    page_size = QPageSize(QSizeF(58.0, 220.0), QPageSize.Unit.Millimeter)
    printer.setPageSize(page_size)
    printer.setPageMargins(QMarginsF(3, 4, 3, 4), QPageLayout.Unit.Millimeter)

    dlg = QPrintDialog(printer, parent)
    dlg.setWindowTitle("Imprimer le ticket")
    if dlg.exec() != QPrintDialog.DialogCode.Accepted:
        return

    _render_ticket(printer, data)


def _render_ticket(printer: QPrinter, data: dict) -> None:
    painter = QPainter()
    if not painter.begin(printer):
        return

    try:
        W = painter.viewport().width()
        y = 0

        def lh(pt: int) -> int:
            return int(pt * 1.6)

        def draw(
            text: str,
            pt: int,
            bold: bool = False,
            align=Qt.AlignmentFlag.AlignLeft,
            x0: int = 0,
            w: int | None = None,
        ) -> None:
            nonlocal y
            f = QFont("Courier New", pt)
            f.setBold(bold)
            painter.setFont(f)
            h = lh(pt)
            painter.drawText(
                QRect(x0, y, (w if w is not None else W - x0), h),
                int(align) | Qt.TextFlag.TextSingleLine,
                text,
            )
            y += h

        def rule(dashed: bool = True, thick: bool = False) -> None:
            nonlocal y
            painter.setPen(
                QPen(
                    Qt.GlobalColor.black,
                    2 if thick else 1,
                    Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine,
                )
            )
            painter.drawLine(0, y + 5, W, y + 5)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            y += 16

        # ── En-tête ──────────────────────────────────────────────────────
        draw(STORE_NAME, 30, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        draw(STORE_TAGLINE, 16, align=Qt.AlignmentFlag.AlignCenter)
        draw(STORE_PHONE, 20, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        y += 6
        rule()

        draw(f"Ticket : {data['ticket_num']:06d}", 16)
        draw(data["date_str"], 15)
        y += 4
        rule()

        # ── En-tête colonnes ──────────────────────────────────────────────
        cw = [int(W * 0.42), int(W * 0.11), int(W * 0.23), int(W * 0.24)]
        cx = [sum(cw[:i]) for i in range(4)]
        h_hdr = lh(16)
        f_hdr = QFont("Courier New", 16)
        f_hdr.setBold(True)
        painter.setFont(f_hdr)
        for col, (label, x, w_col, al) in enumerate(
            zip(
                ["ARTICLE", "QTE", "P.U", "TOTAL"],
                cx, cw,
                [Qt.AlignmentFlag.AlignLeft] + [Qt.AlignmentFlag.AlignRight] * 3,
            )
        ):
            painter.drawText(
                QRect(x, y, w_col, h_hdr), int(al) | Qt.TextFlag.TextSingleLine, label
            )
        y += h_hdr
        painter.setPen(QPen(Qt.GlobalColor.black, 2, Qt.PenStyle.SolidLine))
        painter.drawLine(0, y, W, y)
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        y += 8

        # ── Lignes produits ────────────────────────────────────────────────
        f_body = QFont("Courier New", 16)
        painter.setFont(f_body)
        h_body = lh(16)

        for line in data["lines"]:
            for x, w_col, text, al in [
                (cx[0], cw[0], line["label"], Qt.AlignmentFlag.AlignLeft),
                (cx[1], cw[1], str(line["qty"]), Qt.AlignmentFlag.AlignRight),
                (cx[2], cw[2], _da(line["unit_cents"]), Qt.AlignmentFlag.AlignRight),
                (cx[3], cw[3], _da(line["total_cents"]), Qt.AlignmentFlag.AlignRight),
            ]:
                painter.drawText(
                    QRect(x, y, w_col, h_body), int(al) | Qt.TextFlag.TextSingleLine, text
                )
            y += h_body
            painter.setPen(QPen(Qt.GlobalColor.lightGray, 1, Qt.PenStyle.DotLine))
            painter.drawLine(0, y, W, y)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            y += 6

        y += 4
        rule(dashed=False, thick=True)

        # Nb articles
        draw(f"NBR ART: {data['nb_articles']}   TQTE: {data['nb_pieces']}", 15)
        y += 4

        # Total
        h_tot = lh(22)
        f_tot = QFont("Courier New", 22)
        f_tot.setBold(True)
        painter.setFont(f_tot)
        painter.drawText(
            QRect(0, y, W // 2, h_tot),
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
            "TOTAL",
        )
        painter.drawText(
            QRect(W // 2, y, W // 2, h_tot),
            Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
            f"{_da(data['total_cents'])} DA",
        )
        y += h_tot + 4
        rule(dashed=False, thick=True)

        # ── Pied ──────────────────────────────────────────────────────────
        y += 8
        draw("Merci pour votre confiance !", 15, align=Qt.AlignmentFlag.AlignCenter)
        draw(f"{STORE_NAME}  —  {STORE_PHONE}", 14, align=Qt.AlignmentFlag.AlignCenter)

    finally:
        painter.end()
