"""Dialogue d'aperçu du ticket de caisse avec bouton d'impression."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.printing import STORE_NAME, STORE_PHONE, STORE_TAGLINE, _collect_ticket_data, _da


class TicketPreviewDialog(QDialog):
    """Affiche un aperçu du ticket et propose de l'imprimer."""

    def __init__(
        self,
        batch_id: int | None = None,
        movement_id: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ticket de caisse")
        self.setMinimumWidth(340)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.CustomizeWindowHint
        )
        self._batch_id = batch_id
        self._movement_id = movement_id
        self._data: dict | None = None
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

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
        lines_html = ""
        for line in d["lines"]:
            lines_html += (
                f"<tr>"
                f"<td style='padding:4px 2px;border-bottom:1px dotted #d4d0cb;'>{line['label']}</td>"
                f"<td style='text-align:right;padding:4px 2px;border-bottom:1px dotted #d4d0cb;'>{line['qty']}</td>"
                f"<td style='text-align:right;padding:4px 2px;border-bottom:1px dotted #d4d0cb;'>{_da(line['unit_cents'])}</td>"
                f"<td style='text-align:right;padding:4px 2px;border-bottom:1px dotted #d4d0cb;'>{_da(line['total_cents'])}</td>"
                f"</tr>"
            )

        total_da = _da(d["total_cents"])
        return f"""
<html><body style="font-family:'Courier New',Courier,monospace;font-size:11px;
                   color:#1a1a1a;background:#fefdfb;margin:10px 8px;">

  <!-- Logo / En-tête -->
  <div style="text-align:center;margin-bottom:4px;">
    <span style="display:inline-block;border:1.5px solid #1a1a1a;border-radius:6px;
                 padding:2px 8px;font-size:9px;letter-spacing:1px;">
      <span style="font-size:13px;font-weight:bold;">ms</span>
    </span>
  </div>
  <p style="text-align:center;font-weight:bold;font-size:15px;
            letter-spacing:3px;margin:2px 0;">{STORE_NAME}</p>
  <p style="text-align:center;font-size:9px;color:#5a5650;margin:1px 0;">{STORE_TAGLINE}</p>
  <p style="text-align:center;font-weight:bold;font-size:11px;margin:2px 0 6px;">{STORE_PHONE}</p>

  <hr style="border:none;border-top:1px dashed #c9c5bf;margin:6px 0;">

  <p style="margin:2px 0;">Ticket : <b>{d['ticket_num']:06d}</b></p>
  <p style="margin:2px 0;">{d['date_str']}</p>

  <hr style="border:none;border-top:1px dashed #c9c5bf;margin:6px 0;">

  <!-- Tableau produits -->
  <table width="100%" cellspacing="0" cellpadding="0"
         style="border-collapse:collapse;font-size:10px;">
    <thead>
      <tr style="border-bottom:2px solid #1a1a1a;">
        <th style="text-align:left;padding:3px 2px;">ARTICLE</th>
        <th style="text-align:right;padding:3px 2px;">QTE</th>
        <th style="text-align:right;padding:3px 2px;">P.U</th>
        <th style="text-align:right;padding:3px 2px;">TOTAL</th>
      </tr>
    </thead>
    <tbody>{lines_html}</tbody>
  </table>

  <hr style="border:none;border-top:1px dashed #c9c5bf;margin:6px 0;">

  <p style="margin:2px 0;font-size:9px;">
    NBR ART: {d['nb_articles']} &nbsp; TQTE: {d['nb_pieces']}
  </p>

  <table width="100%" style="margin-top:4px;">
    <tr>
      <td style="font-size:14px;font-weight:bold;">TOTAL</td>
      <td style="text-align:right;font-size:14px;font-weight:bold;">{total_da} DA</td>
    </tr>
  </table>

  <hr style="border:none;border-top:2px solid #1a1a1a;margin:6px 0;">

  <p style="text-align:center;font-size:9px;color:#5a5650;margin:8px 0 2px;">
    Merci pour votre confiance !
  </p>
  <p style="text-align:center;font-size:9px;color:#5a5650;margin:2px 0;">
    {STORE_NAME} &mdash; {STORE_PHONE}
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
        page_size = QPageSize(QSizeF(58.0, 220.0), QPageSize.Unit.Millimeter)
        printer.setPageSize(page_size)
        printer.setPageMargins(QMarginsF(3, 4, 3, 4), QPageLayout.Unit.Millimeter)

        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Imprimer le ticket")
        if dlg.exec() == QPrintDialog.DialogCode.Accepted and self._data:
            _render_ticket(printer, self._data)
