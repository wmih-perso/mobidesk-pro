"""Dialogue d'aperçu du ticket de caisse avec bouton d'impression."""

from __future__ import annotations

from PySide6.QtCore import Qt  # noqa: F401 — utilisé dans _render_html
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.money import format_balance_ticket
from app.printing import _collect_ticket_data, _da
from app.ticket_config import load_ticket_config
from app.ui.frameless_dialog import FramelessDialog


class TicketPreviewDialog(FramelessDialog):
    """Affiche un aperçu du ticket et propose de l'imprimer."""

    def __init__(
        self,
        batch_id: int | None = None,
        movement_id: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(360)
        self._batch_id = batch_id
        self._movement_id = movement_id
        self._data: dict | None = None
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(
            "🖨  Ticket de caisse",
            gradient="qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #374151,stop:1 #1e3a5f)",
            height=54,
        ))

        inner = QWidget()
        inner.setStyleSheet("background: white;")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        root.addWidget(inner)

        # Zone de prévisualisation
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: #f0ede8;")

        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        self._browser.setFixedWidth(240)
        self._browser.setMinimumHeight(460)
        self._browser.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._browser.setStyleSheet(
            "QTextBrowser { background: #fefdfb; border: none;"
            " font-family: 'Courier New', Courier, monospace; }"
        )

        center = QWidget()
        center_layout = QHBoxLayout(center)
        center_layout.setContentsMargins(0, 16, 0, 16)
        center_layout.addStretch()
        center_layout.addWidget(self._browser)
        center_layout.addStretch()

        scroll.setWidget(center)
        layout.addWidget(scroll, stretch=1)

        # Boutons
        btn_row = QHBoxLayout()
        close_btn = QPushButton("Fermer")
        close_btn.setObjectName("SecondaryButton")
        close_btn.setFixedWidth(110)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        btn_row.addStretch()

        self._print_btn = QPushButton("🖨  Imprimer")
        self._print_btn.setFixedHeight(38)
        self._print_btn.setMinimumWidth(140)
        self._print_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " border-radius: 7px; font-weight: 700; font-size: 13px; padding: 0 16px; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:pressed { background: #1e40af; }"
            "QPushButton:disabled { background: #cbd5e1; color: #94a3b8; }"
        )
        self._print_btn.clicked.connect(self._on_print)
        btn_row.addWidget(self._print_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Chargement
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._data = _collect_ticket_data(self._batch_id, self._movement_id)
        if self._data:
            self._browser.setHtml(self._render_html())
        else:
            self._browser.setHtml("<p style='color:red;'>Aucune donnée pour ce ticket.</p>")
            self._print_btn.setEnabled(False)

    def _render_html(self) -> str:
        d = self._data
        cfg = load_ticket_config()
        STORE_NAME = cfg["store_name"]
        STORE_PHONE = cfg["store_phone"]
        STORE_TAGLINE = cfg["store_tagline"]

        lines_html = ""
        for line in d["lines"]:
            lines_html += (
                f"<tr>"
                f"<td style='padding:4px 2px;border-bottom:1px dotted #d4d0cb;font-size:10px;'>{line['label']}</td>"
                f"<td style='text-align:right;padding:4px 2px;border-bottom:1px dotted #d4d0cb;font-size:10px;vertical-align:top;'>{line['qty']}</td>"
                f"<td style='text-align:right;padding:4px 2px;border-bottom:1px dotted #d4d0cb;font-size:10px;vertical-align:top;'>{_da(line['unit_cents'])}</td>"
                f"<td style='text-align:right;padding:4px 2px;border-bottom:1px dotted #d4d0cb;font-size:10px;vertical-align:top;'>{_da(line['total_cents'])}</td>"
                f"</tr>"
            )

        remise_html = ""
        ttc_label = "TOTAL"
        if d.get("remise_cents", 0) > 0:
            remise_html = f"""
  <table width="100%" style="font-size:10px;margin:2px 0;">
    <tr><td>TOTAL :</td><td style="text-align:right;">{_da(d['total_brut_cents'])} DA</td></tr>
    <tr><td style="color:#dc2626;">REMISE :</td><td style="text-align:right;color:#dc2626;">- {_da(d['remise_cents'])} DA</td></tr>
  </table>
  <hr style="border:none;border-top:1px solid #1a1a1a;margin:3px 0;">"""
            ttc_label = "TTC À PAYER"

        solde_html = ""
        if d.get("has_reseller") and d.get("versement_cents", -1) >= 0:
            solde_html = f"""
  <hr style="border:none;border-top:1px dashed #888;margin:6px 0 3px;">
  <table width="100%" style="font-size:10px;">
    <tr><td>Anc. solde :</td><td style="text-align:right;">{format_balance_ticket(d['balance_before_cents'])}</td></tr>
    <tr><td>Total achat :</td><td style="text-align:right;">{_da(d['total_cents'])} DA</td></tr>
    <tr><td>Versement :</td><td style="text-align:right;">{_da(d['versement_cents'])} DA</td></tr>
  </table>
  <hr style="border:none;border-top:1px solid #1a1a1a;margin:3px 0;">
  <table width="100%" style="font-size:11px;font-weight:bold;">
    <tr><td>Nouv. solde :</td><td style="text-align:right;">{format_balance_ticket(d['balance_after_cents'])}</td></tr>
  </table>"""

        client_block = ""
        if d.get("reseller_name"):
            client_block = f"""
  <div style="border:1px dashed #888;border-radius:3px;padding:4px 6px;margin:6px 0;">
    <p style="margin:0;font-weight:bold;font-size:11px;">&#9658; Client : {d['reseller_name']}</p>
    {"<p style='margin:1px 0;font-size:10px;color:#555;'>&#9990; " + d['reseller_phone'] + "</p>" if d.get('reseller_phone') else ""}
  </div>"""

        cashier_line = (
            f"<p style='margin:2px 0;font-size:10px;color:#555;'>Caissier : {d['cashier_name']}</p>"
            if d.get("cashier_name") else ""
        )

        return f"""
<html><body style="font-family:'Courier New',Courier,monospace;font-size:11px;
                   color:#1a1a1a;background:#fefdfb;margin:10px 8px;">

  <p style="text-align:center;font-weight:bold;font-size:17px;
            letter-spacing:2px;margin:4px 0 1px;">{STORE_NAME}</p>
  <p style="text-align:center;font-size:9px;color:#555;margin:1px 0;">{STORE_TAGLINE}</p>
  <p style="text-align:center;font-weight:bold;font-size:11px;margin:2px 0 6px;">&#9990; {STORE_PHONE}</p>

  <hr style="border:none;border-top:1.5px solid #1a1a1a;border-bottom:1px solid #1a1a1a;margin:6px 0 4px;">

  <table width="100%" style="margin:2px 0;"><tr>
    <td style="font-size:11px;">N&#176; <b>#{d['ticket_num']:06d}</b></td>
    <td style="text-align:right;font-size:10px;color:#555;">{d['date_str']}</td>
  </tr></table>
  {cashier_line}
  {client_block}

  <hr style="border:none;border-top:1.5px solid #1a1a1a;border-bottom:1px solid #1a1a1a;margin:6px 0 4px;">

  <table width="100%" cellspacing="0" cellpadding="0"
         style="border-collapse:collapse;font-size:10px;">
    <thead>
      <tr>
        <th style="text-align:left;padding:3px 2px;border-bottom:1.5px solid #1a1a1a;">LIBELLE</th>
        <th style="text-align:right;padding:3px 2px;border-bottom:1.5px solid #1a1a1a;">QTE</th>
        <th style="text-align:right;padding:3px 2px;border-bottom:1.5px solid #1a1a1a;">P.U</th>
        <th style="text-align:right;padding:3px 2px;border-bottom:1.5px solid #1a1a1a;">TOTAL</th>
      </tr>
    </thead>
    <tbody>{lines_html}</tbody>
  </table>

  <hr style="border:none;border-top:1px solid #1a1a1a;margin:4px 0 2px;">
  <p style="margin:2px 0;font-size:9px;color:#555;">
    NBR ART : {d['nb_articles']}  &nbsp; TQTE : {d['nb_pieces']}
  </p>

  {remise_html}

  <div style="border:1.5px solid #1a1a1a;border-radius:3px;padding:5px 6px;margin:6px 0;
              display:flex;justify-content:space-between;">
    <span style="font-size:13px;font-weight:bold;">{ttc_label}</span>
    <span style="font-size:13px;font-weight:bold;">{_da(d['total_cents'])} DA</span>
  </div>

  {solde_html}

  <hr style="border:none;border-top:1px dashed #888;margin:6px 0;">
  <p style="text-align:center;font-weight:bold;font-size:10px;margin:4px 0 1px;">
    Merci pour votre confiance !
  </p>
  <p style="text-align:center;font-size:9px;color:#888;margin:1px 0;">
    &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
  </p>

</body></html>
"""

    # ------------------------------------------------------------------
    # Impression
    # ------------------------------------------------------------------

    def _on_print(self) -> None:
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        from app.printing import _render_ticket

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        page_size = QPageSize(QSizeF(40.0, 220.0), QPageSize.Unit.Millimeter)
        printer.setPageSize(page_size)
        printer.setPageMargins(QMarginsF(2, 3, 2, 3), QPageLayout.Unit.Millimeter)

        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Imprimer le ticket")
        if dlg.exec() == QPrintDialog.DialogCode.Accepted and self._data:
            _render_ticket(printer, self._data)
