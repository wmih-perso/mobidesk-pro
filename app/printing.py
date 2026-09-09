"""Impression de tickets de caisse sur imprimante thermique 58mm."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QMarginsF, QRect, QSizeF, Qt
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPen
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QMessageBox

from app.database import session_scope
from app.models import Display, StockMovement
from app.money import format_balance_ticket
from app.ticket_config import load_ticket_config


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

        reseller_name = ""
        reseller_phone = ""
        supplier_name = ""
        supplier_phone = ""
        cashier_name = ""
        for m in movements:
            if m.reseller_id and m.reseller:
                reseller_name = m.reseller.name or ""
                reseller_phone = m.reseller.phone or ""
            if m.supplier_id and m.supplier:
                supplier_name = m.supplier.name or ""
                supplier_phone = m.supplier.phone or ""
            if m.cashier_name and not cashier_name:
                cashier_name = m.cashier_name

        versement_cents = movements[0].versement_cents if movements else 0
        remise_cents = movements[0].remise_cents if movements else 0
        balance_before = movements[0].reseller_balance_before_cents if movements else 0
        has_reseller = bool(reseller_name)

        supplier_versement = movements[0].supplier_versement_cents if movements else 0
        supplier_balance_before = movements[0].supplier_balance_before_cents if movements else 0
        has_supplier = bool(supplier_name) and supplier_versement > 0

        for m in movements:
            display = session.get(Display, m.display_id)
            cat3 = (display.category or "")[:3].upper() if display else ""
            ref = (display.reference or "") if display else ""
            label = f"{cat3} | {ref}" if (cat3 or ref) else ((display.brand or "?") if display else "?")
            qty = abs(m.change_quantity)
            # Pour un achat fournisseur, le prix unitaire est le prix d'achat
            unit_cents = m.unit_sale_price_cents or m.unit_purchase_price_cents or 0
            lines.append({
                "label": label,
                "category": display.category if display else "",
                "reference": display.reference if display else "",
                "qty": qty,
                "unit_cents": unit_cents,
                "total_cents": unit_cents * qty,
            })

        total_after_remise = sum(l["total_cents"] for l in lines)
        total_brut = total_after_remise + remise_cents
        new_balance = balance_before + total_after_remise - versement_cents if has_reseller else 0
        supplier_new_balance = (
            supplier_balance_before + total_after_remise - supplier_versement
            if has_supplier else 0
        )

        return {
            "ticket_num": ticket_num,
            "date_str": (
                ticket_date.strftime("%d/%m/%Y  %H:%M")
                if ticket_date
                else datetime.now().strftime("%d/%m/%Y  %H:%M")
            ),
            "lines": lines,
            "total_brut_cents": total_brut,
            "remise_cents": remise_cents,
            "total_cents": total_after_remise,
            "nb_articles": len(lines),
            "nb_pieces": sum(l["qty"] for l in lines),
            "reseller_name": reseller_name,
            "reseller_phone": reseller_phone,
            "supplier_name": supplier_name,
            "supplier_phone": supplier_phone,
            "cashier_name": cashier_name,
            "has_reseller": has_reseller,
            "has_supplier": has_supplier,
            "versement_cents": versement_cents,
            "balance_before_cents": balance_before,
            "balance_after_cents": new_balance,
            "supplier_versement_cents": supplier_versement,
            "supplier_balance_before_cents": supplier_balance_before,
            "supplier_balance_after_cents": supplier_new_balance,
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
    page_size = QPageSize(QSizeF(40.0, 220.0), QPageSize.Unit.Millimeter)
    printer.setPageSize(page_size)
    printer.setPageMargins(QMarginsF(2, 3, 2, 3), QPageLayout.Unit.Millimeter)

    dlg = QPrintDialog(printer, parent)
    dlg.setWindowTitle("Imprimer le ticket")
    if dlg.exec() != QPrintDialog.DialogCode.Accepted:
        return

    _render_ticket(printer, data)


def _render_ticket(printer: QPrinter, data: dict) -> None:
    cfg = load_ticket_config()
    STORE_NAME = cfg["store_name"]
    STORE_PHONE = cfg["store_phone"]
    STORE_TAGLINE = cfg["store_tagline"]

    painter = QPainter()
    if not painter.begin(printer):
        return

    try:
        W = painter.viewport().width()
        y = 0

        def lh(pt: int) -> int:
            return int(pt * 1.55)

        def draw(text: str, pt: int, bold: bool = False,
                 align=Qt.AlignmentFlag.AlignLeft,
                 x0: int = 0, w: int | None = None) -> None:
            nonlocal y
            f = QFont("Courier New", pt)
            f.setBold(bold)
            painter.setFont(f)
            h = lh(pt)
            painter.drawText(
                QRect(x0, y, w if w is not None else W - x0, h),
                int(align) | Qt.TextFlag.TextSingleLine, text,
            )
            y += h

        def hline(thick: bool = False, dashed: bool = False, gap: int = 6) -> None:
            nonlocal y
            y += gap
            pen = QPen(Qt.GlobalColor.black, 3 if thick else 1,
                       Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawLine(0, y, W, y)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            y += gap

        def double_rule() -> None:
            nonlocal y
            y += 4
            painter.setPen(QPen(Qt.GlobalColor.black, 3, Qt.PenStyle.SolidLine))
            painter.drawLine(0, y, W, y)
            y += 5
            painter.setPen(QPen(Qt.GlobalColor.black, 1, Qt.PenStyle.SolidLine))
            painter.drawLine(0, y, W, y)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            y += 6

        # ── En-tête ──────────────────────────────────────────────────────
        draw(STORE_NAME, 20, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        draw(STORE_TAGLINE, 11, align=Qt.AlignmentFlag.AlignCenter)
        draw(f"☎ {STORE_PHONE}", 13, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        double_rule()

        # ── Infos ticket ─────────────────────────────────────────────────
        h_row = lh(13)
        f13 = QFont("Courier New", 13)
        f13b = QFont("Courier New", 13)
        f13b.setBold(True)
        painter.setFont(f13b)
        painter.drawText(QRect(0, y, W // 2, h_row),
                         Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                         f"N° #{data['ticket_num']:06d}")
        painter.setFont(f13)
        painter.drawText(QRect(W // 2, y, W // 2, h_row),
                         Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                         data["date_str"])
        y += h_row

        if data.get("cashier_name"):
            draw(f"Caissier : {data['cashier_name']}", 11)

        if data.get("reseller_name"):
            y += 4
            from PySide6.QtGui import QColor
            painter.setPen(QPen(Qt.GlobalColor.black, 1, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(QRect(0, y, W, lh(13) + (lh(11) if data.get("reseller_phone") else 0) + 8), 4, 4)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            y += 4
            draw(f"▸ Client : {data['reseller_name']}", 13, bold=True, x0=4, w=W - 8)
            if data.get("reseller_phone"):
                draw(f"☎ {data['reseller_phone']}", 11, x0=4, w=W - 8)
            y += 4

        double_rule()

        # ── En-tête colonnes ─────────────────────────────────────────────
        cw = [int(W * 0.48), int(W * 0.10), int(W * 0.20), int(W * 0.22)]
        cx = [sum(cw[:i]) for i in range(4)]
        h_hdr = lh(12)
        f_hdr = QFont("Courier New", 12)
        f_hdr.setBold(True)
        painter.setFont(f_hdr)
        for label, x, w_col, al in zip(
            ["ARTICLE", "Q", "P.U", "TOT"],
            cx, cw,
            [Qt.AlignmentFlag.AlignLeft] + [Qt.AlignmentFlag.AlignRight] * 3,
        ):
            painter.drawText(QRect(x, y, w_col, h_hdr), int(al) | Qt.TextFlag.TextSingleLine, label)
        y += h_hdr
        painter.setPen(QPen(Qt.GlobalColor.black, 2, Qt.PenStyle.SolidLine))
        painter.drawLine(0, y, W, y)
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        y += 6

        # ── Lignes produits ───────────────────────────────────────────────
        f_body = QFont("Courier New", 13)
        f_sub = QFont("Courier New", 10)
        h_body = lh(13)
        h_sub = lh(10)

        for line in data["lines"]:
            cat3 = line.get("category", "")[:3]
            ref = line.get("reference", "")
            article_label = f"{cat3} | {ref}" if (cat3 or ref) else line["label"]

            painter.setFont(f_body)
            painter.drawText(QRect(cx[0], y, cw[0], h_body),
                             Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                             article_label)
            painter.drawText(QRect(cx[1], y, cw[1], h_body),
                             Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                             str(line["qty"]))
            painter.drawText(QRect(cx[2], y, cw[2], h_body),
                             Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                             _da(line["unit_cents"]))
            painter.drawText(QRect(cx[3], y, cw[3], h_body),
                             Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                             _da(line["total_cents"]))
            y += h_body

            painter.setPen(QPen(Qt.GlobalColor.lightGray, 1, Qt.PenStyle.DotLine))
            painter.drawLine(0, y + 2, W, y + 2)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            y += 8

        hline(thick=True, gap=4)

        # Nb articles
        draw(f"NBR ART : {data['nb_articles']}   TQTE : {data['nb_pieces']}", 11)
        y += 4

        # ── Section totaux ───────────────────────────────────────────────
        def right_row(label: str, value: str, pt: int = 12, bold_val: bool = False,
                      label_frac: float = 0.5) -> None:
            nonlocal y
            h = lh(pt)
            lbl_w = int(W * label_frac)
            val_w = W - lbl_w
            f_lbl = QFont("Courier New", pt)
            painter.setFont(f_lbl)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            painter.drawText(QRect(0, y, lbl_w, h),
                             Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine, label)
            f_val = QFont("Courier New", pt)
            f_val.setBold(bold_val)
            painter.setFont(f_val)
            painter.drawText(QRect(lbl_w, y, val_w, h),
                             Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine, value)
            y += h

        if data.get("remise_cents", 0) > 0:
            right_row("TOTAL :", f"{_da(data['total_brut_cents'])} DA")
            right_row("REMISE :", f"- {_da(data['remise_cents'])} DA")
            hline(gap=3)

        # Total encadré
        h_tot = lh(18)
        margin = 4
        painter.setPen(QPen(Qt.GlobalColor.black, 2, Qt.PenStyle.SolidLine))
        painter.drawRect(QRect(0, y, W, h_tot + margin * 2))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        y += margin
        f_tot = QFont("Courier New", 18)
        f_tot.setBold(True)
        painter.setFont(f_tot)
        painter.drawText(QRect(6, y, W // 2, h_tot),
                         Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                         "TTC À PAYER" if data.get("remise_cents", 0) > 0 else "TOTAL")
        painter.drawText(QRect(W // 2, y, W // 2 - 6, h_tot),
                         Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                         f"{_da(data['total_cents'])} DA")
        y += h_tot + margin + 6

        # ── Section solde revendeur ───────────────────────────────────────
        if data.get("has_reseller") and (data.get("versement_cents") or 0) >= 0:
            hline(dashed=True, gap=4)
            draw("Anc. solde :", 10)
            draw(format_balance_ticket(data['balance_before_cents']), 11,
                 align=Qt.AlignmentFlag.AlignRight)
            right_row("Total achat :", f"{_da(data['total_cents'])} DA", pt=10)
            right_row("Versement :", f"{_da(data['versement_cents'])} DA", pt=10)
            hline(gap=3)
            draw("Nouv. solde :", 10)
            draw(format_balance_ticket(data['balance_after_cents']), 11,
                 bold=True, align=Qt.AlignmentFlag.AlignRight)
            y += 4

        # ── Section solde fournisseur ─────────────────────────────────────
        if data.get("has_supplier"):
            hline(dashed=True, gap=4)
            right_row("Anc. solde four. :", format_balance_ticket(data['supplier_balance_before_cents']))
            right_row("Total achat :", f"{_da(data['total_cents'])} DA")
            right_row("Versement :", f"{_da(data['supplier_versement_cents'])} DA")
            hline(gap=3)
            right_row("Nouv. solde four. :", format_balance_ticket(data['supplier_balance_after_cents']), bold_val=True)
            y += 4

        # ── Pied ──────────────────────────────────────────────────────────
        hline(dashed=True, gap=4)
        y += 4
        draw("Merci pour votre confiance !", 12, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        draw("━" * 20, 10, align=Qt.AlignmentFlag.AlignCenter)

    finally:
        painter.end()
