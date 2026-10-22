"""Fenêtres de retour : client/revendeur et fournisseur (voir app/returns.py).

Chaque fenêtre suit les mêmes 3 étapes, visibles à l'écran :
1. choisir le contact puis l'opération d'origine (vente ou achat) ;
2. indiquer les quantités (limitées à ce qui est retournable) ;
3. choisir le règlement — le montant et la nouvelle dette s'affichent
   avant de valider.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.models import StockMovement
from app.money import format_balance_ticket, format_da
from app.returns import (
    create_customer_return,
    create_supplier_return,
    list_pending_defectives,
    list_purchases_for_return,
    list_sales_for_return,
    settle_pending_exchange,
)
from app.services import StockError, get_reseller, list_resellers, list_suppliers
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernSpinBox, add_clear_button
from app.search import matches

COUNTER = "counter"  # valeur du combo « Client comptoir »

_STEP_STYLE = "font-weight: 700; font-size: 14px; color: #1e3a8a;"
_HINT_STYLE = "color: #94a3b8; font-size: 12px;"
_CHOICE_STYLE = (
    "QPushButton { background: #ffffff; color: #374151; border: 1px solid #cbd5e1;"
    " border-radius: 8px; padding: 9px 16px; font-weight: 600; }"
    "QPushButton:hover { border-color: #2563eb; color: #1d4ed8; }"
    "QPushButton:checked { background: #2563eb; color: #ffffff; border-color: #2563eb; }"
    "QPushButton:disabled { background: #f1f5f9; color: #94a3b8; border-color: #e2e8f0; }"
)


def _fmt_date(dt) -> str:
    return dt.strftime("%d/%m/%Y  %H:%M")


def _make_table(headers: list[str], stretch_column: int) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(42)
    table.setFrameShape(QFrame.Shape.NoFrame)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    return table


def _cell(table: QTableWidget, row: int, column: int, text: str, *, right: bool = False) -> None:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    table.setItem(row, column, item)


def _choice_group(parent: QWidget, layout: QHBoxLayout, choices: list[tuple[str, str]]) -> QButtonGroup:
    group = QButtonGroup(parent)
    group.setExclusive(True)
    for key, label in choices:
        button = QPushButton(label)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_CHOICE_STYLE)
        button.setProperty("choice", key)
        group.addButton(button)
        layout.addWidget(button)
    layout.addStretch()
    return group


def _checked_choice(group: QButtonGroup) -> str | None:
    button = group.checkedButton()
    return button.property("choice") if button is not None else None


def _check_choice(group: QButtonGroup, key: str) -> None:
    for button in group.buttons():
        if button.property("choice") == key:
            button.setChecked(True)


def _qty_spin(maximum: int, on_change) -> ModernSpinBox:
    spin = ModernSpinBox()
    spin.setRange(0, maximum)
    spin.setMinimumHeight(32)
    spin.setEnabled(maximum > 0)
    spin.valueChanged.connect(on_change)
    return spin


class _ReturnDialogBase(FramelessDialog):
    """Squelette commun : en-tête, corps défilant, résumé, erreurs, boutons."""

    title = ""
    gradient = "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1a1a2e,stop:1 #1565c0)"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(980, 720)
        self.setModal(True)
        self.return_id: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(self.title, gradient=self.gradient))

        body = QWidget()
        body.setStyleSheet("background: white;")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(24, 16, 24, 12)
        self.body_layout.setSpacing(10)
        root.addWidget(body, stretch=1)

        footer = QFrame()
        footer.setStyleSheet("QFrame { background: #f0f9ff; border-top: 1px solid #bae6fd; }")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 12, 20, 12)
        footer_layout.setSpacing(12)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-size: 14px; color: #0f172a; background: transparent; border: none;")
        self.summary_label.setWordWrap(True)
        footer_layout.addWidget(self.summary_label, stretch=1)

        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)

        self.save_btn = QPushButton("✔  Valider le retour")
        self.save_btn.setMinimumHeight(42)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._on_save)
        footer_layout.addWidget(self.save_btn)
        root.addWidget(footer)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            "color: #dc2626; font-size: 12px; background: #fef2f2;"
            " border: 1px solid #fca5a5; border-radius: 6px; padding: 8px 12px;"
        )
        self.error_label.setWordWrap(True)
        self.error_label.hide()

    def _step(self, text: str, hint: str = "") -> None:
        label = QLabel(text)
        label.setStyleSheet(_STEP_STYLE)
        self.body_layout.addWidget(label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setStyleSheet(_HINT_STYLE)
            hint_label.setWordWrap(True)
            self.body_layout.addWidget(hint_label)

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def _on_save(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Retour client / revendeur
# ---------------------------------------------------------------------------


class CustomerReturnDialog(_ReturnDialogBase):
    """Un client ou un revendeur rend un ou plusieurs produits d'une vente."""

    title = "📥  Retour revendeur / client"

    def __init__(self, batch_id: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self._sales = []
        self._line_widgets: list[tuple[object, ModernSpinBox, QComboBox]] = []
        self._balance_cents = 0
        self._build_ui()
        if batch_id is not None:
            self._preselect_sale(batch_id)
        else:
            self._reload_sales()

    def _build_ui(self) -> None:
        self._step("1.  Qui rend le produit, et de quelle vente ?")

        row = QHBoxLayout()
        row.setSpacing(10)
        self.contact_combo = QComboBox()
        self.contact_combo.setMinimumHeight(36)
        self.contact_combo.setMinimumWidth(240)
        self.contact_combo.addItem("Anciennes ventes au détail (sans revendeur)", COUNTER)
        with session_scope() as session:
            for reseller in list_resellers(session):
                self.contact_combo.addItem(reseller.name, reseller.id)
        self.contact_combo.currentIndexChanged.connect(self._on_contact_changed)
        row.addWidget(self.contact_combo)

        self.ticket_input = QLineEdit()

        add_clear_button(self.ticket_input)
        self.ticket_input.setPlaceholderText("N° de ticket")
        self.ticket_input.setMinimumHeight(36)
        self.ticket_input.setFixedWidth(140)
        self.ticket_input.textChanged.connect(self._reload_sales)
        row.addWidget(self.ticket_input)

        self.search_input = QLineEdit()

        add_clear_button(self.search_input)
        self.search_input.setPlaceholderText("Rechercher un produit (référence, marque, modèle)...")
        self.search_input.setMinimumHeight(36)
        self.search_input.textChanged.connect(self._reload_sales)
        row.addWidget(self.search_input, stretch=1)
        self.body_layout.addLayout(row)

        self.sales_table = _make_table(["Date", "N° ticket", "Produits", "Total", "Encore retournable"], 2)
        self.sales_table.setFixedHeight(180)
        self.sales_table.itemSelectionChanged.connect(self._on_sale_selected)
        self.body_layout.addWidget(self.sales_table)

        self._step(
            "2.  Quantités rendues",
            "« Bon » : le produit revient en stock.  « Défectueux » : il est mis de côté "
            "pour être renvoyé au fournisseur.",
        )
        self.lines_table = _make_table(
            ["Produit", "Vendu", "Déjà rendu", "Prix unitaire", "À retourner", "État", "Montant"], 0
        )
        self.lines_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.lines_table.verticalHeader().setDefaultSectionSize(48)
        self.lines_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.lines_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.lines_table.setColumnWidth(4, 140)
        self.lines_table.setColumnWidth(5, 140)
        self.body_layout.addWidget(self.lines_table, stretch=1)

        self._step("3.  Remboursement")
        choices_row = QHBoxLayout()
        self.settlement_group = _choice_group(
            self, choices_row, [("debt", "Déduire de la dette"), ("cash", "Rembourser en espèces")]
        )
        self.settlement_group.buttonClicked.connect(self._update_summary)
        self.body_layout.addLayout(choices_row)
        self.body_layout.addWidget(self.error_label)

        self._on_contact_changed()

    # --- Contact et ventes -------------------------------------------------

    def _reseller_id(self) -> int | None:
        value = self.contact_combo.currentData()
        return None if value == COUNTER else value

    def _on_contact_changed(self) -> None:
        reseller_id = self._reseller_id()
        debt_button = next(b for b in self.settlement_group.buttons() if b.property("choice") == "debt")
        debt_button.setEnabled(reseller_id is not None)
        _check_choice(self.settlement_group, "debt" if reseller_id is not None else "cash")
        self._balance_cents = 0
        if reseller_id is not None:
            with session_scope() as session:
                self._balance_cents = get_reseller(session, reseller_id).balance_cents
        self._reload_sales()

    def _preselect_sale(self, batch_id: int) -> None:
        with session_scope() as session:
            sale = session.query(StockMovement).filter(
                StockMovement.movement_batch_id == batch_id, StockMovement.is_sale.is_(True)
            ).first()
            reseller_id = sale.reseller_id if sale else None
        index = self.contact_combo.findData(reseller_id if reseller_id is not None else COUNTER)
        self.contact_combo.blockSignals(True)
        self.contact_combo.setCurrentIndex(max(index, 0))
        self.contact_combo.blockSignals(False)
        self.ticket_input.blockSignals(True)
        self.ticket_input.setText(str(batch_id))
        self.ticket_input.blockSignals(False)
        self._on_contact_changed()

    def _reload_sales(self) -> None:
        ticket_text = self.ticket_input.text().strip().lstrip("#").lstrip("0")
        ticket = int(ticket_text) if ticket_text.isdigit() else None
        if self.ticket_input.text().strip() and ticket is None:
            self._sales = []
        else:
            with session_scope() as session:
                self._sales = list_sales_for_return(
                    session,
                    reseller_id=self._reseller_id(),
                    ticket=ticket,
                    search=self.search_input.text(),
                )

        self.sales_table.clearSelection()
        self.sales_table.setRowCount(len(self._sales))
        for row, sale in enumerate(self._sales):
            products = ", ".join(
                f"{line.quantity} × {line.label}" for line in sale.lines
            )
            _cell(self.sales_table, row, 0, _fmt_date(sale.date))
            _cell(self.sales_table, row, 1, f"#{sale.batch_id:06d}")
            _cell(self.sales_table, row, 2, products)
            _cell(self.sales_table, row, 3, format_da(sale.total_cents), right=True)
            remaining = sale.returnable_count
            _cell(self.sales_table, row, 4, f"{remaining} pièce(s)" if remaining else "✓ Tout rendu")
        self._fill_lines(None)
        if len(self._sales) == 1:
            self.sales_table.selectRow(0)

    def _on_sale_selected(self) -> None:
        rows = self.sales_table.selectionModel().selectedRows()
        self._fill_lines(self._sales[rows[0].row()] if rows else None)

    def _fill_lines(self, sale) -> None:
        self._line_widgets = []
        lines = sale.lines if sale else []
        self.lines_table.setRowCount(len(lines))
        for row, line in enumerate(lines):
            _cell(self.lines_table, row, 0, f"{line.label}  ({line.reference})")
            _cell(self.lines_table, row, 1, str(line.quantity), right=True)
            _cell(self.lines_table, row, 2, str(line.returned), right=True)
            _cell(self.lines_table, row, 3, format_da(line.unit_price_cents), right=True)
            spin = _qty_spin(line.returnable, self._update_summary)
            self.lines_table.setCellWidget(row, 4, spin)
            condition = QComboBox()
            condition.addItem("Bon", "good")
            condition.addItem("Défectueux", "defective")
            condition.setMinimumHeight(32)
            self.lines_table.setCellWidget(row, 5, condition)
            self._line_widgets.append((line, spin, condition))
        self._update_summary()

    # --- Résumé et validation ---------------------------------------------

    def _amount_cents(self) -> int:
        return sum(line.unit_price_cents * spin.value() for line, spin, _c in self._line_widgets)

    def _update_summary(self, *_args) -> None:
        for row, (line, spin, _c) in enumerate(self._line_widgets):
            _cell(self.lines_table, row, 6, format_da(line.unit_price_cents * spin.value()), right=True)
        amount = self._amount_cents()
        self.save_btn.setEnabled(any(spin.value() for _l, spin, _c in self._line_widgets))
        text = f"Montant du retour : <b>{format_da(amount)}</b>"
        if self._reseller_id() is not None:
            after = self._balance_cents - amount if _checked_choice(self.settlement_group) == "debt" else self._balance_cents
            text += (
                f"  ·  Solde revendeur : {format_balance_ticket(self._balance_cents)}"
                f"  →  <b>{format_balance_ticket(after)}</b>"
            )
        elif amount:
            text += "  ·  À rendre au client en espèces"
        self.summary_label.setText(text)

    def _on_save(self) -> None:
        lines = [
            (line.movement_id, spin.value(), condition.currentData())
            for line, spin, condition in self._line_widgets
        ]
        try:
            with session_scope() as session:
                doc = create_customer_return(
                    session, lines=lines, settlement=_checked_choice(self.settlement_group) or ""
                )
                self.return_id = doc.id
        except StockError as error:
            self._show_error(str(error))
            return
        self.accept()


# ---------------------------------------------------------------------------
# Retour fournisseur
# ---------------------------------------------------------------------------


class SupplierReturnDialog(_ReturnDialogBase):
    """Renvoi au fournisseur : produits d'un achat, ou défectueux rendus
    par des clients."""

    title = "📤  Retour fournisseur"
    gradient = "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1a1a2e,stop:1 #c2410c)"

    def __init__(
        self,
        supplier_id: int | None = None,
        purchase_batch_id: int | None = None,
        defectives: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._purchases = []
        self._purchase_widgets: list[tuple[object, ModernSpinBox]] = []
        self._defective_widgets: list[tuple[object, ModernSpinBox]] = []
        self._balances: dict[int, int] = {}
        self._build_ui()
        if supplier_id is not None:
            self.supplier_combo.setCurrentIndex(max(self.supplier_combo.findData(supplier_id), 0))
        self._set_source("defective" if defectives else "purchase")
        if purchase_batch_id is not None:
            for row, purchase in enumerate(self._purchases):
                if purchase.batch_id == purchase_batch_id:
                    self.purchases_table.selectRow(row)
                    break

    def _build_ui(self) -> None:
        self._step("1.  Choisissez la facture d'achat (ou les produits défectueux)")

        row = QHBoxLayout()
        row.setSpacing(10)
        self.supplier_combo = QComboBox()
        self.supplier_combo.setMinimumHeight(36)
        self.supplier_combo.setMinimumWidth(240)
        self.supplier_combo.addItem("Tous les fournisseurs", None)
        with session_scope() as session:
            for supplier in list_suppliers(session):
                self.supplier_combo.addItem(supplier.name, supplier.id)
                self._balances[supplier.id] = supplier.balance_cents
            defective_count = sum(d.remaining for d in list_pending_defectives(session))
        self.supplier_combo.currentIndexChanged.connect(self._on_supplier_changed)
        row.addWidget(self.supplier_combo)

        self.search_input = QLineEdit()

        add_clear_button(self.search_input)
        self.search_input.setPlaceholderText("Rechercher (n° de facture, fournisseur, produit)...")
        self.search_input.setMinimumHeight(36)
        self.search_input.textChanged.connect(self._reload)
        row.addWidget(self.search_input, stretch=1)
        self.body_layout.addLayout(row)

        source_row = QHBoxLayout()
        self.source_group = _choice_group(
            self,
            source_row,
            [
                ("purchase", "📦  Depuis un achat"),
                ("defective", f"⚠  Produits défectueux en attente ({defective_count})"),
            ],
        )
        self.source_group.buttonClicked.connect(lambda b: self._set_source(b.property("choice")))
        self.body_layout.addLayout(source_row)

        self.source_stack = QStackedWidget()
        self.body_layout.addWidget(self.source_stack, stretch=1)

        # Page « Depuis un achat »
        purchase_page = QWidget()
        purchase_layout = QVBoxLayout(purchase_page)
        purchase_layout.setContentsMargins(0, 0, 0, 0)
        purchase_layout.setSpacing(8)
        self.purchases_table = _make_table(
            ["Date d'achat", "Fournisseur", "N° facture", "Pièces achetées", "Nb pièces", "Total"], 3
        )
        self.purchases_table.setFixedHeight(190)
        self.purchases_table.itemSelectionChanged.connect(self._on_purchase_selected)
        purchase_layout.addWidget(self.purchases_table)
        hint = QLabel(
            "2.  Produits de la facture sélectionnée — quantités à renvoyer, limitées à ce "
            "qui a été acheté et à ce qui reste en stock."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(_STEP_STYLE)
        purchase_layout.addWidget(hint)
        self.purchase_lines_table = _make_table(
            ["Produit", "Acheté", "Déjà renvoyé", "Prix d'achat", "À renvoyer", "Montant"], 0
        )
        self._setup_qty_column(self.purchase_lines_table, 4)
        purchase_layout.addWidget(self.purchase_lines_table, stretch=1)
        self.source_stack.addWidget(purchase_page)

        # Page « Produits défectueux »
        defective_page = QWidget()
        defective_layout = QVBoxLayout(defective_page)
        defective_layout.setContentsMargins(0, 0, 0, 0)
        defective_layout.setSpacing(8)
        hint = QLabel("2.  Produits rendus défectueux par vos clients, pas encore renvoyés.")
        hint.setStyleSheet(_STEP_STYLE)
        defective_layout.addWidget(hint)
        self.defective_table = _make_table(
            ["Produit", "Rendu par", "Le", "En attente", "Coût d'achat", "À renvoyer", "Montant"], 0
        )
        self._setup_qty_column(self.defective_table, 5)
        defective_layout.addWidget(self.defective_table, stretch=1)
        self.source_stack.addWidget(defective_page)

        self._step("3.  Règlement par le fournisseur")
        choices_row = QHBoxLayout()
        self.settlement_group = _choice_group(
            self,
            choices_row,
            [
                ("debt", "Avoir (déduit de ma dette)"),
                ("cash", "Remboursé en espèces"),
                ("exchange", "Échange (produit remplacé plus tard)"),
            ],
        )
        _check_choice(self.settlement_group, "debt")
        self.settlement_group.buttonClicked.connect(self._update_summary)
        self.body_layout.addLayout(choices_row)
        self.body_layout.addWidget(self.error_label)

        self._on_supplier_changed()

    @staticmethod
    def _setup_qty_column(table: QTableWidget, column: int) -> None:
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.verticalHeader().setDefaultSectionSize(48)
        table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(column, 140)

    def _supplier_id(self) -> int | None:
        """Fournisseur du retour : celui de la facture sélectionnée, sinon
        celui choisi dans la liste (None = « Tous les fournisseurs »)."""
        if self._source() == "purchase":
            purchase = self._selected_purchase()
            if purchase is not None:
                return purchase.contact_id
        return self.supplier_combo.currentData()

    def _selected_purchase(self):
        rows = self.purchases_table.selectionModel().selectedRows()
        return self._purchases[rows[0].row()] if rows else None

    def _source(self) -> str:
        return _checked_choice(self.source_group) or "purchase"

    def _set_source(self, source: str) -> None:
        _check_choice(self.source_group, source)
        self.source_stack.setCurrentIndex(0 if source == "purchase" else 1)
        self._reload()

    def _on_supplier_changed(self) -> None:
        self._reload()

    def _reload(self) -> None:
        if self._source() == "purchase":
            self._reload_purchases()
        else:
            self._reload_defectives()
        self._update_summary()

    def _reload_purchases(self) -> None:
        with session_scope() as session:
            self._purchases = list_purchases_for_return(
                session, supplier_id=self.supplier_combo.currentData(), search=self.search_input.text()
            )
        self.purchases_table.clearSelection()
        self.purchases_table.setRowCount(len(self._purchases))
        for row, purchase in enumerate(self._purchases):
            _cell(self.purchases_table, row, 0, _fmt_date(purchase.date))
            _cell(self.purchases_table, row, 1, purchase.contact_name)
            _cell(self.purchases_table, row, 2, purchase.invoice_number or f"(n° interne #{purchase.batch_id:06d})")
            _cell(self.purchases_table, row, 3, ", ".join(
                f"{line.quantity} × {line.label}" for line in purchase.lines
            ))
            _cell(self.purchases_table, row, 4, str(purchase.piece_count), right=True)
            _cell(self.purchases_table, row, 5, format_da(purchase.total_cents), right=True)
        self._fill_purchase_lines(None)
        if len(self._purchases) == 1:
            self.purchases_table.selectRow(0)

    def _on_purchase_selected(self) -> None:
        rows = self.purchases_table.selectionModel().selectedRows()
        self._fill_purchase_lines(self._purchases[rows[0].row()] if rows else None)

    def _fill_purchase_lines(self, purchase) -> None:
        self._purchase_widgets = []
        lines = purchase.lines if purchase else []
        table = self.purchase_lines_table
        table.setRowCount(len(lines))
        for row, line in enumerate(lines):
            _cell(table, row, 0, f"{line.label}  ({line.reference})")
            _cell(table, row, 1, str(line.quantity), right=True)
            _cell(table, row, 2, str(line.returned), right=True)
            _cell(table, row, 3, format_da(line.unit_price_cents), right=True)
            spin = _qty_spin(line.returnable, self._update_summary)
            table.setCellWidget(row, 4, spin)
            self._purchase_widgets.append((line, spin))
        self._update_summary()

    def _reload_defectives(self) -> None:
        with session_scope() as session:
            pending = list_pending_defectives(session)
        search = self.search_input.text()
        pending = [d for d in pending if matches(search, d.reference, d.designation, d.category)]
        self._defective_widgets = []
        table = self.defective_table
        table.setRowCount(len(pending))
        for row, defective in enumerate(pending):
            _cell(table, row, 0, f"{defective.label}  ({defective.reference})")
            _cell(table, row, 1, defective.customer_name)
            _cell(table, row, 2, defective.returned_at.strftime("%d/%m/%Y"))
            _cell(table, row, 3, str(defective.remaining), right=True)
            _cell(table, row, 4, format_da(defective.unit_cost_cents), right=True)
            spin = _qty_spin(defective.remaining, self._update_summary)
            table.setCellWidget(row, 5, spin)
            self._defective_widgets.append((defective, spin))

    def _active_lines(self) -> list[tuple[object, ModernSpinBox, int]]:
        """(ligne, quantité, coût unitaire) de la source affichée."""
        if self._source() == "purchase":
            return [(line, spin, line.unit_price_cents) for line, spin in self._purchase_widgets]
        return [(d, spin, d.unit_cost_cents) for d, spin in self._defective_widgets]

    def _update_summary(self, *_args) -> None:
        if self._source() == "purchase":
            table, column = self.purchase_lines_table, 5
        else:
            table, column = self.defective_table, 6
        for row, (_line, spin, cost) in enumerate(self._active_lines()):
            _cell(table, row, column, format_da(cost * spin.value()), right=True)

        amount = sum(cost * spin.value() for _l, spin, cost in self._active_lines())
        settlement = _checked_choice(self.settlement_group)
        supplier_id = self._supplier_id()
        balance = self._balances.get(supplier_id, 0)
        after = balance - amount if settlement == "debt" else balance
        text = f"Montant : <b>{format_da(amount)}</b>"
        if supplier_id is not None:
            text += (
                f"  ·  Solde fournisseur : {format_balance_ticket(balance)}"
                f"  →  <b>{format_balance_ticket(after)}</b>"
            )
        if settlement == "exchange" and amount:
            text += "  ·  Le produit de remplacement reviendra en stock à sa réception."
        self.summary_label.setText(text)

    def _on_save(self) -> None:
        if self._supplier_id() is None:
            self._show_error(
                "Cliquez sur une facture d'achat dans la liste."
                if self._source() == "purchase" else
                "Choisissez le fournisseur à qui renvoyer les produits défectueux."
            )
            return
        if self._source() == "purchase":
            kwargs = dict(purchase_lines=[(line.movement_id, spin.value()) for line, spin in self._purchase_widgets])
        else:
            kwargs = dict(defective_lines=[(d.line_id, spin.value()) for d, spin in self._defective_widgets])
        try:
            with session_scope() as session:
                doc = create_supplier_return(
                    session,
                    supplier_id=self._supplier_id(),
                    settlement=_checked_choice(self.settlement_group) or "",
                    **kwargs,
                )
                self.return_id = doc.id
        except StockError as error:
            self._show_error(str(error))
            return
        self.accept()


# ---------------------------------------------------------------------------
# Échange en attente réglé par un remboursement
# ---------------------------------------------------------------------------


class SettleExchangeDialog(FramelessDialog):
    """Le fournisseur rembourse au lieu d'échanger : avoir ou espèces."""

    def __init__(self, return_id: int, amount_cents: int, parent=None) -> None:
        super().__init__(parent)
        self._return_id = return_id
        self.setMinimumWidth(460)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(f"Régler le retour R{return_id}", height=54))

        body = QWidget()
        # Fond blanc limité au corps : sans sélecteur, il s'appliquerait aussi
        # aux boutons (bouton principal blanc sur blanc, invisible).
        body.setObjectName("DialogBody")
        body.setStyleSheet("QWidget#DialogBody { background: white; }")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)
        info = QLabel(
            f"Le fournisseur ne remplace pas le produit mais rembourse "
            f"<b>{format_da(amount_cents)}</b>. Comment ?"
            if amount_cents else
            "Le fournisseur ne remplace pas le produit mais le rembourse. Comment ?"
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        choices_row = QHBoxLayout()
        self.group = _choice_group(
            self, choices_row, [("debt", "Avoir (déduit de ma dette)"), ("cash", "Espèces")]
        )
        _check_choice(self.group, "debt")
        layout.addLayout(choices_row)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626; font-size: 12px;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        ok_btn = QPushButton("✔  Valider")
        ok_btn.clicked.connect(self._on_save)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)
        root.addWidget(body)

    def _on_save(self) -> None:
        try:
            with session_scope() as session:
                settle_pending_exchange(session, self._return_id, settlement=_checked_choice(self.group))
        except StockError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.accept()
