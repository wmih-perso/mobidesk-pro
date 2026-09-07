"""Page générique Revendeurs / Fournisseurs — liste + CRUD + historique des mouvements."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
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
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import format_da
from app.services import (
    StockError,
    create_reseller,
    create_supplier,
    deactivate_reseller,
    deactivate_supplier,
    list_movements,
    list_resellers,
    list_suppliers,
    update_reseller,
    update_supplier,
)

ContactMode = Literal["resellers", "suppliers"]


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
        add_btn = QPushButton(f"+ Nouveau {'revendeur' if self._mode == 'resellers' else 'fournisseur'}")
        add_btn.clicked.connect(self._on_add)
        header_row.addWidget(add_btn)
        layout.addLayout(header_row)
        layout.addSpacing(6)

        hint_text = (
            "Sélectionnez un contact pour voir son historique de mouvements."
        )
        hint = QLabel(hint_text)
        hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(hint)
        layout.addSpacing(14)

        # Splitter vertical : liste en haut, détail en bas
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # --- Tableau des contacts ---
        list_card = QFrame()
        list_card.setObjectName("Card")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        self.contacts_table = QTableWidget(0, 4)
        self.contacts_table.setHorizontalHeaderLabels(["Nom", "Téléphone", "Adresse", ""])
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

        # --- Panneau de détail (historique mouvements) ---
        self._detail_panel = _MovementDetailPanel()
        splitter.addWidget(self._detail_panel)
        splitter.setSizes([280, 280])

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

            actions = QWidget()
            a_layout = QHBoxLayout(actions)
            a_layout.setContentsMargins(4, 2, 4, 2)
            a_layout.setSpacing(4)

            edit_btn = QPushButton("✏")
            edit_btn.setObjectName("IconButton")
            edit_btn.setFixedSize(28, 28)
            edit_btn.setStyleSheet("padding:0; font-size:13px;")
            edit_btn.setToolTip("Modifier")
            edit_btn.clicked.connect(lambda _c, cid=c.id: self._on_edit(cid))
            a_layout.addWidget(edit_btn)

            del_btn = QPushButton("×")
            del_btn.setObjectName("IconButton")
            del_btn.setFixedSize(28, 28)
            del_btn.setStyleSheet("padding:0; font-size:15px; color:#dc2626; font-weight:700;")
            del_btn.setToolTip("Supprimer")
            del_btn.clicked.connect(lambda _c, cid=c.id: self._on_delete(cid))
            a_layout.addWidget(del_btn)

            self.contacts_table.setCellWidget(i, 3, actions)

        # Recharge le détail si un contact était sélectionné
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
            else:
                movements = list_movements(session, supplier_id=contact_id)
            rows = []
            for m in movements:
                is_entry = m.change_quantity > 0
                unit_price = m.unit_purchase_price_cents if is_entry else m.unit_sale_price_cents
                qty = abs(m.change_quantity)
                rows.append((
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
        self._detail_panel.load(
            contact_name,
            rows,
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
                        create_reseller(
                            session,
                            name=dialog.field_name,
                            phone=dialog.field_phone,
                            address=dialog.field_address,
                            notes=dialog.field_notes,
                        )
                    else:
                        create_supplier(
                            session,
                            name=dialog.field_name,
                            phone=dialog.field_phone,
                            address=dialog.field_address,
                            notes=dialog.field_notes,
                        )
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
                        update_reseller(
                            session,
                            contact_id,
                            name=dialog.field_name,
                            phone=dialog.field_phone,
                            address=dialog.field_address,
                            notes=dialog.field_notes,
                        )
                    else:
                        update_supplier(
                            session,
                            contact_id,
                            name=dialog.field_name,
                            phone=dialog.field_phone,
                            address=dialog.field_address,
                            notes=dialog.field_notes,
                        )
                self.refresh()
            except StockError as e:
                QMessageBox.warning(self, "Erreur", str(e))

    def _on_delete(self, contact_id: int) -> None:
        confirm = QMessageBox.question(
            self,
            "Supprimer le contact",
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


# ---------------------------------------------------------------------------
# Panneau de détail des mouvements
# ---------------------------------------------------------------------------

class _MovementDetailPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._refresh_cb = None
        self._global_refresh_cb = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self._header = QLabel("Sélectionnez un contact pour voir ses mouvements")
        self._header.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #374151; "
            "padding: 12px 16px; border-bottom: 1px solid #f1f5f9;"
        )
        card_layout.addWidget(self._header)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Pièce", "Type", "Qté", "Prix unit.", "Total", "", ""]
        )
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
        self._table.setColumnWidth(6, 36)
        self._table.setColumnWidth(7, 36)
        card_layout.addWidget(self._table, stretch=1)

        self._empty = QLabel("Aucun mouvement pour ce contact.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color: #9ca3af; font-size: 12px; padding: 20px;")
        self._empty.setVisible(False)
        card_layout.addWidget(self._empty)

        layout.addWidget(card)

    def load(self, contact_name: str, rows: list[tuple], refresh_cb=None, global_refresh_cb=None) -> None:
        self._refresh_cb = refresh_cb
        self._global_refresh_cb = global_refresh_cb
        self._header.setText(f"Mouvements — {contact_name}  ({len(rows)} transaction(s))")
        self._table.setRowCount(len(rows))
        has_rows = bool(rows)
        self._table.setVisible(has_rows)
        self._empty.setVisible(not has_rows)
        for i, (date, ref, type_, qty, price, total, batch_id, movement_id, is_sale) in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(date))
            self._table.setItem(i, 1, QTableWidgetItem(ref))
            type_item = QTableWidgetItem(type_)
            if type_ == "Entrée":
                type_item.setForeground(Qt.GlobalColor.darkGreen)
            else:
                type_item.setForeground(Qt.GlobalColor.red)
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
            edit_btn.setToolTip("Modifier ce lot")
            edit_btn.clicked.connect(lambda _c, bid=batch_id, mid=movement_id: self._on_edit(bid, mid))
            self._table.setCellWidget(i, 6, edit_btn)

            if is_sale:
                print_btn = QPushButton("🖨")
                print_btn.setObjectName("IconButton")
                print_btn.setFixedSize(28, 28)
                print_btn.setStyleSheet("padding: 0; font-size: 13px;")
                print_btn.setToolTip("Imprimer le ticket")
                print_btn.clicked.connect(
                    lambda _c, bid=batch_id, mid=movement_id: self._on_print(bid, mid)
                )
                self._table.setCellWidget(i, 7, print_btn)

    def _on_print(self, batch_id: int | None, movement_id: int) -> None:
        from app.printing import print_sale_ticket
        print_sale_ticket(batch_id=batch_id, movement_id=movement_id, parent=self)

    def _on_edit(self, batch_id: int | None, movement_id: int) -> None:
        from app.ui.movement_edit_dialog import MovementEditDialog
        from PySide6.QtWidgets import QDialog
        dlg = MovementEditDialog(batch_id=batch_id, movement_id=movement_id, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._refresh_cb:
                self._refresh_cb()
            if self._global_refresh_cb:
                self._global_refresh_cb()

    def clear(self) -> None:
        self._refresh_cb = None
        self._header.setText("Sélectionnez un contact pour voir ses mouvements")
        self._table.setRowCount(0)
        self._table.setVisible(True)
        self._empty.setVisible(False)


# ---------------------------------------------------------------------------
# Dialog Ajouter / Modifier contact
# ---------------------------------------------------------------------------

class _ContactDialog(QDialog):
    def __init__(self, mode: ContactMode, contact=None, parent=None) -> None:
        super().__init__(parent)
        is_reseller = mode == "resellers"
        noun = "revendeur" if is_reseller else "fournisseur"
        self.setWindowTitle(f"{'Modifier' if contact else 'Nouveau'} {noun}")
        self.setModal(True)
        self.setMinimumWidth(380)

        self.field_name = contact.name if contact else ""
        self.field_phone = contact.phone if contact else ""
        self.field_address = contact.address if contact else ""
        self.field_notes = contact.notes if contact else ""

        layout = QVBoxLayout(self)
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

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Enregistrer")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("SecondaryButton")
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

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
