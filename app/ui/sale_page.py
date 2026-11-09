"""Page de vente comptoir — interface POS intégrée (pas de dialog)."""

from __future__ import annotations

import datetime
import json

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QMessageBox,
    QStyledItemDelegate,
    QTabBar,
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
from app.paths import get_data_dir
from pathlib import Path

from app.labels import split_models
from app.money import cents_to_da, da_to_cents, format_balance, format_da
from app.services import StockError, apply_stock_batch, list_displays, list_resellers
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, ModernSpinBox, add_clear_button
from app.locations import location_label
from app.search import brand_words, filter_and_rank

MONTH_NAMES_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
DAY_NAMES_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

SEARCH_VISIBLE_ROWS = 8  # hauteur de la liste des résultats de recherche (en produits)
MAX_OPEN_SALES = 5  # ventes en cours en même temps (onglets)


def _pending_sales_path() -> Path:
    """Ventes en cours (onglets), gardées si MobiDesk est fermé."""
    return get_data_dir() / "pending_sales.json"


def _empty_cart() -> dict:
    return {"reseller_id": None, "remise_da": 0.0, "lines": []}
_RESULT_INFO_ROLE = Qt.ItemDataRole.UserRole + 1  # (stock, prix gros en centimes)


class _SearchResultDelegate(QStyledItemDelegate):
    """Ligne de résultat : produit à gauche ; stock et prix gros alignés
    en colonnes à droite (stock en rouge si rupture)."""

    STOCK_WIDTH = 110
    PRICE_WIDTH = 130

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        info = index.data(_RESULT_INFO_ROLE)
        if not info:
            return
        quantity, price_cents = info
        rect = option.rect.adjusted(0, 0, -16, 0)
        price_rect = QRect(rect.right() - self.PRICE_WIDTH, rect.top(), self.PRICE_WIDTH, rect.height())
        stock_rect = QRect(price_rect.left() - self.STOCK_WIDTH, rect.top(), self.STOCK_WIDTH, rect.height())
        painter.save()
        font = QFont(option.font)
        painter.setFont(font)
        painter.setPen(QColor("#dc2626") if quantity <= 0 else QColor("#6b7280"))
        painter.drawText(stock_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                         "Rupture" if quantity <= 0 else f"Stock : {quantity}")
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#1d4ed8"))
        painter.drawText(price_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                         format_da(price_cents))
        painter.restore()


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
            "QRadioButton::indicator:hover { border-color: #2563eb; }"
            "QRadioButton::indicator:checked {"
            "  background: #2563eb;"
            "  border: 2px solid #2563eb;"
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
            "border: 2px solid #2563eb; border-radius: 6px; "
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
            "QPushButton { background: #2563eb; color: white; border: none; border-radius: 6px; "
            "padding: 10px 24px; font-weight: 800; font-size: 14px; }"
            "QPushButton:hover { background: #1e40af; }"
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
    """Page vente comptoir. Plusieurs ventes peuvent être en cours en même
    temps, une par onglet (ex. un revendeur qui hésite pendant qu'on sert un
    client) : l'écran affiche le panier de l'onglet actif, les autres sont
    gardés en mémoire — et dans pending_sales.json, pour les retrouver après
    une fermeture de MobiDesk."""

    sale_completed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._line_rows: list[
            tuple[int, int, int, ModernSpinBox, ModernDoubleSpinBox, QComboBox]
        ] = []
        self._display_choices: list[tuple[int, str, str, str, int, int, int]] = []
        self._models_by_id: dict[int, list[str]] = {}  # modèles compatibles par produit
        self._location_by_id: dict[int, str] = {}  # emplacement affiché par produit
        self._aliases_by_id: dict[int, str] = {}  # anciennes références (produits fusionnés)
        self._carts: list[dict] = [_empty_cart()]
        self._current = 0
        self._loading = False  # pendant le chargement d'un panier dans l'écran
        self._save_timer = None
        self._build_ui()
        self._reload_products()
        self._restore_pending_sales()

    def _reload_products(self) -> None:
        with session_scope() as session:
            displays = list_displays(session)
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
                for d in displays
            ]
            self._models_by_id = {d.id: split_models(d.phone_model) for d in displays}
            self._aliases_by_id = {d.id: d.aliases for d in displays if d.aliases}
            # Emplacement (Boîte 7…) : affiché, jamais utilisé par la recherche.
            self._location_by_id = {
                d.id: location_label(d.location_number) for d in displays
            }
            self._brand_words = brand_words(d.brand for d in displays if d.brand)
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
        from PySide6.QtCore import QTimer

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── ONGLETS : une vente en cours par onglet ───────────────────
        tabs_row = QFrame()
        tabs_row.setStyleSheet("QFrame { background: #11111f; }")
        tl = QHBoxLayout(tabs_row)
        tl.setContentsMargins(16, 6, 16, 0)
        tl.setSpacing(6)
        self.sale_tabs = QTabBar()
        self.sale_tabs.setTabsClosable(True)
        self.sale_tabs.setExpanding(False)
        self.sale_tabs.setDrawBase(False)
        self.sale_tabs.setStyleSheet(
            "QTabBar::tab { background: #2d2d44; color: #cbd5e1; border: none; padding: 8px 14px;"
            " margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px;"
            " font-weight: 600; }"
            "QTabBar::tab:selected { background: #1a1a2e; color: #ffffff; border-top: 3px solid #2563eb; }"
            "QTabBar::tab:hover { color: #ffffff; }"
        )
        self.sale_tabs.addTab("")
        self.sale_tabs.currentChanged.connect(self._on_tab_changed)
        self.sale_tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        tl.addWidget(self.sale_tabs)
        self.new_sale_btn = QPushButton("＋  Nouvelle vente en parallèle")
        self.new_sale_btn.setToolTip("Servir un autre client sans perdre le panier en cours")
        self.new_sale_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_sale_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #93c5fd; border: 1px dashed #3b82f6;"
            " border-radius: 6px; padding: 6px 12px; font-weight: 700; margin-bottom: 4px; }"
            "QPushButton:hover { background: #1e3a8a; color: white; }"
        )
        self.new_sale_btn.clicked.connect(self._new_sale_tab)
        tl.addWidget(self.new_sale_btn)
        tl.addStretch()
        root.addWidget(tabs_row)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(700)
        self._save_timer.timeout.connect(self.save_pending_sales)

        # ── HEADER ────────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet("background-color: #1a1a2e; border-radius: 0;")
        header.setFixedHeight(110)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 14, 24, 14)
        hl.setSpacing(24)

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
        sl = QLabel("Vente en gros")
        sl.setStyleSheet("color: #9ca3af; font-size: 13px;")
        dc.addWidget(sl)
        hl.addLayout(dc)

        self._add_vsep(hl)

        # Revendeur (obligatoire : toutes les ventes sont en gros)
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
        self.reseller_combo.currentIndexChanged.connect(self._on_cart_changed)
        reseller_col.addWidget(self.reseller_combo)
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
        add_clear_button(self.search_input)
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
        bl.addLayout(search_row)

        # Résultats de recherche
        self.search_results = QListWidget()
        self.search_results.setVisible(False)
        self.search_results.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #e0e0e0; border-radius: 6px; "
            "font-size: 13px; outline: none; }"
            "QListWidget::item { padding: 8px 14px; border-bottom: 1px solid #f5f5f5; }"
            "QListWidget::item:selected { background: #eff6ff; color: #1e40af; }"
            "QListWidget::item:hover { background: #f8faff; }"
        )
        self.search_results.itemClicked.connect(self._on_result_clicked)
        self.search_results.setItemDelegate(_SearchResultDelegate(self.search_results))
        # Toujours la hauteur de 8 produits (même s'il y a moins de
        # résultats) ; au-delà, la liste défile.
        self.search_results.ensurePolished()  # applique le style (marges des lignes)
        self.search_results.addItem("—")
        row_height = self.search_results.sizeHintForRow(0)
        self.search_results.clear()
        self.search_results.setFixedHeight(row_height * SEARCH_VISIBLE_ROWS + 2 * self.search_results.frameWidth())
        bl.addWidget(self.search_results)

        # Tableau — 8 colonnes + ×
        self.lines_table = QTableWidget(0, 9)
        self.lines_table.setHorizontalHeaderLabels(
            ["Emplacement", "Référence", "Modèle", "Stock", "Désignation", "Prix U.", "Qté", "Montant", ""]
        )
        self.lines_table.setStyleSheet(
            "QTableWidget { background: white; border: 1px solid #e0e0e0; border-radius: 8px; "
            "gridline-color: #f0f0f0; alternate-background-color: #f8fdfc; }"
            "QHeaderView::section { background: #2563eb; color: white; font-weight: 700; "
            "padding: 8px 10px; border: none; border-right: 1px solid #1d4ed8; font-size: 12px; }"
            "QHeaderView::section:last { border-right: none; }"
            "QTableWidget::item { padding: 4px 8px; border: none; }"
            "QTableWidget::item:selected { background: #bfdbfe; color: #212121; }"
            "QTableWidget::item:hover { background: #eff6ff; }"
        )
        hdr = self.lines_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)   # Emplacement
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)   # Référence
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)   # Modèle
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)   # Stock
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch) # Désignation
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)   # Prix U.
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)   # Qté
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)   # Montant
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)   # ×
        self.lines_table.setColumnWidth(0, 110)
        self.lines_table.setColumnWidth(1, 110)
        self.lines_table.setColumnWidth(2, 120)
        self.lines_table.setColumnWidth(3, 60)
        self.lines_table.setColumnWidth(5, 160)
        self.lines_table.setColumnWidth(6, 130)
        self.lines_table.setColumnWidth(7, 140)
        self.lines_table.setColumnWidth(8, 46)
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
        footer.setStyleSheet("QFrame { background: #1a1a2e; border-top: 2px solid #2563eb; }")
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
                "QPushButton { background: #2563eb; color: white; border: none; border-radius: 5px; "
                "padding: 8px 18px; font-weight: 700; font-size: 14px; }"
                "QPushButton:hover { background: #1d4ed8; }"
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
        # Toutes les ventes sont en gros, à un revendeur (« Client comptoir »
        # pour un client de passage) ; les anciennes ventes au détail restent
        # dans l'historique.
        return "wholesale"

    # ──────────────────────────────────────────────────────────────────
    # Recherche
    # ──────────────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        self.search_results.clear()
        if not text.strip():
            self.search_results.setVisible(False)
            return
        already = {d_id for d_id, *_ in self._line_rows}
        matches = filter_and_rank(
            text,
            [c for c in self._display_choices if c[0] not in already],
            lambda c: (c[1], c[2], c[3], self._aliases_by_id.get(c[0], "")),  # référence, marque + modèle, catégorie, alias
            optional_words=self._brand_words,
        )
        if not matches:
            self.search_results.setVisible(False)
            return
        for d_id, ref, label, cat, ret, who, qs in matches:
            parts = [p for p in [cat, label] if p]  # sans la référence
            location = self._location_by_id.get(d_id, "")
            text = "  ·  ".join(parts) + (f"     📦 {location}" if location else "")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (d_id, ref, label, cat, ret, who, qs))
            item.setData(_RESULT_INFO_ROLE, (qs, who))  # stock, prix gros
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

        # Col 0 — Emplacement (où prendre la pièce)
        location_item = QTableWidgetItem(self._location_by_id.get(display_id, ""))
        location_item.setFlags(location_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        location_item.setForeground(QColor("#92400e"))
        self.lines_table.setItem(row, 0, location_item)

        # Col 1 — Référence
        ref_item = QTableWidgetItem(ref)
        ref_item.setFlags(ref_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        ref_item.setForeground(Qt.GlobalColor.darkGray)
        self.lines_table.setItem(row, 1, ref_item)

        # Col 2 — Modèle compatible (liste des modèles du produit)
        model_combo = QComboBox()
        models = self._models_by_id.get(display_id, [])
        if len(models) != 1:
            model_combo.addItem("—", "")  # plusieurs modèles : à choisir
        for m in models:
            model_combo.addItem(m, m)
        model_combo.setFixedHeight(34)
        model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        model_combo.currentIndexChanged.connect(self._on_cart_changed)
        self.lines_table.setCellWidget(row, 2, _spin_cell(model_combo))

        # Col 3 — Stock
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
        self.lines_table.setItem(row, 3, stock_item)

        # Col 4 — Désignation
        desc = f"{label or ref}  ·  {category}" if category else (label or ref)
        desc_item = QTableWidgetItem(desc)
        desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.lines_table.setItem(row, 4, desc_item)

        # Col 5 — Prix U.
        price_spin = ModernDoubleSpinBox()
        price_spin.setRange(0, 99_999_999)
        price_spin.setDecimals(0)
        price_spin.setSuffix(" DA")
        price_spin.setFixedHeight(34)
        price_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        price_spin.setValue(cents_to_da(base_price))
        price_spin.valueChanged.connect(self._update_total)
        self.lines_table.setCellWidget(row, 5, _spin_cell(price_spin))

        # Col 6 — Qté
        qty_spin = ModernSpinBox()
        qty_spin.setRange(1, 1_000_000)
        qty_spin.setFixedHeight(34)
        qty_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        qty_spin.valueChanged.connect(self._update_total)
        self.lines_table.setCellWidget(row, 6, _spin_cell(qty_spin))

        # Col 7 — Montant
        total_item = QTableWidgetItem(format_da(base_price))
        total_item.setFlags(total_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        total_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        total_item.setForeground(Qt.GlobalColor.darkGray)
        self.lines_table.setItem(row, 7, total_item)

        # Col 8 — ×
        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet(
            "QPushButton { background: #fee2e2; color: #dc2626; border: none; border-radius: 4px; "
            "font-size: 16px; font-weight: 700; padding: 0; }"
            "QPushButton:hover { background: #fca5a5; }"
        )
        remove_btn.setToolTip("Supprimer")
        remove_btn.clicked.connect(lambda: self._remove_line_row(qty_spin))
        self.lines_table.setCellWidget(row, 8, _centered(remove_btn))

        self._line_rows.append(
            (display_id, retail_cents, wholesale_cents, qty_spin, price_spin, model_combo)
        )
        self._update_total()

    def _remove_line_row(self, qty_spin: ModernSpinBox) -> None:
        for i, (_d_id, _ret, _who, spin, _price, _model) in enumerate(self._line_rows):
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
        for i, (_d_id, _ret, _who, qty_spin, price_spin, _model) in enumerate(self._line_rows):
            line_total = da_to_cents(price_spin.value()) * qty_spin.value()
            subtotal += line_total
            item = self.lines_table.item(i, 7)
            if item:
                item.setText(format_da(line_total))
        remise = min(da_to_cents(self.remise_spin.value()), subtotal)
        net = max(0, subtotal - remise)
        da_val = cents_to_da(net)
        self.total_label.setText(f"{da_val:,.2f}".replace(",", " ").replace(".", ","))
        n = self.lines_table.rowCount()
        self.count_label.setText(f"Nombre de produits : {n}" if n > 0 else "Panier vide")
        self.confirm_btn.setText(f"→  Passer au paiement  —  {format_da(net)}")
        self._on_cart_changed()

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
            for _d_id, _ret, _who, qty, price, _model in self._line_rows
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
        for display_id, _ret, _who, qty_spin, price_spin, model_combo in self._line_rows:
            unit_price = da_to_cents(price_spin.value())
            if discount_ratio > 0:
                unit_price = round(unit_price * (1 - discount_ratio))
            lines.append(
                (display_id, qty_spin.value(), unit_price, model_combo.currentData() or "")
            )

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

        self._finish_current_sale()
        self._reload_products()
        self.sale_completed.emit()

        if batch_id is not None:
            from app.ui.ticket_dialog import TicketPreviewDialog
            dlg = TicketPreviewDialog(batch_id=batch_id, parent=None)
            dlg.exec()

    # ──────────────────────────────────────────────────────────────────
    # Ventes en parallèle (onglets)
    # ──────────────────────────────────────────────────────────────────

    def _snapshot(self) -> dict:
        """Panier affiché à l'écran → données."""
        return {
            "reseller_id": self.reseller_combo.currentData(),
            "remise_da": self.remise_spin.value(),
            "lines": [
                {
                    "display_id": d_id,
                    "qty": qty.value(),
                    "price_da": price.value(),
                    "model": model.currentData() or "",
                }
                for d_id, _ret, _who, qty, price, model in self._line_rows
            ],
        }

    def _load_cart(self, cart: dict) -> None:
        """Données → panier affiché à l'écran."""
        self._loading = True
        try:
            self.lines_table.setRowCount(0)
            self._line_rows.clear()
            self.error_label.setText("")
            index = self.reseller_combo.findData(cart.get("reseller_id"))
            self.reseller_combo.setCurrentIndex(max(index, 0))
            self.remise_spin.setValue(float(cart.get("remise_da") or 0))
            choices = {c[0]: c for c in self._display_choices}
            for line in cart.get("lines", []):
                choice = choices.get(line.get("display_id"))
                if choice is None:
                    continue  # produit supprimé entre-temps
                d_id, ref, label, cat, ret, who, qs = choice
                self._add_line_row(d_id, ref, label, ret, who, cat, qs)
                _d, _r, _w, qty_spin, price_spin, model_combo = self._line_rows[-1]
                saved_model = model_combo.findData(line.get("model") or "")
                if saved_model >= 0:
                    model_combo.setCurrentIndex(saved_model)
                qty_spin.setValue(int(line.get("qty") or 1))
                price_spin.setValue(float(line.get("price_da") or 0))
        finally:
            self._loading = False
        self._update_total()

    def _tab_title(self, index: int) -> str:
        cart = self._snapshot() if index == self._current else self._carts[index]
        title = f"🛒  Vente {index + 1}"
        reseller_id = cart.get("reseller_id")
        if reseller_id is not None:
            name = self.reseller_combo.itemText(self.reseller_combo.findData(reseller_id))
            if name:
                title += f"  ·  {name.split('  ·  ')[0][:18]}"
        count = len(cart.get("lines", []))
        if count:
            title += f"  ({count} art.)"
        return title

    def _refresh_tab_titles(self) -> None:
        for index in range(self.sale_tabs.count()):
            self.sale_tabs.setTabText(index, self._tab_title(index))
        self.new_sale_btn.setEnabled(self.sale_tabs.count() < MAX_OPEN_SALES)

    def _on_cart_changed(self, *_args) -> None:
        if self._loading or not hasattr(self, "sale_tabs"):
            return
        self._carts[self._current] = self._snapshot()
        self._refresh_tab_titles()
        if self._save_timer is not None:
            self._save_timer.start()

    def _on_tab_changed(self, index: int) -> None:
        if self._loading or index < 0 or index == self._current or index >= len(self._carts):
            return
        self._carts[self._current] = self._snapshot()
        self._current = index
        self._load_cart(self._carts[index])
        self._refresh_tab_titles()
        self.search_input.setFocus()

    def _new_sale_tab(self) -> None:
        if self.sale_tabs.count() >= MAX_OPEN_SALES:
            return
        self._carts[self._current] = self._snapshot()
        self._carts.append(_empty_cart())
        self._loading = True
        self.sale_tabs.addTab("")
        self._loading = False
        self.sale_tabs.setCurrentIndex(len(self._carts) - 1)

    def _remove_tab(self, index: int) -> None:
        """Retire l'onglet `index` (sans confirmation) et affiche un autre
        panier ; il reste toujours au moins un onglet."""
        if len(self._carts) == 1:
            self._carts[0] = _empty_cart()
            self._load_cart(self._carts[0])
            self._refresh_tab_titles()
            self.save_pending_sales()
            return
        if index != self._current:
            self._carts[self._current] = self._snapshot()
        self._carts.pop(index)
        self._loading = True
        self.sale_tabs.removeTab(index)
        self._loading = False
        self._current = min(self.sale_tabs.currentIndex(), len(self._carts) - 1)
        self._load_cart(self._carts[self._current])
        self._refresh_tab_titles()
        self.save_pending_sales()

    def _on_tab_close_requested(self, index: int) -> None:
        cart = self._snapshot() if index == self._current else self._carts[index]
        if cart.get("lines"):
            answer = QMessageBox.question(
                self, "Annuler la vente",
                f"La vente {index + 1} contient {len(cart['lines'])} produit(s).\n"
                "Voulez-vous vraiment l'annuler ?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._remove_tab(index)

    def _finish_current_sale(self) -> None:
        """Vente validée : son onglet se ferme (ou se vide si c'est le seul)."""
        self._remove_tab(self._current)

    # --- conservation des ventes en cours -----------------------------

    def save_pending_sales(self) -> None:
        """Enregistre les ventes en cours (paniers non vides) dans
        pending_sales.json — appelé après chaque changement (avec un court
        délai) et à la fermeture de MobiDesk."""
        if not hasattr(self, "sale_tabs"):
            return
        self._carts[self._current] = self._snapshot()
        carts = [cart for cart in self._carts if cart.get("lines")]
        try:
            path = _pending_sales_path()
            if carts:
                path.write_text(json.dumps({"carts": carts}, ensure_ascii=False, indent=2), encoding="utf-8")
            elif path.exists():
                path.unlink()
        except OSError:
            pass  # sans gravité : les paniers restent à l'écran

    def _restore_pending_sales(self) -> None:
        try:
            data = json.loads(_pending_sales_path().read_text(encoding="utf-8"))
            carts = [cart for cart in data.get("carts", []) if cart.get("lines")][:MAX_OPEN_SALES]
        except (OSError, ValueError, AttributeError):
            carts = []
        if not carts:
            self._refresh_tab_titles()
            return
        self._carts = carts
        self._loading = True
        while self.sale_tabs.count() < len(carts):
            self.sale_tabs.addTab("")
        self.sale_tabs.setCurrentIndex(0)
        self._loading = False
        self._current = 0
        self._load_cart(self._carts[0])
        self._refresh_tab_titles()

