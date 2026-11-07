"""Impression des tickets (ventes, achats, bons de retour) sur l'imprimante
choisie dans Paramètres > Imprimante (étiquettes Niimbot B1 ou autre)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QMarginsF, QRect, QSizeF, Qt
from PySide6.QtGui import QFont, QImage, QPageLayout, QPageSize, QPainter, QPen
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import QMessageBox

from app.database import session_scope
from app.labels import article_label
from app.models import Display, StockMovement
from app.money import format_balance_ticket, group_thousands
from app.printer_config import PAPER_LABEL, PAPER_ROLL, PAPER_SHEET, load_printer_config, paper_description
from app.ticket_config import load_ticket_config


def _da(cents: int) -> str:
    """Centimes → chaîne DA sans suffixe (ex: 250000 → '2 500')."""
    return group_thousands(round(cents / 100))


def _da_line(cents: int) -> str:
    """Prix d'une ligne de ticket, comme sur les tickets fournisseurs :
    168000 → '1 680.00'."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{group_thousands(cents // 100)}.{cents % 100:02d}"


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
            label = (
                article_label(display.category, display.phone_model, display.reference, m.model_label)
                if display else "?"
            )
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


def collect_return_data(return_id: int) -> dict | None:
    """Données d'un bon de retour, au format du ticket de caisse (voir
    _render_ticket) avec en plus : titre, libellé du contact, libellé du
    total, règlement et lignes de solde."""
    from app.returns import SETTLEMENT_LABELS, contact_name, get_return
    from app.services import StockError

    with session_scope() as session:
        try:
            doc = get_return(session, return_id)
        except StockError:
            return None
        is_customer = doc.kind == "customer"
        contact = doc.reseller if is_customer else doc.supplier
        lines = []
        for line in doc.lines:
            label = article_label(line.display.category, line.display.phone_model, line.display.reference)
            if line.condition == "defective":
                label += " (déf.)"
            lines.append({
                "label": label,
                "category": line.display.category or "",
                "reference": label,
                "qty": line.quantity,
                "unit_cents": line.unit_price_cents,
                "total_cents": line.total_cents,
            })

        balance_rows = []
        if contact is not None and doc.settlement != "legacy":
            balance_rows = [
                ("Anc. solde :", format_balance_ticket(doc.balance_before_cents)),
                ("Nouv. solde :", format_balance_ticket(doc.balance_after_cents)),
            ]
        if doc.settlement == "exchange":
            total_label = "ÉCHANGE"
        elif is_customer:
            total_label = "REMBOURSÉ" if doc.settlement == "cash" else "AVOIR"
        else:
            total_label = "TOTAL"

        return {
            "doc_title": "BON DE RETOUR" + ("" if is_customer else " FOURNISSEUR"),
            "ticket_num": doc.id,
            "date_str": doc.created_at.strftime("%d/%m/%Y  %H:%M"),
            "lines": lines,
            "total_brut_cents": doc.amount_cents,
            "remise_cents": 0,
            "total_cents": doc.amount_cents,
            "total_label": total_label,
            "nb_articles": len(lines),
            "nb_pieces": sum(l["qty"] for l in lines),
            "reseller_name": contact_name(doc),
            "reseller_phone": contact.phone if contact else "",
            "contact_label": "Client" if is_customer else "Fournisseur",
            "cashier_name": doc.cashier_name,
            "settlement_label": SETTLEMENT_LABELS.get(doc.settlement, ""),
            "source_ticket": doc.source_batch_id,
            "balance_rows": balance_rows,
            "has_reseller": False,
            "has_supplier": False,
        }


def print_sale_ticket(
    batch_id: int | None = None,
    movement_id: int | None = None,
    parent=None,
) -> None:
    """Imprime le ticket d'une vente / d'un mouvement."""
    data = _collect_ticket_data(batch_id, movement_id)
    if not data:
        QMessageBox.warning(parent, "Impression", "Aucun mouvement trouvé pour ce ticket.")
        return
    print_ticket_data(data, parent)


# ---------------------------------------------------------------------------
# Imprimante
# ---------------------------------------------------------------------------


SHEET_MARGIN_MM = 10  # une imprimante de bureau n'imprime pas jusqu'au bord
THERMAL_DPI = 203.2  # 8 points par mm : imprimantes à tickets (XP-80C…) et Niimbot


def roll_printable_mm(paper_width_mm: float) -> float:
    """Largeur réellement imprimable d'un rouleau : 72 mm sur 80 mm,
    48 mm sur 58 mm (tête d'impression des imprimantes thermiques)."""
    return 72.0 if paper_width_mm >= 80 else 48.0 if paper_width_mm >= 58 else paper_width_mm - 4


def roll_print_width_mm(cfg: dict) -> float:
    """Largeur imprimée du ticket sur rouleau : réglage manuel, sinon la
    largeur imprimable standard (72 mm sur 80 mm, 48 mm sur 58 mm)."""
    manual = float(cfg.get("roll_print_width_mm") or 0)
    return manual if manual > 0 else roll_printable_mm(float(cfg["ticket_width_mm"]))


def _roll_dots(cfg: dict) -> int:
    """Largeur du ticket en points de l'imprimante thermique (576 pour 72 mm)."""
    return round(roll_print_width_mm(cfg) / 25.4 * THERMAL_DPI)
CUT_PADDING = 10  # espace (unités du ticket) entre le ticket et le pointillé


def _page_spec(cfg: dict, ticket_height: int) -> tuple[float, float, float]:
    """(largeur, hauteur, marge) de la page en mm selon le type de papier.
    Pour un rouleau, la page fait exactement la longueur du ticket."""
    paper = cfg.get("paper", PAPER_LABEL)
    if paper == PAPER_SHEET:
        return 210.0, 297.0, SHEET_MARGIN_MM
    if paper == PAPER_ROLL:
        # Pleine page sans marge : c'est le ticket qui est centré dans la
        # zone que le pilote déclare imprimable (voir _render_ticket). Une
        # marge ici s'ajoutait à celle du pilote et décalait le ticket.
        width = float(cfg["ticket_width_mm"])
        printable = roll_print_width_mm(cfg)
        height = ticket_height * printable / TICKET_WIDTH + 8
        return width, height, 0.0
    return float(cfg["label_width_mm"]), float(cfg["label_height_mm"]), 1.0


def apply_page_layout(device, cfg: dict, ticket_height: int) -> None:
    """Taille de page, portrait et marges sur un QPrinter (ou un QPdfWriter
    pour les tests)."""
    width, height, margin = _page_spec(cfg, ticket_height)
    device.setPageSize(QPageSize(
        QSizeF(width, height), QPageSize.Unit.Millimeter, "MobiDesk",
        QPageSize.SizeMatchPolicy.ExactMatch,
    ))
    device.setPageOrientation(QPageLayout.Orientation.Portrait)
    if cfg.get("paper") == PAPER_ROLL and hasattr(device, "setFullPage"):
        device.setFullPage(True)  # repère = bord du papier (QPrinter seulement)
    device.setPageMargins(QMarginsF(margin, margin, margin, margin), QPageLayout.Unit.Millimeter)


def make_printer(cfg: dict, ticket_height: int) -> QPrinter:
    """QPrinter réglé selon Paramètres > Imprimante : imprimante choisie
    (ou celle par défaut de Windows) et type de papier."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if cfg.get("printer_name"):
        printer.setPrinterName(cfg["printer_name"])
    apply_page_layout(printer, cfg, ticket_height)
    return printer


def print_ticket_data(data: dict, parent=None, cfg: dict | None = None) -> bool:
    """Imprime un ticket (données de _collect_ticket_data ou
    collect_return_data) avec les réglages de l'imprimante. Renvoie True
    si l'impression a été envoyée."""
    cfg = cfg or load_printer_config()
    name = cfg.get("printer_name")
    if name and name not in QPrinterInfo.availablePrinterNames():
        # Ne jamais imprimer en silence sur une autre imprimante.
        QMessageBox.warning(
            parent, "Impression",
            f"L'imprimante « {name} » est introuvable. Vérifiez qu'elle est branchée et "
            "allumée, ou choisissez-en une autre dans Paramètres > Imprimante des tickets.",
        )
        return False
    printer = make_printer(cfg, _ticket_height(data))
    if not cfg.get("direct"):
        dialog = QPrintDialog(printer, parent)
        dialog.setWindowTitle("Imprimer le ticket")
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return False
    elif not printer.isValid():
        QMessageBox.warning(
            parent, "Impression",
            "Aucune imprimante disponible. Vérifiez qu'elle est branchée et installée dans "
            "Windows, puis choisissez-la dans Paramètres > Imprimante des tickets.",
        )
        return False
    if not _render_ticket(printer, data, cfg, copies=max(1, int(cfg.get("copies", 1)))):
        QMessageBox.warning(
            parent, "Impression",
            f"Impossible d'imprimer sur « {printer.printerName()} ». Vérifiez que "
            "l'imprimante est allumée et branchée.",
        )
        return False
    return True


def test_ticket_data() -> dict:
    """Ticket d'exemple pour « Imprimer un test » (rien n'est enregistré)."""
    cfg = load_printer_config()
    lines = [
        {"label": "AFF Galaxy A12", "category": "Afficheur", "reference": "A12",
         "qty": 1, "unit_cents": 250000, "total_cents": 250000},
        {"label": "BAT iPhone 11", "category": "Batterie", "reference": "IP11",
         "qty": 2, "unit_cents": 120000, "total_cents": 240000},
    ]
    return {
        "doc_title": "TICKET DE TEST",
        "test_info": paper_description(cfg),
        "ticket_num": 0,
        "date_str": datetime.now().strftime("%d/%m/%Y  %H:%M"),
        "lines": lines,
        "total_brut_cents": 490000,
        "remise_cents": 0,
        "total_cents": 490000,
        "nb_articles": len(lines),
        "nb_pieces": 3,
        "frame": True,
    }


def print_test_ticket(parent=None, cfg: dict | None = None) -> bool:
    data = test_ticket_data()
    if cfg is not None:
        data["test_info"] = paper_description(cfg)
    return print_ticket_data(data, parent, cfg=cfg)


# ---------------------------------------------------------------------------
# Dessin du ticket
# ---------------------------------------------------------------------------

# Le ticket est dessiné dans un repère fixe de TICKET_WIDTH unités de large,
# polices en « pixels » de ce repère, puis réduit (sans déformation) pour
# tenir entièrement sur l'étiquette — quelle que soit la résolution de
# l'imprimante (203 ppp pour une imprimante d'étiquettes, 600+ pour une
# imprimante de bureau).
TICKET_WIDTH = 300


# Ticket thermique : tout le texte en gras (traits de 2 points au lieu d'un,
# lisibles sur une tête thermique). Courier New gras a la même largeur que
# le normal : la mise en page ne change pas.
_heavy_text = False


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont("Courier New")
    font.setPixelSize(size)
    font.setBold(bold or _heavy_text)
    return font


def _ticket_height(data: dict) -> int:
    """Hauteur du ticket dans le repère TICKET_WIDTH (dessin à blanc)."""
    image = QImage(TICKET_WIDTH, 10, QImage.Format.Format_Mono)
    painter = QPainter(image)
    try:
        return _draw_ticket(painter, data, TICKET_WIDTH)
    finally:
        painter.end()


def thermal_ticket_image(data: dict, width_dots: int) -> QImage:
    """Ticket dessiné en noir et blanc pur (sans gris ni lissage), à la
    résolution de l'imprimante thermique : un point de l'image = un point de
    la tête d'impression. Le texte reste net — les gris du lissage
    deviendraient des pointillés flous sur une imprimante qui ne fait que
    du noir."""
    height = max(1, _ticket_height(data))
    scale = width_dots / TICKET_WIDTH
    global _heavy_text
    ruler = 60 if data.get("frame") else 0  # place pour la règle du ticket de test
    image = QImage(width_dots, max(1, round(height * scale) + ruler), QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    _heavy_text = True
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
        painter.scale(scale, scale)
        _draw_ticket(painter, data, TICKET_WIDTH)
        if data.get("frame"):  # ticket de test : bords et règle pour régler
            painter.resetTransform()
            painter.setPen(QPen(Qt.GlobalColor.black, 2))
            painter.drawRect(QRect(1, 1, image.width() - 3, image.height() - 3))
            _draw_mm_ruler(painter, image.width(), image.height())
    finally:
        _heavy_text = False
        painter.end()
    return image.convertToFormat(
        QImage.Format.Format_Mono,
        Qt.ImageConversionFlag.MonoOnly | Qt.ImageConversionFlag.ThresholdDither,
    )


def _draw_mm_ruler(painter: QPainter, width: int, height: int) -> None:
    """Règle graduée (mm) en bas du ticket de test thermique : un trait tous
    les 5 mm, un nombre tous les 10 mm. Le premier et le dernier nombre
    lisibles indiquent de combien le ticket est coupé à gauche / à droite."""
    dots_per_mm = THERMAL_DPI / 25.4
    base = height - 6
    painter.setPen(QPen(Qt.GlobalColor.black, 2))
    painter.drawLine(0, base, width, base)
    font = QFont("Courier New")
    font.setPixelSize(18)
    font.setBold(True)
    painter.setFont(font)
    mm = 0
    while mm * dots_per_mm <= width:
        x = round(mm * dots_per_mm)
        painter.drawLine(x, base - (22 if mm % 10 == 0 else 12), x, base)
        if mm % 10 == 0:
            painter.drawText(QRect(x - 20, base - 46, 40, 20), int(Qt.AlignmentFlag.AlignCenter), str(mm))
        mm += 5


def _render_ticket(device, data: dict, cfg: dict, copies: int = 1) -> bool:
    """Imprime `data` sur `device` (QPrinter ou QPdfWriter) selon le type
    de papier : réduit pour tenir sur une étiquette, à taille réelle sur
    une feuille A4 (avec un pointillé de découpe) ou sur un rouleau
    (image noir et blanc à la résolution de l'imprimante thermique)."""
    height = max(1, _ticket_height(data))
    paper = cfg.get("paper", PAPER_LABEL)
    thermal = thermal_ticket_image(data, _roll_dots(cfg)) if paper == PAPER_ROLL else None
    painter = QPainter()
    if not painter.begin(device):
        return False
    try:
        dots_per_mm = device.resolution() / 25.4
        for copy in range(copies):
            if copy:
                device.newPage()
            viewport = painter.viewport()
            painter.save()
            if paper == PAPER_SHEET:
                box_w, box_h = TICKET_WIDTH + 2 * CUT_PADDING, height + 2 * CUT_PADDING
                target = float(cfg["ticket_width_mm"]) * dots_per_mm
                scale = min(target / box_w, viewport.height() / box_h)
                painter.scale(scale, scale)
                painter.setPen(QPen(Qt.GlobalColor.black, 1, Qt.PenStyle.DashLine))
                painter.drawRect(QRect(0, 0, box_w - 1, box_h - 1))
                painter.translate(CUT_PADDING, CUT_PADDING)
            elif paper == PAPER_ROLL:
                # Image sans lissage (à 203 ppp, un point de l'image = un point
                # de l'imprimante), centrée dans la zone imprimable déclarée
                # par le pilote, ± le décalage réglé à la main.
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                layout = QPageLayout(device.pageLayout())
                layout.setUnits(QPageLayout.Unit.Millimeter)
                minimum = layout.minimumMargins()
                paper_w = viewport.width() / dots_per_mm
                printable_left = minimum.left()
                printable_w = max(1.0, paper_w - minimum.left() - minimum.right())
                target_w = min(roll_print_width_mm(cfg), printable_w)
                left_mm = printable_left + (printable_w - target_w) / 2 + float(cfg.get("roll_offset_mm") or 0)
                target = QRect(
                    round(left_mm * dots_per_mm), round(minimum.top() * dots_per_mm),
                    round(target_w * dots_per_mm),
                    round(thermal.height() * target_w * dots_per_mm / thermal.width()),
                )
                painter.drawImage(target, thermal)
                painter.restore()
                continue
            else:
                scale = min(viewport.width() / TICKET_WIDTH, viewport.height() / height)
                painter.translate((viewport.width() - TICKET_WIDTH * scale) / 2, 0)
                painter.scale(scale, scale)
            _draw_ticket(painter, data, TICKET_WIDTH)
            if data.get("frame"):
                painter.setPen(QPen(Qt.GlobalColor.black, 2))
                painter.drawRect(QRect(0, 0, TICKET_WIDTH - 1, height - 1))
            painter.restore()
    finally:
        painter.end()
    return True


def _draw_ticket(painter: QPainter, data: dict, W: int) -> int:
    """Dessine le ticket sur `painter` (largeur W) et renvoie sa hauteur."""
    cfg = load_ticket_config()
    STORE_NAME = cfg["store_name"]
    STORE_PHONE = cfg["store_phone"]
    STORE_TAGLINE = cfg["store_tagline"]
    y = 0

    def lh(pt: int) -> int:
        return int(pt * 1.55)

    def draw(text: str, pt: int, bold: bool = False,
             align=Qt.AlignmentFlag.AlignLeft,
             x0: int = 0, w: int | None = None) -> None:
        nonlocal y
        painter.setFont(_font(pt, bold))
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

    painter.setPen(QPen(Qt.GlobalColor.black, 1))

    # ── En-tête ──────────────────────────────────────────────────────
    draw(STORE_NAME, 20, bold=True, align=Qt.AlignmentFlag.AlignCenter)
    draw(STORE_TAGLINE, 11, align=Qt.AlignmentFlag.AlignCenter)
    draw(f"☎ {STORE_PHONE}", 13, bold=True, align=Qt.AlignmentFlag.AlignCenter)
    double_rule()

    if data.get("doc_title"):
        draw(data["doc_title"], 14, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        if data.get("source_ticket"):
            draw(f"Ticket d'origine : #{data['source_ticket']:06d}", 10,
                 align=Qt.AlignmentFlag.AlignCenter)
        if data.get("test_info"):
            draw(data["test_info"], 10, align=Qt.AlignmentFlag.AlignCenter)
        hline(gap=3)

    # ── Infos ticket ─────────────────────────────────────────────────
    h_row = lh(13)
    painter.setFont(_font(13, bold=True))
    painter.drawText(QRect(0, y, W // 2, h_row),
                     Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                     f"N° #{data['ticket_num']:06d}")
    painter.setFont(_font(13))
    painter.drawText(QRect(W // 2, y, W // 2, h_row),
                     Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                     data["date_str"])
    y += h_row

    if data.get("cashier_name"):
        draw(f"Caissier : {data['cashier_name']}", 11)

    if data.get("reseller_name"):
        y += 4
        painter.setPen(QPen(Qt.GlobalColor.black, 1, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(QRect(0, y, W, lh(13) + (lh(11) if data.get("reseller_phone") else 0) + 8), 4, 4)
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        y += 4
        contact_label = data.get("contact_label", "Client")
        draw(f"▸ {contact_label} : {data['reseller_name']}", 13, bold=True, x0=4, w=W - 8)
        if data.get("reseller_phone"):
            draw(f"☎ {data['reseller_phone']}", 11, x0=4, w=W - 8)
        y += 4

    double_rule()

    # ── En-tête colonnes ─────────────────────────────────────────────
    # Quantité centrée dans sa propre colonne, bien séparée du prix (comme
    # sur les tickets fournisseurs).
    cw = [int(W * 0.37), int(W * 0.13), int(W * 0.25), int(W * 0.25)]
    cx = [sum(cw[:i]) for i in range(4)]
    h_hdr = lh(12)
    painter.setFont(_font(12, bold=True))
    for label, x, w_col, al in zip(
        ["ARTICLE", "QTE", "P.U", "TOTAL"],
        cx, cw,
        [Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter]
        + [Qt.AlignmentFlag.AlignRight] * 2,
    ):
        painter.drawText(QRect(x, y, w_col, h_hdr), int(al) | Qt.TextFlag.TextSingleLine, label)
    y += h_hdr
    painter.setPen(QPen(Qt.GlobalColor.black, 2, Qt.PenStyle.SolidLine))
    painter.drawLine(0, y, W, y)
    painter.setPen(QPen(Qt.GlobalColor.black, 1))
    y += 6

    # ── Lignes produits ───────────────────────────────────────────────
    h_body = lh(13)
    for line in data["lines"]:
        painter.setFont(_font(13))
        # Libellé (catégorie + modèle) : passe à la ligne s'il est long, sans
        # rien couper (le ticket s'allonge d'autant).
        wrap = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
        needed = painter.fontMetrics().boundingRect(QRect(0, 0, cw[0] - 4, 1000), wrap, line["label"]).height()
        label_h = max(h_body, needed + 4)
        painter.drawText(QRect(cx[0], y, cw[0] - 4, label_h), wrap, line["label"])
        painter.drawText(QRect(cx[1], y, cw[1], h_body),
                         Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextSingleLine,
                         str(line["qty"]))
        for column, cents in ((2, line["unit_cents"]), (3, line["total_cents"])):
            # Un grand montant (« 150 000.00 ») ne doit pas toucher la
            # colonne voisine : police réduite jusqu'à ce qu'il tienne.
            text, size, room = _da_line(cents), 13, cw[column] - 10
            while size > 8 and painter.fontMetrics().horizontalAdvance(text) > room:
                size -= 1
                painter.setFont(_font(size))
            painter.drawText(QRect(cx[column], y, cw[column], h_body),
                             Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                             text)
            painter.setFont(_font(13))
        y += label_h

        painter.setPen(QPen(Qt.GlobalColor.black, 1, Qt.PenStyle.DotLine))
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
        painter.setFont(_font(pt))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawText(QRect(0, y, lbl_w, h),
                         Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine, label)
        painter.setFont(_font(pt, bold_val))
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
    painter.setFont(_font(18, bold=True))
    painter.drawText(QRect(6, y, W // 2, h_tot),
                     Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextSingleLine,
                     data.get("total_label")
                     or ("TTC À PAYER" if data.get("remise_cents", 0) > 0 else "TOTAL"))
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

    # ── Règlement et solde d'un bon de retour ─────────────────────────
    if data.get("settlement_label") or data.get("balance_rows"):
        hline(dashed=True, gap=4)
        if data.get("settlement_label"):
            right_row("Règlement :", data["settlement_label"], pt=10)
        for label, value in data.get("balance_rows", []):
            draw(label, 10)
            draw(value, 11, bold=label.startswith("Nouv"), align=Qt.AlignmentFlag.AlignRight)
        y += 4

    # ── Pied ──────────────────────────────────────────────────────────
    hline(dashed=True, gap=4)
    y += 4
    draw("Merci pour votre confiance !", 12, bold=True, align=Qt.AlignmentFlag.AlignCenter)
    draw("━" * 20, 10, align=Qt.AlignmentFlag.AlignCenter)
    return y
