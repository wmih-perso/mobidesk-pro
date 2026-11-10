"""Page générique Revendeurs / Fournisseurs — liste + CRUD + historique des mouvements."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import session as _session
from app.database import session_scope
from app.labels import article_label
from app.money import format_balance, format_balance_ticket, format_da, format_supplier_balance
from app.search import filter_and_rank, matches
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ALIGN_RIGHT, right_align_headers
from app.services import (
    StockError,
    create_reseller,
    create_supplier,
    deactivate_reseller,
    deactivate_supplier,
    list_movements,
    list_resellers,
    list_reseller_payments,
    list_supplier_payments,
    list_suppliers,
    record_reseller_payment,
    record_supplier_payment,
    set_supplier_balance,
    update_reseller,
    update_supplier,
)

ContactMode = Literal["resellers", "suppliers"]


def _format_for(mode: ContactMode):
    """Revendeur : « - 500 DA » = il nous doit. Fournisseur : « 500 DA à payer »."""
    return format_balance if mode == "resellers" else format_supplier_balance


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
        self._contacts: list = []  # liste affichée (filtrée par la recherche)
        self._all_contacts: list = []
        self._detail_id: int | None = None  # contact dont l'historique est ouvert
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Deux vues dans la même page : la liste des contacts, puis
        l'historique d'un contact (double-clic), avec « ← Retour »."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        list_page = QWidget()
        layout = QVBoxLayout(list_page)
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

        hint = QLabel("Double-cliquez sur un contact pour ouvrir son historique.")
        hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(hint)
        layout.addSpacing(10)

        search_row = QHBoxLayout()
        self._contact_search = QLineEdit()
        self._contact_search.setPlaceholderText(f"🔍  Rechercher un {noun} (nom, téléphone, adresse)…")
        self._contact_search.setClearButtonEnabled(True)
        self._contact_search.setMinimumHeight(36)
        self._contact_search.setMaximumWidth(520)
        self._contact_search.textChanged.connect(self._render_contacts)
        search_row.addWidget(self._contact_search)
        self._contact_count = QLabel("")
        self._contact_count.setStyleSheet("color: #64748b; font-size: 12px; padding-left: 10px;")
        search_row.addWidget(self._contact_count)
        search_row.addStretch()
        layout.addLayout(search_row)
        layout.addSpacing(10)

        # --- Tableau des contacts ---
        list_card = QFrame()
        list_card.setObjectName("Card")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        balance_header = "Solde" if self._mode == "resellers" else "Dette"
        col_headers = ["Nom", "Téléphone", "Adresse", balance_header, ""]

        self.contacts_table = QTableWidget(0, len(col_headers))
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

        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.contacts_table.setColumnWidth(
            4, 210 if self._mode == "suppliers" and _session.is_admin() else 175
        )

        self.contacts_table.cellDoubleClicked.connect(self._on_row_double_clicked)
        list_layout.addWidget(self.contacts_table)

        self._empty_label = QLabel(
            "Aucun contact enregistré.\nCliquez sur « Nouveau » pour en ajouter un."
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #94a3b8; font-size: 13px; padding: 24px;")
        self._empty_label.setVisible(False)
        list_layout.addWidget(self._empty_label)
        layout.addWidget(list_card, stretch=1)
        self._stack.addWidget(list_page)

        # --- Page historique d'un contact ---
        detail_page = QWidget()
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.setContentsMargins(24, 16, 24, 24)
        detail_layout.setSpacing(8)
        top_row = QHBoxLayout()
        back_btn = QPushButton(f"←  Retour aux {title_text.lower()}")
        back_btn.setObjectName("SecondaryButton")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self._show_list)
        top_row.addWidget(back_btn)
        top_row.addStretch()
        pay_btn = QPushButton("💰  Versement")
        pay_btn.clicked.connect(lambda: self._detail_id and self._on_versement(self._detail_id))
        top_row.addWidget(pay_btn)
        if self._mode == "suppliers" and _session.is_admin():
            adjust_btn = QPushButton("⚖  Ajuster le solde")
            adjust_btn.setObjectName("SecondaryButton")
            adjust_btn.clicked.connect(lambda: self._detail_id and self._on_adjust(self._detail_id))
            top_row.addWidget(adjust_btn)
        detail_layout.addLayout(top_row)
        self._detail_panel = _DetailPanel(mode=self._mode)
        detail_layout.addWidget(self._detail_panel, stretch=1)
        self._stack.addWidget(detail_page)
        back_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), detail_page)
        back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        back_shortcut.activated.connect(self._show_list)

    # ------------------------------------------------------------------
    # Données
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        with session_scope() as session:
            if self._mode == "resellers":
                self._all_contacts = list_resellers(session)
            else:
                self._all_contacts = list_suppliers(session)
        self._render_contacts()
        if self._detail_id is not None:
            if self._contact(self._detail_id) is None:  # supprimé entre-temps
                self._show_list()
            else:
                self._load_detail(self._detail_id)

    def _contact(self, contact_id: int):
        return next((c for c in self._all_contacts if c.id == contact_id), None)

    def _render_contacts(self) -> None:
        """Liste des contacts, filtrée par le champ de recherche du haut."""
        query = self._contact_search.text()
        self._contacts = filter_and_rank(
            query, self._all_contacts, lambda c: (c.name, c.phone or "", c.address or "")
        )
        total = len(self._all_contacts)
        self._contact_count.setText(
            f"{len(self._contacts)} sur {total}" if query.strip() else f"{total} au total"
        )

        has = bool(self._contacts)
        self.contacts_table.setVisible(has)
        self._empty_label.setVisible(not has)
        self._empty_label.setText(
            "Aucun contact ne correspond à la recherche." if self._all_contacts
            else "Aucun contact enregistré.\nCliquez sur « Nouveau » pour en ajouter un."
        )
        self.contacts_table.blockSignals(True)
        self.contacts_table.setRowCount(0)
        self.contacts_table.setRowCount(len(self._contacts))

        for i, c in enumerate(self._contacts):
            self.contacts_table.setItem(i, 0, QTableWidgetItem(c.name))
            self.contacts_table.setItem(i, 1, QTableWidgetItem(c.phone or ""))
            self.contacts_table.setItem(i, 2, QTableWidgetItem(c.address or ""))

            from PySide6.QtGui import QColor
            balance = c.balance_cents
            bal_item = QTableWidgetItem(_format_for(self._mode)(balance))
            bal_item.setForeground(QColor(_balance_color(balance)))
            bal_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.contacts_table.setItem(i, 3, bal_item)

            adjust_cb = (
                self._on_adjust if self._mode == "suppliers" and _session.is_admin() else None
            )
            actions = _contact_action_cell(c.id, c.balance_cents,
                                           edit_cb=self._on_edit,
                                           delete_cb=self._on_delete,
                                           versement_cb=self._on_versement,
                                           adjust_cb=adjust_cb)
            self.contacts_table.setCellWidget(i, 4, actions)
            if c.id == self._detail_id:
                self.contacts_table.selectRow(i)
        self.contacts_table.blockSignals(False)

    def _on_row_double_clicked(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._contacts):
            self._open_contact(self._contacts[row].id)

    def _open_contact(self, contact_id: int) -> None:
        """Ouvre la page historique du contact (à la place de la liste)."""
        self._detail_id = contact_id
        self._load_detail(contact_id, new_contact=True)
        self._stack.setCurrentIndex(1)

    def _show_list(self) -> None:
        """← Retour : la liste, avec sa recherche et sa sélection."""
        self._stack.setCurrentIndex(0)
        self.contacts_table.setFocus()

    def _load_detail(self, contact_id: int, new_contact: bool = False) -> None:
        contact = self._contact(contact_id)
        balance = contact.balance_cents if contact else 0
        contact_name = contact.name if contact else ""
        if contact is not None and contact.phone:
            contact_name += f"      ☎ {contact.phone}"
        with session_scope() as session:
            if self._mode == "resellers":
                movements = list_movements(session, reseller_id=contact_id)
                payments = list_reseller_payments(session, contact_id)
            else:
                movements = list_movements(session, supplier_id=contact_id)
                payments = list_supplier_payments(session, contact_id)

            mvt_rows = []
            for m in movements:
                is_entry = m.change_quantity > 0
                unit_price = (m.unit_purchase_price_cents if is_entry else m.unit_sale_price_cents) or 0
                qty = abs(m.change_quantity)
                date = m.created_at.strftime("%d/%m/%Y %H:%M")
                d = m.display
                ref = d.reference if d else "—"
                product = article_label(d.category, d.phone_model, d.reference, m.model_label) if d else "—"
                # Texte de recherche : date, référence, produit (catégorie,
                # marque, modèle) et anciennes références fusionnées.
                search_text = " ".join(
                    [date, ref, product]
                    + ([d.category, d.brand, d.phone_model, d.aliases or ""] if d else [])
                )
                mvt_rows.append((
                    date,
                    ref,
                    product,
                    "Entrée" if is_entry else "Sortie",
                    qty,
                    format_da(unit_price),
                    format_da(unit_price * qty),
                    m.movement_batch_id,
                    m.id,
                    m.is_sale,
                    search_text,
                ))

            fmt = _format_for(self._mode)
            pay_rows = []
            for p in payments:
                is_adjust = getattr(p, "kind", "payment") == "adjust"
                pay_rows.append((
                    p.created_at.strftime("%d/%m/%Y %H:%M"),
                    "Ajustement" if is_adjust else format_da(p.amount_cents),
                    fmt(p.balance_before_cents),
                    fmt(p.balance_after_cents),
                    p.note or "—",
                    p.cashier_name or "—",
                ))

        self._detail_panel.load(
            contact_name, mvt_rows, pay_rows, balance,
            refresh_cb=lambda: self._load_detail(contact_id),
            global_refresh_cb=self.stock_changed.emit,
            new_contact=new_contact,
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
        contact = self._contact(contact_id)
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
        if self._detail_id == contact_id:
            self._detail_id = None
            self._detail_panel.clear()
            self._show_list()
        self.refresh()

    def _on_versement(self, contact_id: int) -> None:
        contact = self._contact(contact_id)
        if contact is None:
            return
        dlg = _VersementDialog(contact.name, contact.balance_cents, mode=self._mode, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                with session_scope() as session:
                    if self._mode == "resellers":
                        record_reseller_payment(session, contact_id, dlg.amount_cents, dlg.note)
                    else:
                        record_supplier_payment(session, contact_id, dlg.amount_cents, dlg.note)
                self.refresh()
            except StockError as e:
                QMessageBox.warning(self, "Erreur", str(e))

    def _on_adjust(self, supplier_id: int) -> None:
        contact = self._contact(supplier_id)
        if contact is None:
            return
        dlg = _VersementDialog(
            contact.name, contact.balance_cents, mode=self._mode, adjust=True, parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                with session_scope() as session:
                    set_supplier_balance(session, supplier_id, dlg.amount_cents, dlg.note)
                self.refresh()
            except StockError as e:
                QMessageBox.warning(self, "Erreur", str(e))


# ---------------------------------------------------------------------------
# Helpers cellules d'actions
# ---------------------------------------------------------------------------

def _contact_action_cell(
    contact_id, balance_cents, *, edit_cb, delete_cb, versement_cb, adjust_cb=None
) -> QWidget:
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

    if adjust_cb is not None:
        adjust_btn = QPushButton("⚖")
        adjust_btn.setObjectName("IconButton")
        adjust_btn.setFixedSize(28, 28)
        adjust_btn.setToolTip("Ajuster le solde (corriger ce qu'on doit au fournisseur)")
        adjust_btn.setStyleSheet("padding:0; font-size:13px;")
        adjust_btn.clicked.connect(lambda _c, cid=contact_id: adjust_cb(cid))
        layout.addWidget(adjust_btn)

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

PAGE_SIZE = 25


def _search_box(placeholder: str, on_change) -> QLineEdit:
    box = QLineEdit()
    box.setPlaceholderText(placeholder)
    box.setClearButtonEnabled(True)
    box.setMinimumHeight(32)
    box.textChanged.connect(on_change)
    return box


class _Pager(QWidget):
    """◀ Page 1 / 3 ▶ + compteur, sous un tableau."""

    def __init__(self, on_page) -> None:
        super().__init__()
        self._on_page = on_page
        self.page = 1
        self.pages = 1
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 12, 6)
        self._count = QLabel("")
        self._count.setStyleSheet("color: #64748b; font-size: 12px;")
        row.addWidget(self._count)
        row.addStretch()
        self._prev = QPushButton("◀")
        self._next = QPushButton("▶")
        for button in (self._prev, self._next):
            button.setObjectName("IconButton")
            button.setFixedSize(32, 28)
        self._prev.clicked.connect(lambda: self._go(self.page - 1))
        self._next.clicked.connect(lambda: self._go(self.page + 1))
        self._label = QLabel("")
        self._label.setStyleSheet("font-weight: 600; color: #374151; padding: 0 8px;")
        row.addWidget(self._prev)
        row.addWidget(self._label)
        row.addWidget(self._next)
        row.addStretch()

    def _go(self, page: int) -> None:
        page = max(1, min(page, self.pages))
        if page != self.page:
            self.page = page
            self._on_page()

    def update_state(self, shown: int, total: int, noun: str, searching: bool) -> None:
        self.pages = max(1, -(-shown // PAGE_SIZE))
        self.page = max(1, min(self.page, self.pages))
        self._label.setText(f"Page {self.page} / {self.pages}")
        self._prev.setEnabled(self.page > 1)
        self._next.setEnabled(self.page < self.pages)
        self._count.setText(
            f"{shown} {noun}(s) trouvé(s) sur {total}" if searching else f"{total} {noun}(s)"
        )

    def slice(self, rows: list) -> list:
        start = (self.page - 1) * PAGE_SIZE
        return rows[start:start + PAGE_SIZE]


class _DetailPanel(QWidget):
    def __init__(self, mode: ContactMode = "resellers") -> None:
        super().__init__()
        self._mode = mode
        self._refresh_cb = None
        self._global_refresh_cb = None
        self._all_mvt: list = []
        self._all_pay: list = []
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
        self._header.setStyleSheet("font-size: 17px; font-weight: 700; color: #0f172a;")
        header_row.addWidget(self._header)
        header_row.addStretch()
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
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: none; }"
            "QTabBar::tab { padding: 6px 16px; font-size: 12px; font-weight: 600; }"
            # Fond bleu de l'onglet actif (style commun) : texte en blanc,
            # sinon le texte bleu disparaît sur le fond bleu.
            "QTabBar::tab:selected { background-color: #2563eb; color: #ffffff; border-color: #2563eb; }"
        )
        self._mvt_tab = self._build_movements_table()
        self._pay_tab = self._build_payments_table()
        self._tabs.addTab(self._mvt_tab, "Mouvements stock")
        self._tabs.addTab(self._pay_tab, "Versements")
        card_layout.addWidget(self._tabs, stretch=1)

        layout.addWidget(card)

    def _build_movements_table(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._mvt_search = _search_box(
            "Rechercher : produit, référence ou date (ex. 06/11)", self._on_mvt_search
        )
        search_row = QHBoxLayout()
        search_row.setContentsMargins(12, 8, 12, 6)
        search_row.addWidget(self._mvt_search)
        vl.addLayout(search_row)

        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Référence", "Produit", "Type", "Qté", "Prix unit.", "Total", "", ""]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setFrameShape(QFrame.Shape.NoFrame)
        h = self._table.horizontalHeader()
        for column in (0, 1, 3, 4, 5, 6):
            h.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right_align_headers(self._table, (5, 6))  # Prix unit., Total
        self._table.setColumnWidth(7, 36)
        self._table.setColumnWidth(8, 36)

        self._mvt_empty = QLabel("Aucun mouvement pour ce contact.")
        self._mvt_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mvt_empty.setStyleSheet("color: #9ca3af; font-size: 12px; padding: 20px;")
        self._mvt_empty.setVisible(False)

        self._mvt_pager = _Pager(self._render_movements)

        vl.addWidget(self._table)
        vl.addWidget(self._mvt_empty)
        vl.addWidget(self._mvt_pager)
        return w

    def _build_payments_table(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._pay_search = _search_box(
            "Rechercher : date (ex. 06/11), montant ou note", self._on_pay_search
        )
        search_row = QHBoxLayout()
        search_row.setContentsMargins(12, 8, 12, 6)
        search_row.addWidget(self._pay_search)
        vl.addLayout(search_row)

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
        right_align_headers(self._pay_table, (1, 2, 3))  # Montant versé, soldes

        self._pay_empty = QLabel("Aucun versement enregistré.")
        self._pay_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pay_empty.setStyleSheet("color: #9ca3af; font-size: 12px; padding: 20px;")
        self._pay_empty.setVisible(False)

        self._pay_pager = _Pager(self._render_payments)

        vl.addWidget(self._pay_table)
        vl.addWidget(self._pay_empty)
        vl.addWidget(self._pay_pager)
        return w

    def load(self, contact_name: str, mvt_rows: list, pay_rows: list,
             balance: int = 0, refresh_cb=None, global_refresh_cb=None,
             new_contact: bool = False) -> None:
        """`new_contact` : autre contact sélectionné → recherches vidées et
        retour à la page 1 (un simple rafraîchissement les garde)."""
        self._refresh_cb = refresh_cb
        self._global_refresh_cb = global_refresh_cb
        self._header.setText(contact_name)
        self._tabs.setTabText(0, f"Mouvements stock ({len(mvt_rows)})" if mvt_rows else "Mouvements stock")

        color = _balance_color(balance)
        label = "Solde" if self._mode == "resellers" else "Dette"
        self._balance_badge.setText(f"{label} : {_format_for(self._mode)(balance)}")
        self._balance_badge.setStyleSheet(
            f"font-size: 13px; font-weight: 700; padding: 3px 10px; "
            f"border-radius: 10px; background: #f1f5f9; color: {color};"
        )

        self._all_mvt = mvt_rows
        self._all_pay = pay_rows
        if new_contact:
            for box in (self._mvt_search, self._pay_search):
                box.blockSignals(True)
                box.clear()
                box.blockSignals(False)
            self._mvt_pager.page = self._pay_pager.page = 1
        self._render_movements()
        self._render_payments()

        if pay_rows:
            self._tabs.setTabText(1, f"Versements ({len(pay_rows)})")
        else:
            self._tabs.setTabText(1, "Versements")

    # -- Recherche et pages ---------------------------------------------

    def _on_mvt_search(self) -> None:
        self._mvt_pager.page = 1
        self._render_movements()

    def _on_pay_search(self) -> None:
        self._pay_pager.page = 1
        self._render_payments()

    def _render_movements(self) -> None:
        query = self._mvt_search.text()
        # ligne : (date, réf, produit, type, qté, prix, total, lot, id, vente, texte de recherche)
        rows = [r for r in self._all_mvt if matches(query, r[-1])]
        self._mvt_pager.update_state(len(rows), len(self._all_mvt), "mouvement", bool(query.strip()))
        page_rows = self._mvt_pager.slice(rows)

        self._table.setRowCount(0)  # retire aussi les boutons des lignes précédentes
        self._table.setRowCount(len(page_rows))
        has_mvt = bool(page_rows)
        self._table.setVisible(has_mvt)
        self._mvt_empty.setVisible(not has_mvt)
        self._mvt_empty.setText(
            "Aucun mouvement ne correspond à la recherche." if self._all_mvt
            else "Aucun mouvement pour ce contact."
        )
        for i, (date, ref, product, type_, qty, price, total, batch_id, movement_id, is_sale, _text) \
                in enumerate(page_rows):
            self._table.setItem(i, 0, QTableWidgetItem(date))
            self._table.setItem(i, 1, QTableWidgetItem(ref))
            self._table.setItem(i, 2, QTableWidgetItem(product))
            type_item = QTableWidgetItem(type_)
            type_item.setForeground(Qt.GlobalColor.darkGreen if type_ == "Entrée" else Qt.GlobalColor.red)
            self._table.setItem(i, 3, type_item)
            self._table.setItem(i, 4, QTableWidgetItem(str(qty)))
            price_item = QTableWidgetItem(price)
            price_item.setTextAlignment(ALIGN_RIGHT)
            self._table.setItem(i, 5, price_item)
            total_item = QTableWidgetItem(total)
            total_item.setTextAlignment(ALIGN_RIGHT)
            self._table.setItem(i, 6, total_item)

            edit_btn = QPushButton("✏")
            edit_btn.setObjectName("IconButton")
            edit_btn.setFixedSize(28, 28)
            edit_btn.setStyleSheet("padding: 0; font-size: 12px;")
            edit_btn.clicked.connect(lambda _c, bid=batch_id, mid=movement_id: self._on_edit(bid, mid))
            self._table.setCellWidget(i, 7, edit_btn)

            if is_sale:
                print_btn = QPushButton("🖨")
                print_btn.setObjectName("IconButton")
                print_btn.setFixedSize(28, 28)
                print_btn.setStyleSheet("padding: 0; font-size: 13px;")
                print_btn.clicked.connect(
                    lambda _c, bid=batch_id, mid=movement_id: self._on_print(bid, mid)
                )
                self._table.setCellWidget(i, 8, print_btn)

    def _render_payments(self) -> None:
        query = self._pay_search.text()
        rows = [r for r in self._all_pay if matches(query, *r)]
        self._pay_pager.update_state(len(rows), len(self._all_pay), "versement", bool(query.strip()))
        page_rows = self._pay_pager.slice(rows)

        self._pay_table.setRowCount(len(page_rows))
        has_pay = bool(page_rows)
        self._pay_table.setVisible(has_pay)
        self._pay_empty.setVisible(not has_pay)
        self._pay_empty.setText(
            "Aucun versement ne correspond à la recherche." if self._all_pay
            else "Aucun versement enregistré."
        )
        for i, (date, amount, bal_before, bal_after, note, cashier) in enumerate(page_rows):
            self._pay_table.setItem(i, 0, QTableWidgetItem(date))
            amt_item = QTableWidgetItem(amount)
            amt_item.setForeground(Qt.GlobalColor.darkGreen)
            amt_item.setTextAlignment(ALIGN_RIGHT)
            self._pay_table.setItem(i, 1, amt_item)
            for column, balance in ((2, bal_before), (3, bal_after)):
                balance_item = QTableWidgetItem(balance)
                balance_item.setTextAlignment(ALIGN_RIGHT)
                self._pay_table.setItem(i, column, balance_item)
            self._pay_table.setItem(i, 4, QTableWidgetItem(note))
            self._pay_table.setItem(i, 5, QTableWidgetItem(cashier))

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
        self._balance_badge.setText("")
        self._all_mvt = []
        self._all_pay = []
        self._table.setRowCount(0)
        self._table.setVisible(True)
        self._mvt_empty.setVisible(False)
        self._pay_table.setRowCount(0)
        self._pay_table.setVisible(True)
        self._pay_empty.setVisible(False)
        self._mvt_pager.update_state(0, 0, "mouvement", False)
        self._pay_pager.update_state(0, 0, "versement", False)


# ---------------------------------------------------------------------------
# Dialog Versement
# ---------------------------------------------------------------------------

class _VersementDialog(FramelessDialog):
    """Versement d'un revendeur / à un fournisseur. Avec `adjust=True`
    (fournisseur, admin) : saisie directe du nouveau solde dû."""

    def __init__(self, reseller_name: str, balance_cents: int, mode: ContactMode = "resellers",
                 adjust: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(380)
        self.amount_cents = 0
        self.note = ""
        self._adjust = adjust

        from app.money import cents_to_da, da_to_cents
        from app.ui.widgets import ModernDoubleSpinBox

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(
            f"{'⚖  Ajuster le solde' if adjust else '💰  Versement'} — {reseller_name}",
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
        current = "Dette actuelle" if mode == "suppliers" else "Solde actuel"
        solde_lbl = QLabel(
            f"{current} : <b style='color:{color};'>{_format_for(mode)(balance_cents)}</b>"
        )
        solde_lbl.setStyleSheet("font-size: 13px; color: #374151;")
        layout.addWidget(solde_lbl)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._amount_spin = ModernDoubleSpinBox()
        self._amount_spin.setDecimals(0)
        self._amount_spin.setSuffix(" DA")
        self._amount_spin.setFixedHeight(36)
        if adjust:
            # Négatif = avoir (le fournisseur nous doit).
            self._amount_spin.setRange(-999_999_999, 999_999_999)
            self._amount_spin.setValue(cents_to_da(balance_cents))
            form.addRow("Montant dû *", self._amount_spin)
            hint = QLabel("Ce que vous devez au fournisseur (négatif = il vous doit).")
            hint.setStyleSheet("color: #64748b; font-size: 11px;")
            form.addRow("", hint)
        else:
            self._amount_spin.setRange(1, 999_999_999)
            if balance_cents > 0:
                self._amount_spin.setValue(cents_to_da(balance_cents))
            form.addRow("Montant *", self._amount_spin)

        self._note_input = QLineEdit()
        self._note_input.setPlaceholderText(
            "Raison de la correction (optionnel)" if adjust
            else "Ex : paiement espèces, chèque n°... (optionnel)"
        )
        form.addRow("Note", self._note_input)

        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setObjectName("ErrorLabel")
        layout.addWidget(self._error)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(8)
        btns_row.addStretch()
        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setMinimumWidth(100)
        cancel_btn.setStyleSheet(
            "QPushButton { background: #ffffff; color: #424242; border: 1px solid #bdbdbd;"
            " border-radius: 6px; font-weight: 600; padding: 0 14px; }"
            "QPushButton:hover { background: #f5f5f5; border-color: #9e9e9e; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btns_row.addWidget(cancel_btn)
        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedHeight(38)
        save_btn.setMinimumWidth(130)
        save_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " border-radius: 6px; font-weight: 700; font-size: 13px; padding: 0 16px; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:pressed { background: #1e40af; }"
        )
        save_btn.clicked.connect(self._accept)
        btns_row.addWidget(save_btn)
        layout.addLayout(btns_row)

        self._da_to_cents = da_to_cents

    def _accept(self) -> None:
        amount = self._da_to_cents(self._amount_spin.value())
        if amount <= 0 and not self._adjust:
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
        cancel_btn.setFixedHeight(38)
        cancel_btn.setMinimumWidth(100)
        cancel_btn.setStyleSheet(
            "QPushButton { background: #ffffff; color: #424242; border: 1px solid #bdbdbd;"
            " border-radius: 6px; font-weight: 600; padding: 0 14px; }"
            "QPushButton:hover { background: #f5f5f5; border-color: #9e9e9e; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btns_row.addWidget(cancel_btn)
        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedHeight(38)
        save_btn.setMinimumWidth(130)
        save_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " border-radius: 6px; font-weight: 700; font-size: 13px; padding: 0 16px; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:pressed { background: #1e40af; }"
        )
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
