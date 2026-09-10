"""Dialogue de vente unifié — style POS (caisse enregistreuse)."""

from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
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
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import cents_to_da, da_to_cents, format_da
from app.services import StockError, apply_stock_batch, list_displays, list_resellers
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox


def _spin_cell(widget: QWidget, v_margin: int = 4) -> QWidget:
    wrapper = QWidget()
    wrapper.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(4, v_margin, 4, v_margin)
    layout.setSpacing(0)
    layout.addWidget(widget)
    return wrapper


def _centered(widget: QWidget, h_margin: int = 4, v_margin: int = 4) -> QWidget:
    wrapper = QWidget()
    wrapper.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(h_margin, v_margin, h_margin, v_margin)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(widget)
    return wrapper


class SaleDialog(FramelessDialog):
    """Vente multi-pièces — interface style caisse."""

    def __init__(self, display_id: int | None = None) -> None:
        super().__init__()
        self.resize(1000, 700)
        self.setMinimumSize(800, 560)
        self.setModal(True)
        self._line_rows: list[tuple[int, int, int, ModernSpinBox, ModernDoubleSpinBox]] = []
        self._display_choices: list[tuple[int, str, str, str, int, int]] = []
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
                    d.quantity,
                )
                for d in list_displays(session)
            ]
        if display_id is not None:
            self._preload_display(display_id)

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── HEADER SOMBRE ──────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet("background-color: #1a1a2e; border-radius: 0;")
        header.setFixedHeight(90)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(20)

        # Bloc gauche : Mode de vente + Remise
        left_info = QVBoxLayout()
        left_info.setSpacing(6)

        # Toggle détail / gros
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(4)
        self.retail_button = QPushButton("Détail")
        self.retail_button.setCheckable(True)
        self.retail_button.setChecked(True)
        self._style_toggle(self.retail_button, True)
        self.retail_button.clicked.connect(lambda: self._set_sale_type("retail"))
        toggle_row.addWidget(self.retail_button)

        self.wholesale_button = QPushButton("Gros")
        self.wholesale_button.setCheckable(True)
        self._style_toggle(self.wholesale_button, False)
        self.wholesale_button.clicked.connect(lambda: self._set_sale_type("wholesale"))
        toggle_row.addWidget(self.wholesale_button)
        toggle_row.addStretch()
        left_info.addLayout(toggle_row)

        # Remise
        remise_row = QHBoxLayout()
        remise_row.setSpacing(8)
        remise_lbl = QLabel("Remise :")
        remise_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        remise_row.addWidget(remise_lbl)
        self.remise_spin = ModernDoubleSpinBox()
        self.remise_spin.setRange(0, 99_999_999)
        self.remise_spin.setDecimals(0)
        self.remise_spin.setSuffix(" DA")
        self.remise_spin.setFixedWidth(120)
        self.remise_spin.setStyleSheet(
            "QDoubleSpinBox { background: #2d2d44; color: white; border: 1px solid #444; "
            "border-radius: 4px; padding: 4px 8px; font-size: 13px; }"
        )
        self.remise_spin.valueChanged.connect(self._update_total)
        remise_row.addWidget(self.remise_spin)
        remise_row.addStretch()
        left_info.addLayout(remise_row)

        header_layout.addLayout(left_info)

        # Séparateur vertical
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("background: #444; border: none;")
        sep1.setFixedWidth(1)
        header_layout.addWidget(sep1)

        # Bloc centre : date + infos vente
        center_info = QVBoxLayout()
        center_info.setSpacing(4)
        now = datetime.datetime.now()
        day_names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        day_str = day_names[now.weekday()]
        date_str = f"{day_str}  {now.day} {now.strftime('%B %Y')}"
        date_lbl = QLabel(date_str.capitalize())
        date_lbl.setStyleSheet("color: #d1d5db; font-size: 12px; font-weight: 600;")
        center_info.addWidget(date_lbl)
        vente_lbl = QLabel("Nouvelle vente")
        vente_lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
        center_info.addWidget(vente_lbl)
        header_layout.addLayout(center_info)

        # Revendeur (masqué par défaut)
        self.reseller_col = QVBoxLayout()
        self.reseller_col.setSpacing(2)
        reseller_title = QLabel("Revendeur")
        reseller_title.setStyleSheet("color: #9ca3af; font-size: 11px;")
        self.reseller_col.addWidget(reseller_title)
        self.reseller_combo = QComboBox()
        self.reseller_combo.addItem("— Sélectionner —", None)
        with session_scope() as session:
            for r in list_resellers(session):
                self.reseller_combo.addItem(
                    f"{r.name}" + (f"  ·  {r.phone}" if r.phone else ""), r.id
                )
        self.reseller_combo.setStyleSheet(
            "QComboBox { background: #2d2d44; color: white; border: 1px solid #444; "
            "border-radius: 4px; padding: 4px 8px; font-size: 12px; min-width: 160px; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
        )
        self.reseller_combo.currentIndexChanged.connect(self._on_reseller_changed)
        self.reseller_col.addWidget(self.reseller_combo)
        self.reseller_widget = QWidget()
        self.reseller_widget.setLayout(self.reseller_col)
        self.reseller_widget.setVisible(False)
        header_layout.addWidget(self.reseller_widget)

        header_layout.addStretch()

        # Bloc droit : Total grand
        total_col = QVBoxLayout()
        total_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        total_title = QLabel("Total :")
        total_title.setStyleSheet("color: #9ca3af; font-size: 13px; font-weight: 600;")
        total_title.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_col.addWidget(total_title)
        self.total_label = QLabel("0,00")
        self.total_label.setStyleSheet(
            "color: #00e676; font-size: 36px; font-weight: 900; letter-spacing: -1px;"
        )
        self.total_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_col.addWidget(self.total_label)
        header_layout.addLayout(total_col)

        # Bouton fermer intégré
        close_btn = QPushButton("✕")
        close_btn.setObjectName("FramelessCloseBtn")
        close_btn.setFixedSize(38, 38)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)

        # Draggable
        self._drag_header = header
        header.mousePressEvent = self._header_mouse_press
        header.mouseMoveEvent = self._header_mouse_move
        header.mouseReleaseEvent = self._header_mouse_release

        root.addWidget(header)

        # ── CORPS ──────────────────────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background: #f5f5f5;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 8)
        body_layout.setSpacing(8)

        # Barre de recherche
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_icon_lbl = QLabel("Recherche :")
        search_icon_lbl.setStyleSheet("font-weight: 700; color: #374151; font-size: 13px;")
        search_row.addWidget(search_icon_lbl)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Référence, modèle, catégorie...")
        self.search_input.setStyleSheet(
            "QLineEdit { background: white; border: 2px solid #2563eb; border-radius: 6px; "
            "padding: 8px 14px; font-size: 13px; }"
            "QLineEdit:focus { border: 2px solid #1e40af; }"
        )
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._on_add_clicked)
        search_row.addWidget(self.search_input, stretch=1)

        add_btn = QPushButton("+ Ajouter Produit")
        add_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none; border-radius: 6px; "
            "padding: 9px 16px; font-weight: 700; font-size: 13px; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        add_btn.clicked.connect(self._on_add_clicked)
        search_row.addWidget(add_btn)
        body_layout.addLayout(search_row)

        # Liste de résultats de recherche
        self.search_results = QListWidget()
        self.search_results.setFixedHeight(120)
        self.search_results.setVisible(False)
        self.search_results.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #e0e0e0; border-radius: 6px; "
            "font-size: 13px; outline: none; }"
            "QListWidget::item { padding: 8px 14px; border-bottom: 1px solid #f5f5f5; }"
            "QListWidget::item:selected { background: #eff6ff; color: #1e40af; }"
            "QListWidget::item:hover { background: #f8faff; }"
        )
        self.search_results.itemClicked.connect(self._on_result_clicked)
        body_layout.addWidget(self.search_results)

        # Tableau panier — 7 colonnes dont Stock
        self.lines_table = QTableWidget(0, 7)
        self.lines_table.setHorizontalHeaderLabels(
            ["Référence", "Désignation", "Stock", "Prix U.", "Qté", "Montant", ""]
        )
        self.lines_table.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #e0e0e0; border-radius: 8px; "
            "gridline-color: #f0f0f0; }"
            "QHeaderView::section { background: #2563eb; color: white; font-weight: 700; "
            "padding: 8px 10px; border: none; border-right: 1px solid #1d4ed8; font-size: 12px; }"
            "QHeaderView::section:last { border-right: none; }"
            "QTableWidget::item { padding: 4px 8px; border: none; }"
            "QTableWidget::item:selected { background: #bfdbfe; color: #212121; }"
            "QTableWidget::item:hover { background: #eff6ff; }"
        )
        tbl_header = self.lines_table.horizontalHeader()
        tbl_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        tbl_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.lines_table.setColumnWidth(0, 110)
        self.lines_table.setColumnWidth(2, 60)   # Stock
        self.lines_table.setColumnWidth(3, 150)  # Prix U.
        self.lines_table.setColumnWidth(4, 120)  # Qté
        self.lines_table.setColumnWidth(5, 130)  # Montant
        self.lines_table.setColumnWidth(6, 46)   # ×
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.verticalHeader().setDefaultSectionSize(48)
        self.lines_table.setMinimumHeight(120)
        self.lines_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.lines_table.setShowGrid(True)
        self.lines_table.setAlternatingRowColors(True)
        self.lines_table.setStyleSheet(
            self.lines_table.styleSheet()
            + "QTableWidget { alternate-background-color: #f8fdfc; }"
        )
        body_layout.addWidget(self.lines_table, stretch=1)

        # Erreur
        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setStyleSheet("color: #dc2626; font-size: 12px;")
        self.error_label.setWordWrap(True)
        body_layout.addWidget(self.error_label)

        root.addWidget(body, stretch=1)

        # ── BARRE DE PIED ──────────────────────────────────────────────
        footer = QFrame()
        footer.setStyleSheet(
            "QFrame { background: #1a1a2e; border-top: 2px solid #2563eb; }"
        )
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 10, 16, 10)
        footer_layout.setSpacing(6)

        # ── Ligne versement (toujours dans le layout, visible/caché selon mode) ──
        vers_row = QHBoxLayout()
        vers_row.setSpacing(16)

        vers_lbl = QLabel("💰  Versement :")
        vers_lbl.setStyleSheet("color: #94a3b8; font-size: 13px;")
        vers_row.addWidget(vers_lbl)

        self.versement_spin = ModernDoubleSpinBox()
        self.versement_spin.setRange(0, 999_999_999)
        self.versement_spin.setDecimals(0)
        self.versement_spin.setSuffix(" DA")
        self.versement_spin.setFixedWidth(180)
        self.versement_spin.setStyleSheet(
            "QDoubleSpinBox { background: #1e293b; color: #00e676; "
            "border: 2px solid #2563eb; border-radius: 6px; "
            "padding: 5px 10px; font-size: 14px; font-weight: 700; }"
            "QDoubleSpinBox:focus { border: 2px solid #00e676; }"
        )
        self.versement_spin.valueChanged.connect(self._update_reste)
        vers_row.addWidget(self.versement_spin)

        vers_sep = QLabel("|")
        vers_sep.setStyleSheet("color: #334155; font-size: 16px;")
        vers_row.addWidget(vers_sep)

        reste_lbl_title = QLabel("Reste dû :")
        reste_lbl_title.setStyleSheet("color: #94a3b8; font-size: 13px;")
        vers_row.addWidget(reste_lbl_title)

        self._reste_label = QLabel("0 DA  ✓")
        self._reste_label.setStyleSheet("color: #00e676; font-size: 15px; font-weight: 700;")
        vers_row.addWidget(self._reste_label)

        vers_row.addStretch()

        # Widgets de la ligne versement — initialement cachés
        self._vers_widgets = [vers_lbl, self.versement_spin, vers_sep,
                              reste_lbl_title, self._reste_label]
        for w in self._vers_widgets:
            w.setVisible(False)

        footer_layout.addLayout(vers_row)

        # ── Ligne boutons ──────────────────────────────────────────────
        btn_hl = QHBoxLayout()
        btn_hl.setSpacing(8)
        cancel_btn = self._action_btn("✕  Fermer (Echap)", "#ef4444", "#dc2626")
        cancel_btn.clicked.connect(self.reject)
        btn_hl.addWidget(cancel_btn)
        btn_hl.addStretch()
        self.count_label = QLabel("Nombre de produits : 0")
        self.count_label.setStyleSheet("color: #9ca3af; font-size: 12px;")
        btn_hl.addWidget(self.count_label)
        btn_hl.addSpacing(16)
        self.confirm_btn = self._action_btn("✔  Valider la vente", "#2563eb", "#1e40af")
        self.confirm_btn.setMinimumWidth(180)
        self.confirm_btn.clicked.connect(self._on_save)
        btn_hl.addWidget(self.confirm_btn)
        footer_layout.addLayout(btn_hl)

        root.addWidget(footer)

    def _action_btn(self, text: str, bg: str, bg_hover: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: white; border: none; border-radius: 6px; "
            f"padding: 10px 18px; font-weight: 700; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {bg_hover}; }}"
            f"QPushButton:pressed {{ background: {bg_hover}; }}"
        )
        return btn

    def _style_toggle(self, btn: QPushButton, active: bool) -> None:
        if active:
            btn.setStyleSheet(
                "QPushButton { background: #2563eb; color: white; border: none; border-radius: 5px; "
                "padding: 6px 14px; font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #1d4ed8; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background: #2d2d44; color: #9ca3af; border: none; border-radius: 5px; "
                "padding: 6px 14px; font-weight: 600; font-size: 12px; }"
                "QPushButton:hover { color: white; background: #3d3d5c; }"
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
        self.reseller_widget.setVisible(is_wholesale)
        for w in self._vers_widgets:
            w.setVisible(is_wholesale)
        for _d_id, retail_cents, wholesale_cents, qty_spin, price_spin in self._line_rows:
            price_spin.setValue(cents_to_da(wholesale_cents if is_wholesale else retail_cents))
        self._update_total()
        if is_wholesale:
            self._sync_versement_to_total()

    def _on_reseller_changed(self) -> None:
        if self._sale_type() == "wholesale":
            self._sync_versement_to_total()

    def _sync_versement_to_total(self) -> None:
        subtotal = sum(
            da_to_cents(price.value()) * qty.value()
            for _d_id, _ret, _who, qty, price in self._line_rows
        )
        remise = min(da_to_cents(self.remise_spin.value()), subtotal)
        net = max(0, subtotal - remise)
        self.versement_spin.blockSignals(True)
        self.versement_spin.setValue(cents_to_da(net))
        self.versement_spin.blockSignals(False)
        self._update_reste()

    def _update_reste(self) -> None:
        subtotal = sum(
            da_to_cents(price.value()) * qty.value()
            for _d_id, _ret, _who, qty, price in self._line_rows
        )
        remise = min(da_to_cents(self.remise_spin.value()), subtotal)
        net = max(0, subtotal - remise)
        vers = da_to_cents(self.versement_spin.value())
        reste = max(0, net - vers)
        if reste == 0:
            self._reste_label.setText("0 DA  ✓")
            self._reste_label.setStyleSheet(
                "color: #00e676; font-size: 15px; font-weight: 700;"
            )
        else:
            self._reste_label.setText(f"{format_da(reste)} DA  ⚠")
            self._reste_label.setStyleSheet(
                "color: #f87171; font-size: 15px; font-weight: 700;"
            )

    # ------------------------------------------------------------------
    # Recherche et lignes
    # ------------------------------------------------------------------

    def _preload_display(self, display_id: int) -> None:
        for d_id, ref, label, cat, retail, wholesale, qty_stock in self._display_choices:
            if d_id == display_id:
                self._add_line_row(d_id, ref, label, retail, wholesale, cat, qty_stock)
                break

    def _on_search_changed(self, text: str) -> None:
        text = text.strip().lower()
        self.search_results.clear()
        if not text:
            self.search_results.setVisible(False)
            return
        already = {d_id for d_id, *_ in self._line_rows}
        matches = [
            (d_id, ref, label, cat, ret, who, qs)
            for d_id, ref, label, cat, ret, who, qs in self._display_choices
            if (text in ref.lower() or text in label.lower() or text in cat.lower())
            and d_id not in already
        ]
        if not matches:
            self.search_results.setVisible(False)
            return
        for d_id, ref, label, cat, ret, who, qs in matches:
            parts = []
            if cat:
                parts.append(cat)
            if label:
                parts.append(label)
            parts.append(ref)
            display_text = "  ·  ".join(parts)
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, (d_id, ref, label, cat, ret, who, qs))
            self.search_results.addItem(item)
        self.search_results.setVisible(True)

    def _on_add_clicked(self) -> None:
        items = self.search_results.selectedItems()
        if items:
            self._on_result_clicked(items[0])
        elif self.search_results.count() == 1:
            self._on_result_clicked(self.search_results.item(0))

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        d_id, ref, label, cat, ret, who, qs = item.data(Qt.ItemDataRole.UserRole)
        self._add_line_row(d_id, ref, label, ret, who, cat, qs)
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
        qty_stock: int = 0,
    ) -> None:
        from PySide6.QtGui import QColor
        is_wholesale = self._sale_type() == "wholesale"
        base_price = wholesale_cents if is_wholesale else retail_cents
        row = self.lines_table.rowCount()
        self.lines_table.insertRow(row)

        # Col 0 — Référence
        ref_item = QTableWidgetItem(ref)
        ref_item.setFlags(ref_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        ref_item.setForeground(Qt.GlobalColor.darkGray)
        ref_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.lines_table.setItem(row, 0, ref_item)

        # Col 1 — Désignation
        desc = f"{label or ref}  ·  {category}" if category else (label or ref)
        desc_item = QTableWidgetItem(desc)
        desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.lines_table.setItem(row, 1, desc_item)

        # Col 2 — Stock
        stock_item = QTableWidgetItem(str(qty_stock))
        stock_item.setFlags(stock_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        stock_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if qty_stock <= 1:
            stock_item.setForeground(QColor("#dc2626"))
            stock_item.setBackground(QColor("#fef2f2"))
        elif qty_stock <= 3:
            stock_item.setForeground(QColor("#d97706"))
            stock_item.setBackground(QColor("#fffbeb"))
        else:
            stock_item.setForeground(QColor("#16a34a"))
        self.lines_table.setItem(row, 2, stock_item)

        # Col 3 — Prix unitaire
        price_spin = ModernDoubleSpinBox()
        price_spin.setRange(0, 99_999_999)
        price_spin.setDecimals(0)
        price_spin.setSuffix(" DA")
        price_spin.setFixedHeight(34)
        price_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        price_spin.setValue(cents_to_da(base_price))
        price_spin.valueChanged.connect(self._update_total)
        self.lines_table.setCellWidget(row, 3, _spin_cell(price_spin))

        # Col 4 — Quantité
        qty_spin = ModernSpinBox()
        qty_spin.setRange(1, 1_000_000)
        qty_spin.setFixedHeight(34)
        qty_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        qty_spin.valueChanged.connect(self._update_total)
        self.lines_table.setCellWidget(row, 4, _spin_cell(qty_spin))

        # Col 5 — Montant
        total_item = QTableWidgetItem(format_da(base_price))
        total_item.setFlags(total_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        total_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        total_item.setForeground(Qt.GlobalColor.darkGray)
        self.lines_table.setItem(row, 5, total_item)

        # Col 6 — Supprimer
        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet(
            "QPushButton { background: #fee2e2; color: #dc2626; border: none; border-radius: 4px; "
            "font-size: 16px; font-weight: 700; padding: 0; }"
            "QPushButton:hover { background: #fca5a5; }"
        )
        remove_btn.setToolTip("Supprimer")
        remove_btn.clicked.connect(lambda: self._remove_line_row(qty_spin))
        self.lines_table.setCellWidget(row, 6, _centered(remove_btn))

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
            item = self.lines_table.item(i, 5)
            if item:
                item.setText(format_da(line_total))
        remise = min(da_to_cents(self.remise_spin.value()), subtotal)
        net = max(0, subtotal - remise)
        da_val = cents_to_da(net)
        self.total_label.setText(f"{da_val:,.2f}".replace(",", " ").replace(".", ","))
        self.confirm_btn.setText(f"✔  Valider — {format_da(net)}")
        self.count_label.setText(f"Nombre de produits : {self.lines_table.rowCount()}")

        if self._sale_type() == "wholesale":
            self._sync_versement_to_total()

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

        versement_cents = None
        if reseller_id is not None and sale_type == "wholesale":
            versement_cents = int(da_to_cents(self.versement_spin.value()))

        batch_id = None
        try:
            with session_scope() as session:
                _, batch_id = apply_stock_batch(
                    session,
                    direction=-1,
                    reason="Vente",
                    lines=lines,
                    is_sale=True,
                    sale_price_type=sale_type,
                    reseller_id=reseller_id,
                    versement_cents=versement_cents,
                    remise_cents=remise_cents,
                )
        except StockError as error:
            self.error_label.setText(str(error))
            return

        self.accept()

        if batch_id is not None:
            from app.ui.ticket_dialog import TicketPreviewDialog
            dlg = TicketPreviewDialog(batch_id=batch_id, parent=None)
            dlg.exec()
