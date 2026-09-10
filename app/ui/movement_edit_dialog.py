"""Boîte de dialogue d'édition d'un lot de mouvements (achat ou vente).

Permet de modifier la quantité et le prix de chaque produit, d'en ajouter
ou d'en supprimer. Au moment de la validation, le lot original est annulé
(cancel_movement_batch) et un nouveau lot est créé avec les nouvelles données.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from app.database import session_scope
from app.money import cents_to_da, da_to_cents, format_da
from app.services import (
    StockError,
    apply_stock_batch,
    cancel_movement_batch,
    list_displays,
)
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox


class MovementEditDialog(FramelessDialog):
    """Édition d'un lot de mouvements identifié par movement_batch_id."""

    def __init__(self, batch_id: int | None = None, movement_id: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self._batch_id = batch_id
        self._movement_id = movement_id
        self._line_rows: list[tuple[int, ModernSpinBox, ModernDoubleSpinBox]] = []
        self._direction: int = 1
        self._is_sale: bool = False
        self._sale_price_type: str | None = None
        self._supplier_id: int | None = None
        self._reseller_id: int | None = None
        self._reason: str = ""
        self._show_price: bool = False

        self._load_batch_info()
        self._build_ui()

    # ------------------------------------------------------------------
    # Chargement des données d'origine
    # ------------------------------------------------------------------

    def _load_batch_info(self) -> None:
        with session_scope() as session:
            from app.models import StockMovement
            if self._batch_id is not None:
                movements = (
                    session.query(StockMovement)
                    .filter(StockMovement.movement_batch_id == self._batch_id)
                    .all()
                )
            else:
                movements = (
                    session.query(StockMovement)
                    .filter(StockMovement.id == self._movement_id)
                    .all()
                )
            if not movements:
                return

            first = movements[0]
            self._direction = 1 if first.change_quantity > 0 else -1
            self._is_sale = first.is_sale
            self._sale_price_type = first.sale_price_type or None
            self._supplier_id = first.supplier_id
            self._reseller_id = first.reseller_id
            self._reason = first.reason
            self._show_price = True  # toujours afficher le prix en mode édition

            self._initial_lines: list[tuple[int, str, int, int]] = []
            for m in movements:
                ref = m.display.reference if m.display else f"id={m.display_id}"
                brand = m.display.brand if m.display else ""
                model = m.display.phone_model if m.display else ""
                label = f"{ref} — {brand} {model}".strip()
                price = (
                    m.unit_purchase_price_cents
                    if self._direction == 1
                    else m.unit_sale_price_cents
                )
                qty = abs(m.change_quantity)
                self._initial_lines.append((m.display_id, label, qty, price))

            # Charger tous les produits disponibles pour la recherche
            self._display_choices = [
                (
                    d.id,
                    f"{d.reference} — {d.brand} {d.phone_model} (stock : {d.quantity})".strip(),
                    d.purchase_price_cents if self._direction == 1
                    else (
                        d.sale_price_wholesale_cents
                        if self._sale_price_type == "wholesale"
                        else d.sale_price_retail_cents
                    ),
                )
                for d in list_displays(session)
            ]

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        if self._direction == 1:
            title = "Modifier l'achat fournisseur"
        elif self._is_sale:
            title = "Modifier la vente"
        else:
            title = "Modifier l'ajustement de stock"
        self.setMinimumWidth(600)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(title))

        body = QWidget()
        body.setStyleSheet("background: white;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        root.addWidget(body)

        lot_ref = f"Lot #{self._batch_id}" if self._batch_id is not None else f"Mouvement #{self._movement_id}"
        info = QLabel(f"{lot_ref}  —  Motif : {self._reason}")
        info.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(info)

        # Barre de recherche pour ajouter un produit
        search_label = QLabel("Ajouter un produit :")
        search_label.setStyleSheet("font-weight: 600; margin-top: 8px;")
        layout.addWidget(search_label)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Référence, marque, modèle…")
        self._search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search_input)

        self._search_results = QListWidget()
        self._search_results.setFixedHeight(100)
        self._search_results.setVisible(False)
        self._search_results.itemClicked.connect(self._on_result_clicked)
        layout.addWidget(self._search_results)

        # Tableau des lignes
        columns = ["Produit", "Quantité", "Prix unitaire", ""]
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(columns)
        h = self._table.horizontalHeader()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 130)
        self._table.setColumnWidth(3, 36)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(44)
        self._table.setFixedHeight(200)
        layout.addWidget(self._table)

        # Total
        total_row = QHBoxLayout()
        total_row.addStretch()
        total_row.addWidget(QLabel("Total :"))
        self._total_label = QLabel("0 DA")
        self._total_label.setStyleSheet("font-weight: 700;")
        total_row.addWidget(self._total_label)
        layout.addLayout(total_row)

        self._error_label = QLabel("")
        self._error_label.setObjectName("ErrorLabel")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setMinimumWidth(110)
        cancel_btn.setStyleSheet(
            "QPushButton { background: #ffffff; color: #424242; border: 1px solid #bdbdbd;"
            " border-radius: 6px; font-weight: 600; padding: 0 14px; }"
            "QPushButton:hover { background: #f5f5f5; border-color: #9e9e9e; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedHeight(38)
        save_btn.setMinimumWidth(140)
        save_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none;"
            " border-radius: 6px; font-weight: 700; font-size: 13px; padding: 0 18px; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:pressed { background: #1e40af; }"
        )
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

        # Pré-remplir avec les lignes d'origine
        for display_id, label, qty, price_cents in self._initial_lines:
            self._add_row(display_id, label, qty, price_cents)

    # ------------------------------------------------------------------
    # Recherche + ajout de ligne
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        text = text.strip().lower()
        self._search_results.clear()
        if not text:
            self._search_results.setVisible(False)
            return

        already = {d for d, _, _ in self._line_rows}
        matches = [
            (did, lbl, price)
            for did, lbl, price in self._display_choices
            if text in lbl.lower() and did not in already
        ]
        if not matches:
            self._search_results.setVisible(False)
            return

        for did, lbl, price in matches:
            item = QListWidgetItem(lbl)
            item.setData(Qt.ItemDataRole.UserRole, (did, price))
            self._search_results.addItem(item)
        self._search_results.setVisible(True)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        did, price = item.data(Qt.ItemDataRole.UserRole)
        lbl = item.text()
        self._add_row(did, lbl, 1, price)
        self._search_input.clear()
        self._search_results.clear()
        self._search_results.setVisible(False)

    def _add_row(self, display_id: int, label: str, qty: int, price_cents: int) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        name_item = QTableWidgetItem(label)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 0, name_item)

        qty_spin = ModernSpinBox()
        qty_spin.setRange(1, 1_000_000)
        qty_spin.setFixedHeight(36)
        qty_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        qty_spin.setValue(qty)
        qty_spin.valueChanged.connect(self._update_total)
        self._table.setCellWidget(row, 1, qty_spin)

        price_spin = ModernDoubleSpinBox()
        price_spin.setRange(0, 10_000_000)
        price_spin.setDecimals(0)
        price_spin.setSuffix(" DA")
        price_spin.setFixedHeight(36)
        price_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        price_spin.setValue(cents_to_da(price_cents))
        price_spin.valueChanged.connect(self._update_total)
        self._table.setCellWidget(row, 2, price_spin)

        rm_btn = QPushButton("×")
        rm_btn.setObjectName("IconButton")
        rm_btn.setStyleSheet("padding: 0; font-size: 15px; color: #dc2626; font-weight: 700;")
        rm_btn.clicked.connect(lambda: self._remove_row(qty_spin))
        self._table.setCellWidget(row, 3, rm_btn)

        self._line_rows.append((display_id, qty_spin, price_spin))
        self._update_total()

    def _remove_row(self, qty_spin: ModernSpinBox) -> None:
        for i, (_, spin, _) in enumerate(self._line_rows):
            if spin is qty_spin:
                self._table.removeRow(i)
                self._line_rows.pop(i)
                break
        self._update_total()

    def _update_total(self) -> None:
        total = sum(
            da_to_cents(price.value()) * spin.value()
            for _, spin, price in self._line_rows
        )
        self._total_label.setText(format_da(total))

    # ------------------------------------------------------------------
    # Sauvegarde
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        if not self._line_rows:
            self._error_label.setText("Ajoutez au moins un produit.")
            return

        lines = [
            (did, spin.value(), da_to_cents(price.value()))
            for did, spin, price in self._line_rows
        ]

        try:
            with session_scope() as session:
                cancel_movement_batch(session, batch_id=self._batch_id, movement_id=self._movement_id)
                apply_stock_batch(
                    session,
                    direction=self._direction,
                    lines=lines,
                    is_sale=self._is_sale,
                    sale_price_type=self._sale_price_type,
                    supplier_id=self._supplier_id,
                    reseller_id=self._reseller_id,
                    reason=self._reason,
                )
        except StockError as e:
            self._error_label.setText(str(e))
            return
        except Exception as e:
            self._error_label.setText(f"Erreur inattendue : {e}")
            return

        self.accept()
