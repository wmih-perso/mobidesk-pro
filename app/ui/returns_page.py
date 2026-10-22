"""Onglet « Retours » : point d'entrée unique pour tous les retours.

Deux actions (retour client/revendeur, retour fournisseur), les alertes
(produits défectueux à renvoyer, échanges fournisseur en attente) et la
liste de tous les bons de retour.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.labels import article_label
from app.money import format_da
from app.returns import (
    SETTLEMENT_LABELS,
    contact_name,
    count_pending_defectives,
    list_return_docs,
    receive_exchange,
)
from app.services import StockError
from app.ui.return_dialogs import CustomerReturnDialog, SettleExchangeDialog, SupplierReturnDialog
from app.ui.widgets import EmptyState, add_clear_button, apply_card_shadow

COLUMNS = ["Date", "Bon", "Type", "Contact", "Produits", "Montant", "Règlement", "Statut", ""]

FILTERS = [
    ("all", "Tous"),
    ("customer", "📥  Clients / revendeurs"),
    ("supplier", "📤  Fournisseurs"),
    ("pending", "⏳  En attente"),
]

_PILL_STYLE = (
    "QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
    " border-radius: 16px; padding: 6px 14px; font-weight: 600; }}"
    "QPushButton:hover {{ border-color: #2563eb; }}"
)


def _action_card(icon: str, title: str, subtitle: str, color: str) -> QPushButton:
    button = QPushButton(f"{icon}  {title}\n{subtitle}")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(84)
    button.setStyleSheet(
        f"QPushButton {{ background: {color}; color: white; border: none; border-radius: 12px;"
        " padding: 14px 22px; font-size: 16px; font-weight: 700; text-align: left; }"
        "QPushButton:hover { background: #0f172a; }"
    )
    return button


def _small_button(text: str, bg: str, fg: str, border: str) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
        " border-radius: 6px; padding: 4px 10px; font-weight: 700; font-size: 12px; min-height: 0; }"
        f"QPushButton:hover {{ border-color: {fg}; }}"
    )
    return button


class ReturnsPage(QWidget):
    """Page Retours. Émet `stock_changed` après chaque opération qui
    modifie le stock ou les dettes."""

    stock_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._filter = "all"
        self._docs = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        title = QLabel("↩  Retours")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        layout.addWidget(title)

        actions = QHBoxLayout()
        actions.setSpacing(16)
        customer_btn = _action_card(
            "📥", "Retour revendeur / client", "Un produit vendu revient chez vous", "#1d4ed8"
        )
        customer_btn.clicked.connect(self._on_customer_return)
        actions.addWidget(customer_btn)
        supplier_btn = _action_card(
            "📤", "Retour fournisseur", "Un produit repart chez le fournisseur", "#c2410c"
        )
        supplier_btn.clicked.connect(lambda: self._on_supplier_return())
        actions.addWidget(supplier_btn)
        layout.addLayout(actions)

        # Alerte produits défectueux à renvoyer
        self.defective_banner = QFrame()
        self.defective_banner.setStyleSheet(
            "QFrame { background: #fff7ed; border: 1px solid #fdba74; border-radius: 10px; }"
        )
        banner_layout = QHBoxLayout(self.defective_banner)
        banner_layout.setContentsMargins(16, 10, 16, 10)
        self.defective_label = QLabel("")
        self.defective_label.setStyleSheet("color: #9a3412; font-weight: 700; border: none;")
        banner_layout.addWidget(self.defective_label, stretch=1)
        send_btn = QPushButton("📤  Renvoyer au fournisseur")
        send_btn.clicked.connect(lambda: self._on_supplier_return(defectives=True))
        banner_layout.addWidget(send_btn)
        layout.addWidget(self.defective_banner)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 12)
        card_layout.setSpacing(10)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self._filter_buttons: dict[str, QPushButton] = {}
        for key, label in FILTERS:
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c, k=key: self._set_filter(k))
            self._filter_buttons[key] = button
            filter_row.addWidget(button)
        filter_row.addStretch()
        self.search_input = QLineEdit()
        add_clear_button(self.search_input)
        self.search_input.setPlaceholderText("Rechercher (n° de bon, contact, produit)...")
        self.search_input.setMinimumWidth(320)
        self.search_input.setMinimumHeight(36)
        self.search_input.textChanged.connect(self.refresh)
        filter_row.addWidget(self.search_input)
        card_layout.addLayout(filter_row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._on_print_selected)
        card_layout.addWidget(self.table, stretch=1)

        self.empty_state = EmptyState(
            "↩",
            "Aucun retour",
            "Utilisez les boutons ci-dessus pour enregistrer un retour.",
        )
        card_layout.addWidget(self.empty_state, stretch=1)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #64748b; font-size: 12px;")
        card_layout.addWidget(self.summary_label)
        layout.addWidget(card, stretch=1)

        self._style_filters()

    # ------------------------------------------------------------------

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._style_filters()
        self.refresh()

    def _style_filters(self) -> None:
        for key, button in self._filter_buttons.items():
            active = key == self._filter
            button.setStyleSheet(_PILL_STYLE.format(
                bg="#2563eb" if active else "#ffffff",
                fg="#ffffff" if active else "#374151",
                border="#2563eb" if active else "#cbd5e1",
            ))

    def refresh(self) -> None:
        kind = self._filter if self._filter in ("customer", "supplier") else None
        with session_scope() as session:
            self._docs = list_return_docs(
                session,
                kind=kind,
                search=self.search_input.text(),
                pending_only=self._filter == "pending",
            )
            rows = [
                (
                    doc.id,
                    doc.created_at.strftime("%d/%m/%Y  %H:%M"),
                    doc.kind,
                    contact_name(doc),
                    ", ".join(
                        f"{line.quantity} × {article_label(line.display.category, line.display.phone_model, line.display.reference)}"
                        + (" (déf.)" if line.condition == "defective" else "")
                        for line in doc.lines
                    ),
                    doc.amount_cents,
                    SETTLEMENT_LABELS.get(doc.settlement, ""),
                    doc.status,
                )
                for doc in self._docs
            ]
            defective_count = count_pending_defectives(session)
            pending_count = len(list_return_docs(session, pending_only=True))

        self.defective_banner.setVisible(defective_count > 0)
        self.defective_label.setText(
            f"⚠  {defective_count} produit(s) défectueux rendu(s) par des clients, "
            "à renvoyer au fournisseur."
        )
        self._filter_buttons["pending"].setText(
            f"⏳  En attente ({pending_count})" if pending_count else "⏳  En attente"
        )

        self.table.setVisible(bool(rows))
        self.empty_state.setVisible(not rows)
        self.table.setRowCount(len(rows))
        for row, (doc_id, date, kind, contact, products, amount, settlement, status) in enumerate(rows):
            is_customer = kind == "customer"
            values = [
                date,
                f"R{doc_id}",
                "📥 Client" if is_customer else "📤 Fournisseur",
                contact,
                products,
                format_da(amount),
                settlement,
                "⏳ Échange attendu" if status == "pending" else "✓ Terminé",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, doc_id)
                if column == 2:
                    item.setForeground(Qt.GlobalColor.darkBlue if is_customer else Qt.GlobalColor.darkRed)
                if column == 7 and status == "pending":
                    item.setForeground(Qt.GlobalColor.darkYellow)
                self.table.setItem(row, column, item)
            self.table.setCellWidget(row, len(COLUMNS) - 1, self._row_actions(doc_id, status, amount))

        customers = sum(1 for r in rows if r[2] == "customer")
        self.summary_label.setText(
            f"{len(rows)} bon(s) de retour  —  {customers} client(s)  ·  {len(rows) - customers} fournisseur(s)"
        )

    def _row_actions(self, doc_id: int, status: str, amount: int) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        if status == "pending":
            received = _small_button("📦 Échange reçu", "#e8f5e9", "#2e7d32", "#a5d6a7")
            received.setToolTip("Le produit de remplacement est arrivé : il revient en stock.")
            received.clicked.connect(lambda: self._on_receive_exchange(doc_id))
            layout.addWidget(received)
            refunded = _small_button("💵 Remboursé", "#fff3e0", "#e65100", "#ffcc80")
            refunded.setToolTip("Le fournisseur rembourse au lieu d'échanger.")
            refunded.clicked.connect(lambda: self._on_settle_exchange(doc_id, amount))
            layout.addWidget(refunded)
        print_btn = _small_button("🖨", "#f1f5f9", "#374151", "#cbd5e1")
        print_btn.setToolTip("Voir / imprimer le bon de retour")
        print_btn.clicked.connect(lambda: self._show_receipt(doc_id))
        print_btn.setFixedWidth(40)
        layout.addWidget(print_btn)
        layout.addStretch()
        return wrapper

    # ------------------------------------------------------------------

    def _after_return(self, return_id: int | None) -> None:
        self.refresh()
        self.stock_changed.emit()
        if return_id is not None:
            self._show_receipt(return_id)

    def _show_receipt(self, return_id: int) -> None:
        from app.ui.ticket_dialog import TicketPreviewDialog

        TicketPreviewDialog(return_id=return_id, parent=self).exec()

    def _on_print_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self._show_receipt(self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole))

    def _on_customer_return(self) -> None:
        dialog = CustomerReturnDialog(parent=self)
        if dialog.exec() == CustomerReturnDialog.DialogCode.Accepted:
            self._after_return(dialog.return_id)

    def _on_supplier_return(self, defectives: bool = False) -> None:
        dialog = SupplierReturnDialog(defectives=defectives, parent=self)
        if dialog.exec() == SupplierReturnDialog.DialogCode.Accepted:
            self._after_return(dialog.return_id)

    def _on_receive_exchange(self, doc_id: int) -> None:
        confirm = QMessageBox.question(
            self,
            "Échange reçu",
            f"Confirmer la réception du produit de remplacement (bon R{doc_id}) ?\n"
            "Il sera remis en stock, sans nouveau paiement.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            with session_scope() as session:
                receive_exchange(session, doc_id)
        except StockError as error:
            QMessageBox.warning(self, "Erreur", str(error))
            return
        self.refresh()
        self.stock_changed.emit()

    def _on_settle_exchange(self, doc_id: int, amount: int) -> None:
        dialog = SettleExchangeDialog(doc_id, amount, parent=self)
        if dialog.exec() == SettleExchangeDialog.DialogCode.Accepted:
            self.refresh()
            self.stock_changed.emit()
