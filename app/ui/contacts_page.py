"""Page générique Revendeurs / Fournisseurs — liste + CRUD + historique des mouvements."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import format_balance, format_balance_ticket, format_da
from app.ui.frameless_dialog import FramelessDialog
from app.services import (
    StockError,
    create_reseller,
    create_supplier,
    deactivate_reseller,
    deactivate_supplier,
    list_movements,
    list_resellers,
    list_reseller_payments,
    list_suppliers,
    record_reseller_payment,
    update_reseller,
    update_supplier,
)

ContactMode = Literal["resellers", "suppliers"]


def _balance_color(cents: int) -> str:
    if cents > 0:
        return "#dc2626"   # rouge — doit de l'argent
    if cents < 0:
        return "#2563eb"   # bleu — on lui doit
    return "#16a34a"       # vert — soldé


class ContactsPage(QWidget):
    """Page de gestion des revendeurs ou des fournisseurs."""

    stock_changed = Signal()

    def __init__(self, mode: ContactMode) -> None:
        super().__init__()
        self._mode = mode
        self._contacts: list = []
        self._selected_id: int | None = None
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(0)

        # En-tête
        header_row = QHBoxLayout()
        title_text = "Revendeurs" if self._mode == "resellers" else "Fournisseurs"
        title = QLabel(title_text)
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        header_row.addWidget(title)
        header_row.addStretch()
        noun = "revendeur" if self._mode == "resellers" else "fournisseur"
        add_btn = QPushButton(f"+ Nouveau {noun}")
        add_btn.clicked.connect(self._on_add)
        header_row.addWidget(add_btn)
        layout.addLayout(header_row)
        layout.addSpacing(6)

        hint = QLabel("Sélectionnez un contact pour voir son historique.")
        hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(hint)
        layout.addSpacing(14)

        # Splitter vertical
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # --- Tableau des contacts ---
        list_card = QFrame()
        list_card.setObjectName("Card")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        if self._mode == "resellers":
            col_headers = ["Nom", "Téléphone", "Adresse", "Solde", ""]
            ncols = 5
        else:
            col_headers = ["Nom", "Téléphone", "Adresse", ""]
            ncols = 4

        self.contacts_table = QTableWidget(0, ncols)
        self.contacts_table.setHorizontalHeaderLabels(col_headers)
        self.contacts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.contacts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.contacts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.contacts_table.setAlternatingRowColors(True)
        self.contacts_table.setShowGrid(False)
        self.contacts_table.verticalHeader().setVisible(False)
        self.contacts_table.verticalHeader().setDefaultSectionSize(40)
        self.contacts_table.setFrameShape(QFrame.Shape.NoFrame)
        h = self.contacts_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if self._mode == "resellers":
            h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            self.contacts_table.setColumnWidth(4, 140)
        else:
            h.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self.contacts_table.setColumnWidth(3, 80)

        self.contacts_table.itemSelectionChanged.connect(self._on_selection_changed)
        list_layout.addWidget(self.contacts_table)

        self._empty_label = QLabel(
            "Aucun contact enregistré.\nCliquez sur « Nouveau » pour en ajouter un."
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #94a3b8; font-size: 13px; padding: 24px;")
        self._empty_label.setVisible(False)
        list_layout.addWidget(self._empty_label)
        splitter.addWidget(list_card)

        # --- Panneau de détail ---
        self._detail_panel = _DetailPanel(mode=self._mode)
        splitter.addWidget(self._detail_panel)
        splitter.setSizes([300, 300])

        layout.addWidget(splitter, stretch=1)

    # ------------------------------------------------------------------
    # Données
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        with session_scope() as session:
            if self._mode == "resellers":
                self._contacts = list_resellers(session)
            else:
                self._contacts = list_suppliers(session)

        has = bool(self._contacts)
        self.contacts_table.setVisible(has)
        self._empty_label.setVisible(not has)
        self.contacts_table.setRowCount(len(self._contacts))

        for i, c in enumerate(self._contacts):
            self.contacts_table.setItem(i, 0, QTableWidgetItem(c.name))
            self.contacts_table.setItem(i, 1, QTableWidgetItem(c.phone or ""))
            self.contacts_table.setItem(i, 2, QTableWidgetItem(c.address or ""))

            if self._mode == "resellers":
                balance = c.balance_cents
                bal_item = QTableWidgetItem(format_balance(balance))
                bal_item.setForeground(Qt.GlobalColor.black)
                from PySide6.QtGui import QColor
                bal_item.setForeground(QColor(_balance_color(balance)))
                bal_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.contacts_table.setItem(i, 3, bal_item)

                actions = _reseller_action_cell(c.id, c.balance_cents,
                                                edit_cb=self._on_edit,
                                                delete_cb=self._on_delete,
                                                versement_cb=self._on_versement)
                self.contacts_table.setCellWidget(i, 4, actions)
            else:
                actions = _supplier_action_cell(c.id,
                                                edit_cb=self._on_edit,
                                                delete_cb=self._on_delete)
                self.contacts_table.setCellWidget(i, 3, actions)

        if self._selected_id is not None:
            self._load_detail(self._selected_id)

    def _on_selection_changed(self) -> None:
        rows = self.contacts_table.selectionModel().selectedRows()
        if not rows:
            self._selected_id = None
            self._detail_panel.clear()
            return
        row = rows[0].row()
        if row < len(self._contacts):
            contact = self._contacts[row]
            self._selected_id = contact.id
            self._load_detail(contact.id, contact.name)

    def _load_detail(self, contact_id: int, contact_name: str = "") -> None:
        with session_scope() as session:
            if self._mode == "resellers":
                movements = list_movements(session, reseller_id=contact_id)
                payments = list_reseller_payments(session, contact_id)
                # Solde actuel
                reseller = next((c for c in self._contacts if c.id == contact_id), None)
                balance = reseller.balance_cents if reseller else 0
            else:
                movements = list_movements(session, supplier_id=contact_id)
                payments = []
                balance = 0

            mvt_rows = []
            for m in movements:
                is_entry = m.change_quantity > 0
                unit_price = m.unit_purchase_price_cents if is_entry else m.unit_sale_price_cents
                qty = abs(m.change_quantity)
                mvt_rows.append((
                    m.created_at.strftime("%d/%m/%Y %H:%M"),
                    m.display.reference if m.display else "—",
                    "Entrée" if is_entry else "Sortie",
                    qty,
                    format_da(unit_price),
                    format_da(unit_price * qty),
                    m.movement_batch_id,
                    m.id,
                    m.is_sale,
                ))

            pay_rows = []
            for p in payments:
                pay_rows.append((
                    p.created_at.strftime("%d/%m/%Y %H:%M"),
                    format_da(p.amount_cents),
                    format_balance(p.balance_before_cents),
                    format_balance(p.balance_after_cents),
                    p.note or "—",
                    p.cashier_name or "—",
                ))

        self._detail_panel.load(
            contact_name, mvt_rows, pay_rows, balance,
            refresh_cb=lambda: self._load_detail(contact_id, contact_name),
            global_refresh_cb=self.stock_changed.emit,
        )

    # ------------------------------------------------------------------
    # Actions CRUD
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        dialog = _ContactDialog(self._mode, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                with session_scope() as session:
                    if self._mode == "resellers":
                        create_reseller(session, name=dialog.field_name, phone=dialog.field_phone,
                                        address=dialog.field_address, notes=dialog.field_notes)
                    else:
                        create_supplier(session, name=dialog.field_name, phone=dialog.field_phone,
                                        address=dialog.field_address, notes=dialog.field_notes)
                self.refresh()
            except StockError as e:
                QMessageBox.warning(self, "Erreur", str(e))

    def _on_edit(self, contact_id: int) -> None:
        contact = next((c for c in self._contacts if c.id == contact_id), None)
        if contact is None:
            return
        dialog = _ContactDialog(self._mode, contact=contact, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                with session_scope() as session:
                    if self._mode == "resellers":
                        update_reseller(session, contact_id, name=dialog.field_name,
                                        phone=dialog.field_phone, address=dialog.field_address,
                                        notes=dialog.field_notes)
                    else:
                        update_supplier(session, contact_id, name=dialog.field_name,
                                        phone=dialog.field_phone, address=dialog.field_address,
                                        notes=dialog.field_notes)
                self.refresh()
            except StockError as e:
                QMessageBox.warning(self, "Erreur", str(e))

    def _on_delete(self, contact_id: int) -> None:
        confirm = QMessageBox.question(
            self, "Supprimer le contact",
            "Ce contact sera retiré de la liste. L'historique des mouvements reste intact.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        with session_scope() as session:
            if self._mode == "resellers":
                deactivate_reseller(session, contact_id)
            else:
                deactivate_supplier(session, contact_id)
        if self._selected_id == contact_id:
            self._selected_id = None
            self._detail_panel.clear()
        self.refresh()

    def _on_versement(self, reseller_id: int) -> None:
        contact = next((c for c in self._contacts if c.id == reseller_id), None)
        if contact is None:
            return
        dlg = _VersementDialog(contact.name, contact.balance_cents, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                with session_scope() as session:
                    record_reseller_payment(session, reseller_id, dlg.amount_cents, dlg.note)
                self.refresh()
            except StockError as e:
                QMessageBox.warning(self, "Erreur", str(e))


# ---------------------------------------------------------------------------
# Helpers cellules d'actions
# ---------------------------------------------------------------------------

def _reseller_action_cell(contact_id, balance_cents, *, edit_cb, delete_cb, versement_cb) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(w)
    layout.setContentsMargins(4, 2, 4, 2)
    layout.setSpacing(4)

    pay_btn = QPushButton("💰 Versement")
    pay_btn.setFixedHeight(26)
    has_balance = balance_cents > 0
    pay_btn.setStyleSheet(
        f"QPushButton {{ background: {'#dc2626' if has_balance else '#e2e8f0'}; "
        f"color: {'white' if has_balance else '#64748b'}; border: none; border-radius: 4px; "
        f"padding: 0 8px; font-size: 11px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {'#b91c1c' if has_balance else '#cbd5e1'}; }}"
    )
    pay_btn.clicked.connect(lambda _c, cid=contact_id: versement_cb(cid))
    layout.addWidget(pay_btn)

    edit_btn = QPushButton("✏")
    edit_btn.setObjectName("IconButton")
    edit_btn.setFixedSize(28, 28)
    edit_btn.setStyleSheet("padding:0; font-size:13px;")
    edit_btn.clicked.connect(lambda _c, cid=contact_id: edit_cb(cid))
    layout.addWidget(edit_btn)

    del_btn = QPushButton("×")
    del_btn.setObjectName("IconButton")
    del_btn.setFixedSize(28, 28)
    del_btn.setStyleSheet("padding:0; font-size:15px; color:#dc2626; font-weight:700;")
    del_btn.clicked.connect(lambda _c, cid=contact_id: delete_cb(cid))
    layout.addWidget(del_btn)

    return w


def _supplier_action_cell(contact_id, *, edit_cb, delete_cb) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(w)
    layout.setContentsMargins(4, 2, 4, 2)
    layout.setSpacing(4)

    edit_btn = QPushButton("✏")
    edit_btn.setObjectName("IconButton")
    edit_btn.setFixedSize(28, 28)
    edit_btn.setStyleSheet("padding:0; font-size:13px;")
    edit_btn.clicked.connect(lambda _c, cid=contact_id: edit_cb(cid))
    layout.addWidget(edit_btn)

    del_btn = QPushButton("×")
    del_btn.setObjectName("IconButton")
    del_btn.setFixedSize(28, 28)
    del_btn.setStyleSheet("padding:0; font-size:15px; color:#dc2626; font-weight:700;")
    del_btn.clicked.connect(lambda _c, cid=contact_id: delete_cb(cid))
    layout.addWidget(del_btn)

    return w


# ---------------------------------------------------------------------------
# Panneau de détail (mouvements + versements)
# ---------------------------------------------------------------------------

class _DetailPanel(QWidget):
    def __init__(self, mode: ContactMode = "resellers") -> None:
        super().__init__()
        self._mode = mode
        self._refresh_cb = None
        self._global_refresh_cb = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # Header avec nom + solde
        header_row = QHBoxLayout()
        header_row.setContentsMargins(16, 10, 16, 10)
        self._header = QLabel("Sélectionnez un contact pour voir ses mouvements")
        self._header.setStyleSheet("font-size: 13px; font-weight: 600; color: #374151;")
        header_row.addWidget(self._header)
        header_row.addStretch()
        if mode == "resellers":
            self._balance_badge = QLabel("")
            self._balance_badge.setStyleSheet(
                "font-size: 13px; font-weight: 700; padding: 3px 10px; "
                "border-radius: 10px; background: #f1f5f9; color: #374151;"
            )
            header_row.addWidget(self._balance_badge)
        card_layout.addLayout(header_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #f1f5f9; border: none; max-height: 1px;")
        card_layout.addWidget(sep)

        # Tabs
        if mode == "resellers":
            self._tabs = QTabWidget()
            self._tabs.setStyleSheet(
                "QTabWidget::pane { border: none; }"
                "QTabBar::tab { padding: 6px 16px; font-size: 12px; font-weight: 600; }"
                "QTabBar::tab:selected { color: #2563eb; border-bottom: 2px solid #2563eb; }"
            )
            self._mvt_tab = self._build_movements_table()
            self._pay_tab = self._build_payments_table()
            self._tabs.addTab(self._mvt_tab, "Mouvements stock")
            self._tabs.addTab(self._pay_tab, "Versements")
            card_layout.addWidget(self._tabs, stretch=1)
        else:
            self._mvt_tab = self._build_movements_table()
            card_layout.addWidget(self._mvt_tab, stretch=1)

        layout.addWidget(card)

    def _build_movements_table(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(["Date", "Pièce", "Type", "Qté", "Prix unit.", "Total", "", ""])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setFrameShape(QFrame.Shape.NoFrame)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._table.setColumnWidth(6, 36)
        self._table.setColumnWidth(7, 36)

        self._mvt_empty = QLabel("Aucun mouvement pour ce contact.")
        self._mvt_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mvt_empty.setStyleSheet("color: #9ca3af; font-size: 12px; padding: 20px;")
        self._mvt_empty.setVisible(False)

        vl.addWidget(self._table)
        vl.addWidget(self._mvt_empty)
        return w

    def _build_payments_table(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._pay_table = QTableWidget(0, 6)
        self._pay_table.setHorizontalHeaderLabels(
            ["Date", "Montant versé", "Solde avant", "Solde après", "Note", "Caissier"]
        )
        self._pay_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pay_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._pay_table.setAlternatingRowColors(True)
        self._pay_table.setShowGrid(False)
        self._pay_table.verticalHeader().setVisible(False)
        self._pay_table.verticalHeader().setDefaultSectionSize(36)
        self._pay_table.setFrameShape(QFrame.Shape.NoFrame)
        ph = self._pay_table.horizontalHeader()
        ph.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        ph.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        ph.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        ph.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        ph.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        ph.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        ph.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._pay_empty = QLabel("Aucun versement enregistré.")
        self._pay_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pay_empty.setStyleSheet("color: #9ca3af; font-size: 12px; padding: 20px;")
        self._pay_empty.setVisible(False)

        vl.addWidget(self._pay_table)
        vl.addWidget(self._pay_empty)
        return w

    def load(self, contact_name: str, mvt_rows: list, pay_rows: list,
             balance: int = 0, refresh_cb=None, global_refresh_cb=None) -> None:
        self._refresh_cb = refresh_cb
        self._global_refresh_cb = global_refresh_cb
        self._header.setText(f"Historique — {contact_name}  ({len(mvt_rows)} mouvement(s))")

        if self._mode == "resellers" and hasattr(self, "_balance_badge"):
            color = _balance_color(balance)
            self._balance_badge.setText(f"Solde : {format_balance(balance)}")
            self._balance_badge.setStyleSheet(
                f"font-size: 13px; font-weight: 700; padding: 3px 10px; "
                f"border-radius: 10px; background: #f1f5f9; color: {color};"
            )

        # Mouvements
        self._table.setRowCount(len(mvt_rows))
        has_mvt = bool(mvt_rows)
        self._table.setVisible(has_mvt)
        self._mvt_empty.setVisible(not has_mvt)
        for i, (date, ref, type_, qty, price, total, batch_id, movement_id, is_sale) in enumerate(mvt_rows):
            self._table.setItem(i, 0, QTableWidgetItem(date))
            self._table.setItem(i, 1, QTableWidgetItem(ref))
            type_item = QTableWidgetItem(type_)
            type_item.setForeground(Qt.GlobalColor.darkGreen if type_ == "Entrée" else Qt.GlobalColor.red)
            self._table.setItem(i, 2, type_item)
            self._table.setItem(i, 3, QTableWidgetItem(str(qty)))
            self._table.setItem(i, 4, QTableWidgetItem(price))
            total_item = QTableWidgetItem(total)
            total_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 5, total_item)

            edit_btn = QPushButton("✏")
            edit_btn.setObjectName("IconButton")
            edit_btn.setFixedSize(28, 28)
            edit_btn.setStyleSheet("padding: 0; font-size: 12px;")
            edit_btn.clicked.connect(lambda _c, bid=batch_id, mid=movement_id: self._on_edit(bid, mid))
            self._table.setCellWidget(i, 6, edit_btn)

            if is_sale:
                print_btn = QPushButton("🖨")
                print_btn.setObjectName("IconButton")
                print_btn.setFixedSize(28, 28)
                print_btn.setStyleSheet("padding: 0; font-size: 13px;")
                print_btn.clicked.connect(
                    lambda _c, bid=batch_id, mid=movement_id: self._on_print(bid, mid)
                )
                self._table.setCellWidget(i, 7, print_btn)

        # Versements (mode revendeurs uniquement)
        if self._mode == "resellers":
            self._pay_table.setRowCount(len(pay_rows))
            has_pay = bool(pay_rows)
            self._pay_table.setVisible(has_pay)
            self._pay_empty.setVisible(not has_pay)
            for i, (date, amount, bal_before, bal_after, note, cashier) in enumerate(pay_rows):
                self._pay_table.setItem(i, 0, QTableWidgetItem(date))
                amt_item = QTableWidgetItem(amount)
                amt_item.setForeground(Qt.GlobalColor.darkGreen)
                amt_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._pay_table.setItem(i, 1, amt_item)
                self._pay_table.setItem(i, 2, QTableWidgetItem(bal_before))
                self._pay_table.setItem(i, 3, QTableWidgetItem(bal_after))
                self._pay_table.setItem(i, 4, QTableWidgetItem(note))
                self._pay_table.setItem(i, 5, QTableWidgetItem(cashier))

            if pay_rows:
                self._tabs.setTabText(1, f"Versements ({len(pay_rows)})")
            else:
                self._tabs.setTabText(1, "Versements")

    def _on_print(self, batch_id, movement_id) -> None:
        from app.ui.ticket_dialog import TicketPreviewDialog
        dlg = TicketPreviewDialog(batch_id=batch_id, movement_id=movement_id, parent=self)
        dlg.exec()

    def _on_edit(self, batch_id, movement_id) -> None:
        from app.ui.movement_edit_dialog import MovementEditDialog
        dlg = MovementEditDialog(batch_id=batch_id, movement_id=movement_id, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._refresh_cb:
                self._refresh_cb()
            if self._global_refresh_cb:
                self._global_refresh_cb()

    def clear(self) -> None:
        self._refresh_cb = None
        self._header.setText("Sélectionnez un contact pour voir ses mouvements")
        if self._mode == "resellers" and hasattr(self, "_balance_badge"):
            self._balance_badge.setText("")
        self._table.setRowCount(0)
        self._table.setVisible(True)
        self._mvt_empty.setVisible(False)
        if self._mode == "resellers":
            self._pay_table.setRowCount(0)
            self._pay_table.setVisible(True)
            self._pay_empty.setVisible(False)


# ---------------------------------------------------------------------------
# Dialog Versement
# ---------------------------------------------------------------------------

class _VersementDialog(FramelessDialog):
    def __init__(self, reseller_name: str, balance_cents: int, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(380)
        self.amount_cents = 0
        self.note = ""

        from app.money import cents_to_da, da_to_cents
        from app.ui.widgets import ModernDoubleSpinBox

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(
            f"💰  Versement — {reseller_name}",
            gradient="qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1a1a2e,stop:1 #1e3a5f)",
        ))

        body = QWidget()
        body.setStyleSheet("background: white;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        root.addWidget(body)

        # Solde actuel
        color = _balance_color(balance_cents)
        solde_lbl = QLabel(f"Solde actuel : <b style='color:{color};'>{format_balance(balance_cents)}</b>")
        solde_lbl.setStyleSheet("font-size: 13px; color: #374151;")
        layout.addWidget(solde_lbl)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._amount_spin = ModernDoubleSpinBox()
        self._amount_spin.setRange(1, 999_999_999)
        self._amount_spin.setDecimals(0)
        self._amount_spin.setSuffix(" DA")
        self._amount_spin.setFixedHeight(36)
        if balance_cents > 0:
            self._amount_spin.setValue(cents_to_da(balance_cents))
        form.addRow("Montant *", self._amount_spin)

        self._note_input = QLineEdit()
        self._note_input.setPlaceholderText("Ex : paiement espèces, chèque n°... (optionnel)")
        form.addRow("Note", self._note_input)

        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setObjectName("ErrorLabel")
        layout.addWidget(self._error)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(8)
        btns_row.addStretch()
        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setFixedHeight(40)
        cancel_btn.clicked.connect(self.reject)
        btns_row.addWidget(cancel_btn)
        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedHeight(40)
        save_btn.clicked.connect(self._accept)
        btns_row.addWidget(save_btn)
        layout.addLayout(btns_row)

        self._da_to_cents = da_to_cents

    def _accept(self) -> None:
        amount = self._da_to_cents(self._amount_spin.value())
        if amount <= 0:
            self._error.setText("Le montant doit être supérieur à 0.")
            return
        self.amount_cents = amount
        self.note = self._note_input.text().strip()
        self.accept()


# ---------------------------------------------------------------------------
# Dialog Ajouter / Modifier contact
# ---------------------------------------------------------------------------

class _ContactDialog(FramelessDialog):
    def __init__(self, mode: ContactMode, contact=None, parent=None) -> None:
        super().__init__(parent)
        is_reseller = mode == "resellers"
        noun = "revendeur" if is_reseller else "fournisseur"
        title = f"{'Modifier' if contact else 'Nouveau'} {noun}"
        self.setModal(True)
        self.setMinimumWidth(400)

        self.field_name = contact.name if contact else ""
        self.field_phone = contact.phone if contact else ""
        self.field_address = contact.address if contact else ""
        self.field_notes = contact.notes if contact else ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(title))
        _body = QWidget()
        _body.setStyleSheet("background: white;")
        layout = QVBoxLayout(_body)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(10)
        root.addWidget(_body)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name = QLineEdit(self.field_name)
        self._name.setPlaceholderText("Nom complet ou raison sociale")
        form.addRow("Nom *", self._name)

        self._phone = QLineEdit(self.field_phone)
        self._phone.setPlaceholderText("Ex : 0555 12 34 56")
        form.addRow("Téléphone", self._phone)

        self._address = QLineEdit(self.field_address)
        self._address.setPlaceholderText("Ville ou adresse")
        form.addRow("Adresse", self._address)

        self._notes = QLineEdit(self.field_notes)
        self._notes.setPlaceholderText("Notes internes (optionnel)")
        form.addRow("Notes", self._notes)

        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setObjectName("ErrorLabel")
        layout.addWidget(self._error)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(8)
        btns_row.addStretch()
        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setFixedHeight(40)
        cancel_btn.clicked.connect(self.reject)
        btns_row.addWidget(cancel_btn)
        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedHeight(40)
        save_btn.clicked.connect(self._accept)
        btns_row.addWidget(save_btn)
        layout.addLayout(btns_row)

    def _accept(self) -> None:
        name = self._name.text().strip()
        if not name:
            self._error.setText("Le nom est obligatoire.")
            return
        self.field_name = name
        self.field_phone = self._phone.text().strip()
        self.field_address = self._address.text().strip()
        self.field_notes = self._notes.text().strip()
        self.accept()
