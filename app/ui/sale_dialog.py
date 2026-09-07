"""Dialogue de vente unifié — client au détail ou revendeur en gros, multi-pièces."""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import (
    QSizePolicy,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import cents_to_da, da_to_cents, format_da
from app.services import StockError, apply_stock_batch, list_displays, list_resellers
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox


def _spin_cell(widget: QWidget, v_margin: int = 8) -> QWidget:
    """Wrapper pour spinbox : occupe toute la largeur de la cellule."""
    wrapper = QWidget()
    wrapper.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(6, v_margin, 6, v_margin)
    layout.setSpacing(0)
    layout.addWidget(widget)
    return wrapper


def _centered(widget: QWidget, h_margin: int = 4, v_margin: int = 6) -> QWidget:
    """Enveloppe un widget dans un conteneur centré pour les cellules de tableau."""
    wrapper = QWidget()
    wrapper.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(h_margin, v_margin, h_margin, v_margin)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(widget)
    return wrapper


class SaleDialog(QDialog):
    """Vente multi-pièces : client au détail ou revendeur en gros."""

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Nouvelle vente")
        # Supprime les boutons Réduire et Restaurer — garde uniquement X
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.CustomizeWindowHint
        )
        self.resize(900, 700)
        self.setMinimumSize(740, 540)
        self.setModal(True)
        self._drag_pos: QPoint | None = None
        # (display_id, retail_cents, wholesale_cents, qty_spin, price_spin)
        self._line_rows: list[tuple[int, int, int, ModernSpinBox, ModernDoubleSpinBox]] = []
        self._display_choices: list[tuple[int, str, str, int, int]] = []
        self._build_ui()
        with session_scope() as session:
            self._display_choices = [
                (
                    d.id,
                    d.reference,
                    f"{d.brand} {d.phone_model}".strip(),
                    d.category or "",
                    d.sale_price_retail_cents,
                    d.sale_price_wholesale_cents,
                )
                for d in list_displays(session)
            ]
        if display_id is not None:
            self._preload_display(display_id)

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # En-tête
        header = QFrame()
        header.setStyleSheet("background: #1e293b; border-radius: 0;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Nouvelle vente")
        title.setStyleSheet("color: white; font-size: 15px; font-weight: 700;")
        header_layout.addWidget(title)
        layout.addWidget(header)

        # Corps
        body = QWidget()
        body.setStyleSheet("background: #ffffff;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 18, 20, 18)
        body_layout.setSpacing(14)

        # --- Toggle client / revendeur ---
        toggle_frame = QFrame()
        toggle_frame.setStyleSheet(
            "background: #f1f5f9; border-radius: 10px; padding: 2px;"
        )
        toggle_layout = QHBoxLayout(toggle_frame)
        toggle_layout.setContentsMargins(3, 3, 3, 3)
        toggle_layout.setSpacing(3)

        self.retail_button = QPushButton("Client detail")
        self.retail_button.setCheckable(True)
        self.retail_button.setChecked(True)
        self._style_toggle(self.retail_button, True)
        self.retail_button.clicked.connect(lambda: self._set_sale_type("retail"))
        toggle_layout.addWidget(self.retail_button)

        self.wholesale_button = QPushButton("Revendeur gros")
        self.wholesale_button.setCheckable(True)
        self._style_toggle(self.wholesale_button, False)
        self.wholesale_button.clicked.connect(lambda: self._set_sale_type("wholesale"))
        toggle_layout.addWidget(self.wholesale_button)

        body_layout.addWidget(toggle_frame)

        # --- Revendeur (masqué par défaut) ---
        self.reseller_row = QWidget()
        reseller_layout = QHBoxLayout(self.reseller_row)
        reseller_layout.setContentsMargins(0, 0, 0, 0)
        reseller_label = QLabel("Revendeur :")
        reseller_label.setStyleSheet("font-weight: 600; color: #374151;")
        reseller_label.setFixedWidth(90)
        reseller_layout.addWidget(reseller_label)
        self.reseller_combo = QComboBox()
        self.reseller_combo.addItem("— Sélectionner un revendeur —", None)
        with session_scope() as session:
            for r in list_resellers(session):
                self.reseller_combo.addItem(
                    f"{r.name}" + (f"  ·  {r.phone}" if r.phone else ""), r.id
                )
        reseller_layout.addWidget(self.reseller_combo, stretch=1)
        body_layout.addWidget(self.reseller_row)
        self.reseller_row.setVisible(False)

        # --- Recherche + Ajouter ---
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Ajouter une pièce au panier...")
        self.search_input.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_input, stretch=1)

        add_btn = QPushButton("+ Ajouter")
        add_btn.setFixedWidth(100)
        add_btn.clicked.connect(self._on_add_clicked)
        search_row.addWidget(add_btn)
        body_layout.addLayout(search_row)

        # Résultats de recherche
        self.search_results = QListWidget()
        self.search_results.setFixedHeight(100)
        self.search_results.setVisible(False)
        self.search_results.itemClicked.connect(self._on_result_clicked)
        body_layout.addWidget(self.search_results)

        # --- Tableau des lignes ---
        self.lines_table = QTableWidget(0, 5)
        self.lines_table.setHorizontalHeaderLabels(["PIÈCE", "QTÉ", "PRIX UNIT.", "TOTAL", ""])
        self.lines_table.setStyleSheet(
            "QHeaderView::section { background: #f8fafc; color: #64748b; font-size: 11px; font-weight: 700;"
            " padding: 10px 12px; border: none; border-bottom: 2px solid #e2e8f0; letter-spacing: 0.5px; }"
            "QTableWidget { border: 1px solid #e2e8f0; border-radius: 8px; gridline-color: transparent; }"
            "QTableWidget::item { padding: 0 12px; }"
        )
        tbl_header = self.lines_table.horizontalHeader()
        tbl_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tbl_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.lines_table.setColumnWidth(1, 130)
        self.lines_table.setColumnWidth(2, 170)
        self.lines_table.setColumnWidth(3, 140)
        self.lines_table.setColumnWidth(4, 50)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.verticalHeader().setDefaultSectionSize(56)
        self.lines_table.setMinimumHeight(120)
        self.lines_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.lines_table.setShowGrid(False)
        self.lines_table.setAlternatingRowColors(True)
        body_layout.addWidget(self.lines_table, stretch=1)

        # --- Remise ---
        remise_row = QHBoxLayout()
        remise_row.addStretch()
        remise_label = QLabel("Remise (optionnel)")
        remise_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        remise_row.addWidget(remise_label)
        remise_row.addSpacing(12)

        self.remise_spin = ModernDoubleSpinBox()
        self.remise_spin.setRange(0, 99_999_999)
        self.remise_spin.setDecimals(0)
        self.remise_spin.setFixedWidth(140)
        self.remise_spin.setSuffix(" DA")
        self.remise_spin.valueChanged.connect(self._update_total)
        remise_row.addWidget(self.remise_spin)
        body_layout.addLayout(remise_row)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #e2e8f0;")
        body_layout.addWidget(sep)

        # Total à payer
        total_row = QHBoxLayout()
        total_text = QLabel("Total à payer")
        total_text.setStyleSheet("color: #374151; font-weight: 600; font-size: 14px;")
        total_row.addWidget(total_text)
        total_row.addStretch()
        self.total_label = QLabel("0 DA")
        self.total_label.setStyleSheet("color: #111827; font-weight: 800; font-size: 22px;")
        total_row.addWidget(self.total_label)
        body_layout.addLayout(total_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        body_layout.addWidget(self.error_label)

        layout.addWidget(body, stretch=1)

        # --- Boutons d'action ---
        footer = QWidget()
        footer.setStyleSheet(
            "background: #f8fafc; border-top: 1px solid #e2e8f0;"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 14, 20, 14)
        footer_layout.setSpacing(10)

        cancel_btn = QPushButton("Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setFixedWidth(110)
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)

        footer_layout.addStretch()

        self.confirm_btn = QPushButton("Confirmer — 0 DA")
        self.confirm_btn.setFixedHeight(40)
        self.confirm_btn.setMinimumWidth(200)
        self.confirm_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; border: none; border-radius: 8px; font-weight: 700; font-size: 14px; padding: 0 20px; }"
            "QPushButton:hover { background: #15803d; }"
            "QPushButton:pressed { background: #166534; }"
        )
        self.confirm_btn.clicked.connect(self._on_save)
        footer_layout.addWidget(self.confirm_btn)

        layout.addWidget(footer)

    def _style_toggle(self, btn: QPushButton, active: bool) -> None:
        if active:
            btn.setStyleSheet(
                "QPushButton { background: #2563eb; color: white; border: none; border-radius: 8px; padding: 9px 16px; font-weight: 700; font-size: 13px; }"
                "QPushButton:hover { background: #1d4ed8; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #6b7280; border: none; border-radius: 8px; padding: 9px 16px; font-weight: 600; font-size: 13px; }"
                "QPushButton:hover { color: #374151; background: #e2e8f0; }"
            )

    # ------------------------------------------------------------------
    # Type de vente
    # ------------------------------------------------------------------

    def _sale_type(self) -> str:
        return "wholesale" if self.wholesale_button.isChecked() else "retail"

    def _set_sale_type(self, sale_type: str) -> None:
        is_wholesale = sale_type == "wholesale"
        self.retail_button.setChecked(not is_wholesale)
        self.wholesale_button.setChecked(is_wholesale)
        self._style_toggle(self.retail_button, not is_wholesale)
        self._style_toggle(self.wholesale_button, is_wholesale)
        self.reseller_row.setVisible(is_wholesale)
        for _d_id, retail_cents, wholesale_cents, qty_spin, price_spin in self._line_rows:
            price_spin.setValue(cents_to_da(wholesale_cents if is_wholesale else retail_cents))
        self._update_total()

    # ------------------------------------------------------------------
    # Recherche et lignes
    # ------------------------------------------------------------------

    def _preload_display(self, display_id: int) -> None:
        for d_id, ref, label, cat, retail, wholesale in self._display_choices:
            if d_id == display_id:
                self._add_line_row(d_id, ref, label, retail, wholesale, cat)
                break

    def _on_search_changed(self, text: str) -> None:
        text = text.strip().lower()
        self.search_results.clear()
        if not text:
            self.search_results.setVisible(False)
            return
        already = {d_id for d_id, *_ in self._line_rows}
        matches = [
            (d_id, ref, label, cat, ret, who)
            for d_id, ref, label, cat, ret, who in self._display_choices
            if (text in ref.lower() or text in label.lower() or text in cat.lower())
            and d_id not in already
        ]
        if not matches:
            self.search_results.setVisible(False)
            return
        for d_id, ref, label, cat, ret, who in matches:
            parts = []
            if cat:
                parts.append(cat)
            if label:
                parts.append(label)
            parts.append(ref)
            display_text = "  ·  ".join(parts)
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, (d_id, ref, label, cat, ret, who))
            self.search_results.addItem(item)
        self.search_results.setVisible(True)

    def _on_add_clicked(self) -> None:
        items = self.search_results.selectedItems()
        if items:
            self._on_result_clicked(items[0])
        elif self.search_results.count() == 1:
            self._on_result_clicked(self.search_results.item(0))

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        d_id, ref, label, cat, ret, who = item.data(Qt.ItemDataRole.UserRole)
        self._add_line_row(d_id, ref, label, ret, who, cat)
        self.search_input.clear()
        self.search_results.clear()
        self.search_results.setVisible(False)

    def _add_line_row(
        self,
        display_id: int,
        ref: str,
        label: str,
        retail_cents: int,
        wholesale_cents: int,
        category: str = "",
    ) -> None:
        is_wholesale = self._sale_type() == "wholesale"
        base_price = wholesale_cents if is_wholesale else retail_cents
        row = self.lines_table.rowCount()
        self.lines_table.insertRow(row)

        # Cellule Pièce : nom en gras + "catégorie · référence" en gris dessous
        name_widget = QWidget()
        name_layout = QVBoxLayout(name_widget)
        name_layout.setContentsMargins(12, 6, 8, 6)
        name_layout.setSpacing(2)
        name_lbl = QLabel(label or ref)
        name_lbl.setStyleSheet("font-weight: 700; color: #111827; font-size: 12px;")
        name_layout.addWidget(name_lbl)
        sub_text = f"{category}  ·  {ref}" if category else ref
        ref_lbl = QLabel(sub_text)
        ref_lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
        name_layout.addWidget(ref_lbl)
        self.lines_table.setCellWidget(row, 0, name_widget)

        qty_spin = ModernSpinBox()
        qty_spin.setRange(1, 1_000_000)
        qty_spin.setFixedHeight(36)
        qty_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        qty_spin.valueChanged.connect(self._update_total)
        self.lines_table.setCellWidget(row, 1, _spin_cell(qty_spin))

        price_spin = ModernDoubleSpinBox()
        price_spin.setRange(0, 99_999_999)
        price_spin.setDecimals(0)
        price_spin.setSuffix(" DA")
        price_spin.setFixedHeight(36)
        price_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        price_spin.setValue(cents_to_da(base_price))
        price_spin.valueChanged.connect(self._update_total)
        self.lines_table.setCellWidget(row, 2, _spin_cell(price_spin))

        total_item = QTableWidgetItem(format_da(base_price))
        total_item.setFlags(total_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        total_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        total_item.setForeground(Qt.GlobalColor.darkGray)
        self.lines_table.setItem(row, 3, total_item)

        remove_btn = QPushButton("×")
        remove_btn.setObjectName("IconButton")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet("padding: 0; font-size: 16px; color: #dc2626; font-weight: 700;")
        remove_btn.setToolTip("Supprimer")
        remove_btn.clicked.connect(lambda: self._remove_line_row(qty_spin))
        self.lines_table.setCellWidget(row, 4, _centered(remove_btn, h_margin=8))

        self._line_rows.append((display_id, retail_cents, wholesale_cents, qty_spin, price_spin))
        self._update_total()

    def _remove_line_row(self, qty_spin: ModernSpinBox) -> None:
        for i, (_d_id, _ret, _who, spin, _price) in enumerate(self._line_rows):
            if spin is qty_spin:
                self.lines_table.removeRow(i)
                self._line_rows.pop(i)
                break
        self._update_total()

    def _update_total(self) -> None:
        subtotal = 0
        for i, (_d_id, _ret, _who, qty_spin, price_spin) in enumerate(self._line_rows):
            line_total = da_to_cents(price_spin.value()) * qty_spin.value()
            subtotal += line_total
            item = self.lines_table.item(i, 3)
            if item:
                item.setText(format_da(line_total))
        remise = min(da_to_cents(self.remise_spin.value()), subtotal)
        net = max(0, subtotal - remise)
        net_str = format_da(net)
        self.total_label.setText(net_str)
        self.confirm_btn.setText(f"Confirmer — {net_str}")

    # ------------------------------------------------------------------
    # Sauvegarde
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        if not self._line_rows:
            self.error_label.setText("Ajoutez au moins une pièce au panier.")
            return

        sale_type = self._sale_type()
        reseller_id = self.reseller_combo.currentData() if sale_type == "wholesale" else None

        subtotal_cents = sum(
            da_to_cents(price.value()) * qty.value()
            for _d_id, _ret, _who, qty, price in self._line_rows
        )
        remise_cents = min(da_to_cents(self.remise_spin.value()), subtotal_cents)
        discount_ratio = remise_cents / subtotal_cents if subtotal_cents > 0 else 0.0

        lines = []
        for display_id, _ret, _who, qty_spin, price_spin in self._line_rows:
            unit_price = da_to_cents(price_spin.value())
            if discount_ratio > 0:
                unit_price = round(unit_price * (1 - discount_ratio))
            lines.append((display_id, qty_spin.value(), unit_price))

        try:
            with session_scope() as session:
                apply_stock_batch(
                    session,
                    direction=-1,
                    reason="Vente",
                    lines=lines,
                    is_sale=True,
                    sale_price_type=sale_type,
                    reseller_id=reseller_id,
                )
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()
