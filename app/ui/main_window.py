"""Fenêtre principale — gestion du stock de produits."""

from __future__ import annotations

import csv
import datetime
import os
import sys
from pathlib import Path

from PySide6.QtCore import QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.locations import location_label, location_sort_key
from app.money import cents_to_da, format_da
from app.paths import get_database_path, is_frozen
from app.services import (
    StockError,
    UNKNOWN_CATEGORY_COLOR,
    category_colors,
    list_categories,
    count_low_stock_displays,
    count_movements,
    deactivate_display,
    list_displays,
    list_movements,
)
from app.backup import is_backup_due, is_close_backup_due, load_backup_config
from app.ticket_config import load_ticket_config, save_ticket_config
from app.ui.backup_panel import BackupPanel, ClosingBackupDialog, run_backup
from app.ui.capital_page import CapitalPage
from app.ui.expenses_page import ExpensesPage
from app.ui.category_panel import CategoryPanel
from app.ui.change_password_dialog import ChangePasswordDialog
from app.ui.contacts_page import ContactsPage
from app.ui.display_dialog import DisplayDialog
from app.ui.history_page import HistoriquePage
from app.ui.printer_panel import PrinterPanel
from app.ui.profit_tab import ProfitTab
from app.ui.resellers_panel import ResellersPanel
from app.ui.returns_page import ReturnsPage
from app.ui.sale_dialog import SaleDialog
from app.ui.sale_page import SalePage
from app.ui.sidebar import Sidebar
from app.ui.stock_adjust_dialog import StockAdjustmentDialog, SupplierPurchaseDialog
from app.ui.users_panel import UsersPanel
from app.ui.updater_dialog import UpdaterDialog
from app.ui.widgets import (
    EmptyState,
    HiddenStatCard,
    IconStatCard,
    ALIGN_RIGHT,
    add_clear_button,
    apply_card_shadow,
    category_badge,
    color_dot_icon,
    right_align_headers,
)
from app.updater import (
    UpdateCheckError,
    UpdateInfo,
    build_swap_script,
    check_for_update,
    launch_swap_and_exit,
)
from app.version import APP_VERSION


class _UpdateCheckThread(QThread):
    found = Signal(object)
    not_found = Signal()
    failed = Signal(str)

    def run(self) -> None:
        try:
            info = check_for_update()
        except UpdateCheckError as error:
            self.failed.emit(str(error))
            return
        if info is None:
            self.not_found.emit()
        else:
            self.found.emit(info)

DISPLAY_COLUMNS = [
    "Emplacement",
    "Référence",
    "Catégorie",
    "Marque",
    "Modèle compatible",
    "Qualité",
    "Prix achat",
    "Prix vente (détail)",
    "Prix vente (gros)",
    "Quantité",
    "Stock min.",
    "Alerte",
    "",
]

# Colonnes « Prix vente » mises en couleur dans le tableau des produits :
# index de colonne -> (couleur du texte, titre court avec pastille). Le
# titre court ne sert qu'à l'écran ; l'export CSV garde DISPLAY_COLUMNS.
CATEGORY_COLUMN = DISPLAY_COLUMNS.index("Catégorie")
# Colonnes qu'un clic sur le titre permet de classer -> clé de tri.
LOCATION_COLUMN = DISPLAY_COLUMNS.index("Emplacement")
SORTABLE_COLUMNS = {
    LOCATION_COLUMN: lambda d: location_sort_key(d.location_number),
    CATEGORY_COLUMN: lambda d: (d.category or "").lower(),
    DISPLAY_COLUMNS.index("Quantité"): lambda d: d.quantity,
}
# Colonnes de montants, alignées à droite (titres compris).
MONEY_COLUMNS = {
    DISPLAY_COLUMNS.index("Prix achat"),
    DISPLAY_COLUMNS.index("Prix vente (détail)"),
    DISPLAY_COLUMNS.index("Prix vente (gros)"),
}
PRICE_COLUMN_STYLES = {
    DISPLAY_COLUMNS.index("Prix vente (détail)"): ("#15803d", "🟢 Prix détail"),
    DISPLAY_COLUMNS.index("Prix vente (gros)"): ("#1d4ed8", "🔵 Prix gros"),
}

# Filtre « état du stock » du tableau des produits : (libellé, valeur).
STOCK_FILTERS = [
    ("Stock : tous", None),
    ("⚠  Stock faible", "low"),
    ("⛔  Rupture (0)", "out"),
    ("✓  En stock", "in"),
]

MOVEMENT_COLUMNS = ["Date", "Pièce", "Mouvement", "Avant", "Après", "Motif", ""]

MONTH_NAMES_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

PAGE_SIZE_CHOICES = [25, 50, 100]  # 25 produits par page par défaut

APP_ICON_PATH = Path(__file__).resolve().parent / "resources" / "app_icon.png"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))

        self._current_page = 1
        self._page_size = PAGE_SIZE_CHOICES[0]
        self._filtered_displays: list = []
        self._category_colors: dict[str, str] = {}
        self._only_low_stock = False

        self._movements_current_page = 1
        self._movements_page_size = PAGE_SIZE_CHOICES[0]
        self._movements_total_count = 0
        self._movements_filter_type = "all"

        self._build_ui()
        self._start_clock()
        self._start_backup_timer()
        self._close_backup_done = False
        # Arrêt / déconnexion de Windows : dernière sauvegarde avant de quitter.
        QApplication.instance().commitDataRequest.connect(self._backup_on_session_end)
        self.refresh()

    # ------------------------------------------------------------------
    # Plein écran permanent
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Fermeture de MobiDesk : sauvegarde cloud s'il y a eu des
        changements (option « à la fermeture » de la sauvegarde)."""
        self.sale_page.save_pending_sales()  # ventes en cours (onglets)
        if not self._close_backup_done:
            self._close_backup_done = True
            config = load_backup_config()
            if is_close_backup_due(config):
                ClosingBackupDialog(config, parent=self).exec()
        super().closeEvent(event)

    def _backup_on_session_end(self, _manager) -> None:
        """Windows s'éteint : pas le temps d'afficher une fenêtre, on envoie
        la sauvegarde en attendant au plus 20 secondes."""
        self.sale_page.save_pending_sales()
        if self._close_backup_done:
            return
        self._close_backup_done = True
        config = load_backup_config()
        if not is_close_backup_due(config):
            return
        import threading

        from app.backup import perform_backup

        worker = threading.Thread(target=perform_backup, args=(config,), daemon=True)
        worker.start()
        worker.join(timeout=20)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.showMaximized()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowStateChange:
            if not self.isMaximized() and not self.isMinimized():
                QTimer.singleShot(0, self.showMaximized)

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.page_selected.connect(self._on_nav_selected)
        self.sidebar.new_sale_requested.connect(self._on_new_sale)
        self.sidebar.add_product_requested.connect(self._on_add)
        self.sidebar.refresh_requested.connect(self._on_manual_refresh)
        root_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, stretch=1)

        self.sale_page = SalePage()                            # index 0
        self.sale_page.sale_completed.connect(self.refresh)
        self.stack.addWidget(self.sale_page)
        self.stack.addWidget(self._build_stock_page())       # index 1
        self.historique_page = HistoriquePage()              # index 2
        self.historique_page.stock_changed.connect(self.refresh)
        self.stack.addWidget(self.historique_page)
        self.profit_tab = ProfitTab()                        # index 3
        self.stack.addWidget(self.profit_tab)
        self.stack.addWidget(self._build_settings_page())   # index 4
        self.stack.addWidget(self._build_contacts_page())   # index 5
        self.stack.addWidget(self._build_categories_page()) # index 6
        self.returns_page = ReturnsPage()                    # index 7
        self.returns_page.stock_changed.connect(self.refresh)
        self.stack.addWidget(self.returns_page)
        self.users_panel = UsersPanel()                      # index 8
        self.stack.addWidget(self.users_panel)
        self.capital_page = CapitalPage()                    # index 9 (admin)
        self.stack.addWidget(self.capital_page)
        self.expenses_page = ExpensesPage()                  # index 10 (admin)
        self.stack.addWidget(self.expenses_page)

        root_layout.addWidget(self._build_status_bar())

        # Afficher le compte connecté dans la sidebar et la barre de statut
        from app import session as _session
        user = _session.get_current_user()
        if user:
            self.sidebar.set_current_user(user.display_name, user.role)
            self.merge_btn.setVisible(user.role == "admin")
            self._set_status_user(user.display_name, user.role)

        self.sidebar.select_page("stock")
        self.stack.setCurrentIndex(1)

    def _build_home_page(self) -> QWidget:
        from app.ui.icons import home_icon
        page = QWidget()
        page.setObjectName("HomePage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 30, 40, 30)
        outer.setSpacing(0)

        # En-tête
        header_row = QHBoxLayout()
        greeting = QLabel("Bienvenue sur MobiDesk Pro")
        greeting.setObjectName("HomeGreeting")
        header_row.addWidget(greeting)
        header_row.addStretch()
        self.home_date_label = QLabel("")
        self.home_date_label.setObjectName("HomeSubtitle")
        header_row.addWidget(self.home_date_label)
        outer.addLayout(header_row)

        outer.addSpacing(6)
        sub = QLabel("Gestion de stock et de ventes — Sélectionnez une section pour commencer.")
        sub.setObjectName("HomeSubtitle")
        outer.addWidget(sub)

        outer.addSpacing(30)

        # Grille de boutons (2 × 4)
        from PySide6.QtWidgets import QGridLayout
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(18)
        grid.setContentsMargins(0, 0, 0, 0)

        buttons = [
            ("monitor",   "Stock",         "#7b1fa2", "stock"),
            ("list",      "Historique",    "#f57c00", "historique"),
            ("coin",      "Bénéfices",     "#2563eb", "profit"),
            ("home",      "Vente comptoir","#43a047", "sale"),
            ("users",     "Contacts",      "#1976d2", "contacts"),
            ("download",  "Retours",       "#e64a19", "returns"),
            ("tag",       "Catégories",    "#00acc1", "categories"),
            ("gear",      "Paramètres",    "#546e7a", "settings"),
        ]

        for idx, (icon_key, label, color, page_key) in enumerate(buttons):
            row, col = divmod(idx, 4)
            btn = self._make_home_button(home_icon(icon_key, color, size=64), label, page_key)
            grid.addWidget(btn, row, col)

        outer.addWidget(grid_widget)
        outer.addStretch()
        return page

    def _make_home_button(self, icon, label: str, page_key: str) -> QWidget:
        card = QFrame()
        card.setObjectName("HomeCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(12)

        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(QSize(64, 64)))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        text_label = QLabel(label)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #212121; border: none;")
        layout.addWidget(text_label)

        card.setFixedSize(160, 140)

        def _on_click(event, key=page_key):
            if key == "sale":
                self._on_new_sale()
            else:
                self._on_nav_selected(key)
                self.sidebar.select_page(key)

        card.mousePressEvent = _on_click
        return card

    def _build_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("StatusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        # Badge utilisateur connecté
        self._status_user_badge = QLabel("")
        self._status_user_badge.setStyleSheet(
            "background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.30);"
            " border-radius: 10px; padding: 1px 10px;"
            " color: white; font-size: 12px; font-weight: 700;"
        )
        self._status_user_badge.hide()
        layout.addWidget(self._status_user_badge)

        sep0 = QLabel("|")
        sep0.setObjectName("StatusBarLabel")
        sep0.hide()
        self._status_user_sep = sep0
        layout.addWidget(sep0)

        self._status_alert_label = QLabel("⚠  Stock bas : 0")
        self._status_alert_label.setObjectName("StatusBarLabel")
        layout.addWidget(self._status_alert_label)

        layout.addStretch()

        self.date_label = QLabel("")
        self.date_label.setObjectName("StatusBarLabel")
        layout.addWidget(self.date_label)

        sep = QLabel("|")
        sep.setObjectName("StatusBarLabel")
        layout.addWidget(sep)

        self.time_label = QLabel("")
        self.time_label.setObjectName("StatusBarLabel")
        layout.addWidget(self.time_label)

        sep2 = QLabel("|")
        sep2.setObjectName("StatusBarLabel")
        layout.addWidget(sep2)

        from app.version import APP_VERSION as _V
        version_label = QLabel(f"v{_V}")
        version_label.setObjectName("StatusBarLabel")
        layout.addWidget(version_label)

        return bar

    def _set_status_user(self, display_name: str, role: str) -> None:
        icon = "🔑" if role == "admin" else "👤"
        self._status_user_badge.setText(f"{icon}  {display_name}")
        self._status_user_badge.show()
        self._status_user_sep.show()

    def _start_backup_timer(self) -> None:
        """Vérifie toutes les minutes si la sauvegarde du jour est due — et
        peu après le démarrage, pour rattraper une heure déjà passée."""
        self._backup_attempted_on: datetime.date | None = None
        timer = QTimer(self)
        timer.timeout.connect(self._check_backup)
        timer.start(60_000)
        QTimer.singleShot(15_000, self._check_backup)

    def _check_backup(self) -> None:
        now = datetime.datetime.now()
        config = load_backup_config()
        # Une seule tentative automatique par jour et par lancement : en cas
        # d'échec (pas d'internet...), on ne réessaie pas toutes les minutes.
        if self._backup_attempted_on == now.date() or not is_backup_due(config, now):
            return
        self._backup_attempted_on = now.date()

        def _done(success: bool, message: str) -> None:
            if hasattr(self, "backup_panel"):
                self.backup_panel._refresh_last_backup()
            if not success:
                QMessageBox.warning(self, "Sauvegarde automatique", message)

        run_backup(_done, config=config)

    def _start_clock(self) -> None:
        self._update_clock()
        timer = QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)
        self._clock_timer = timer

    def _update_clock(self) -> None:
        now = datetime.datetime.now()
        date_str = f"{now.day} {MONTH_NAMES_FR[now.month - 1]} {now.year}"
        self.date_label.setText(date_str)
        self.time_label.setText(now.strftime("%H:%M"))

    # ---------------- Page « Stock » (tableau de bord + produits) ---------

    def _build_stock_page(self) -> QWidget:
        return self._build_displays_tab()

    def _build_stat_cards(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(16)

        self.total_card = IconStatCard(
            "📦", "Références", "0", "Produits enregistrés",
            accent="#2563eb", accent_bg="#eff6ff", sparkline_seed=1,
        )
        self.low_stock_card = IconStatCard(
            "⚠️", "Stock bas", "0", "Produits en alerte",
            accent="#ef4444", accent_bg="#fef2f2", sparkline_seed=4,
        )
        # Clic sur la carte : n'afficher que les produits en stock faible.
        self.low_stock_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.low_stock_card.setToolTip("Afficher les produits en stock faible")
        self.low_stock_card.mousePressEvent = lambda _event: self._set_only_low_stock(True)
        self.stock_value_card = IconStatCard(
            "📊", "Pièces en stock", "0", "Unités disponibles",
            accent="#7c3aed", accent_bg="#f5f3ff", sparkline_seed=7,
        )
        self.stock_balance_card = HiddenStatCard(
            "💼", "Solde de stock", "Valeur totale d'achat du stock",
            accent="#059669", accent_bg="#ecfdf5", sparkline_seed=3,
        )

        for card in (self.total_card, self.low_stock_card, self.stock_value_card, self.stock_balance_card):
            layout.addWidget(card, stretch=1)

        return layout

    def _build_displays_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(24, 16, 24, 20)
        outer_layout.setSpacing(14)

        # Barre de recherche centrée
        search_row = QHBoxLayout()
        search_row.setSpacing(0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Rechercher par référence, marque ou modèle...")
        self.search_input.setObjectName("PageSearchInput")
        add_clear_button(self.search_input)
        self.search_input.setMinimumWidth(380)
        self.search_input.setMaximumWidth(620)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(300)
        self._search_debounce.timeout.connect(self._on_filters_changed)
        self.search_input.textChanged.connect(lambda: self._search_debounce.start())

        search_row.addStretch()
        search_row.addWidget(self.search_input)
        search_row.addSpacing(10)

        # Filtres : se combinent entre eux et avec la recherche.
        def _filter_combo(width: int) -> QComboBox:
            combo = QComboBox()
            combo.setMinimumHeight(38)
            combo.setMinimumWidth(width)
            combo.currentIndexChanged.connect(self._on_filters_changed)
            return combo

        self.category_filter = _filter_combo(190)
        self.category_filter.setToolTip("Filtrer par catégorie")
        search_row.addWidget(self.category_filter)
        search_row.addSpacing(8)
        self.stock_filter = _filter_combo(170)
        self.stock_filter.setToolTip("Filtrer par état du stock")
        self.stock_filter.blockSignals(True)  # le tableau n'existe pas encore
        for label, value in STOCK_FILTERS:
            self.stock_filter.addItem(label, value)
        self.stock_filter.blockSignals(False)
        search_row.addWidget(self.stock_filter)
        search_row.addSpacing(8)
        self.reset_filters_btn = QPushButton("✕  Réinitialiser")
        self.reset_filters_btn.setObjectName("SecondaryButton")
        self.reset_filters_btn.setToolTip("Retirer la recherche et tous les filtres")
        self.reset_filters_btn.clicked.connect(self._reset_filters)
        self.reset_filters_btn.setVisible(False)
        search_row.addWidget(self.reset_filters_btn)
        search_row.addStretch()
        self.merge_btn = QPushButton("🔗  Doublons")
        self.merge_btn.setObjectName("SecondaryButton")
        self.merge_btn.setToolTip("Fusionner un même produit créé par plusieurs fournisseurs")
        self.merge_btn.clicked.connect(self._on_merge_duplicates)
        self.merge_btn.setVisible(False)  # admin seulement
        search_row.addWidget(self.merge_btn)
        self._reload_filter_choices()

        outer_layout.addLayout(self._build_stat_cards())
        outer_layout.addLayout(search_row)

        container = QFrame()
        container.setObjectName("Card")
        apply_card_shadow(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)

        actions_row = QHBoxLayout()
        actions_row.addStretch()

        export_button = QPushButton("⬇  Exporter")
        export_button.setObjectName("SecondaryButton")
        export_button.clicked.connect(self._on_export_csv)
        actions_row.addWidget(export_button)

        columns_button = QPushButton("☰")
        columns_button.setObjectName("IconButton")
        columns_button.setFixedSize(38, 32)
        columns_button.clicked.connect(self._on_columns_clicked)
        actions_row.addWidget(columns_button)
        layout.addLayout(actions_row)

        self.displays_table = QTableWidget(0, len(DISPLAY_COLUMNS))
        self.displays_table.setHorizontalHeaderLabels(DISPLAY_COLUMNS)
        # Le QSS de l'en-tête écrase toute couleur posée par cellule : on
        # repère les colonnes de prix par une pastille de leur couleur.
        for column_index, (_color, title) in PRICE_COLUMN_STYLES.items():
            self.displays_table.horizontalHeaderItem(column_index).setText(title)
        right_align_headers(self.displays_table, MONEY_COLUMNS)
        self.displays_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.displays_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.displays_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.displays_table.setAlternatingRowColors(True)
        self.displays_table.setShowGrid(False)
        self.displays_table.verticalHeader().setVisible(False)
        self.displays_table.verticalHeader().setDefaultSectionSize(40)
        self.displays_table.setFrameShape(QFrame.Shape.NoFrame)
        self.displays_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        alert_column_index = len(DISPLAY_COLUMNS) - 2
        action_column_index = len(DISPLAY_COLUMNS) - 1
        self.displays_table.horizontalHeader().setSectionResizeMode(
            alert_column_index, QHeaderView.ResizeMode.Fixed
        )
        self.displays_table.horizontalHeader().setSectionResizeMode(
            action_column_index, QHeaderView.ResizeMode.Fixed
        )
        self.displays_table.horizontalHeader().setSectionResizeMode(
            LOCATION_COLUMN, QHeaderView.ResizeMode.ResizeToContents
        )
        self.displays_table.setColumnWidth(alert_column_index, 120)
        self.displays_table.setColumnWidth(action_column_index, 96)
        self.displays_table.horizontalHeader().setStretchLastSection(False)
        self.displays_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.displays_table.doubleClicked.connect(self._on_edit)
        self._sort_column: int | None = None
        self._sort_descending = False
        self.displays_table.horizontalHeader().setSectionsClickable(True)
        self.displays_table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        for column in SORTABLE_COLUMNS:
            self.displays_table.horizontalHeaderItem(column).setToolTip(
                "Cliquer pour classer (croissant, décroissant, puis ordre habituel)"
            )

        self.filter_hint = QFrame()
        self.filter_hint.setStyleSheet(
            "QFrame { background: #fff7ed; border: 1px solid #fdba74; border-radius: 8px; }"
        )
        hint_layout = QHBoxLayout(self.filter_hint)
        hint_layout.setContentsMargins(12, 8, 12, 8)
        self.filter_hint_label = QLabel("")
        self.filter_hint_label.setStyleSheet("color: #9a3412; font-weight: 700; border: none;")
        hint_layout.addWidget(self.filter_hint_label, stretch=1)
        clear_filter_btn = QPushButton("Enlever le filtre")
        clear_filter_btn.setObjectName("SecondaryButton")
        clear_filter_btn.clicked.connect(self._clear_filters_keep_search)
        hint_layout.addWidget(clear_filter_btn)
        self.filter_hint.setVisible(False)
        layout.addWidget(self.filter_hint)
        layout.addWidget(self.displays_table, stretch=1)

        self.displays_empty_state = EmptyState(
            "📦",
            "Aucun produit trouvé",
            "Commencez par ajouter de nouveaux produits à votre stock.",
            "+  Ajouter un produit",
        )
        self.displays_empty_state.action_button.clicked.connect(self._on_add)
        layout.addWidget(self.displays_empty_state, stretch=1)

        layout.addWidget(self._build_pagination_bar())

        outer_layout.addWidget(container, stretch=1)
        return outer

    def _build_pagination_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 6, 4, 2)
        layout.setSpacing(10)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("SummaryLabel")
        layout.addWidget(self.summary_label)

        layout.addStretch()

        page_size_label = QLabel("Par page")
        page_size_label.setObjectName("SummaryLabel")
        layout.addWidget(page_size_label)

        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems([str(n) for n in PAGE_SIZE_CHOICES])
        self.page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        layout.addWidget(self.page_size_combo)

        self.first_page_button = QPushButton("⟨⟨")
        self.first_page_button.setObjectName("PageNavButton")
        self.first_page_button.clicked.connect(lambda: self._go_to_page(1))
        layout.addWidget(self.first_page_button)

        self.prev_page_button = QPushButton("⟨")
        self.prev_page_button.setObjectName("PageNavButton")
        self.prev_page_button.clicked.connect(lambda: self._go_to_page(self._current_page - 1))
        layout.addWidget(self.prev_page_button)

        self.page_indicator_label = QLabel("1")
        self.page_indicator_label.setObjectName("PageIndicator")
        self.page_indicator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_indicator_label.setFixedWidth(32)
        layout.addWidget(self.page_indicator_label)

        self.next_page_button = QPushButton("⟩")
        self.next_page_button.setObjectName("PageNavButton")
        self.next_page_button.clicked.connect(lambda: self._go_to_page(self._current_page + 1))
        layout.addWidget(self.next_page_button)

        self.last_page_button = QPushButton("⟩⟩")
        self.last_page_button.setObjectName("PageNavButton")
        self.last_page_button.clicked.connect(lambda: self._go_to_page(self._total_pages()))
        layout.addWidget(self.last_page_button)

        return bar

    def _build_movements_pagination_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 6, 4, 2)
        layout.setSpacing(10)

        self.movements_summary_label = QLabel("")
        self.movements_summary_label.setObjectName("SummaryLabel")
        layout.addWidget(self.movements_summary_label)

        layout.addStretch()

        page_size_label = QLabel("Par page")
        page_size_label.setObjectName("SummaryLabel")
        layout.addWidget(page_size_label)

        self.movements_page_size_combo = QComboBox()
        self.movements_page_size_combo.addItems([str(n) for n in PAGE_SIZE_CHOICES])
        self.movements_page_size_combo.currentIndexChanged.connect(
            self._on_movements_page_size_changed
        )
        layout.addWidget(self.movements_page_size_combo)

        self.movements_first_page_button = QPushButton("⟨⟨")
        self.movements_first_page_button.setObjectName("PageNavButton")
        self.movements_first_page_button.clicked.connect(
            lambda: self._go_to_movements_page(1)
        )
        layout.addWidget(self.movements_first_page_button)

        self.movements_prev_page_button = QPushButton("⟨")
        self.movements_prev_page_button.setObjectName("PageNavButton")
        self.movements_prev_page_button.clicked.connect(
            lambda: self._go_to_movements_page(self._movements_current_page - 1)
        )
        layout.addWidget(self.movements_prev_page_button)

        self.movements_page_indicator_label = QLabel("1")
        self.movements_page_indicator_label.setObjectName("PageIndicator")
        self.movements_page_indicator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.movements_page_indicator_label.setFixedWidth(32)
        layout.addWidget(self.movements_page_indicator_label)

        self.movements_next_page_button = QPushButton("⟩")
        self.movements_next_page_button.setObjectName("PageNavButton")
        self.movements_next_page_button.clicked.connect(
            lambda: self._go_to_movements_page(self._movements_current_page + 1)
        )
        layout.addWidget(self.movements_next_page_button)

        self.movements_last_page_button = QPushButton("⟩⟩")
        self.movements_last_page_button.setObjectName("PageNavButton")
        self.movements_last_page_button.clicked.connect(
            lambda: self._go_to_movements_page(self._movements_total_pages())
        )
        layout.addWidget(self.movements_last_page_button)

        return bar

    def _build_movements_filter_row(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 10, 8, 4)
        layout.setSpacing(6)

        filter_label = QLabel("Filtrer :")
        filter_label.setStyleSheet("color: #6b7280; font-size: 12px; font-weight: 600;")
        layout.addWidget(filter_label)

        self._movements_filter_buttons: dict[str, QPushButton] = {}
        filters = [
            ("all",     "Tous"),
            ("sales",   "Ventes"),
            ("entries", "Entrées"),
            ("exits",   "Sorties hors vente"),
        ]
        for key, label in filters:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setObjectName("FilterPill")
            btn.setStyleSheet(self._pill_style(key == "all"))
            btn.clicked.connect(lambda _c, k=key: self._on_movement_filter(k))
            layout.addWidget(btn)
            self._movements_filter_buttons[key] = btn

        layout.addStretch()
        return bar

    def _pill_style(self, active: bool) -> str:
        if active:
            return (
                "QPushButton { background:#2563eb; color:white; border:none; border-radius:14px;"
                " padding:4px 14px; font-size:12px; font-weight:700; }"
                "QPushButton:hover { background:#1d4ed8; }"
            )
        return (
            "QPushButton { background:#f5f5f5; color:#757575; border:1px solid #e0e0e0;"
            " border-radius:14px; padding:4px 14px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:#eff6ff; color:#1e40af; }"
            "QPushButton:checked { background:#2563eb; color:white; border:none; }"
        )

    def _on_movement_filter(self, filter_key: str) -> None:
        self._movements_filter_type = filter_key
        for k, btn in self._movements_filter_buttons.items():
            active = k == filter_key
            btn.setChecked(active)
            btn.setStyleSheet(self._pill_style(active))
        self._movements_current_page = 1
        self._render_movements_page()

    def _build_movements_tab(self) -> QWidget:
        container = QFrame()
        container.setObjectName("Card")
        apply_card_shadow(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        layout.addWidget(self._build_movements_filter_row())

        self.movements_table = QTableWidget(0, len(MOVEMENT_COLUMNS))
        self.movements_table.setHorizontalHeaderLabels(MOVEMENT_COLUMNS)
        self.movements_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.movements_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.movements_table.setAlternatingRowColors(True)
        self.movements_table.setShowGrid(False)
        self.movements_table.verticalHeader().setVisible(False)
        self.movements_table.verticalHeader().setDefaultSectionSize(38)
        self.movements_table.setFrameShape(QFrame.Shape.NoFrame)
        self.movements_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.movements_table.horizontalHeader().setStretchLastSection(False)
        self.movements_table.horizontalHeader().setSectionResizeMode(
            len(MOVEMENT_COLUMNS) - 2, QHeaderView.ResizeMode.Stretch
        )
        self.movements_table.horizontalHeader().setSectionResizeMode(
            len(MOVEMENT_COLUMNS) - 1, QHeaderView.ResizeMode.Fixed
        )
        self.movements_table.setColumnWidth(len(MOVEMENT_COLUMNS) - 1, 36)
        self.movements_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.movements_table, stretch=1)

        self.movements_empty_state = EmptyState(
            "📋",
            "Aucun mouvement de stock",
            "Les entrées et sorties de stock apparaîtront ici.",
        )
        layout.addWidget(self.movements_empty_state, stretch=1)

        layout.addWidget(self._build_movements_pagination_bar())

        return container

    # ---------------- Page « Paramètres » ---------------------------------

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(24, 20, 24, 24)
        page_layout.setSpacing(0)

        # Titre hors carte
        header_row = QHBoxLayout()
        title = QLabel("⚙️  Paramètres")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        header_row.addWidget(title)
        header_row.addStretch()
        info_label = QLabel(f"MobiDesk Pro  —  v{APP_VERSION}")
        info_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        header_row.addWidget(info_label)
        page_layout.addLayout(header_row)
        page_layout.addSpacing(16)

        # Carte scrollable
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 24, 28, 28)
        card_layout.setSpacing(0)

        def _separator():
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("color: #e5e7eb; background: #e5e7eb; max-height: 1px;")
            return line

        card_layout.addWidget(self._build_ticket_section())
        card_layout.addSpacing(20)
        card_layout.addWidget(_separator())
        card_layout.addSpacing(20)
        card_layout.addWidget(self._build_printer_section())
        card_layout.addSpacing(20)
        card_layout.addWidget(_separator())
        card_layout.addSpacing(20)
        card_layout.addWidget(self._build_database_section())
        card_layout.addSpacing(20)
        card_layout.addWidget(_separator())
        card_layout.addSpacing(20)
        card_layout.addWidget(self._build_backup_section())
        card_layout.addSpacing(20)
        card_layout.addWidget(_separator())
        card_layout.addSpacing(20)
        card_layout.addWidget(self._build_security_section())
        card_layout.addSpacing(20)
        card_layout.addWidget(_separator())
        card_layout.addSpacing(20)
        card_layout.addWidget(self._build_update_section())

        scroll.setWidget(card)
        page_layout.addWidget(scroll)
        return page

    def _build_contacts_page(self) -> QWidget:
        """Revendeurs et fournisseurs réunis dans un seul onglet « Contacts »."""
        self.resellers_contacts_page = ContactsPage("resellers")
        self.resellers_contacts_page.stock_changed.connect(self.refresh)
        self.suppliers_contacts_page = ContactsPage("suppliers")
        self.suppliers_contacts_page.stock_changed.connect(self.refresh)

        self.contacts_tabs = QTabWidget()
        self.contacts_tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: #f5f5f5; }"
            "QTabBar::tab { background: #eeeeee; color: #616161; border: 1px solid #e0e0e0; "
            "border-bottom: none; padding: 10px 22px; border-radius: 6px 6px 0 0; font-weight: 600; margin-right: 2px; }"
            "QTabBar::tab:hover { background: #eff6ff; color: #1e40af; }"
            "QTabBar::tab:selected { background: #2563eb; color: white; border-color: #2563eb; }"
        )
        self.contacts_tabs.addTab(self.resellers_contacts_page, "👥  Revendeurs")
        self.contacts_tabs.addTab(self.suppliers_contacts_page, "🚚  Fournisseurs")
        self.contacts_tabs.currentChanged.connect(lambda _i: self._refresh_contacts())
        return self.contacts_tabs

    def _refresh_contacts(self) -> None:
        self.contacts_tabs.currentWidget().refresh()

    def _build_categories_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(0)

        title = QLabel("Catégories")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        layout.addWidget(title)
        layout.addSpacing(6)

        hint = QLabel("Les catégories apparaissent dans le formulaire d'ajout de produit pour organiser votre stock.")
        hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(16)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        self.category_panel_page = CategoryPanel()
        card_layout.addWidget(self.category_panel_page)

        layout.addWidget(card)
        layout.addStretch()
        return page

    def _build_categories_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("Catégories de produits")
        label.setStyleSheet("font-weight: 700;")
        section_layout.addWidget(label)

        hint = QLabel(
            "Les catégories ajoutées ici apparaissent automatiquement dans "
            "le formulaire d'ajout d'un produit."
        )
        hint.setStyleSheet("color: #8991ac; font-size: 12px;")
        hint.setWordWrap(True)
        section_layout.addWidget(hint)

        self.category_panel = CategoryPanel()
        section_layout.addWidget(self.category_panel)

        return section

    def _build_resellers_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("Revendeurs")
        label.setStyleSheet("font-weight: 700;")
        section_layout.addWidget(label)

        hint = QLabel(
            "Les revendeurs ajoutés ici sont sélectionnables lors d'une vente en gros."
        )
        hint.setStyleSheet("color: #8991ac; font-size: 12px;")
        hint.setWordWrap(True)
        section_layout.addWidget(hint)

        self.resellers_panel = ResellersPanel()
        section_layout.addWidget(self.resellers_panel)

        return section

    def _build_backup_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("☁️  Sauvegarde automatique — Cloud")
        label.setStyleSheet("font-weight: 700; font-size: 14px; color: #1e3a8a;")
        section_layout.addWidget(label)

        hint = QLabel(
            "La base de données est envoyée automatiquement vers le cloud "
            "chaque jour à l'heure configurée (si l'application est ouverte)."
        )
        hint.setStyleSheet("color: #8991ac; font-size: 12px;")
        hint.setWordWrap(True)
        section_layout.addWidget(hint)

        self.backup_panel = BackupPanel()
        section_layout.addWidget(self.backup_panel)

        return section

    def _build_printer_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("🖨  Imprimante des tickets")
        label.setStyleSheet("font-weight: 700; font-size: 14px; color: #1e3a8a;")
        section_layout.addWidget(label)

        hint = QLabel(
            "Imprimante et taille d'étiquette utilisées pour tous les tickets "
            "(ventes, achats, bons de retour)."
        )
        hint.setStyleSheet("color: #8991ac; font-size: 12px;")
        hint.setWordWrap(True)
        section_layout.addWidget(hint)

        self.printer_panel = PrinterPanel()
        section_layout.addWidget(self.printer_panel)
        return section

    def _build_ticket_section(self) -> QWidget:
        from PySide6.QtWidgets import QLineEdit as _LE
        section = QFrame()
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lbl = QLabel("🧾  Informations du ticket de caisse")
        lbl.setStyleSheet("font-weight: 700; font-size: 14px; color: #1e3a8a;")
        lay.addWidget(lbl)

        hint = QLabel("Ces informations apparaissent en en-tête sur chaque ticket imprimé.")
        hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        cfg = load_ticket_config()

        form = QFrame()
        form_lay = QVBoxLayout(form)
        form_lay.setContentsMargins(0, 0, 0, 0)
        form_lay.setSpacing(8)

        def _row(label_text: str, value: str) -> _LE:
            row = QHBoxLayout()
            row.setSpacing(12)
            lbl_w = QLabel(label_text)
            lbl_w.setFixedWidth(140)
            lbl_w.setStyleSheet("color: #555; font-size: 13px;")
            field = _LE()
            field.setText(value)
            field.setMinimumHeight(36)
            row.addWidget(lbl_w)
            row.addWidget(field)
            form_lay.addLayout(row)
            return field

        self._ticket_name_field = _row("Nom du magasin", cfg["store_name"])
        self._ticket_phone_field = _row("Téléphone", cfg["store_phone"])
        self._ticket_tagline_field = _row("Slogan / Activité", cfg["store_tagline"])

        lay.addWidget(form)

        save_btn = QPushButton("💾  Enregistrer")
        save_btn.setFixedWidth(160)
        save_btn.setFixedHeight(38)

        def _save():
            new_cfg = {
                "store_name": self._ticket_name_field.text().strip(),
                "store_phone": self._ticket_phone_field.text().strip(),
                "store_tagline": self._ticket_tagline_field.text().strip(),
            }
            save_ticket_config(new_cfg)
            save_btn.setText("✓  Enregistré")
            QTimer.singleShot(2000, lambda: save_btn.setText("💾  Enregistrer"))

        save_btn.clicked.connect(_save)
        lay.addWidget(save_btn)

        return section

    def _build_database_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("🗄️  Emplacement de la base de données")
        label.setStyleSheet("font-weight: 700; font-size: 14px; color: #1e3a8a;")
        section_layout.addWidget(label)

        db_path = get_database_path()

        path_row = QHBoxLayout()
        path_value = QLabel(str(db_path))
        path_value.setObjectName("DatabasePathValue")
        path_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_value.setWordWrap(True)
        path_row.addWidget(path_value, stretch=1)

        open_folder_button = QPushButton("📂  Ouvrir le dossier")
        open_folder_button.setObjectName("SecondaryButton")
        open_folder_button.clicked.connect(lambda: self._open_database_folder(db_path))
        path_row.addWidget(open_folder_button)

        section_layout.addLayout(path_row)

        hint = QLabel(
            "C'est ce fichier qu'il faut copier pour sauvegarder ou transférer "
            "les données vers un autre PC."
        )
        hint.setStyleSheet("color: #8991ac; font-size: 12px;")
        hint.setWordWrap(True)
        section_layout.addWidget(hint)

        return section

    def _open_database_folder(self, db_path: Path) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(db_path.parent)))

    def _build_security_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("🔒  Sécurité")
        label.setStyleSheet("font-weight: 700; font-size: 14px; color: #1e3a8a;")
        section_layout.addWidget(label)

        row = QHBoxLayout()
        change_password_button = QPushButton("🔒  Changer le mot de passe")
        change_password_button.setObjectName("SecondaryButton")
        change_password_button.clicked.connect(self._on_change_password)
        row.addWidget(change_password_button)
        row.addStretch()
        section_layout.addLayout(row)

        return section

    def _on_change_password(self) -> None:
        dialog = ChangePasswordDialog()
        if dialog.exec() == ChangePasswordDialog.DialogCode.Accepted:
            QMessageBox.information(
                self, "Mot de passe", "Le mot de passe a été changé avec succès."
            )

    def _build_update_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("🔄  Mises à jour")
        label.setStyleSheet("font-weight: 700; font-size: 14px; color: #1e3a8a;")
        section_layout.addWidget(label)

        row = QHBoxLayout()
        self.update_button = QPushButton("🔄  Vérifier les mises à jour")
        self.update_button.setObjectName("SecondaryButton")
        self.update_button.clicked.connect(self._on_check_for_update)
        row.addWidget(self.update_button)
        row.addStretch()
        section_layout.addLayout(row)

        hint_text = (
            "Vérifie sur GitHub si une nouvelle version est disponible et "
            "propose de l'installer. Nécessite une connexion internet "
            "ponctuelle — le reste de l'application fonctionne hors ligne."
        )
        if not is_frozen():
            hint_text += "\n(Disponible uniquement sur la version installée.)"
            self.update_button.setEnabled(False)
            self.update_button.setToolTip("Disponible uniquement sur la version installée.")

        hint = QLabel(hint_text)
        hint.setStyleSheet("color: #8991ac; font-size: 12px;")
        hint.setWordWrap(True)
        section_layout.addWidget(hint)

        return section

    def _on_check_for_update(self) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("🔄  Vérification...")

        thread = _UpdateCheckThread()
        thread.found.connect(self._on_update_found)
        thread.not_found.connect(self._on_update_not_found)
        thread.failed.connect(self._on_update_check_failed)
        thread.finished.connect(lambda: self._reset_update_button())
        self._update_check_thread = thread
        thread.start()

    def _reset_update_button(self) -> None:
        self.update_button.setEnabled(True)
        self.update_button.setText("🔄  Vérifier les mises à jour")

    def _on_update_not_found(self) -> None:
        QMessageBox.information(
            self, "Mises à jour", "Vous utilisez déjà la dernière version."
        )

    def _on_update_check_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Mises à jour", message)

    def _on_update_found(self, info: UpdateInfo) -> None:
        confirm = QMessageBox.question(
            self,
            "Mise à jour disponible",
            f"La version {info.version} est disponible. Mettre à jour maintenant ?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        dialog = UpdaterDialog(info)
        if dialog.exec() != UpdaterDialog.DialogCode.Accepted or dialog.downloaded_path is None:
            QMessageBox.warning(
                self, "Mise à jour", "Le téléchargement de la mise à jour a échoué."
            )
            return

        bat_path = build_swap_script(
            new_exe=dialog.downloaded_path,
            current_exe=Path(sys.executable),
            pid=os.getpid(),
        )
        launch_swap_and_exit(bat_path)
        # Le script de remplacement ne relance plus l'app automatiquement
        # (un relancement immédiat après le remplacement échouait de façon
        # répétée avec "Failed to load Python DLL" sur certains postes,
        # sans cause isolée malgré plusieurs délais testés) — on prévient
        # donc l'utilisateur ici, avant la fermeture, qu'il devra relancer
        # lui-même l'application dans quelques instants.
        QMessageBox.information(
            self,
            "Mise à jour",
            "La mise à jour va être installée. L'application va se fermer — "
            "veuillez la relancer depuis son raccourci dans quelques secondes.",
        )
        # QApplication.quit() ne fait que planifier la fin de la boucle
        # d'événements Qt — rien ne garantit que le process meure vite
        # (thread résiduel, event loop qui met du temps à sortir). Le
        # script de remplacement attend justement la disparition de ce
        # PID exact : une sortie tardive ou bloquée laisse sa boucle
        # d'attente tourner indéfiniment. os._exit() termine le process
        # immédiatement, sans passer par un nettoyage Qt qui pourrait
        # traîner — sûr ici puisque toute session DB a déjà été fermée
        # avant l'affichage du dialogue de mise à jour.
        os._exit(0)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_nav_selected(self, page_key: str) -> None:
        if page_key == "ventes":
            self.stack.setCurrentIndex(0)
        elif page_key in ("dashboard", "stock", "displays", "home"):
            self.search_input.clear()
            self._set_only_low_stock(False)
            self.stack.setCurrentIndex(1)
        elif page_key in ("historique", "movements"):
            self.historique_page.refresh()
            self.stack.setCurrentIndex(2)
        elif page_key == "profit":
            self.profit_tab.refresh()
            self.stack.setCurrentIndex(3)
        elif page_key == "alerts":
            self.stack.setCurrentIndex(1)
            self._set_only_low_stock(True)
        elif page_key == "purchase":
            self._on_purchase()
            self.sidebar.select_page(self._active_nav_key())
        elif page_key == "stock_adjust":
            self._on_stock_adjust()
            self.sidebar.select_page(self._active_nav_key())
        elif page_key == "settings":
            self.stack.setCurrentIndex(4)
        elif page_key in ("contacts", "resellers", "suppliers"):
            if page_key != "contacts":
                self.contacts_tabs.setCurrentIndex(0 if page_key == "resellers" else 1)
            self._refresh_contacts()
            self.stack.setCurrentIndex(5)
            self.sidebar.select_page("contacts")
        elif page_key == "categories":
            self.stack.setCurrentIndex(6)
        elif page_key == "returns":
            self.returns_page.refresh()
            self.stack.setCurrentIndex(7)
        elif page_key == "accounts":
            self.users_panel.refresh()
            self.stack.setCurrentIndex(8)
        elif page_key == "capital":
            from app import session as _session
            if _session.is_admin():
                self.capital_page.refresh()
                self.stack.setCurrentIndex(9)
        elif page_key == "expenses":
            from app import session as _session
            if _session.is_admin():
                self.expenses_page.refresh()
                self.stack.setCurrentIndex(10)

    def _set_only_low_stock(self, value: bool) -> None:
        """Filtre « Stock faible » (carte Stock bas, alerte) ou retour à tous."""
        self._only_low_stock = value
        index = self.stock_filter.findData("low" if value else None)
        if index != self.stock_filter.currentIndex():
            self.stock_filter.setCurrentIndex(index)  # relance le rafraîchissement

    def _active_nav_key(self) -> str:
        idx = self.stack.currentIndex()
        if idx == 0:
            return "ventes"
        if idx == 1:
            return "stock"
        if idx == 2:
            return "historique"
        if idx == 3:
            return "profit"
        if idx == 4:
            return "settings"
        if idx == 5:
            return "contacts"
        if idx == 6:
            return "categories"
        if idx == 7:
            return "returns"
        if idx == 8:
            return "accounts"
        if idx == 9:
            return "capital"
        if idx == 10:
            return "expenses"
        return "stock"

    # ------------------------------------------------------------------
    # Rafraîchissement des données
    # ------------------------------------------------------------------

    def _on_filters_changed(self) -> None:
        self._current_page = 1
        self.refresh()

    def _on_manual_refresh(self) -> None:
        self.refresh()
        # Forcer aussi le rafraîchissement des pages contacts actives
        idx = self.stack.currentIndex()
        if idx == 5:
            self._refresh_contacts()
        elif idx == 7:
            self.returns_page.refresh()

    def refresh(self) -> None:
        self._refresh_displays()
        self.sale_page._reload_products()
        # Rafraîchir historique et bénéfices seulement si visible pour éviter des requêtes inutiles
        if self.stack.currentIndex() == 2:
            self.historique_page.refresh()
        if self.stack.currentIndex() == 3:
            self.profit_tab.refresh()
        if self.stack.currentIndex() == 7:
            self.returns_page.refresh()
        if self.stack.currentIndex() == 9:
            self.capital_page.refresh()

    def _reload_filter_choices(self) -> None:
        """(Re)remplit la liste Catégorie en gardant le choix."""
        with session_scope() as session:
            categories = [(c.name, c.color) for c in list_categories(session)]
        for combo, all_label, entries in (
            (self.category_filter, "Catégorie : toutes", categories),
        ):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label, None)
            for name, color in entries:
                if color:
                    combo.addItem(color_dot_icon(color), name, name)
                else:
                    combo.addItem(name, name)
            combo.setCurrentIndex(max(combo.findData(current), 0))
            combo.blockSignals(False)

    def _filters_active(self) -> bool:
        return bool(
            self.search_input.text().strip()
            or self.category_filter.currentData()
            or self.stock_filter.currentData()
        )

    def _active_filter_labels(self) -> list[str]:
        return [
            combo.currentText()
            for combo in (self.category_filter, self.stock_filter)
            if combo.currentData()
        ]

    def _show_filter_hint(self, filters: list[str]) -> None:
        if filters:
            names = ", ".join(f"« {name} »" for name in filters)
            self.filter_hint_label.setText(
                f"⚠  Aucun résultat avec le filtre {names}. "
                "Voici ce qui existe sans ce filtre :"
            )
        self.filter_hint.setVisible(bool(filters))

    def _clear_filters_keep_search(self) -> None:
        for combo in (self.category_filter, self.stock_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._on_filters_changed()

    def _on_header_clicked(self, column: int) -> None:
        """Clic sur « Catégorie » ou « Quantité » : croissant, puis
        décroissant, puis retour à l'ordre habituel."""
        if column not in SORTABLE_COLUMNS:
            return
        if self._sort_column != column:
            self._sort_column, self._sort_descending = column, False
        elif not self._sort_descending:
            self._sort_descending = True
        else:
            self._sort_column = None
        self._current_page = 1
        self._sort_displays()
        self._render_current_page(None)

    def _sort_displays(self) -> None:
        # Flèche dans le titre (le style de l'en-tête masque l'indicateur Qt).
        for column in SORTABLE_COLUMNS:
            arrow = ""
            if column == self._sort_column:
                arrow = "  ▼" if self._sort_descending else "  ▲"
            self.displays_table.horizontalHeaderItem(column).setText(DISPLAY_COLUMNS[column] + arrow)
        if self._sort_column is None:
            return
        key = SORTABLE_COLUMNS[self._sort_column]
        # Tri stable : à égalité, l'ordre de la recherche est conservé.
        self._filtered_displays.sort(key=key, reverse=self._sort_descending)

    def _reset_filters(self) -> None:
        for combo in (self.category_filter, self.stock_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.search_input.clear()  # relance le rafraîchissement
        self._on_filters_changed()

    def _refresh_displays(self) -> None:
        self._reload_filter_choices()
        with session_scope() as session:
            all_active = list_displays(session, only_active=True)
            self._filtered_displays = list_displays(
                session,
                search=self.search_input.text(),
                category=self.category_filter.currentData(),
                stock_state=self.stock_filter.currentData(),
            )
            # Rien trouvé à cause d'un filtre : on montre ce qui existe sans
            # les filtres, avec un bandeau qui l'explique.
            hidden_by = self._active_filter_labels()
            showing_unfiltered = False
            if not self._filtered_displays and self.search_input.text().strip() and hidden_by:
                self._filtered_displays = list_displays(session, search=self.search_input.text())
                showing_unfiltered = bool(self._filtered_displays)
        self._sort_displays()
        self._show_filter_hint(hidden_by if showing_unfiltered else [])
        self.reset_filters_btn.setVisible(self._filters_active())
        with session_scope() as session:
            total_count = len(all_active)
            low_stock_count = count_low_stock_displays(session)
            stock_value_cents = sum(d.purchase_price_cents * d.quantity for d in all_active)
            self._category_colors = category_colors(session)

        total_qty = sum(d.quantity for d in all_active)
        self.total_card.set_value(str(total_count))
        self.low_stock_card.set_value(str(low_stock_count))
        self.stock_value_card.set_value(str(total_qty))
        self.stock_balance_card.set_value(format_da(stock_value_cents))
        self.sidebar.set_low_stock_badge(low_stock_count)
        alert_text = f"⚠  Stock bas : {low_stock_count}" if low_stock_count > 0 else "Stock : OK"
        self._status_alert_label.setText(alert_text)
        self._status_alert_label.setObjectName(
            "StatusBarAlert" if low_stock_count > 0 else "StatusBarLabel"
        )

        self._render_current_page(total_count)

    def _total_pages(self) -> int:
        if not self._filtered_displays:
            return 1
        return max(1, -(-len(self._filtered_displays) // self._page_size))

    def _go_to_page(self, page: int) -> None:
        page = max(1, min(page, self._total_pages()))
        if page == self._current_page:
            return
        self._current_page = page
        self._render_current_page(None)

    def _on_page_size_changed(self) -> None:
        self._page_size = PAGE_SIZE_CHOICES[self.page_size_combo.currentIndex()]
        self._current_page = 1
        self._render_current_page(None)

    def _render_current_page(self, total_active_count: int | None) -> None:
        filtered = self._filtered_displays
        total_pages = self._total_pages()
        self._current_page = max(1, min(self._current_page, total_pages))

        start = (self._current_page - 1) * self._page_size
        page_items = filtered[start : start + self._page_size]

        has_rows = len(page_items) > 0
        self.displays_table.setVisible(has_rows)
        self.displays_empty_state.setVisible(not has_rows)

        self.displays_table.setRowCount(len(page_items))
        for row_index, d in enumerate(page_items):
            values = [
                location_label(d.location_number),
                d.reference,
                d.category,
                d.brand,
                d.phone_model,
                d.quality,
                format_da(d.purchase_price_cents),
                format_da(d.sale_price_retail_cents),
                format_da(d.sale_price_wholesale_cents),
                d.quantity,
                d.min_stock,
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, d.id)
                if column_index in MONEY_COLUMNS:
                    item.setTextAlignment(ALIGN_RIGHT)
                price_style = PRICE_COLUMN_STYLES.get(column_index)
                if price_style is not None:
                    color, _title = price_style
                    item.setForeground(QBrush(QColor(color)))
                self.displays_table.setItem(row_index, column_index, item)

            self._set_category_badge(row_index, d.id, d.category)
            self._set_alert_badge(row_index, d.id, d.is_low_stock)
            self._set_row_actions(row_index, d.id)

        if total_active_count is None:
            with session_scope() as session:
                total_active_count = len(list_displays(session, only_active=True))

        self.summary_label.setText(
            f"{len(filtered)} produit(s) affiché(s) sur {total_active_count} au total."
        )
        self.page_indicator_label.setText(str(self._current_page))
        self.first_page_button.setEnabled(self._current_page > 1)
        self.prev_page_button.setEnabled(self._current_page > 1)
        self.next_page_button.setEnabled(self._current_page < total_pages)
        self.last_page_button.setEnabled(self._current_page < total_pages)

    def _set_category_badge(self, row_index: int, display_id: int, category: str) -> None:
        # La cellule garde un élément vide (sélection, identifiant du produit) ;
        # le badge coloré est posé par-dessus.
        placeholder = QTableWidgetItem("")
        placeholder.setData(Qt.ItemDataRole.UserRole, display_id)
        placeholder.setToolTip(category)
        self.displays_table.setItem(row_index, CATEGORY_COLUMN, placeholder)
        if not category:
            self.displays_table.removeCellWidget(row_index, CATEGORY_COLUMN)
            return
        color = self._category_colors.get(category, UNKNOWN_CATEGORY_COLOR)
        self.displays_table.setCellWidget(
            row_index, CATEGORY_COLUMN, category_badge(category, color)
        )

    def _set_alert_badge(self, row_index: int, display_id: int, is_low_stock: bool) -> None:
        column_index = len(DISPLAY_COLUMNS) - 2
        if not is_low_stock:
            item = QTableWidgetItem("")
            item.setData(Qt.ItemDataRole.UserRole, display_id)
            self.displays_table.setItem(row_index, column_index, item)
            self.displays_table.removeCellWidget(row_index, column_index)
            return

        badge = QLabel("Stock faible")
        badge.setObjectName("LowStockBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setWordWrap(False)
        badge.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(8, 0, 8, 0)
        wrapper_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        wrapper_layout.addStretch()

        placeholder = QTableWidgetItem("")
        placeholder.setData(Qt.ItemDataRole.UserRole, display_id)
        self.displays_table.setItem(row_index, column_index, placeholder)
        self.displays_table.setCellWidget(row_index, column_index, wrapper)

    def _set_row_actions(self, row_index: int, display_id: int) -> None:
        column_index = len(DISPLAY_COLUMNS) - 1

        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(2, 2, 2, 2)
        wrapper_layout.setSpacing(4)

        sale_button = QPushButton("🛒")
        sale_button.setObjectName("IconButton")
        sale_button.setFixedSize(24, 24)
        sale_button.setStyleSheet("padding: 0; font-size: 13px;")
        sale_button.setToolTip("Nouvelle vente")
        sale_button.clicked.connect(
            lambda _checked, d_id=display_id: self._on_row_sale(d_id)
        )
        wrapper_layout.addWidget(sale_button)

        add_stock_button = QPushButton("+")
        add_stock_button.setObjectName("IconButton")
        add_stock_button.setFixedSize(24, 24)
        add_stock_button.setStyleSheet("padding: 0; font-size: 14px;")
        add_stock_button.setToolTip("Achat fournisseur")
        add_stock_button.clicked.connect(
            lambda _checked, d_id=display_id: self._on_row_purchase(d_id)
        )
        wrapper_layout.addWidget(add_stock_button)

        delete_button = QPushButton("×")
        delete_button.setObjectName("IconButton")
        delete_button.setFixedSize(24, 24)
        delete_button.setStyleSheet("padding: 0; font-size: 15px; color: #dc2626; font-weight: 700;")
        delete_button.setToolTip("Supprimer")
        delete_button.clicked.connect(
            lambda _checked, d_id=display_id: self._on_row_delete(d_id)
        )
        wrapper_layout.addWidget(delete_button)

        wrapper_layout.addStretch()

        placeholder = QTableWidgetItem("")
        placeholder.setData(Qt.ItemDataRole.UserRole, display_id)
        self.displays_table.setItem(row_index, column_index, placeholder)
        self.displays_table.setCellWidget(row_index, column_index, wrapper)

    def _movements_total_pages(self) -> int:
        if self._movements_total_count == 0:
            return 1
        return max(1, -(-self._movements_total_count // self._movements_page_size))

    def _go_to_movements_page(self, page: int) -> None:
        page = max(1, min(page, self._movements_total_pages()))
        if page == self._movements_current_page:
            return
        self._movements_current_page = page
        self._render_movements_page()

    def _on_movements_page_size_changed(self) -> None:
        self._movements_page_size = PAGE_SIZE_CHOICES[
            self.movements_page_size_combo.currentIndex()
        ]
        self._movements_current_page = 1
        self._render_movements_page()

    def _refresh_movements(self) -> None:
        self._render_movements_page()

    def _on_print_ticket(self, batch_id: int | None, movement_id: int) -> None:
        from app.ui.ticket_dialog import TicketPreviewDialog
        dlg = TicketPreviewDialog(batch_id=batch_id, movement_id=movement_id, parent=self)
        dlg.exec()

    def _render_movements_page(self) -> None:
        with session_scope() as session:
            self._movements_total_count = count_movements(
                session, movement_type=self._movements_filter_type
            )
            total_pages = self._movements_total_pages()
            self._movements_current_page = max(
                1, min(self._movements_current_page, total_pages)
            )
            offset = (self._movements_current_page - 1) * self._movements_page_size
            movements = list_movements(
                session,
                movement_type=self._movements_filter_type,
                limit=self._movements_page_size,
                offset=offset,
            )
            def _reason_with_contact(m):
                if m.supplier is not None:
                    return f"{m.reason} — {m.supplier.name}"
                if m.reseller is not None:
                    return f"{m.reason} — {m.reseller.name}"
                return m.reason

            rows = [
                (
                    m.created_at.strftime("%d/%m/%Y %H:%M"),
                    m.display.reference,
                    "Entrée" if m.change_quantity > 0 else "Sortie",
                    m.quantity_before,
                    m.quantity_after,
                    _reason_with_contact(m),
                    m.is_sale,
                    m.movement_batch_id,
                    m.id,
                )
                for m in movements
            ]

        has_rows = len(rows) > 0
        self.movements_table.setVisible(has_rows)
        self.movements_empty_state.setVisible(not has_rows)

        self.movements_table.setRowCount(len(rows))
        for row_index, row_data in enumerate(rows):
            values = row_data[:6]
            is_sale, batch_id, movement_id = row_data[6], row_data[7], row_data[8]
            for column_index, value in enumerate(values):
                self.movements_table.setItem(
                    row_index, column_index, QTableWidgetItem(str(value))
                )
            if is_sale:
                from PySide6.QtWidgets import QPushButton
                print_btn = QPushButton("🖨")
                print_btn.setObjectName("IconButton")
                print_btn.setFixedSize(28, 28)
                print_btn.setStyleSheet("padding: 0; font-size: 13px;")
                print_btn.setToolTip("Imprimer le ticket")
                print_btn.clicked.connect(
                    lambda _c, bid=batch_id, mid=movement_id: self._on_print_ticket(bid, mid)
                )
                self.movements_table.setCellWidget(row_index, 6, print_btn)

        self.movements_summary_label.setText(
            f"{len(rows)} mouvement(s) affiché(s) sur {self._movements_total_count} au total."
        )
        self.movements_page_indicator_label.setText(str(self._movements_current_page))
        self.movements_first_page_button.setEnabled(self._movements_current_page > 1)
        self.movements_prev_page_button.setEnabled(self._movements_current_page > 1)
        self.movements_next_page_button.setEnabled(
            self._movements_current_page < total_pages
        )
        self.movements_last_page_button.setEnabled(
            self._movements_current_page < total_pages
        )

    # ------------------------------------------------------------------
    # Sélection courante
    # ------------------------------------------------------------------

    def _selected_display_id(self) -> int | None:
        selected_rows = self.displays_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        item = self.displays_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        dialog = DisplayDialog()
        if dialog.exec() == DisplayDialog.DialogCode.Accepted:
            self.refresh()

    def _on_edit(self) -> None:
        display_id = self._selected_display_id()
        if display_id is None:
            QMessageBox.information(self, "Modifier", "Veuillez sélectionner un produit.")
            return
        dialog = DisplayDialog(display_id)
        if dialog.exec() == DisplayDialog.DialogCode.Accepted:
            self.refresh()

    def _on_new_sale(self) -> None:
        self.stack.setCurrentIndex(0)
        self.sidebar.select_page("ventes")

    def _on_new_sale_dialog(self) -> None:
        dialog = SaleDialog(self._selected_display_id())
        if dialog.exec() == SaleDialog.DialogCode.Accepted:
            self.refresh()

    def _on_merge_duplicates(self) -> None:
        from app.ui.merge_dialog import MergeDialog

        dialog = MergeDialog(self)
        dialog.exec()
        if dialog.changed:
            self.refresh()

    def _on_purchase(self) -> None:
        dialog = SupplierPurchaseDialog(self._selected_display_id())
        if dialog.exec() == SupplierPurchaseDialog.DialogCode.Accepted:
            self.refresh()

    def _on_stock_adjust(self) -> None:
        dialog = StockAdjustmentDialog(self._selected_display_id())
        if dialog.exec() == StockAdjustmentDialog.DialogCode.Accepted:
            self.refresh()

    def _on_row_sale(self, display_id: int) -> None:
        self.sale_page.add_product(display_id)
        self.stack.setCurrentIndex(0)
        self.sidebar.select_page("ventes")

    def _on_row_purchase(self, display_id: int) -> None:
        dialog = SupplierPurchaseDialog(display_id)
        if dialog.exec() == SupplierPurchaseDialog.DialogCode.Accepted:
            self.refresh()

    def _on_row_delete(self, display_id: int) -> None:
        confirm = QMessageBox.question(
            self,
            "Confirmer la suppression",
            "Voulez-vous vraiment supprimer ce produit ? "
            "Il n'apparaîtra plus dans la liste mais son historique est conservé.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            with session_scope() as session:
                deactivate_display(session, display_id)
        except StockError as error:
            QMessageBox.warning(self, "Erreur", str(error))
            return

        self.refresh()

    def _on_export_csv(self) -> None:
        if not self._filtered_displays:
            QMessageBox.information(self, "Exporter", "Aucun produit à exporter.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les produits", "produits.csv", "Fichiers CSV (*.csv)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as csv_file:
                writer = csv.writer(csv_file, delimiter=";")
                writer.writerow(DISPLAY_COLUMNS[:-2])
                for d in self._filtered_displays:
                    writer.writerow(
                        [
                            location_label(d.location_number),
                            d.reference,
                            d.category,
                            d.brand,
                            d.phone_model,
                            d.quality,
                            f"{round(cents_to_da(d.purchase_price_cents))}",
                            f"{round(cents_to_da(d.sale_price_retail_cents))}",
                            f"{round(cents_to_da(d.sale_price_wholesale_cents))}",
                            d.quantity,
                            d.min_stock,
                        ]
                    )
                    # Note: ordre CSV aligné sur DISPLAY_COLUMNS (prix achat avant prix vente)
        except OSError as error:
            QMessageBox.warning(self, "Erreur d'export", f"Impossible d'écrire le fichier : {error}")
            return

        QMessageBox.information(self, "Export réussi", f"{len(self._filtered_displays)} produit(s) exporté(s).")

    def _on_columns_clicked(self) -> None:
        QMessageBox.information(
            self,
            "Personnaliser les colonnes",
            "La personnalisation des colonnes affichées sera disponible dans une prochaine version.",
        )
