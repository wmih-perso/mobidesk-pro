"""Page de vente comptoir — interface POS intégrée (pas de dialog)."""

from __future__ import annotations

import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
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
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import cents_to_da, da_to_cents, format_balance, format_da
from app.services import StockError, apply_stock_batch, list_displays, list_resellers
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox

MONTH_NAMES_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
DAY_NAMES_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _spin_cell(widget: QWidget, v_margin: int = 4) -> QWidget:
    wrapper = QWidget()
    wrapper.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(wrapper)
    lay.setContentsMargins(4, v_margin, 4, v_margin)
    lay.setSpacing(0)
    lay.addWidget(widget)
    return wrapper


def _centered(widget: QWidget) -> QWidget:
    wrapper = QWidget()
    wrapper.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(wrapper)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(widget)
    return wrapper


# ──────────────────────────────────────────────────────────────────────────────
# Popup de paiement
# ──────────────────────────────────────────────────────────────────────────────

class PaymentDialog(FramelessDialog):
    """Popup de choix du mode de paiement (paiement complet ou versement)."""

    def __init__(
        self,
        total_net_cents: int,
        reseller_name: str,
        reseller_balance_cents: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._total_net = total_net_cents
        self._balance = reseller_balance_cents
        self.versement_cents: int = total_net_cents  # valeur retournée

        self.setModal(True)
        self.setFixedWidth(500)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header("Passer au paiement"))

        # Corps sur fond sombre
        body = QWidget()
        body.setStyleSheet(
            "QWidget#PayBody { background: #1a1a2e; }"
            "QLabel { color: #e2e8f0; }"
            "QRadioButton { color: #e2e8f0; font-size: 14px; padding: 4px; background: transparent; }"
            "QRadioButton::indicator {"
            "  width: 18px; height: 18px;"
            "  border: 2px solid #64748b;"
            "  border-radius: 9px;"
            "  background: #0f172a;"
            "}"
            "QRadioButton::indicator:hover { border-color: #00897b; }"
            "QRadioButton::indicator:checked {"
            "  background: #00897b;"
            "  border: 2px solid #00897b;"
            "}"
        )
        body.setObjectName("PayBody")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(24, 20, 24, 24)
        body_lay.setSpacing(16)
        root.addWidget(body)

        # Alias pour la suite
        root = body_lay

        # ── Revendeur + total
        info_frame = QFrame()
        info_frame.setStyleSheet(
            "QFrame { background: #0f172a; border-radius: 8px; border: 1px solid #1e3a5f; }"
        )
        info_lay = QVBoxLayout(info_frame)
        info_lay.setContentsMargins(16, 14, 16, 14)
        info_lay.setSpacing(8)

        reseller_lbl = QLabel(f"Revendeur :  {reseller_name}")
        reseller_lbl.setStyleSheet("color: #94a3b8; font-size: 13px;")
        info_lay.addWidget(reseller_lbl)

        total_row = QHBoxLayout()
        total_row.addWidget(QLabel("Total de la vente :"))
        total_val = QLabel(format_da(total_net_cents))
        total_val.setStyleSheet("color: #00e676; font-size: 22px; font-weight: 800;")
        total_row.addStretch()
        total_row.addWidget(total_val)
        info_lay.addLayout(total_row)

        # Dette courante si > 0
        if reseller_balance_cents > 0:
            debt_frame = QFrame()
            debt_frame.setStyleSheet(
                "QFrame { background: #3b0f0f; border-radius: 6px; border: 1px solid #7f1d1d; }"
            )
            debt_lay = QHBoxLayout(debt_frame)
            debt_lay.setContentsMargins(12, 8, 12, 8)
            debt_icon = QLabel("⚠")
            debt_icon.setStyleSheet("color: #f87171; font-size: 16px;")
            debt_lay.addWidget(debt_icon)
            debt_txt = QLabel(f"Dette en cours :  {format_balance(reseller_balance_cents)}")
            debt_txt.setStyleSheet("color: #f87171; font-size: 14px; font-weight: 700;")
            debt_lay.addWidget(debt_txt)
            debt_lay.addStretch()
            info_lay.addWidget(debt_frame)
        elif reseller_balance_cents < 0:
            credit_frame = QFrame()
            credit_frame.setStyleSheet(
                "QFrame { background: #052e16; border-radius: 6px; border: 1px solid #14532d; }"
            )
            credit_lay = QHBoxLayout(credit_frame)
            credit_lay.setContentsMargins(12, 8, 12, 8)
            credit_txt = QLabel(f"Crédit disponible :  {format_balance(reseller_balance_cents)}")
            credit_txt.setStyleSheet("color: #4ade80; font-size: 14px; font-weight: 700;")
            credit_lay.addWidget(credit_txt)
            credit_lay.addStretch()
            info_lay.addWidget(credit_frame)

        root.addWidget(info_frame)

        # ── Choix du mode de paiement
        mode_lbl = QLabel("Mode de paiement :")
        mode_lbl.setStyleSheet("color: #94a3b8; font-size: 13px; font-weight: 600;")
        root.addWidget(mode_lbl)

        self._btn_group = QButtonGroup(self)

        self._radio_full = QRadioButton(f"Paiement complet  ({format_da(total_net_cents)})")
        self._radio_full.setChecked(True)
        self._btn_group.addButton(self._radio_full, 0)
        root.addWidget(self._radio_full)

        self._radio_partial = QRadioButton("Versement partiel")
        self._btn_group.addButton(self._radio_partial, 1)
        root.addWidget(self._radio_partial)

        # Champ versement partiel
        vers_row = QHBoxLayout()
        vers_row.setContentsMargins(24, 0, 0, 0)
        vers_row.setSpacing(12)
        vers_row.addWidget(QLabel("Montant versé :"))
        self._vers_spin = ModernDoubleSpinBox()
        self._vers_spin.setRange(0, 999_999_999)
        self._vers_spin.setDecimals(0)
        self._vers_spin.setSuffix(" DA")
        self._vers_spin.setFixedWidth(200)
        self._vers_spin.setValue(cents_to_da(total_net_cents))
        self._vers_spin.setStyleSheet(
            "QDoubleSpinBox { background: #1e293b; color: #00e676; "
            "border: 2px solid #00897b; border-radius: 6px; "
            "padding: 5px 10px; font-size: 14px; font-weight: 700; }"
            "QDoubleSpinBox:focus { border: 2px solid #00e676; }"
        )
        self._vers_spin.valueChanged.connect(self._update_preview)
        vers_row.addWidget(self._vers_spin)
        vers_row.addStretch()
        self._vers_row_widget = QWidget()
        self._vers_row_widget.setLayout(vers_row)
        self._vers_row_widget.setVisible(False)
        root.addWidget(self._vers_row_widget)

        # Prévisualisation du nouveau solde
        self._preview_frame = QFrame()
        self._preview_frame.setStyleSheet(
            "QFrame { background: #0f2027; border-radius: 6px; border: 1px solid #334155; }"
        )
        preview_lay = QHBoxLayout(self._preview_frame)
        preview_lay.setContentsMargins(14, 10, 14, 10)
        preview_lay.addWidget(QLabel("Solde après vente :"))
        self._preview_lbl = QLabel()
        self._preview_lbl.setStyleSheet("font-size: 15px; font-weight: 700;")
        preview_lay.addStretch()
        preview_lay.addWidget(self._preview_lbl)
        root.addWidget(self._preview_frame)

        # ── Boutons
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background: #334155; border: none; max-height: 1px;")
        root.addWidget(sep2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        cancel_btn = QPushButton("Annuler")
        cancel_btn.setStyleSheet(
            "QPushButton { background: #374151; color: white; border: none; border-radius: 6px; "
            "padding: 10px 20px; font-weight: 700; font-size: 13px; }"
            "QPushButton:hover { background: #4b5563; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        self._confirm_btn = QPushButton("✔  Confirmer la vente")
        self._confirm_btn.setStyleSheet(
            "QPushButton { background: #00897b; color: white; border: none; border-radius: 6px; "
            "padding: 10px 24px; font-weight: 800; font-size: 14px; }"
            "QPushButton:hover { background: #00695c; }"
        )
        self._confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._confirm_btn)
        root.addLayout(btn_row)

        # Connexions
        self._radio_full.toggled.connect(self._on_mode_changed)
        self._radio_partial.toggled.connect(self._on_mode_changed)
        self._update_preview()

    def _on_mode_changed(self) -> None:
        is_partial = self._radio_partial.isChecked()
        self._vers_row_widget.setVisible(is_partial)
        self._update_preview()

    def _update_preview(self) -> None:
        if self._radio_full.isChecked():
            vers = self._total_net
        else:
            vers = da_to_cents(self._vers_spin.value())

        new_balance = self._balance + self._total_net - vers
        if new_balance <= 0:
            # soldé ou crédit → vert
            txt = "0 DA  ✓" if new_balance == 0 else f"{format_balance(new_balance)}  ✓"
            color = "#4ade80"
        else:
            # dette restante → rouge
            txt = format_balance(new_balance)
            color = "#f87171"
            color = "#f87171"
        self._preview_lbl.setText(txt)
        self._preview_lbl.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: 700;")

    def _on_confirm(self) -> None:
        if self._radio_full.isChecked():
            self.versement_cents = self._total_net
        else:
            self.versement_cents = int(da_to_cents(self._vers_spin.value()))
        self.accept()


# ──────────────────────────────────────────────────────────────────────────────
# Page principale
# ──────────────────────────────────────────────────────────────────────────────

class SalePage(QWidget):
    """Page vente comptoir — réinitialisée après chaque vente validée."""

    sale_completed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._line_rows: list[tuple[int, int, int, ModernSpinBox, ModernDoubleSpinBox]] = []
        self._display_choices: list[tuple[int, str, str, str, int, int, int]] = []
        self._build_ui()
        self._reload_products()

    def _reload_products(self) -> None:
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
            resellers = list_resellers(session)

        current_id = self.reseller_combo.currentData() if hasattr(self, "reseller_combo") else None
        self.reseller_combo.blockSignals(True)
        self.reseller_combo.clear()
        self.reseller_combo.addItem("— Sélectionner —", None)
        restore_index = 0
        for i, r in enumerate(resellers, start=1):
            label = r.name + (f"  ·  {r.phone}" if r.phone else "")
            self.reseller_combo.addItem(label, r.id)
            if r.id == current_id:
                restore_index = i
        self.reseller_combo.setCurrentIndex(restore_index)
        self.reseller_combo.blockSignals(False)

    # ──────────────────────────────────────────────────────────────────
    # Construction UI
    # ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── HEADER ────────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet("background-color: #1a1a2e; border-radius: 0;")
        header.setFixedHeight(110)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 14, 24, 14)
        hl.setSpacing(24)

        # Toggle Détail / Gros
        tc = QVBoxLayout()
        tc.setSpacing(4)
        lbl = QLabel("Mode de vente")
        lbl.setStyleSheet("color: #9ca3af; font-size: 13px; font-weight: 600;")
        tc.addWidget(lbl)
        tr = QHBoxLayout()
        tr.setSpacing(4)
        self.retail_button = QPushButton("Détail")
        self.retail_button.setCheckable(True)
        self.retail_button.setChecked(True)
        self._style_toggle(self.retail_button, True)
        self.retail_button.clicked.connect(lambda: self._set_sale_type("retail"))
        tr.addWidget(self.retail_button)
        self.wholesale_button = QPushButton("Gros")
        self.wholesale_button.setCheckable(True)
        self._style_toggle(self.wholesale_button, False)
        self.wholesale_button.clicked.connect(lambda: self._set_sale_type("wholesale"))
        tr.addWidget(self.wholesale_button)
        tr.addStretch()
        tc.addLayout(tr)
        hl.addLayout(tc)

        self._add_vsep(hl)

        # Remise
        rc = QVBoxLayout()
        rc.setSpacing(4)
        rl = QLabel("Remise")
        rl.setStyleSheet("color: #9ca3af; font-size: 13px; font-weight: 600;")
        rc.addWidget(rl)
        self.remise_spin = ModernDoubleSpinBox()
        self.remise_spin.setRange(0, 99_999_999)
        self.remise_spin.setDecimals(0)
        self.remise_spin.setSuffix(" DA")
        self.remise_spin.setFixedWidth(130)
        self.remise_spin.setStyleSheet(
            "QDoubleSpinBox { background: #2d2d44; color: white; border: 1px solid #444; "
            "border-radius: 4px; padding: 6px 10px; font-size: 15px; font-weight: 600; }"
        )
        self.remise_spin.valueChanged.connect(self._update_total)
        rc.addWidget(self.remise_spin)
        hl.addLayout(rc)

        self._add_vsep(hl)

        # Date
        dc = QVBoxLayout()
        dc.setSpacing(2)
        now = datetime.datetime.now()
        date_str = (
            f"{DAY_NAMES_FR[now.weekday()].capitalize()}  "
            f"{now.day} {MONTH_NAMES_FR[now.month - 1]} {now.year}"
        )
        dl = QLabel(date_str)
        dl.setStyleSheet("color: #d1d5db; font-size: 15px; font-weight: 700;")
        dc.addWidget(dl)
        sl = QLabel("Vente comptoir")
        sl.setStyleSheet("color: #9ca3af; font-size: 13px;")
        dc.addWidget(sl)
        hl.addLayout(dc)

        self._add_vsep(hl)

        # Revendeur (masqué par défaut)
        self.reseller_widget = QWidget()
        reseller_col = QVBoxLayout(self.reseller_widget)
        reseller_col.setSpacing(2)
        reseller_col.setContentsMargins(0, 0, 0, 0)
        rt = QLabel("Revendeur")
        rt.setStyleSheet("color: #9ca3af; font-size: 13px; font-weight: 600;")
        reseller_col.addWidget(rt)
        self.reseller_combo = QComboBox()
        self.reseller_combo.addItem("— Sélectionner —", None)
        with session_scope() as session:
            for r in list_resellers(session):
                self.reseller_combo.addItem(r.name + (f"  ·  {r.phone}" if r.phone else ""), r.id)
        self.reseller_combo.setStyleSheet(
            "QComboBox { background: #2d2d44; color: white; border: 1px solid #444; "
            "border-radius: 4px; padding: 6px 10px; font-size: 14px; min-width: 200px; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
        )
        reseller_col.addWidget(self.reseller_combo)
        self.reseller_widget.setVisible(False)
        hl.addWidget(self.reseller_widget)

        hl.addStretch()

        # Total
        total_col = QVBoxLayout()
        total_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tl = QLabel("Total :")
        tl.setStyleSheet("color: #9ca3af; font-size: 15px; font-weight: 700;")
        tl.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_col.addWidget(tl)
        self.total_label = QLabel("0,00")
        self.total_label.setStyleSheet(
            "color: #00e676; font-size: 34px; font-weight: 900; letter-spacing: -1px;"
        )
        self.total_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_col.addWidget(self.total_label)
        hl.addLayout(total_col)

        root.addWidget(header)

        # ── CORPS ─────────────────────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background: #f5f5f5;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(20, 14, 20, 10)
        bl.setSpacing(10)

        # Barre de recherche
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        sl2 = QLabel("Recherche :")
        sl2.setStyleSheet("font-weight: 700; color: #374151; font-size: 13px;")
        search_row.addWidget(sl2)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Référence, modèle, catégorie...")
        self.search_input.setStyleSheet(
            "QLineEdit { background: white; border: 2px solid #00897b; border-radius: 6px; "
            "padding: 8px 14px; font-size: 13px; }"
            "QLineEdit:focus { border: 2px solid #00695c; }"
        )
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._on_add_clicked)
        search_row.addWidget(self.search_input, stretch=1)
        add_btn = QPushButton("+ Ajouter Produit")
        add_btn.setStyleSheet(
            "QPushButton { background: #00897b; color: white; border: none; border-radius: 6px; "
            "padding: 9px 16px; font-weight: 700; font-size: 13px; }"
            "QPushButton:hover { background: #00796b; }"
        )
        add_btn.clicked.connect(self._on_add_clicked)
        search_row.addWidget(add_btn)
        bl.addLayout(search_row)

        # Résultats de recherche
        self.search_results = QListWidget()
        self.search_results.setFixedHeight(130)
        self.search_results.setVisible(False)
        self.search_results.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #e0e0e0; border-radius: 6px; "
            "font-size: 13px; outline: none; }"
            "QListWidget::item { padding: 8px 14px; border-bottom: 1px solid #f5f5f5; }"
            "QListWidget::item:selected { background: #e0f2f1; color: #00695c; }"
            "QListWidget::item:hover { background: #f0faf9; }"
        )
        self.search_results.itemClicked.connect(self._on_result_clicked)
        bl.addWidget(self.search_results)

        # Tableau — 7 colonnes
        self.lines_table = QTableWidget(0, 7)
        self.lines_table.setHorizontalHeaderLabels(
            ["Référence", "Stock", "Désignation", "Prix U.", "Qté", "Montant", ""]
        )
        self.lines_table.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #e0e0e0; border-radius: 8px; "
            "gridline-color: #f0f0f0; alternate-background-color: #f8fdfc; }"
            "QHeaderView::section { background: #00897b; color: white; font-weight: 700; "
            "padding: 8px 10px; border: none; border-right: 1px solid #00796b; font-size: 12px; }"
            "QHeaderView::section:last { border-right: none; }"
            "QTableWidget::item { padding: 4px 8px; border: none; }"
            "QTableWidget::item:selected { background: #b2dfdb; color: #212121; }"
            "QTableWidget::item:hover { background: #e0f2f1; }"
        )
        hdr = self.lines_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)   # Référence
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)   # Stock
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch) # Désignation
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)   # Prix U.
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)   # Qté
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)   # Montant
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)   # ×
        self.lines_table.setColumnWidth(0, 110)
        self.lines_table.setColumnWidth(1, 60)
        self.lines_table.setColumnWidth(3, 160)
        self.lines_table.setColumnWidth(4, 130)
        self.lines_table.setColumnWidth(5, 140)
        self.lines_table.setColumnWidth(6, 46)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.verticalHeader().setDefaultSectionSize(48)
        self.lines_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.lines_table.setShowGrid(True)
        self.lines_table.setAlternatingRowColors(True)
        bl.addWidget(self.lines_table, stretch=1)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626; font-size: 12px; padding: 2px 0;")
        self.error_label.setWordWrap(True)
        bl.addWidget(self.error_label)

        root.addWidget(body, stretch=1)

        # ── FOOTER ────────────────────────────────────────────────────
        footer = QFrame()
        footer.setStyleSheet("QFrame { background: #1a1a2e; border-top: 2px solid #00897b; }")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(20, 12, 20, 12)
        fl.setSpacing(10)

        clear_btn = self._footer_btn("🗑  Vider le panier", "#374151", "#4b5563")
        clear_btn.clicked.connect(self._clear_cart)
        fl.addWidget(clear_btn)

        fl.addStretch()

        self.count_label = QLabel("Panier vide")
        self.count_label.setStyleSheet("color: #9ca3af; font-size: 12px;")
        fl.addWidget(self.count_label)

        fl.addSpacing(20)

        self.confirm_btn = self._footer_btn("→  Passer au paiement  —  0 DA", "#1565c0", "#1976d2")
        self.confirm_btn.setMinimumWidth(260)
        self.confirm_btn.clicked.connect(self._on_pay_clicked)
        fl.addWidget(self.confirm_btn)

        root.addWidget(footer)

    def _footer_btn(self, text: str, bg: str, bg_hover: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: white; border: none; border-radius: 6px; "
            f"padding: 10px 18px; font-weight: 700; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {bg_hover}; }}"
        )
        return btn

    def _add_vsep(self, layout: QHBoxLayout) -> None:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background: #333; border: none;")
        sep.setFixedWidth(1)
        layout.addWidget(sep)

    def _style_toggle(self, btn: QPushButton, active: bool) -> None:
        if active:
            btn.setStyleSheet(
                "QPushButton { background: #00897b; color: white; border: none; border-radius: 5px; "
                "padding: 8px 18px; font-weight: 700; font-size: 14px; }"
                "QPushButton:hover { background: #00796b; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background: #2d2d44; color: #9ca3af; border: none; border-radius: 5px; "
                "padding: 8px 18px; font-weight: 600; font-size: 14px; }"
                "QPushButton:hover { color: white; background: #3d3d5c; }"
            )

    # ──────────────────────────────────────────────────────────────────
    # Type de vente
    # ──────────────────────────────────────────────────────────────────

    def _sale_type(self) -> str:
        return "wholesale" if self.wholesale_button.isChecked() else "retail"

    def _set_sale_type(self, sale_type: str) -> None:
        is_wholesale = sale_type == "wholesale"
        self.retail_button.setChecked(not is_wholesale)
        self.wholesale_button.setChecked(is_wholesale)
        self._style_toggle(self.retail_button, not is_wholesale)
        self._style_toggle(self.wholesale_button, is_wholesale)
        self.reseller_widget.setVisible(is_wholesale)
        for _d_id, retail_cents, wholesale_cents, qty_spin, price_spin in self._line_rows:
            price_spin.setValue(cents_to_da(wholesale_cents if is_wholesale else retail_cents))
        self._update_total()

    # ──────────────────────────────────────────────────────────────────
    # Recherche
    # ──────────────────────────────────────────────────────────────────

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
            parts = [p for p in [cat, label, ref] if p]
            item = QListWidgetItem("  ·  ".join(parts))
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
        self.search_input.setFocus()

    def add_product(self, display_id: int) -> None:
        """Ajoute un produit depuis la page stock (bouton 🛒)."""
        self._reload_products()
        for d_id, ref, label, cat, ret, who, qs in self._display_choices:
            if d_id == display_id:
                self._add_line_row(d_id, ref, label, ret, who, cat, qs)
                break

    # ──────────────────────────────────────────────────────────────────
    # Lignes du panier
    # ──────────────────────────────────────────────────────────────────

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
        is_wholesale = self._sale_type() == "wholesale"
        base_price = wholesale_cents if is_wholesale else retail_cents
        row = self.lines_table.rowCount()
        self.lines_table.insertRow(row)

        # Col 0 — Référence
        ref_item = QTableWidgetItem(ref)
        ref_item.setFlags(ref_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        ref_item.setForeground(Qt.GlobalColor.darkGray)
        self.lines_table.setItem(row, 0, ref_item)

        # Col 1 — Stock
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
        self.lines_table.setItem(row, 1, stock_item)

        # Col 2 — Désignation
        desc = f"{label or ref}  ·  {category}" if category else (label or ref)
        desc_item = QTableWidgetItem(desc)
        desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.lines_table.setItem(row, 2, desc_item)

        # Col 3 — Prix U.
        price_spin = ModernDoubleSpinBox()
        price_spin.setRange(0, 99_999_999)
        price_spin.setDecimals(0)
        price_spin.setSuffix(" DA")
        price_spin.setFixedHeight(34)
        price_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        price_spin.setValue(cents_to_da(base_price))
        price_spin.valueChanged.connect(self._update_total)
        self.lines_table.setCellWidget(row, 3, _spin_cell(price_spin))

        # Col 4 — Qté
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

        # Col 6 — ×
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

    def _clear_cart(self) -> None:
        self.lines_table.setRowCount(0)
        self._line_rows.clear()
        self.remise_spin.setValue(0)
        self.error_label.setText("")
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
        n = self.lines_table.rowCount()
        self.count_label.setText(f"Nombre de produits : {n}" if n > 0 else "Panier vide")
        self.confirm_btn.setText(f"→  Passer au paiement  —  {format_da(net)}")

    # ──────────────────────────────────────────────────────────────────
    # Paiement
    # ──────────────────────────────────────────────────────────────────

    def _on_pay_clicked(self) -> None:
        self.error_label.setText("")

        if not self._line_rows:
            self.error_label.setText("Ajoutez au moins une pièce au panier.")
            return

        sale_type = self._sale_type()

        # En mode Gros : revendeur obligatoire
        if sale_type == "wholesale" and self.reseller_combo.currentData() is None:
            self.reseller_combo.setStyleSheet(
                "QComboBox { background: #2d2d44; color: white; border: 2px solid #dc2626; "
                "border-radius: 4px; padding: 6px 10px; font-size: 14px; min-width: 200px; }"
                "QComboBox::drop-down { border: none; width: 20px; }"
            )
            self.error_label.setText("Veuillez sélectionner un revendeur avant de continuer.")
            return
        else:
            # Remettre le style normal
            self.reseller_combo.setStyleSheet(
                "QComboBox { background: #2d2d44; color: white; border: 1px solid #444; "
                "border-radius: 4px; padding: 6px 10px; font-size: 14px; min-width: 200px; }"
                "QComboBox::drop-down { border: none; width: 20px; }"
            )

        # Calcul du net
        subtotal_cents = sum(
            da_to_cents(price.value()) * qty.value()
            for _d_id, _ret, _who, qty, price in self._line_rows
        )
        remise_cents = min(da_to_cents(self.remise_spin.value()), subtotal_cents)
        net_cents = max(0, subtotal_cents - remise_cents)

        if sale_type == "wholesale":
            reseller_id = self.reseller_combo.currentData()
            reseller_name = self.reseller_combo.currentText()

            # Charger le solde actuel du revendeur
            reseller_balance_cents = 0
            with session_scope() as session:
                from app.services import get_reseller
                r = get_reseller(session, reseller_id)
                reseller_balance_cents = r.balance_cents if r else 0

            dlg = PaymentDialog(
                total_net_cents=net_cents,
                reseller_name=reseller_name,
                reseller_balance_cents=reseller_balance_cents,
                parent=self,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            versement_cents = dlg.versement_cents
        else:
            reseller_id = None
            versement_cents = None

        self._do_save(sale_type, reseller_id, subtotal_cents, remise_cents, versement_cents)

    def _do_save(
        self,
        sale_type: str,
        reseller_id: int | None,
        subtotal_cents: int,
        remise_cents: int,
        versement_cents: int | None,
    ) -> None:
        discount_ratio = remise_cents / subtotal_cents if subtotal_cents > 0 else 0.0
        lines = []
        for display_id, _ret, _who, qty_spin, price_spin in self._line_rows:
            unit_price = da_to_cents(price_spin.value())
            if discount_ratio > 0:
                unit_price = round(unit_price * (1 - discount_ratio))
            lines.append((display_id, qty_spin.value(), unit_price))

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

        self._clear_cart()
        self._reload_products()
        self.sale_completed.emit()

        if batch_id is not None:
            from app.ui.ticket_dialog import TicketPreviewDialog
            dlg = TicketPreviewDialog(batch_id=batch_id, parent=None)
            dlg.exec()
