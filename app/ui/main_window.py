"""Fenêtre principale — gestion du stock de produits."""

from __future__ import annotations

import csv
import datetime
import os
import sys
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QIcon
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
from app.money import cents_to_da, format_da
from app.paths import get_database_path, is_frozen
from app.period import bounds_for_period
from app.services import (
    StockError,
    count_low_stock_displays,
    count_movements,
    deactivate_display,
    list_displays,
    list_movements,
    sum_profit_cents,
)
from app.ui.category_panel import CategoryPanel
from app.ui.change_password_dialog import ChangePasswordDialog
from app.ui.contacts_tab import ContactsTab
from app.ui.display_dialog import DisplayDialog
from app.ui.profit_tab import ProfitTab
from app.ui.repairs_tab import RepairsTab
from app.ui.sidebar import Sidebar
from app.ui.stock_adjust_dialog import (
    StockAdjustmentDialog,
    SupplierPurchaseDialog,
    WholesaleSaleDialog,
)
from app.ui.updater_dialog import UpdaterDialog
from app.ui.widgets import EmptyState, IconStatCard, apply_card_shadow
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
    "Référence",
    "Catégorie",
    "Marque",
    "Modèle",
    "Qualité",
    "Prix d'achat",
    "Prix de vente (détail)",
    "Prix de vente (gros)",
    "Quantité",
    "Stock min.",
    "Alerte",
    "",
]

MOVEMENT_COLUMNS = ["Date", "Pièce", "Mouvement", "Avant", "Après", "Motif"]

MONTH_NAMES_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

PAGE_SIZE_CHOICES = [10, 25, 50, 100]

APP_ICON_PATH = Path(__file__).resolve().parent / "resources" / "app_icon.png"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MobiDesk Pro — Stock des produits")
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(1280, 780)
        self.setMinimumSize(1000, 620)

        self._current_page = 1
        self._page_size = PAGE_SIZE_CHOICES[0]
        self._filtered_displays: list = []
        self._only_low_stock = False

        self._movements_current_page = 1
        self._movements_page_size = PAGE_SIZE_CHOICES[0]
        self._movements_total_count = 0

        self._build_ui()
        self._start_clock()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.page_selected.connect(self._on_nav_selected)
        root_layout.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        root_layout.addWidget(content, stretch=1)

        content_layout.addWidget(self._build_header())

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, stretch=1)

        self.stack.addWidget(self._build_stock_page())
        self.stack.addWidget(self._build_settings_page())

        self.sidebar.select_page("dashboard")

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("TopBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(14)

        titles = QVBoxLayout()
        titles.setSpacing(2)

        self.greeting_label = QLabel("Bonjour 👋")
        self.greeting_label.setObjectName("AppTitle")
        titles.addWidget(self.greeting_label)

        subtitle = QLabel("Voici un aperçu de votre stock aujourd'hui.")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(subtitle)

        layout.addLayout(titles)
        layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Rechercher par référence, marque ou modèle..."
        )
        self.search_input.setObjectName("HeaderSearchInput")
        self.search_input.setFixedWidth(360)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(300)
        self._search_debounce.timeout.connect(self._on_filters_changed)
        self.search_input.textChanged.connect(lambda: self._search_debounce.start())
        layout.addWidget(self.search_input)

        add_product_button = QPushButton("+  Ajouter un produit")
        add_product_button.clicked.connect(self._on_add)
        layout.addWidget(add_product_button)

        layout.addStretch()

        self.datetime_pill = self._build_datetime_pill()
        layout.addWidget(self.datetime_pill)

        return header

    def _build_datetime_pill(self) -> QFrame:
        pill = QFrame()
        pill.setObjectName("DateTimePill")
        layout = QHBoxLayout(pill)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(8)

        icon = QLabel("📅")
        layout.addWidget(icon)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.date_label = QLabel("")
        texts.addWidget(self.date_label)
        self.time_label = QLabel("")
        self.time_label.setObjectName("DateTimeSubLabel")
        texts.addWidget(self.time_label)
        layout.addLayout(texts)

        return pill

    def _start_clock(self) -> None:
        self._update_clock()
        timer = QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)
        self._clock_timer = timer

    def _update_clock(self) -> None:
        now = datetime.datetime.now()
        self.date_label.setText(f"{now.day} {MONTH_NAMES_FR[now.month - 1]} {now.year}")
        self.time_label.setText(now.strftime("%H:%M"))

    # ---------------- Page « Stock » (tableau de bord + produits) ---------

    def _build_stock_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(18)

        layout.addLayout(self._build_stat_cards())

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        self.tabs.addTab(self._build_displays_tab(), "🖥️  Produits")
        self.tabs.addTab(self._build_movements_tab(), "📋  Mouvements de stock")
        self.profit_tab = ProfitTab()
        self.tabs.addTab(self.profit_tab, "💰  Bénéfices")
        self.repairs_tab = RepairsTab()
        self.tabs.addTab(self.repairs_tab, "🔧  Réparations")
        self.contacts_tab = ContactsTab()
        self.tabs.addTab(self.contacts_tab, "👥  Contacts")

        return page

    def _build_stat_cards(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(16)

        self.total_card = IconStatCard(
            "🖥️", "Produits en stock", "0", "Total disponible",
            accent="#0f5c46", accent_bg="#e0efe8", sparkline_seed=1,
        )
        self.low_stock_card = IconStatCard(
            "🔔", "En alerte de stock bas", "0", "Nécessite votre attention",
            accent="#d97706", accent_bg="#fef3c7", sparkline_seed=4,
        )
        self.stock_value_card = IconStatCard(
            "💰", "Valeur du stock (achat)", "0 DA", "Valeur totale d'achat",
            accent="#059669", accent_bg="#d1fae5", sparkline_seed=7,
        )
        self.profit_card = IconStatCard(
            "💵", "Bénéfice (ce mois-ci)", "0 DA", "Sur les ventes du mois en cours",
            accent="#0891b2", accent_bg="#cffafe", sparkline_seed=11,
        )

        for card in (
            self.total_card, self.low_stock_card, self.stock_value_card, self.profit_card,
        ):
            layout.addWidget(card, stretch=1)

        return layout

    def _build_displays_tab(self) -> QWidget:
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
        self.displays_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.displays_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.displays_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.displays_table.setAlternatingRowColors(True)
        self.displays_table.setShowGrid(False)
        self.displays_table.verticalHeader().setVisible(False)
        self.displays_table.verticalHeader().setDefaultSectionSize(40)
        self.displays_table.setFrameShape(QFrame.Shape.NoFrame)
        self.displays_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        # Les colonnes Alerte et Action contiennent des cellWidgets posés
        # après le rendu initial de la ligne — ResizeToContents ignorerait
        # tout setColumnWidth ultérieur, laissant le badge "Stock faible"
        # tronqué. Une largeur fixe leur garantit assez de place.
        alert_column_index = len(DISPLAY_COLUMNS) - 2
        action_column_index = len(DISPLAY_COLUMNS) - 1
        self.displays_table.horizontalHeader().setSectionResizeMode(
            alert_column_index, QHeaderView.ResizeMode.Fixed
        )
        self.displays_table.horizontalHeader().setSectionResizeMode(
            action_column_index, QHeaderView.ResizeMode.Fixed
        )
        self.displays_table.setColumnWidth(alert_column_index, 120)
        self.displays_table.setColumnWidth(action_column_index, 70)
        self.displays_table.horizontalHeader().setStretchLastSection(False)
        self.displays_table.doubleClicked.connect(self._on_edit)
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

        return container

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

        self.first_page_button = QPushButton("⏮")
        self.first_page_button.setObjectName("PageNavButton")
        self.first_page_button.clicked.connect(lambda: self._go_to_page(1))
        layout.addWidget(self.first_page_button)

        self.prev_page_button = QPushButton("‹")
        self.prev_page_button.setObjectName("PageNavButton")
        self.prev_page_button.clicked.connect(lambda: self._go_to_page(self._current_page - 1))
        layout.addWidget(self.prev_page_button)

        self.page_indicator_label = QLabel("1")
        self.page_indicator_label.setObjectName("PageIndicator")
        self.page_indicator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_indicator_label.setFixedWidth(32)
        layout.addWidget(self.page_indicator_label)

        self.next_page_button = QPushButton("›")
        self.next_page_button.setObjectName("PageNavButton")
        self.next_page_button.clicked.connect(lambda: self._go_to_page(self._current_page + 1))
        layout.addWidget(self.next_page_button)

        self.last_page_button = QPushButton("⏭")
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

        self.movements_first_page_button = QPushButton("⏮")
        self.movements_first_page_button.setObjectName("PageNavButton")
        self.movements_first_page_button.clicked.connect(
            lambda: self._go_to_movements_page(1)
        )
        layout.addWidget(self.movements_first_page_button)

        self.movements_prev_page_button = QPushButton("‹")
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

        self.movements_next_page_button = QPushButton("›")
        self.movements_next_page_button.setObjectName("PageNavButton")
        self.movements_next_page_button.clicked.connect(
            lambda: self._go_to_movements_page(self._movements_current_page + 1)
        )
        layout.addWidget(self.movements_next_page_button)

        self.movements_last_page_button = QPushButton("⏭")
        self.movements_last_page_button.setObjectName("PageNavButton")
        self.movements_last_page_button.clicked.connect(
            lambda: self._go_to_movements_page(self._movements_total_pages())
        )
        layout.addWidget(self.movements_last_page_button)

        return bar

    def _build_movements_tab(self) -> QWidget:
        container = QFrame()
        container.setObjectName("Card")
        apply_card_shadow(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

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
        self.movements_table.horizontalHeader().setStretchLastSection(True)
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
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(10)

        title = QLabel("⚙️  Paramètres")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        card_layout.addWidget(title)

        info_label = QLabel(
            f"MobiDesk Pro — Gestion du stock de produits\nVersion {APP_VERSION}"
        )
        info_label.setStyleSheet("color: #8991ac; font-weight: 500;")
        card_layout.addWidget(info_label)

        card_layout.addSpacing(12)
        card_layout.addWidget(self._build_categories_section())

        card_layout.addSpacing(12)
        card_layout.addWidget(self._build_database_section())

        card_layout.addSpacing(12)
        card_layout.addWidget(self._build_security_section())

        card_layout.addSpacing(12)
        card_layout.addWidget(self._build_update_section())

        layout.addWidget(card)
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

    def _build_database_section(self) -> QWidget:
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        label = QLabel("Emplacement de la base de données")
        label.setStyleSheet("font-weight: 700;")
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

        label = QLabel("Sécurité")
        label.setStyleSheet("font-weight: 700;")
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

        label = QLabel("Mises à jour")
        label.setStyleSheet("font-weight: 700;")
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
        if page_key == "dashboard":
            self.search_input.clear()
            self._set_only_low_stock(False)
            self.stack.setCurrentIndex(0)
            self.tabs.setCurrentIndex(0)
        elif page_key == "displays":
            self._set_only_low_stock(False)
            self.stack.setCurrentIndex(0)
            self.tabs.setCurrentIndex(0)
        elif page_key == "movements":
            self.stack.setCurrentIndex(0)
            self.tabs.setCurrentIndex(1)
        elif page_key == "profit":
            self.stack.setCurrentIndex(0)
            self.tabs.setCurrentIndex(2)
        elif page_key == "repairs":
            self.stack.setCurrentIndex(0)
            self.tabs.setCurrentIndex(3)
        elif page_key == "contacts":
            self.stack.setCurrentIndex(0)
            self.tabs.setCurrentIndex(4)
        elif page_key == "alerts":
            self.stack.setCurrentIndex(0)
            self.tabs.setCurrentIndex(0)
            self._set_only_low_stock(True)
        elif page_key == "purchase":
            self._on_purchase()
            self.sidebar.select_page(self._active_nav_key())
        elif page_key == "sale_wholesale":
            self._on_sale_wholesale()
            self.sidebar.select_page(self._active_nav_key())
        elif page_key == "stock_adjust":
            self._on_stock_adjust()
            self.sidebar.select_page(self._active_nav_key())
        elif page_key == "settings":
            self.stack.setCurrentIndex(1)

    def _set_only_low_stock(self, value: bool) -> None:
        if value == self._only_low_stock:
            return
        self._only_low_stock = value
        self._on_filters_changed()

    def _active_nav_key(self) -> str:
        if self.stack.currentIndex() == 1:
            return "settings"
        if self._only_low_stock:
            return "alerts"
        if self.tabs.currentIndex() == 4:
            return "contacts"
        if self.tabs.currentIndex() == 3:
            return "repairs"
        if self.tabs.currentIndex() == 2:
            return "profit"
        return "movements" if self.tabs.currentIndex() == 1 else "displays"

    # ------------------------------------------------------------------
    # Rafraîchissement des données
    # ------------------------------------------------------------------

    def _on_filters_changed(self) -> None:
        self._current_page = 1
        self.refresh()

    def refresh(self) -> None:
        self._refresh_displays()
        self._refresh_movements()
        self._refresh_profit_card()
        self.profit_tab.refresh()
        self.repairs_tab.refresh()
        self.contacts_tab.refresh()

    def _refresh_profit_card(self) -> None:
        start, _ = bounds_for_period("Ce mois-ci")
        with session_scope() as session:
            profit_cents = sum_profit_cents(session, start=start)
        self.profit_card.set_value(format_da(profit_cents))

    def _refresh_displays(self) -> None:
        with session_scope() as session:
            all_active = list_displays(session, only_active=True)
            self._filtered_displays = list_displays(
                session,
                search=self.search_input.text(),
                only_low_stock=self._only_low_stock,
            )
            total_count = len(all_active)
            low_stock_count = count_low_stock_displays(session)
            stock_value = sum(cents_to_da(d.purchase_price_cents) * d.quantity for d in all_active)

        self.total_card.set_value(str(total_count))
        self.low_stock_card.set_value(str(low_stock_count))
        formatted_value = f"{round(stock_value):,} DA".replace(",", " ")
        self.stock_value_card.set_value(formatted_value)
        self.sidebar.set_stock_value(formatted_value)

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
                self.displays_table.setItem(row_index, column_index, item)

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

    def _render_movements_page(self) -> None:
        with session_scope() as session:
            self._movements_total_count = count_movements(session)
            total_pages = self._movements_total_pages()
            self._movements_current_page = max(
                1, min(self._movements_current_page, total_pages)
            )
            offset = (self._movements_current_page - 1) * self._movements_page_size
            movements = list_movements(
                session, limit=self._movements_page_size, offset=offset
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
                )
                for m in movements
            ]

        has_rows = len(rows) > 0
        self.movements_table.setVisible(has_rows)
        self.movements_empty_state.setVisible(not has_rows)

        self.movements_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                self.movements_table.setItem(
                    row_index, column_index, QTableWidgetItem(str(value))
                )

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

    def _on_purchase(self) -> None:
        dialog = SupplierPurchaseDialog(self._selected_display_id())
        if dialog.exec() == SupplierPurchaseDialog.DialogCode.Accepted:
            self.refresh()

    def _on_sale_wholesale(self) -> None:
        dialog = WholesaleSaleDialog(self._selected_display_id())
        if dialog.exec() == WholesaleSaleDialog.DialogCode.Accepted:
            self.refresh()

    def _on_stock_adjust(self) -> None:
        dialog = StockAdjustmentDialog(self._selected_display_id())
        if dialog.exec() == StockAdjustmentDialog.DialogCode.Accepted:
            self.refresh()

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
