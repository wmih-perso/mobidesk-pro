"""Boîtes de dialogue de mouvement de stock (achat, vente en gros, ajustement).

Trois actions dédiées et explicites plutôt que deux formulaires génériques
« Entrée » / « Sortie » où le vrai geste métier (achat fournisseur, vente en
gros, retour/casse/correction) était caché dans un combo motif à ouvrir
pour comprendre. Chaque dialog s'ouvre déjà configuré pour son usage. La
vente au détail n'a pas sa place ici : un afficheur ou une batterie ne se
vend jamais seul, toujours posé — cette vente passe par le module
Réparations (voir app/ui/repair_dialog.py), qui facture la pose incluse.

Chaque dialog gère aussi plusieurs produits en une seule transaction (comme
RepairDialog pour les pièces d'une réparation) : on choisit le
fournisseur/revendeur une seule fois pour tout le lot, on ajoute des lignes
produit + quantité (+ prix pour une vente en gros) dans un tableau, et on
valide tout en une fois via apply_stock_batch.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import cents_to_da, da_to_cents, format_da
from app.services import StockError, apply_stock_batch, list_displays, list_resellers, list_suppliers
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox, field_label

# Motifs proposés pour un ajustement de stock (ni achat, ni vente).
ADJUSTMENT_REASONS = [
    "Retour client",
    "Correction d'inventaire",
    "Produit endommagé",
    "Produit perdu",
    "Autre",
]


class _BaseStockBatchDialog(FramelessDialog):
    """Base commune : recherche produit + tableau de lignes (produit,
    quantité, prix éventuel) + application atomique via apply_stock_batch.

    Sous-classes : fixent `direction`, `is_sale`, `sale_price_type`, et
    construisent l'en-tête spécifique (fournisseur/revendeur/motif).
    """

    direction: int  # +1 pour un achat/entrée, -1 pour une vente/sortie
    is_sale: bool = False
    sale_price_type: str | None = None
    show_price_column: bool = False
    _dialog_title: str = "Mouvement de stock"

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__()
        self.setMinimumWidth(560)
        self.setModal(True)
        self._line_rows: list[tuple[int, ModernSpinBox, ModernDoubleSpinBox | None]] = []

        self._build_ui()
        if display_id is not None:
            self._preload_display(display_id)

    # ------------------------------------------------------------------
    # Construction — la sous-classe appelle _build_header() à l'endroit
    # voulu dans son propre _build_ui() avant d'appeler super()._build_ui().
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout

        layout.addWidget(self._make_header(self._dialog_title))

        inner = QWidget()
        inner.setStyleSheet("background: white;")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(20, 16, 20, 16)
        inner_layout.setSpacing(10)
        layout.addWidget(inner, stretch=1)
        # Redirect self._layout to inner so subclass code appends into body
        self._layout = inner_layout
        layout = inner_layout

        self._header_form = QFormLayout()
        self._header_form.setSpacing(10)
        layout.addLayout(self._header_form)
        self._build_header(self._header_form)

        products_label = QLabel("Produits *")
        products_label.setStyleSheet("font-weight: 700; margin-top: 6px;")
        layout.addWidget(products_label)

        with session_scope() as session:
            self._display_choices = [
                (
                    d.id,
                    f"{d.reference} — {d.brand} {d.phone_model} (stock : {d.quantity})".strip(),
                    self._default_price_cents(d),
                )
                for d in list_displays(session)
            ]

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Rechercher un produit (référence, marque, modèle)..."
        )
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        self.search_results = QListWidget()
        self.search_results.setFixedHeight(110)
        self.search_results.setVisible(False)
        self.search_results.itemClicked.connect(self._on_search_result_clicked)
        layout.addWidget(self.search_results)

        columns = ["Produit", "Quantité"]
        if self.show_price_column:
            columns += ["Prix unitaire", ""]
        else:
            columns += [""]
        self.lines_table = QTableWidget(0, len(columns))
        self.lines_table.setHorizontalHeaderLabels(columns)
        header = self.lines_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.lines_table.setColumnWidth(1, 100)
        if self.show_price_column:
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self.lines_table.setColumnWidth(2, 120)
            self.lines_table.setColumnWidth(3, 36)
        else:
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.lines_table.setColumnWidth(2, 36)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.verticalHeader().setDefaultSectionSize(44)
        self.lines_table.setFixedHeight(160)
        layout.addWidget(self.lines_table)

        if self.show_price_column:
            totals_row = QHBoxLayout()
            totals_row.addStretch()
            totals_row.addWidget(QLabel("Total :"))
            self.total_label = QLabel("0 DA")
            self.total_label.setStyleSheet("font-weight: 700;")
            totals_row.addWidget(self.total_label)
            layout.addLayout(totals_row)
        else:
            self.total_label = None

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setFixedHeight(40)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("✔  Valider")
        save_btn.setFixedHeight(40)
        save_btn.setStyleSheet(
            "QPushButton { background: #00897b; color: white; border: none; border-radius: 6px;"
            " padding: 10px 18px; font-weight: 700; font-size: 13px; }"
            " QPushButton:hover { background: #00796b; }"
        )
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _build_header(self, form: QFormLayout) -> None:
        """Sous-classes : ajouter ici les champs d'en-tête (fournisseur,
        revendeur, motif...). No-op par défaut."""

    def _default_price_cents(self, display) -> int:
        if self.sale_price_type == "wholesale":
            return display.sale_price_wholesale_cents
        if self.sale_price_type == "retail":
            return display.sale_price_retail_cents
        return 0

    # ------------------------------------------------------------------
    # Recherche et lignes de produit — calqué sur RepairDialog.
    # ------------------------------------------------------------------

    def _preload_display(self, display_id: int) -> None:
        for choice_id, label, price_cents in self._display_choices:
            if choice_id == display_id:
                self._add_line_row(choice_id, label, price_cents)
                break

    def _on_search_changed(self, text: str) -> None:
        text = text.strip().lower()
        self.search_results.clear()

        if not text:
            self.search_results.setVisible(False)
            return

        already_added = {display_id for display_id, _spin, _price in self._line_rows}
        matches = [
            (display_id, label, price_cents)
            for display_id, label, price_cents in self._display_choices
            if text in label.lower() and display_id not in already_added
        ]

        if not matches:
            self.search_results.setVisible(False)
            return

        for display_id, label, price_cents in matches:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (display_id, price_cents))
            self.search_results.addItem(item)
        self.search_results.setVisible(True)

    def _on_search_result_clicked(self, item: QListWidgetItem) -> None:
        display_id, price_cents = item.data(Qt.ItemDataRole.UserRole)
        label = item.text()
        self._add_line_row(display_id, label, price_cents)

        self.search_input.clear()
        self.search_results.clear()
        self.search_results.setVisible(False)

    def _add_line_row(self, display_id: int, label: str, price_cents: int) -> None:
        row_index = self.lines_table.rowCount()
        self.lines_table.insertRow(row_index)

        name_item = QTableWidgetItem(label)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.lines_table.setItem(row_index, 0, name_item)

        quantity_spin = ModernSpinBox()
        quantity_spin.setRange(1, 1_000_000)
        quantity_spin.setMinimumHeight(32)
        self.lines_table.setCellWidget(row_index, 1, quantity_spin)

        price_spin: ModernDoubleSpinBox | None = None
        if self.show_price_column:
            price_spin = ModernDoubleSpinBox()
            price_spin.setRange(0, 10_000_000)
            price_spin.setDecimals(0)
            price_spin.setSuffix(" DA")
            price_spin.setMinimumHeight(32)
            price_spin.setValue(cents_to_da(price_cents))
            price_spin.valueChanged.connect(self._update_total)
            self.lines_table.setCellWidget(row_index, 2, price_spin)
            quantity_spin.valueChanged.connect(self._update_total)
            remove_column = 3
        else:
            remove_column = 2

        remove_button = QPushButton("×")
        remove_button.setObjectName("IconButton")
        remove_button.setStyleSheet("padding: 0; font-size: 15px; color: #dc2626; font-weight: 700;")
        remove_button.clicked.connect(lambda: self._remove_line_row(quantity_spin))
        self.lines_table.setCellWidget(row_index, remove_column, remove_button)

        self._line_rows.append((display_id, quantity_spin, price_spin))
        self._update_total()

    def _remove_line_row(self, quantity_spin: ModernSpinBox) -> None:
        for row_index, (_display_id, spin, _price) in enumerate(self._line_rows):
            if spin is quantity_spin:
                self.lines_table.removeRow(row_index)
                self._line_rows.pop(row_index)
                break
        self._update_total()

    def _update_total(self) -> None:
        if self.total_label is None:
            return
        total_cents = sum(
            da_to_cents(price.value()) * spin.value()
            for _display_id, spin, price in self._line_rows
            if price is not None
        )
        self.total_label.setText(format_da(total_cents))

    # ------------------------------------------------------------------
    # Sauvegarde
    # ------------------------------------------------------------------

    def _batch_kwargs(self) -> dict:
        """Sous-classes : fournir reason/supplier_id/reseller_id."""
        raise NotImplementedError

    def _on_save(self) -> None:
        lines = [
            (display_id, spin.value(), da_to_cents(price.value()) if price is not None else None)
            for display_id, spin, price in self._line_rows
        ]

        try:
            kwargs = self._batch_kwargs()
        except StockError as error:
            self.error_label.setText(str(error))
            return

        try:
            with session_scope() as session:
                apply_stock_batch(
                    session,
                    direction=self.direction,
                    lines=lines,
                    is_sale=self.is_sale,
                    sale_price_type=self.sale_price_type,
                    **kwargs,
                )
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()


class SupplierPurchaseDialog(_BaseStockBatchDialog):
    """Achat fournisseur — fait toujours entrer du stock."""

    direction = 1
    is_sale = False
    sale_price_type = None
    show_price_column = True
    _dialog_title = "Achat fournisseur"

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__(display_id)

    def _default_price_cents(self, display) -> int:
        return display.purchase_price_cents

    def _build_header(self, form: QFormLayout) -> None:
        self.supplier_input = QComboBox()
        self.supplier_input.addItem("Aucun", None)
        with session_scope() as session:
            for supplier in list_suppliers(session):
                self.supplier_input.addItem(supplier.name, supplier.id)
        form.addRow(field_label("Fournisseur", icon="users"), self.supplier_input)

    def _batch_kwargs(self) -> dict:
        return dict(reason="Achat fournisseur", supplier_id=self.supplier_input.currentData())


class WholesaleSaleDialog(_BaseStockBatchDialog):
    """Vente en gros à un revendeur — fait toujours sortir du stock."""

    direction = -1
    is_sale = True
    sale_price_type = "wholesale"
    show_price_column = True
    _dialog_title = "Vente en gros (revendeur)"

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__(display_id)

    def _build_header(self, form: QFormLayout) -> None:
        self.reseller_input = QComboBox()
        self.reseller_input.addItem("Aucun", None)
        with session_scope() as session:
            for reseller in list_resellers(session):
                self.reseller_input.addItem(reseller.name, reseller.id)
        form.addRow(field_label("Revendeur", icon="users"), self.reseller_input)

    def _batch_kwargs(self) -> dict:
        return dict(reason="Vente", reseller_id=self.reseller_input.currentData())


class StockAdjustmentDialog(_BaseStockBatchDialog):
    """Ajustement de stock (retour, casse, perte, correction) — le sens
    n'est pas fixé par l'action, il se choisit dans le formulaire."""

    is_sale = False
    sale_price_type = None
    show_price_column = False
    _dialog_title = "Ajustement de stock"

    def __init__(self, display_id: int | None = None) -> None:
        self.direction = 1
        super().__init__(display_id)

    def _build_header(self, form: QFormLayout) -> None:
        self.direction_input = QComboBox()
        self.direction_input.addItem("Entrée (ajout au stock)", 1)
        self.direction_input.addItem("Sortie (retrait du stock)", -1)
        self.direction_input.currentIndexChanged.connect(self._on_direction_changed)
        form.addRow(field_label("Sens *"), self.direction_input)

        self.reason_input = QComboBox()
        self.reason_input.setEditable(True)
        self.reason_input.addItems(ADJUSTMENT_REASONS)
        form.addRow(field_label("Motif *"), self.reason_input)

    def _on_direction_changed(self) -> None:
        self.direction = self.direction_input.currentData()

    def _batch_kwargs(self) -> dict:
        reason = self.reason_input.currentText().strip()
        if not reason:
            raise StockError("Un motif est obligatoire pour ajuster le stock.")
        return dict(reason=reason)
