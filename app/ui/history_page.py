"""Page Historique — Suivi des ventes + Mouvements de stock."""

from __future__ import annotations

import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import format_da
from app.ui.frameless_dialog import FramelessDialog
from app.services import (
    count_movements,
    create_refund_resolution,
    create_replacement,
    has_replacement,
    list_movements,
    list_returns,
    list_returns_for_batch,
    list_sale_batches,
)
from app.ui.widgets import apply_card_shadow

PAGE_SIZE = 50

MONTH_FR = [
    "jan", "fév", "mar", "avr", "mai", "jun",
    "jul", "aoû", "sep", "oct", "nov", "déc",
]


def _fmt_date(dt: datetime.datetime) -> str:
    return f"{dt.day:02d}/{MONTH_FR[dt.month - 1]}/{dt.year}  {dt.hour:02d}:{dt.minute:02d}"


# ──────────────────────────────────────────────────────────────────────────────
# Popup détail vente
# ──────────────────────────────────────────────────────────────────────────────

class _ReturnQtyDialog(FramelessDialog):
    """Mini dialog pour saisir la quantité à retourner."""

    def __init__(self, max_qty: int, product_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(360, 220)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header("Quantité à retourner", height=52))
        _body = QWidget()
        _body.setStyleSheet("background: white;")
        layout = QVBoxLayout(_body)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)
        root.addWidget(_body)

        lbl = QLabel(f"Retourner  <b>{product_name}</b>")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        from app.ui.widgets import ModernSpinBox
        self.qty = ModernSpinBox()
        self.qty.setRange(1, max_qty)
        self.qty.setValue(1)
        layout.addWidget(self.qty)

        hint = QLabel(f"Maximum : {max_qty}")
        hint.setStyleSheet("color: #9ca3af; font-size: 11px;")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        cancel = QPushButton("Annuler")
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()
        ok = QPushButton("Confirmer")
        ok.setStyleSheet(
            "QPushButton { background: #00897b; color: white; border: none; border-radius: 6px; "
            "padding: 8px 18px; font-weight: 700; }"
            "QPushButton:hover { background: #00796b; }"
        )
        ok.clicked.connect(self.accept)
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)


class SaleBatchDetailDialog(FramelessDialog):
    """Détail d'une vente : liste des produits + boutons retour par ligne."""

    def __init__(self, batch: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Détail de la vente")
        self.setMinimumSize(700, 420)
        self.setModal(True)
        self._batch = batch
        self._build_ui(batch)

    def _build_ui(self, batch: dict) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header draggable avec bouton fermer
        header = QFrame()
        header.setStyleSheet("background: #1a1a2e;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 14, 12, 14)
        mode = "Vente gros" if batch["sale_type"] == "wholesale" else "Vente détail"
        title = QLabel(f"🧾  {mode}  —  {_fmt_date(batch['date'])}")
        title.setStyleSheet("color: white; font-size: 14px; font-weight: 700;")
        h_layout.addWidget(title)
        h_layout.addStretch()
        total_lbl = QLabel(format_da(batch["total_cents"]))
        total_lbl.setStyleSheet("color: #00e676; font-size: 18px; font-weight: 900;")
        h_layout.addWidget(total_lbl)
        h_layout.addSpacing(12)
        _close = QPushButton("✕")
        _close.setObjectName("FramelessCloseBtn")
        _close.setFixedSize(38, 38)
        _close.setCursor(Qt.CursorShape.PointingHandCursor)
        _close.clicked.connect(self.reject)
        h_layout.addWidget(_close)
        self._drag_header = header
        header.mousePressEvent = self._header_mouse_press
        header.mouseMoveEvent = self._header_mouse_move
        header.mouseReleaseEvent = self._header_mouse_release
        layout.addWidget(header)

        if batch["reseller"]:
            bar = QLabel(f"  Revendeur : {batch['reseller']}")
            bar.setStyleSheet("background: #2d2d44; color: #9ca3af; padding: 6px 20px; font-size: 12px;")
            layout.addWidget(bar)

        # Tableau avec colonne retour
        movements = batch["movements"]
        table = QTableWidget(len(movements), 6)
        table.setHorizontalHeaderLabels(["Référence", "Désignation", "Qté", "Prix U.", "Montant", ""])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(44)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f8fdfc; }"
            "QHeaderView::section { background: #00897b; color: white; font-weight: 700; "
            "padding: 8px 10px; border: none; border-right: 1px solid #00796b; font-size: 12px; }"
            "QHeaderView::section:last { border-right: none; }"
            "QTableWidget::item:selected { background: #b2dfdb; color: #212121; }"
        )
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 110)
        table.setColumnWidth(2, 55)
        table.setColumnWidth(3, 130)
        table.setColumnWidth(4, 120)
        table.setColumnWidth(5, 120)

        # prix de vente par produit (pour calcul montant retourné)
        self._sale_price: dict[int, int] = {}
        # quantité vendue par produit (pour limite retour)
        self._sold_qty: dict[int, int] = {}
        # référence boutons retour par display_id
        self._ret_btns: dict[int, QPushButton] = {}

        for i, m in enumerate(movements):
            ref = m.display.reference if m.display else "—"
            desc = f"{m.display.brand} {m.display.phone_model}".strip() if m.display else "—"
            qty = abs(m.change_quantity)
            self._sale_price[m.display_id] = m.unit_sale_price_cents
            self._sold_qty[m.display_id] = qty
            table.setItem(i, 0, QTableWidgetItem(ref))
            table.setItem(i, 1, QTableWidgetItem(desc))
            _right(table, i, 2, str(qty))
            _right(table, i, 3, format_da(m.unit_sale_price_cents))
            _right(table, i, 4, format_da(m.unit_sale_price_cents * qty))

            ret_btn = QPushButton("↩  Retour")
            ret_btn.setStyleSheet(
                "QPushButton { background: #fff3e0; color: #e65100; border: 1px solid #ffcc02; "
                "border-radius: 5px; padding: 5px 10px; font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #ffe0b2; }"
            )
            d_id = m.display_id
            r_id = m.reseller_id
            ret_btn.clicked.connect(
                lambda _c, did=d_id, rid=r_id, name=f"{ref} — {desc}":
                    self._do_return(did, rid, name)
            )
            self._ret_btns[d_id] = ret_btn
            w = QWidget()
            w.setStyleSheet("background: transparent;")
            wl = QHBoxLayout(w)
            wl.setContentsMargins(6, 4, 6, 4)
            wl.addWidget(ret_btn)
            table.setCellWidget(i, 5, w)

        layout.addWidget(table, stretch=1)

        # Section retours liés
        self._returns_section = QWidget()
        self._returns_section.setStyleSheet("background: #fff8e1;")
        rs_layout = QVBoxLayout(self._returns_section)
        rs_layout.setContentsMargins(20, 10, 20, 10)
        rs_layout.setSpacing(4)
        self._returns_lbl = QLabel("")
        self._returns_lbl.setStyleSheet("font-size: 12px; color: #e65100;")
        self._returns_lbl.setWordWrap(True)
        rs_layout.addWidget(self._returns_lbl)
        self._returns_section.setVisible(False)
        layout.addWidget(self._returns_section)

        footer = QFrame()
        footer.setStyleSheet("background: #f8fdfc; border-top: 1px solid #e0e0e0;")
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(20, 10, 20, 10)
        self._total_label = QLabel("")
        self._total_label.setStyleSheet("font-weight: 800; font-size: 15px; color: #00897b;")
        f_layout.addStretch()
        f_layout.addWidget(QLabel("Total :  "))
        f_layout.addWidget(self._total_label)
        layout.addWidget(footer)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 10, 16, 14)
        btn_row.addStretch()
        close_btn = QPushButton("Fermer")
        close_btn.setObjectName("SecondaryButton")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._refresh_returns()

    def _refresh_returns(self) -> None:
        batch_id = self._batch["batch_id"]
        total = self._batch["total_cents"]
        with session_scope() as session:
            rets = list_returns_for_batch(session, batch_id)

        # quantité déjà retournée par produit (retours reçus seulement, change > 0)
        returned_qty_by_product: dict[int, int] = {}
        for r in rets:
            if r.change_quantity > 0:  # retour reçu = stock remonte
                did = r.display_id
                returned_qty_by_product[did] = returned_qty_by_product.get(did, 0) + abs(r.change_quantity)

        # mettre à jour les boutons retour
        for d_id, btn in self._ret_btns.items():
            sold = self._sold_qty.get(d_id, 0)
            already = returned_qty_by_product.get(d_id, 0)
            available = max(0, sold - already)
            if available == 0:
                btn.setText("✓ Retourné")
                btn.setEnabled(False)
                btn.setStyleSheet(
                    "QPushButton { background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; "
                    "border-radius: 5px; padding: 5px 10px; font-weight: 700; font-size: 12px; }"
                )
            else:
                btn.setText(f"↩  Retour ({available})")
                btn.setEnabled(True)
                btn.setStyleSheet(
                    "QPushButton { background: #fff3e0; color: #e65100; border: 1px solid #ffcc02; "
                    "border-radius: 5px; padding: 5px 10px; font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #ffe0b2; }"
                )

        if not rets:
            self._returns_section.setVisible(False)
            self._total_label.setText(format_da(total))
            self._total_label.setStyleSheet("font-weight: 800; font-size: 15px; color: #00897b;")
            return

        # montant retourné = qty × prix de vente d'origine
        returned_cents = sum(
            abs(r.change_quantity) * self._sale_price.get(r.display_id, 0)
            for r in rets if r.change_quantity > 0
        )
        lines = []
        for r in rets:
            ref = r.display.reference if r.display else "—"
            qty = abs(r.change_quantity)
            label = "Retour reçu" if r.change_quantity > 0 else "Retour fourn."
            lines.append(f"  ↩ {label} : {qty} × {ref}")

        self._returns_lbl.setText(
            "Retours déclarés :\n" + "\n".join(lines) +
            f"\n  Montant retourné : –{format_da(returned_cents)}"
        )
        self._returns_section.setVisible(True)
        net = total - returned_cents
        self._total_label.setText(f"{format_da(net)}  (net après retours)")
        self._total_label.setStyleSheet("font-weight: 800; font-size: 14px; color: #e65100;")

    def _do_return(self, display_id: int, reseller_id: int | None, name: str) -> None:
        # Calcule la quantité disponible à retourner en temps réel
        with session_scope() as session:
            rets = list_returns_for_batch(session, self._batch["batch_id"])
        already = sum(
            abs(r.change_quantity) for r in rets
            if r.display_id == display_id and r.change_quantity > 0
        )
        max_qty = max(0, self._sold_qty.get(display_id, 0) - already)
        if max_qty == 0:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Déjà retourné",
                "Ce produit a déjà été entièrement retourné.")
            return
        dlg = _ReturnQtyDialog(max_qty, name, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        qty = dlg.qty.value()
        from app.database import session_scope as _ss
        from app.services import StockError, create_return
        from PySide6.QtWidgets import QMessageBox
        try:
            with _ss() as session:
                create_return(
                    session,
                    display_id=display_id,
                    quantity=qty,
                    return_type="received",
                    reseller_id=reseller_id,
                    linked_batch_id=self._batch["batch_id"],
                )
            QMessageBox.information(self, "Retour enregistré",
                f"Retour de {qty} × {name.split(' — ')[0]} enregistré.")
            self._refresh_returns()
        except StockError as e:
            QMessageBox.warning(self, "Erreur", str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Popup détail mouvement
# ──────────────────────────────────────────────────────────────────────────────

class MovementDetailDialog(FramelessDialog):
    """Détail d'un mouvement — avec retour fournisseur si c'est une entrée stock."""

    def __init__(self, row_data: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Détail du mouvement")
        self.setFixedSize(480, 340)
        self.setModal(True)
        self._row = row_data
        self._build_ui(row_data)

    def _build_ui(self, d: dict) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setStyleSheet("background: #1a1a2e;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 14, 12, 14)
        icon = "📥" if d["direction"] == "Entrée" else "📤"
        title = QLabel(f"{icon}  {d['direction']}  —  {d['date']}")
        title.setStyleSheet("color: white; font-size: 14px; font-weight: 700;")
        h_layout.addWidget(title)
        h_layout.addStretch()
        _close = QPushButton("✕")
        _close.setObjectName("FramelessCloseBtn")
        _close.setFixedSize(38, 38)
        _close.setCursor(Qt.CursorShape.PointingHandCursor)
        _close.clicked.connect(self.reject)
        h_layout.addWidget(_close)
        self._drag_header = header
        header.mousePressEvent = self._header_mouse_press
        header.mouseMoveEvent = self._header_mouse_move
        header.mouseReleaseEvent = self._header_mouse_release
        layout.addWidget(header)

        body = QWidget()
        body.setStyleSheet("background: white;")
        b_layout = QVBoxLayout(body)
        b_layout.setContentsMargins(24, 18, 24, 18)
        b_layout.setSpacing(10)

        rows = [
            ("Produit", d["reference"]),
            ("Désignation", d["designation"]),
            ("Quantité", d["qty"]),
            ("Stock avant", str(d["before"])),
            ("Stock après", str(d["after"])),
            ("Motif", d["reason"]),
        ]
        for label, value in rows:
            row_w = QHBoxLayout()
            lbl = QLabel(label + " :")
            lbl.setStyleSheet("color: #6b7280; font-size: 12px; font-weight: 600;")
            lbl.setFixedWidth(110)
            val = QLabel(value)
            val.setStyleSheet("color: #111827; font-size: 13px;")
            row_w.addWidget(lbl)
            row_w.addWidget(val, stretch=1)
            b_layout.addLayout(row_w)

        layout.addWidget(body, stretch=1)

        # Section retours liés (entrées stock seulement)
        if d["direction"] == "Entrée" and d.get("display_id") and d.get("batch_id"):
            self._ret_section = QWidget()
            self._ret_section.setStyleSheet("background: #fff8e1;")
            rs_l = QVBoxLayout(self._ret_section)
            rs_l.setContentsMargins(20, 8, 20, 8)
            self._ret_lbl = QLabel("")
            self._ret_lbl.setStyleSheet("font-size: 12px; color: #e65100;")
            self._ret_lbl.setWordWrap(True)
            rs_l.addWidget(self._ret_lbl)
            self._ret_section.setVisible(False)
            layout.addWidget(self._ret_section)
            self._refresh_supplier_returns()

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 10, 16, 14)
        btn_row.setSpacing(8)

        if d["direction"] == "Entrée" and d.get("display_id"):
            ret_btn = QPushButton("📤  Retour fournisseur")
            ret_btn.setStyleSheet(
                "QPushButton { background: #fff3e0; color: #e65100; border: 1px solid #ffcc02; "
                "border-radius: 6px; padding: 8px 14px; font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #ffe0b2; }"
            )
            ret_btn.clicked.connect(self._do_supplier_return)
            btn_row.addWidget(ret_btn)

        btn_row.addStretch()
        close_btn = QPushButton("Fermer")
        close_btn.setObjectName("SecondaryButton")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _refresh_supplier_returns(self) -> None:
        batch_id = self._row.get("batch_id")
        if not batch_id:
            return
        with session_scope() as session:
            rets = list_returns_for_batch(session, batch_id)
        if not rets:
            if hasattr(self, "_ret_section"):
                self._ret_section.setVisible(False)
            return
        lines = []
        for r in rets:
            ref = r.display.reference if r.display else "—"
            qty = abs(r.change_quantity)
            lines.append(f"  ↩ Retour fournisseur : {qty} × {ref}")
        if hasattr(self, "_ret_lbl"):
            self._ret_lbl.setText("Retours fournisseur :\n" + "\n".join(lines))
            self._ret_section.setVisible(True)

    def _do_supplier_return(self) -> None:
        d = self._row
        name = f"{d['reference']} — {d['designation']}"
        max_qty = d.get("qty_int", 1)
        dlg = _ReturnQtyDialog(max_qty, name, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        qty = dlg.qty.value()
        from app.database import session_scope as _ss
        from app.services import StockError, create_return
        from PySide6.QtWidgets import QMessageBox
        try:
            with _ss() as session:
                create_return(
                    session,
                    display_id=d["display_id"],
                    quantity=qty,
                    return_type="supplier",
                    supplier_id=d.get("supplier_id"),
                    linked_batch_id=d.get("batch_id"),
                )
            QMessageBox.information(self, "Retour enregistré",
                f"Retour fournisseur de {qty} × {d['reference']} enregistré.")
            self._refresh_supplier_returns()
        except StockError as e:
            QMessageBox.warning(self, "Erreur", str(e))


def _right(table: QTableWidget, row: int, col: int, text: str) -> None:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    table.setItem(row, col, item)


# ──────────────────────────────────────────────────────────────────────────────
# Widget filtre réutilisable
# ──────────────────────────────────────────────────────────────────────────────

class _FilterBar(QWidget):
    def __init__(self, type_options: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Rechercher...")
        self.search.setStyleSheet(
            "QLineEdit { background: white; border: 1px solid #bdbdbd; border-radius: 6px; "
            "padding: 7px 12px; font-size: 13px; }"
            "QLineEdit:focus { border: 2px solid #00897b; }"
        )
        self.search.setMinimumWidth(220)
        layout.addWidget(self.search)

        self.type_combo = QComboBox()
        for label, key in type_options:
            self.type_combo.addItem(label, key)
        self.type_combo.setFixedWidth(160)
        layout.addWidget(self.type_combo)

        self.date_combo = QComboBox()
        for label, key in [
            ("Toutes les dates", "all"),
            ("Aujourd'hui", "today"),
            ("Cette semaine", "week"),
            ("Ce mois", "month"),
        ]:
            self.date_combo.addItem(label, key)
        self.date_combo.setFixedWidth(150)
        layout.addWidget(self.date_combo)

        layout.addStretch()

    def date_range(self) -> tuple[datetime.datetime | None, datetime.datetime | None]:
        key = self.date_combo.currentData()
        now = datetime.datetime.now()
        if key == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, None
        if key == "week":
            start = (now - datetime.timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return start, None
        if key == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start, None
        return None, None


# ──────────────────────────────────────────────────────────────────────────────
# Onglet Suivi des ventes
# ──────────────────────────────────────────────────────────────────────────────

class _SalesTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        self._filters = _FilterBar([
            ("Tous les modes", "all"),
            ("Détail seulement", "retail"),
            ("Gros seulement", "wholesale"),
        ])
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.refresh)
        self._filters.search.textChanged.connect(lambda: self._debounce.start())
        self._filters.type_combo.currentIndexChanged.connect(self.refresh)
        self._filters.date_combo.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self._filters)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Mode", "Nb produits", "Total vente", "Revendeur", ""]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(42)
        self._table.setFrameShape(QFrame.Shape.NoFrame)
        self._table.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f8fdfc; }"
            "QHeaderView::section { background: #00897b; color: white; font-weight: 700; "
            "padding: 8px 10px; border: none; border-right: 1px solid #00796b; font-size: 12px; }"
            "QHeaderView::section:last { border-right: none; }"
            "QTableWidget::item { padding: 4px 10px; }"
            "QTableWidget::item:selected { background: #b2dfdb; color: #212121; }"
            "QTableWidget::item:hover { background: #e0f2f1; }"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._table.setColumnWidth(0, 145)
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 140)
        self._table.setColumnWidth(5, 46)
        self._table.doubleClicked.connect(self._on_double_click)

        card_layout.addWidget(self._table, stretch=1)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color: #757575; font-size: 12px; padding: 6px 16px;")
        card_layout.addWidget(self._summary)

        layout.addWidget(card, stretch=1)

    def refresh(self) -> None:
        search = self._filters.search.text().strip()
        sale_type = self._filters.type_combo.currentData()
        date_from, date_to = self._filters.date_range()

        with session_scope() as session:
            self._data = list_sale_batches(
                session,
                search=search,
                date_from=date_from,
                date_to=date_to,
                sale_type_filter=sale_type,
            )

        self._table.setRowCount(len(self._data))
        total_ca = 0
        for i, b in enumerate(self._data):
            mode = "Gros" if b["sale_type"] == "wholesale" else "Détail"
            mode_color = "#1976d2" if b["sale_type"] == "wholesale" else "#00897b"

            self._table.setItem(i, 0, QTableWidgetItem(_fmt_date(b["date"])))

            mode_item = QTableWidgetItem(mode)
            mode_item.setForeground(Qt.GlobalColor.black)
            self._table.setItem(i, 1, mode_item)

            _right(self._table, i, 2, str(b["nb_items"]))
            _right(self._table, i, 3, format_da(b["total_cents"]))
            self._table.setItem(i, 4, QTableWidgetItem(b["reseller"]))

            # Bouton ticket
            print_btn = QPushButton("🖨")
            print_btn.setObjectName("IconButton")
            print_btn.setFixedSize(30, 30)
            print_btn.setStyleSheet("padding: 0; font-size: 13px;")
            print_btn.setToolTip("Imprimer le ticket")
            batch_id = b["batch_id"]
            first_mvt = b["movements"][0] if b["movements"] else None
            if first_mvt:
                print_btn.clicked.connect(
                    lambda _c, bid=batch_id, mid=first_mvt.id: self._print_ticket(bid, mid)
                )
            wrapper = QWidget()
            wrapper.setStyleSheet("background: transparent;")
            wl = QHBoxLayout(wrapper)
            wl.setContentsMargins(6, 0, 6, 0)
            wl.addWidget(print_btn)
            self._table.setCellWidget(i, 5, wrapper)

            # Couleur de fond selon mode
            for col in range(5):
                item = self._table.item(i, col)
                if item and b["sale_type"] == "wholesale":
                    item.setBackground(Qt.GlobalColor.transparent)

            total_ca += b["total_cents"]

        n = len(self._data)
        self._summary.setText(
            f"{n} vente(s)  —  CA total : {format_da(total_ca)}"
        )

    def _print_ticket(self, batch_id: int, movement_id: int) -> None:
        from app.ui.ticket_dialog import TicketPreviewDialog
        dlg = TicketPreviewDialog(batch_id=batch_id, movement_id=movement_id, parent=self)
        dlg.exec()

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._data):
            dlg = SaleBatchDetailDialog(self._data[row], parent=self)
            dlg.exec()


# ──────────────────────────────────────────────────────────────────────────────
# Onglet Mouvements de stock
# ──────────────────────────────────────────────────────────────────────────────

class _MovementsTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._current_page = 1
        self._total_count = 0
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        self._filters = _FilterBar([
            ("Tous les types", "all"),
            ("Ventes", "sales"),
            ("Entrées stock", "entries"),
            ("Sorties (hors vente)", "exits"),
        ])
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._on_filter_change)
        self._filters.search.textChanged.connect(lambda: self._debounce.start())
        self._filters.type_combo.currentIndexChanged.connect(self._on_filter_change)
        self._filters.date_combo.currentIndexChanged.connect(self._on_filter_change)
        layout.addWidget(self._filters)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        COLS = ["Date", "Référence", "Désignation", "Type", "Qté", "Avant", "Après", "Motif", ""]
        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setFrameShape(QFrame.Shape.NoFrame)
        self._table.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f8fdfc; }"
            "QHeaderView::section { background: #00897b; color: white; font-weight: 700; "
            "padding: 8px 10px; border: none; border-right: 1px solid #00796b; font-size: 12px; }"
            "QHeaderView::section:last { border-right: none; }"
            "QTableWidget::item { padding: 4px 8px; }"
            "QTableWidget::item:selected { background: #b2dfdb; color: #212121; }"
            "QTableWidget::item:hover { background: #e0f2f1; }"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 135)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 55)
        self._table.setColumnWidth(5, 70)
        self._table.setColumnWidth(6, 70)
        self._table.setColumnWidth(7, 160)
        self._table.setColumnWidth(8, 46)
        self._table.doubleClicked.connect(self._on_double_click)
        card_layout.addWidget(self._table, stretch=1)

        # Pagination simple
        pag = QWidget()
        pag.setStyleSheet("background: transparent;")
        pag_layout = QHBoxLayout(pag)
        pag_layout.setContentsMargins(12, 6, 12, 8)
        pag_layout.setSpacing(8)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet("color: #757575; font-size: 12px;")
        pag_layout.addWidget(self._summary_lbl)
        pag_layout.addStretch()

        self._prev_btn = QPushButton("‹")
        self._prev_btn.setObjectName("PageNavButton")
        self._prev_btn.clicked.connect(lambda: self._go_page(self._current_page - 1))
        pag_layout.addWidget(self._prev_btn)

        self._page_lbl = QLabel("1")
        self._page_lbl.setObjectName("PageIndicator")
        self._page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_lbl.setFixedWidth(32)
        pag_layout.addWidget(self._page_lbl)

        self._next_btn = QPushButton("›")
        self._next_btn.setObjectName("PageNavButton")
        self._next_btn.clicked.connect(lambda: self._go_page(self._current_page + 1))
        pag_layout.addWidget(self._next_btn)

        card_layout.addWidget(pag)
        layout.addWidget(card, stretch=1)

    def _on_filter_change(self) -> None:
        self._current_page = 1
        self.refresh()

    def _total_pages(self) -> int:
        return max(1, -(-self._total_count // PAGE_SIZE))

    def _go_page(self, page: int) -> None:
        page = max(1, min(page, self._total_pages()))
        if page == self._current_page:
            return
        self._current_page = page
        self.refresh()

    def refresh(self) -> None:
        search = self._filters.search.text().strip().lower()
        mvt_type = self._filters.type_combo.currentData()
        date_from, date_to = self._filters.date_range()

        with session_scope() as session:
            self._total_count = count_movements(
                session, movement_type=mvt_type,
            )
            total_pages = self._total_pages()
            self._current_page = max(1, min(self._current_page, total_pages))
            offset = (self._current_page - 1) * PAGE_SIZE
            movements = list_movements(
                session,
                movement_type=mvt_type,
                limit=PAGE_SIZE,
                offset=offset,
            )

            self._rows = []
            for m in movements:
                ref = m.display.reference if m.display else "—"
                desc = f"{m.display.brand} {m.display.phone_model}".strip() if m.display else "—"
                direction = "Entrée" if m.change_quantity > 0 else "Sortie"
                reason = m.reason
                if m.supplier:
                    reason = f"{m.reason} — {m.supplier.name}"
                elif m.reseller:
                    reason = f"{m.reason} — {m.reseller.name}"

                # Filtre texte côté Python (search sur ref/desc/motif)
                if search:
                    if not (
                        search in ref.lower()
                        or search in desc.lower()
                        or search in reason.lower()
                    ):
                        continue

                # Filtre date
                if date_from and m.created_at < date_from:
                    continue
                if date_to and m.created_at > date_to:
                    continue

                self._rows.append({
                    "date": _fmt_date(m.created_at),
                    "reference": ref,
                    "designation": desc,
                    "direction": direction,
                    "qty": str(abs(m.change_quantity)),
                    "qty_int": abs(m.change_quantity),
                    "before": m.quantity_before,
                    "after": m.quantity_after,
                    "reason": reason,
                    "display_id": m.display_id,
                    "supplier_id": m.supplier_id,
                    "is_sale": m.is_sale,
                    "batch_id": m.movement_batch_id,
                    "mvt_id": m.id,
                })

        self._table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            self._table.setItem(i, 0, QTableWidgetItem(row["date"]))
            self._table.setItem(i, 1, QTableWidgetItem(row["reference"]))
            self._table.setItem(i, 2, QTableWidgetItem(row["designation"]))

            type_item = QTableWidgetItem(row["direction"])
            if row["direction"] == "Entrée":
                type_item.setForeground(Qt.GlobalColor.darkGreen)
            else:
                type_item.setForeground(Qt.GlobalColor.darkRed)
            self._table.setItem(i, 3, type_item)

            _right(self._table, i, 4, row["qty"])
            _right(self._table, i, 5, str(row["before"]))
            _right(self._table, i, 6, str(row["after"]))
            self._table.setItem(i, 7, QTableWidgetItem(row["reason"]))

            if row["is_sale"]:
                print_btn = QPushButton("🖨")
                print_btn.setObjectName("IconButton")
                print_btn.setFixedSize(28, 28)
                print_btn.setStyleSheet("padding: 0; font-size: 13px;")
                print_btn.setToolTip("Imprimer le ticket")
                bid, mid = row["batch_id"], row["mvt_id"]
                print_btn.clicked.connect(
                    lambda _c, b=bid, m=mid: self._print_ticket(b, m)
                )
                w = QWidget()
                w.setStyleSheet("background: transparent;")
                wl = QHBoxLayout(w)
                wl.setContentsMargins(4, 0, 4, 0)
                wl.addWidget(print_btn)
                self._table.setCellWidget(i, 8, w)

        self._summary_lbl.setText(
            f"{len(self._rows)} mouvement(s) affiché(s) sur {self._total_count}"
        )
        self._page_lbl.setText(str(self._current_page))
        self._prev_btn.setEnabled(self._current_page > 1)
        self._next_btn.setEnabled(self._current_page < self._total_pages())

    def _print_ticket(self, batch_id: int, movement_id: int) -> None:
        from app.ui.ticket_dialog import TicketPreviewDialog
        dlg = TicketPreviewDialog(batch_id=batch_id, movement_id=movement_id, parent=self)
        dlg.exec()

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._rows):
            dlg = MovementDetailDialog(self._rows[row], parent=self)
            dlg.exec()


# ──────────────────────────────────────────────────────────────────────────────
# Dialog résolution retour fournisseur
# ──────────────────────────────────────────────────────────────────────────────

class _SupplierResolutionDialog(FramelessDialog):
    """Choisir comment résoudre un retour fournisseur :
    même produit / produit différent / remboursement."""

    def __init__(self, return_row: dict, parent=None) -> None:
        super().__init__(parent)
        self._row = return_row
        self.setWindowTitle("Résolution du retour fournisseur")
        self.setMinimumSize(500, 420)
        self.setModal(True)
        self._mode = "same"          # same | refund
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # Header draggable
        hdr = QFrame()
        hdr.setStyleSheet("background: #1a1a2e;")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(20, 14, 12, 14)
        t = QLabel(f"Résoudre le retour  —  {self._row['ref']}")
        t.setStyleSheet("color: white; font-size: 14px; font-weight: 700;")
        hl.addWidget(t)
        hl.addStretch()
        _close = QPushButton("✕")
        _close.setObjectName("FramelessCloseBtn")
        _close.setFixedSize(38, 38)
        _close.setCursor(Qt.CursorShape.PointingHandCursor)
        _close.clicked.connect(self.reject)
        hl.addWidget(_close)
        self._drag_header = hdr
        hdr.mousePressEvent = self._header_mouse_press
        hdr.mouseMoveEvent = self._header_mouse_move
        hdr.mouseReleaseEvent = self._header_mouse_release
        root.addWidget(hdr)

        body = QWidget()
        body.setStyleSheet("background: white;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 20, 24, 16)
        bl.setSpacing(14)

        # 2 boutons de mode
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self._btn_same = QPushButton("📦  Même produit")
        self._btn_refund = QPushButton("💰  Remboursement")
        for btn in (self._btn_same, self._btn_refund):
            btn.setCheckable(True)
            mode_row.addWidget(btn)
        self._btn_same.setChecked(True)
        self._btn_same.clicked.connect(lambda: self._set_mode("same"))
        self._btn_refund.clicked.connect(lambda: self._set_mode("refund"))
        self._style_mode_btns()
        bl.addLayout(mode_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #e0e0e0;")
        bl.addWidget(sep)

        # Quantité (caché pour remboursement)
        self._qty_section = QWidget()
        qs_l = QVBoxLayout(self._qty_section)
        qs_l.setContentsMargins(0, 0, 0, 0)
        qs_l.setSpacing(6)
        qs_l.addWidget(self._lbl("Quantité"))
        from app.ui.widgets import ModernSpinBox
        self.qty = ModernSpinBox()
        self.qty.setRange(1, self._row["qty_int"])
        self.qty.setValue(self._row["qty_int"])
        self.qty.setFixedWidth(140)
        qs_l.addWidget(self.qty)
        bl.addWidget(self._qty_section)

        # Note (remboursement)
        self._note_section = QWidget()
        ns_l = QVBoxLayout(self._note_section)
        ns_l.setContentsMargins(0, 0, 0, 0)
        ns_l.setSpacing(6)
        ns_l.addWidget(self._lbl("Note (optionnel)"))
        self._note = QLineEdit()
        self._note.setPlaceholderText("Ex: virement reçu, avoir sur prochaine commande...")
        ns_l.addWidget(self._note)
        self._note_section.setVisible(False)
        bl.addWidget(self._note_section)

        self._err = QLabel("")
        self._err.setStyleSheet("color: #dc2626; font-size: 12px;")
        bl.addWidget(self._err)
        bl.addStretch()
        root.addWidget(body, stretch=1)

        # Footer
        footer = QWidget()
        footer.setStyleSheet("background: #f8fafc; border-top: 1px solid #e2e8f0;")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(20, 12, 20, 12)
        cancel = QPushButton("Annuler")
        cancel.setObjectName("SecondaryButton")
        cancel.setFixedWidth(100)
        cancel.clicked.connect(self.reject)
        fl.addWidget(cancel)
        fl.addStretch()
        self._confirm = QPushButton("✔  Confirmer")
        self._confirm.setStyleSheet(
            "QPushButton { background: #00897b; color: white; border: none; border-radius: 6px; "
            "padding: 10px 20px; font-weight: 700; font-size: 13px; }"
            "QPushButton:hover { background: #00695c; }"
        )
        self._confirm.clicked.connect(self._on_confirm)
        fl.addWidget(self._confirm)
        root.addWidget(footer)

    def _lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("font-weight: 700; color: #374151; font-size: 13px;")
        return l

    def _style_mode_btns(self) -> None:
        for btn, active in [
            (self._btn_same, self._mode == "same"),
            (self._btn_refund, self._mode == "refund"),
        ]:
            if active:
                btn.setStyleSheet(
                    "QPushButton { background: #00897b; color: white; border: none; "
                    "border-radius: 6px; padding: 9px 12px; font-weight: 700; font-size: 12px; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #f5f5f5; color: #616161; border: 1px solid #e0e0e0; "
                    "border-radius: 6px; padding: 9px 12px; font-weight: 600; font-size: 12px; }"
                    "QPushButton:hover { background: #e0f2f1; color: #00695c; }"
                )

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._btn_same.setChecked(mode == "same")
        self._btn_refund.setChecked(mode == "refund")
        self._style_mode_btns()
        self._qty_section.setVisible(mode == "same")
        self._note_section.setVisible(mode == "refund")
        self._err.setText("")

    def _on_confirm(self) -> None:
        self._err.setText("")
        row = self._row
        from app.services import StockError
        from PySide6.QtWidgets import QMessageBox
        try:
            with session_scope() as session:
                if self._mode == "same":
                    create_replacement(
                        session,
                        display_id=row["display_id"],
                        quantity=self.qty.value(),
                        supplier_id=row.get("supplier_id"),
                        linked_return_batch_id=row["batch_id"],
                    )
                    msg = f"{self.qty.value()} × {row['ref']} remis en stock."
                else:
                    create_refund_resolution(
                        session,
                        display_id=row["display_id"],
                        linked_return_batch_id=row["batch_id"],
                        supplier_id=row.get("supplier_id"),
                        note=self._note.text().strip(),
                    )
                    msg = "Remboursement enregistré."
            QMessageBox.information(self, "Résolution enregistrée", msg)
            self.accept()
        except StockError as e:
            self._err.setText(str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Onglet Suivi des retours
# ──────────────────────────────────────────────────────────────────────────────

class _ReturnsTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Bouton créer un retour
        btn_row = QHBoxLayout()
        new_return_btn = QPushButton("↩  Déclarer un retour")
        new_return_btn.setStyleSheet(
            "QPushButton { background: #00897b; color: white; border: none; border-radius: 6px; "
            "padding: 9px 18px; font-weight: 700; font-size: 13px; }"
            "QPushButton:hover { background: #00796b; }"
        )
        new_return_btn.clicked.connect(self._on_new_return)
        btn_row.addWidget(new_return_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._filters = _FilterBar([
            ("Tous les retours", "all"),
            ("Reçus (client/revendeur)", "received"),
            ("Envoyés (fournisseur)", "supplier"),
        ])
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.refresh)
        self._filters.search.textChanged.connect(lambda: self._debounce.start())
        self._filters.type_combo.currentIndexChanged.connect(self.refresh)
        self._filters.date_combo.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self._filters)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)

        COLS = ["Date", "Produit", "Désignation", "Type", "Qté", "Contact", "Note", ""]
        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(44)
        self._table.setFrameShape(QFrame.Shape.NoFrame)
        self._table.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f8fdfc; }"
            "QHeaderView::section { background: #00897b; color: white; font-weight: 700; "
            "padding: 8px 10px; border: none; border-right: 1px solid #00796b; font-size: 12px; }"
            "QHeaderView::section:last { border-right: none; }"
            "QTableWidget::item { padding: 4px 10px; }"
            "QTableWidget::item:selected { background: #b2dfdb; color: #212121; }"
            "QTableWidget::item:hover { background: #e0f2f1; }"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 135)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(3, 110)
        self._table.setColumnWidth(4, 50)
        self._table.setColumnWidth(5, 140)
        self._table.setColumnWidth(6, 140)
        self._table.setColumnWidth(7, 140)

        card_layout.addWidget(self._table, stretch=1)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color: #757575; font-size: 12px; padding: 6px 16px;")
        card_layout.addWidget(self._summary)

        layout.addWidget(card, stretch=1)

    def refresh(self) -> None:
        search = self._filters.search.text().strip()
        r_type = self._filters.type_combo.currentData()
        date_from, date_to = self._filters.date_range()

        with session_scope() as session:
            movements = list_returns(
                session,
                search=search,
                date_from=date_from,
                date_to=date_to,
                return_type_filter=r_type,
            )
            # Vérifier les remplacements en une seule session
            rows = []
            for m in movements:
                ref = m.display.reference if m.display else "—"
                desc = f"{m.display.brand} {m.display.phone_model}".strip() if m.display else "—"
                is_received = m.change_quantity > 0
                contact = (m.reseller.name if m.reseller else
                           m.supplier.name if m.supplier else "")
                note = m.reason.split(" — ", 1)[1] if " — " in m.reason else ""
                is_supplier_return = not is_received
                replaced = (
                    has_replacement(session, m.movement_batch_id)
                    if is_supplier_return and m.movement_batch_id else False
                )
                rows.append({
                    "date": _fmt_date(m.created_at),
                    "ref": ref,
                    "desc": desc,
                    "type": "📥 Reçu" if is_received else "📤 Fournisseur",
                    "is_received": is_received,
                    "qty": str(abs(m.change_quantity)),
                    "qty_int": abs(m.change_quantity),
                    "contact": contact,
                    "note": note,
                    "display_id": m.display_id,
                    "supplier_id": m.supplier_id,
                    "batch_id": m.movement_batch_id,
                    "is_supplier_return": is_supplier_return,
                    "replaced": replaced,
                })
        self._rows = rows

        self._table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            self._table.setItem(i, 0, QTableWidgetItem(row["date"]))
            self._table.setItem(i, 1, QTableWidgetItem(row["ref"]))
            self._table.setItem(i, 2, QTableWidgetItem(row["desc"]))

            type_item = QTableWidgetItem(row["type"])
            type_item.setForeground(
                Qt.GlobalColor.darkGreen if row["is_received"] else Qt.GlobalColor.darkRed
            )
            self._table.setItem(i, 3, type_item)
            _right(self._table, i, 4, row["qty"])
            self._table.setItem(i, 5, QTableWidgetItem(row["contact"]))
            self._table.setItem(i, 6, QTableWidgetItem(row["note"]))

            # Colonne 7 : bouton remplacement pour retours fournisseur
            if row["is_supplier_return"] and row.get("display_id"):
                w = QWidget()
                w.setStyleSheet("background: transparent;")
                wl = QHBoxLayout(w)
                wl.setContentsMargins(6, 4, 6, 4)
                if row["replaced"]:
                    done = QPushButton("✓  Remplacé")
                    done.setEnabled(False)
                    done.setStyleSheet(
                        "QPushButton { background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; "
                        "border-radius: 5px; padding: 4px 10px; font-weight: 700; font-size: 11px; }"
                    )
                    wl.addWidget(done)
                else:
                    repl_btn = QPushButton("⚙  Résoudre")
                    repl_btn.setStyleSheet(
                        "QPushButton { background: #e3f2fd; color: #1565c0; border: 1px solid #90caf9; "
                        "border-radius: 5px; padding: 4px 10px; font-weight: 700; font-size: 11px; }"
                        "QPushButton:hover { background: #bbdefb; }"
                    )
                    repl_btn.clicked.connect(
                        lambda _c, r=row: self._on_replacement(r)
                    )
                    wl.addWidget(repl_btn)
                self._table.setCellWidget(i, 7, w)

        n = len(self._rows)
        received = sum(1 for r in self._rows if r["is_received"])
        sent = n - received
        self._summary.setText(
            f"{n} retour(s)  —  {received} reçu(s)  ·  {sent} envoyé(s) fournisseur"
        )

    def _on_replacement(self, row: dict) -> None:
        dlg = _SupplierResolutionDialog(row, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_new_return(self) -> None:
        from app.ui.return_dialog import ReturnDialog
        dlg = ReturnDialog(parent=self)
        if dlg.exec() == ReturnDialog.DialogCode.Accepted:
            self.refresh()


# ──────────────────────────────────────────────────────────────────────────────
# Page Historique principale
# ──────────────────────────────────────────────────────────────────────────────

class HistoriquePage(QWidget):
    """Page Historique avec deux onglets : Suivi des ventes + Mouvements."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tabs = QTabWidget()
        tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: #f5f5f5; }"
            "QTabBar::tab { background: #eeeeee; color: #616161; border: 1px solid #e0e0e0; "
            "border-bottom: none; padding: 10px 22px; border-radius: 6px 6px 0 0; font-weight: 600; margin-right: 2px; }"
            "QTabBar::tab:hover { background: #e0f2f1; color: #00695c; }"
            "QTabBar::tab:selected { background: #00897b; color: white; border-color: #00897b; }"
        )

        self._sales_tab = _SalesTab()
        tabs.addTab(self._sales_tab, "🛒  Suivi des ventes")

        self._movements_tab = _MovementsTab()
        tabs.addTab(self._movements_tab, "📦  Mouvements de stock")

        self._returns_tab = _ReturnsTab()
        tabs.addTab(self._returns_tab, "↩  Retours")

        # Rafraîchir l'onglet quand l'utilisateur clique dessus
        self._tab_widgets = [self._sales_tab, self._movements_tab, self._returns_tab]
        tabs.currentChanged.connect(
            lambda idx: self._tab_widgets[idx].refresh()
            if 0 <= idx < len(self._tab_widgets) else None
        )

        layout.addWidget(tabs)

    def refresh(self) -> None:
        self._sales_tab.refresh()
        self._movements_tab.refresh()
        self._returns_tab.refresh()
