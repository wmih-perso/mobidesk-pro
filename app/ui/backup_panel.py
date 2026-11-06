"""Sauvegarde automatique vers le cloud (S3 / Backblaze B2 / Cloudflare R2).

La page Paramètres n'affiche qu'un résumé et trois boutons : « Configurer »
(compte admin) ouvre BackupConfigDialog pour saisir les informations du
compte — la Secret Key y reste toujours masquée —, « Tester la connexion »
et « Sauvegarder maintenant ».
"""

from __future__ import annotations

import datetime
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app import session as _session
from app.backup import (
    RESTART_FLAG,
    download_for_restore,
    is_configured,
    list_cloud_backups,
    load_backup_config,
    save_backup_config,
    test_connection,
    upload_backup,
)
from app.paths import get_database_path
from app.ui.frameless_dialog import FramelessDialog

# Aides à la saisie selon le fournisseur
_PROVIDERS = {
    "Backblaze B2": {
        "hint_endpoint": "https://s3.<region>.backblazeb2.com  (ex: https://s3.us-west-004.backblazeb2.com)",
        "hint_key": "Key ID (commence par 00...)",
        "hint_secret": "Application Key",
        "guide": (
            "1. Créez un compte sur backblaze.com\n"
            "2. Allez dans «B2 Cloud Storage» → «Buckets» → «Create a Bucket»\n"
            "   Nom : mobidesk-backups  |  Type : Private\n"
            "3. Allez dans «App Keys» → «Add a New Application Key»\n"
            "   Donnez accès au bucket créé\n"
            "4. Copiez le keyID et applicationKey affichés (ils ne s'affichent qu'une fois)\n"
            "5. L'endpoint se trouve dans les infos du bucket (ex: s3.us-west-004.backblazeb2.com)\n"
            "   Ajoutez «https://» devant\n"
            "6. Entrez ces valeurs dans les champs et cliquez «Tester»"
        ),
    },
    "Cloudflare R2": {
        "hint_endpoint": "https://<account-id>.r2.cloudflarestorage.com",
        "hint_key": "Access Key ID",
        "hint_secret": "Secret Access Key",
        "guide": (
            "1. Créez un compte sur cloudflare.com\n"
            "2. Allez dans R2 Object Storage → «Create bucket»\n"
            "   Nom : mobidesk-backups\n"
            "3. Allez dans «Manage R2 API Tokens» → «Create API Token»\n"
            "   Permissions : Object Read & Write sur le bucket créé\n"
            "4. Copiez Access Key ID et Secret Access Key\n"
            "5. L'endpoint est : https://<votre-account-id>.r2.cloudflarestorage.com\n"
            "   (votre Account ID se trouve dans la page d'accueil Cloudflare)\n"
            "6. Entrez ces valeurs dans les champs et cliquez «Tester»"
        ),
    },
}

_REQUIRED_FIELDS = (
    ("endpoint_url", "Endpoint URL"),
    ("access_key_id", "Access Key ID"),
    ("secret_access_key", "Secret Key"),
    ("bucket_name", "Nom du bucket"),
)


class BackupResult(QObject):
    """Ramène le résultat d'une sauvegarde (calculé dans un thread) sur le
    thread de l'interface : Qt interdit de toucher aux widgets, ou
    d'ouvrir une boîte de dialogue, depuis un autre thread."""

    finished = Signal(bool, str)


# Garde une référence aux ponts en cours, jusqu'à leur signal.
_pending: set[BackupResult] = set()


def _bridge(on_done) -> BackupResult:
    bridge = BackupResult()
    _pending.add(bridge)

    def _finish(success: bool, message: str) -> None:
        _pending.discard(bridge)
        bridge.deleteLater()
        on_done(success, message)

    bridge.finished.connect(_finish)
    return bridge


def run_backup(on_done, config: dict | None = None) -> None:
    """upload_backup, avec `on_done(succès, message)` appelé sur le thread
    de l'interface."""
    upload_backup(_bridge(on_done).finished.emit, config=config)


def run_connection_test(on_done, config: dict) -> None:
    """test_connection dans un thread, `on_done` sur le thread de l'interface."""
    bridge = _bridge(on_done)
    threading.Thread(
        target=lambda: bridge.finished.emit(*test_connection(config)), daemon=True
    ).start()


def format_last_backup(config: dict) -> tuple[str, str]:
    """(texte, couleur) de la ligne « Dernière sauvegarde »."""
    last = config.get("last_backup_at")
    if not last:
        return "Dernière sauvegarde : aucune pour l'instant", "#64748b"
    try:
        when = datetime.datetime.fromisoformat(last).strftime("%d/%m/%Y à %H:%M")
    except ValueError:
        when = last
    if config.get("last_backup_ok"):
        return f"Dernière sauvegarde : {when}  ✓", "#059669"
    return f"Dernière sauvegarde : {when}  ✗  {config.get('last_backup_message', '')}", "#ef4444"


# ---------------------------------------------------------------------------
# Section des Paramètres
# ---------------------------------------------------------------------------


class BackupPanel(QWidget):
    """Résumé de la configuration + Configurer / Tester / Sauvegarder."""

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        self._last_backup_label = QLabel("")
        self._last_backup_label.setWordWrap(True)
        layout.addWidget(self._last_backup_label)

        buttons_row = QHBoxLayout()
        self._configure_btn = QPushButton("⚙  Configurer")
        self._configure_btn.clicked.connect(self._on_configure)
        self._configure_btn.setVisible(_session.is_admin())
        buttons_row.addWidget(self._configure_btn)

        self._test_btn = QPushButton("Tester la connexion")
        self._test_btn.setObjectName("SecondaryButton")
        self._test_btn.clicked.connect(self._on_test_connection)
        buttons_row.addWidget(self._test_btn)

        self._backup_now_btn = QPushButton("Sauvegarder maintenant")
        self._backup_now_btn.setObjectName("SecondaryButton")
        self._backup_now_btn.clicked.connect(self._on_backup_now)
        buttons_row.addWidget(self._backup_now_btn)

        self._restore_btn = QPushButton("☁  Récupérer depuis le cloud")
        self._restore_btn.setObjectName("SecondaryButton")
        self._restore_btn.setToolTip("Remplacer la base de ce PC par une sauvegarde du cloud")
        self._restore_btn.clicked.connect(self._on_restore)
        self._restore_btn.setVisible(_session.is_admin())
        buttons_row.addWidget(self._restore_btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    def _refresh(self) -> None:
        config = load_backup_config()
        configured = is_configured()
        if configured:
            account = (
                "Compte : <b style='color:#059669;'>configuré ✓</b>"
                f" — bucket « {config.get('bucket_name')} »"
            )
            if config.get("enabled"):
                auto = f"Automatique : <b style='color:#059669;'>activée à {config.get('hour', 20)}h00</b>"
                if config.get("backup_on_close", True):
                    auto += " <b style='color:#059669;'>+ à la fermeture</b>"
            else:
                auto = "Automatique : <b style='color:#b45309;'>désactivée</b>"
            self._summary_label.setText(f"{account}  ·  {auto}")
        else:
            hint = (
                "cliquez sur « Configurer »." if _session.is_admin()
                else "demandez à l'administrateur de la configurer."
            )
            self._summary_label.setText(
                f"Compte : <b style='color:#ef4444;'>non configuré</b> — {hint}"
            )
        self._test_btn.setEnabled(configured)
        self._backup_now_btn.setEnabled(configured)
        self._restore_btn.setEnabled(configured)
        self._refresh_last_backup()

    def _refresh_last_backup(self) -> None:
        text, color = format_last_backup(load_backup_config())
        self._last_backup_label.setText(text)
        self._last_backup_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _set_status(self, text: str, color: str, bold: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color};" + (" font-weight: 600;" if bold else ""))

    def _on_configure(self) -> None:
        dialog = BackupConfigDialog(parent=self)
        if dialog.exec() == BackupConfigDialog.DialogCode.Accepted:
            self._refresh()
            self._set_status("✓  Configuration enregistrée.", "#059669", bold=True)

    def _on_test_connection(self) -> None:
        self._set_status("Test en cours…", "#64748b")
        self._test_btn.setEnabled(False)

        def _done(success: bool, message: str) -> None:
            self._test_btn.setEnabled(True)
            self._set_status(message, "#059669" if success else "#ef4444", bold=success)

        run_connection_test(_done, load_backup_config())

    def _on_backup_now(self) -> None:
        self._set_status("Sauvegarde en cours…", "#64748b")
        self._backup_now_btn.setEnabled(False)

        def _done(success: bool, message: str) -> None:
            self._set_status(message, "#059669" if success else "#ef4444", bold=success)
            self._backup_now_btn.setEnabled(True)
            self._refresh_last_backup()

        run_backup(_done, config=load_backup_config())

    def _on_restore(self) -> None:
        RestoreDialog(parent=self).exec()


# ---------------------------------------------------------------------------
# Fenêtre « Récupérer depuis le cloud »
# ---------------------------------------------------------------------------


def _run_in_thread(on_done, work) -> None:
    """`work()` dans un thread ; `on_done(succès, résultat ou message)` sur
    le thread de l'interface."""
    results: dict = {}
    bridge = _bridge(lambda ok, msg: on_done(ok, results.get("value") if ok else msg))

    def _target() -> None:
        try:
            results["value"] = work()
            bridge.finished.emit(True, "")
        except Exception as exc:
            bridge.finished.emit(False, str(exc))

    threading.Thread(target=_target, daemon=True).start()


def restart_application() -> None:
    """Relance MobiDesk (la base récupérée s'installe au démarrage)."""
    import sys
    from pathlib import Path

    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from app.paths import is_frozen

    for widget in QApplication.topLevelWidgets():
        # Pas de sauvegarde « à la fermeture » : elle enverrait l'ancienne
        # base, qui deviendrait la plus récente du cloud.
        if hasattr(widget, "_close_backup_done"):
            widget._close_backup_done = True
    if is_frozen():
        args = [RESTART_FLAG]
    else:
        args = [str(Path(__file__).resolve().parents[2] / "main.py"), RESTART_FLAG]
    QProcess.startDetached(sys.executable, args)
    QApplication.closeAllWindows()
    QApplication.quit()


class RestoreDialog(FramelessDialog):
    """Liste des sauvegardes du cloud (la plus récemment déposée en haut)
    et remplacement de la base de ce PC par celle choisie."""

    def __init__(self, parent=None) -> None:
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget

        super().__init__(parent)
        self.setMinimumWidth(680)
        self.setModal(True)
        self._backups: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header("☁  Récupérer depuis le cloud"))

        body = QWidget()
        body.setStyleSheet("background: white;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        warning = QLabel(
            "⚠  La base choisie <b>remplace</b> celle de ce PC. Les ventes, retours et "
            "achats saisis ici après cette sauvegarde seront perdus.<br>"
            "Une copie de la base actuelle est gardée avant le remplacement."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background: #fff7ed; border: 1px solid #fdba74; border-radius: 8px;"
            " padding: 10px; color: #9a3412;"
        )
        layout.addWidget(warning)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Déposée le", "Fichier", "Taille"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(280)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.doubleClicked.connect(self._on_restore)
        layout.addWidget(self._table)

        self._status = QLabel("Chargement de la liste…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #64748b;")
        layout.addWidget(self._status)
        root.addWidget(body)

        footer = QFrame()
        footer.setStyleSheet("QFrame { background: #f0f9ff; border-top: 1px solid #bae6fd; }")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 12, 20, 12)
        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)
        footer_layout.addStretch()
        self._restore_btn = QPushButton("☁  Récupérer")
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_restore)
        footer_layout.addWidget(self._restore_btn)
        root.addWidget(footer)

        _run_in_thread(self._on_listed, lambda: list_cloud_backups(load_backup_config()))

    def _on_listed(self, success: bool, result) -> None:
        from PySide6.QtWidgets import QTableWidgetItem

        if not success:
            self._status.setText(f"Impossible de lire le cloud : {result}")
            self._status.setStyleSheet("color: #ef4444;")
            return
        self._backups = result
        self._table.setRowCount(len(result))
        for row, backup in enumerate(result):
            self._table.setItem(row, 0, QTableWidgetItem(backup.uploaded_at.strftime("%d/%m/%Y  %H:%M")))
            self._table.setItem(row, 1, QTableWidgetItem(backup.name))
            self._table.setItem(row, 2, QTableWidgetItem(f"{backup.size / 1_048_576:.1f} Mo"))
        if result:
            self._table.selectRow(0)
            self._restore_btn.setEnabled(True)
            self._status.setText(f"{len(result)} sauvegarde(s). La plus récente est sélectionnée.")
        else:
            self._status.setText("Aucune sauvegarde (.db) dans le dossier du cloud.")

    def _selected(self):
        rows = self._table.selectionModel().selectedRows()
        return self._backups[rows[0].row()] if rows else None

    def _on_restore(self) -> None:
        backup = self._selected()
        if backup is None:
            return
        modified = datetime.datetime.fromtimestamp(get_database_path().stat().st_mtime)
        extra = ""
        if modified > backup.uploaded_at:
            extra = (
                f"\n\n⚠  La base de ce PC a été modifiée le {modified:%d/%m/%Y à %H:%M}, "
                "APRÈS cette sauvegarde : ces changements seront perdus."
            )
        confirm = QMessageBox.question(
            self, "Récupérer depuis le cloud",
            f"Remplacer la base de ce PC par :\n{backup.name}\n"
            f"(déposée le {backup.uploaded_at:%d/%m/%Y à %H:%M}) ?{extra}\n\n"
            "MobiDesk redémarrera tout seul.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._restore_btn.setEnabled(False)
        self._table.setEnabled(False)
        self._status.setText("Téléchargement et vérification…")
        self._status.setStyleSheet("color: #64748b;")
        config = load_backup_config()
        _run_in_thread(self._on_downloaded, lambda: download_for_restore(config, backup.key))

    def _on_downloaded(self, success: bool, result) -> None:
        if not success:
            self._restore_btn.setEnabled(True)
            self._table.setEnabled(True)
            self._status.setText(f"Échec, la base actuelle n'a pas été touchée : {result}")
            self._status.setStyleSheet("color: #ef4444;")
            return
        self.accept()
        restart_application()


# ---------------------------------------------------------------------------
# Fenêtre « Configurer »
# ---------------------------------------------------------------------------


class BackupConfigDialog(FramelessDialog):
    """Saisie des informations du compte cloud et de l'automatisme. Rien
    n'est enregistré avant « Enregistrer ». La Secret Key est toujours
    affichée masquée (●●●), même quand elle est déjà enregistrée."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(640)
        self.setModal(True)
        self._config = load_backup_config()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header("⚙  Configurer la sauvegarde cloud"))

        body = QWidget()
        body.setStyleSheet("background: white;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        def _lbl(text: str) -> QLabel:
            label = QLabel(text + " :")
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setMinimumHeight(36)
            return label

        provider_row = QHBoxLayout()
        self._provider_combo = QComboBox()
        self._provider_combo.setMinimumHeight(36)
        self._provider_combo.addItems(list(_PROVIDERS))
        self._provider_combo.setCurrentText(self._config.get("provider", "Backblaze B2"))
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self._provider_combo)
        guide_btn = QPushButton("Guide")
        guide_btn.setObjectName("SecondaryButton")
        guide_btn.clicked.connect(self._on_guide)
        provider_row.addWidget(guide_btn)
        provider_row.addStretch()
        grid.addWidget(_lbl("Fournisseur"), 0, 0)
        grid.addLayout(provider_row, 0, 1)

        def _line(key: str, secret: bool = False) -> QLineEdit:
            field = QLineEdit(self._config.get(key, ""))
            field.setMinimumHeight(36)
            if secret:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            return field

        self._endpoint_input = _line("endpoint_url")
        grid.addWidget(_lbl("Endpoint URL"), 1, 0)
        grid.addWidget(self._endpoint_input, 1, 1)
        self._key_input = _line("access_key_id")
        grid.addWidget(_lbl("Access Key ID"), 2, 0)
        grid.addWidget(self._key_input, 2, 1)
        self._secret_input = _line("secret_access_key", secret=True)
        grid.addWidget(_lbl("Secret Key"), 3, 0)
        grid.addWidget(self._secret_input, 3, 1)
        self._bucket_input = _line("bucket_name")
        self._bucket_input.setPlaceholderText("mobidesk-backups")
        grid.addWidget(_lbl("Nom du bucket"), 4, 0)
        grid.addWidget(self._bucket_input, 4, 1)

        schedule_row = QHBoxLayout()
        self._hour_spin = QSpinBox()
        self._hour_spin.setRange(0, 23)
        self._hour_spin.setValue(self._config.get("hour", 20))
        self._hour_spin.setSuffix("h00")
        self._hour_spin.setMinimumHeight(36)
        self._hour_spin.setFixedWidth(90)
        schedule_row.addWidget(self._hour_spin)
        schedule_row.addSpacing(16)
        schedule_row.addWidget(QLabel("Conserver :"))
        self._keep_spin = QSpinBox()
        self._keep_spin.setRange(1, 365)
        self._keep_spin.setValue(self._config.get("keep_last", 30))
        self._keep_spin.setSuffix(" dernières sauvegardes")
        self._keep_spin.setMinimumHeight(36)
        self._keep_spin.setFixedWidth(230)
        schedule_row.addWidget(self._keep_spin)
        schedule_row.addStretch()
        grid.addWidget(_lbl("Heure auto"), 5, 0)
        grid.addLayout(schedule_row, 5, 1)

        self._enabled_check = QCheckBox(
            "Activer la sauvegarde automatique (chaque jour à l'heure choisie,\n"
            "ou dès l'ouverture de l'application si l'heure est passée)"
        )
        self._enabled_check.setChecked(bool(self._config.get("enabled")))
        grid.addWidget(self._enabled_check, 6, 1)
        self._on_close_check = QCheckBox(
            "Sauvegarder aussi à la fermeture de MobiDesk et à l'arrêt du PC\n"
            "(seulement s'il y a eu des changements depuis la dernière sauvegarde)"
        )
        self._on_close_check.setChecked(bool(self._config.get("backup_on_close", True)))
        grid.addWidget(self._on_close_check, 7, 1)
        layout.addLayout(grid)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        root.addWidget(body)

        footer = QFrame()
        footer.setStyleSheet("QFrame { background: #f0f9ff; border-top: 1px solid #bae6fd; }")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 12, 20, 12)
        cancel_btn = QPushButton("✕  Annuler")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)
        footer_layout.addStretch()
        self._test_btn = QPushButton("Tester")
        self._test_btn.setObjectName("SecondaryButton")
        self._test_btn.clicked.connect(self._on_test)
        footer_layout.addWidget(self._test_btn)
        save_btn = QPushButton("💾  Enregistrer")
        save_btn.clicked.connect(self._on_save)
        footer_layout.addWidget(save_btn)
        root.addWidget(footer)

        self._on_provider_changed(self._provider_combo.currentText())

    # ------------------------------------------------------------------

    def _set_status(self, text: str, color: str, bold: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color};" + (" font-weight: 600;" if bold else ""))

    def _on_provider_changed(self, provider: str) -> None:
        info = _PROVIDERS.get(provider, {})
        self._endpoint_input.setPlaceholderText(info.get("hint_endpoint", ""))
        self._key_input.setPlaceholderText(info.get("hint_key", ""))
        self._secret_input.setPlaceholderText(info.get("hint_secret", ""))

    def _on_guide(self) -> None:
        provider = self._provider_combo.currentText()
        QMessageBox.information(self, f"Guide — {provider}", _PROVIDERS.get(provider, {}).get("guide", ""))

    def _form_config(self) -> dict:
        """Les champs du formulaire seulement (sans les résultats de
        sauvegarde, qui peuvent changer pendant que la fenêtre est ouverte)."""
        return {
            "provider": self._provider_combo.currentText(),
            "endpoint_url": self._endpoint_input.text().strip(),
            "access_key_id": self._key_input.text().strip(),
            "secret_access_key": self._secret_input.text().strip(),
            "bucket_name": self._bucket_input.text().strip(),
            "hour": self._hour_spin.value(),
            "keep_last": self._keep_spin.value(),
            "enabled": self._enabled_check.isChecked(),
            "backup_on_close": self._on_close_check.isChecked(),
        }

    def _missing_fields(self, config: dict) -> list[str]:
        return [label for key, label in _REQUIRED_FIELDS if not config[key]]

    def _on_test(self) -> None:
        config = self._form_config()
        missing = self._missing_fields(config)
        if missing:
            self._set_status("Champ(s) manquant(s) : " + ", ".join(missing) + ".", "#ef4444")
            return
        self._set_status("Test en cours…", "#64748b")
        self._test_btn.setEnabled(False)

        def _done(success: bool, message: str) -> None:
            self._test_btn.setEnabled(True)
            self._set_status(message, "#059669" if success else "#ef4444", bold=success)

        run_connection_test(_done, {**load_backup_config(), **config})

    def _on_save(self) -> None:
        config = self._form_config()
        missing = self._missing_fields(config)
        if config["enabled"] and missing:
            self._set_status(
                "Impossible d'activer la sauvegarde automatique : champ(s) manquant(s) — "
                + ", ".join(missing) + ".",
                "#ef4444",
            )
            return
        # Relire le fichier : les résultats de sauvegarde ont pu changer.
        save_backup_config({**load_backup_config(), **config})
        self.accept()


class ClosingBackupDialog(FramelessDialog):
    """Sauvegarde cloud lancée à la fermeture de MobiDesk : la fenêtre se
    ferme toute seule à la fin (ou au bout de 2 minutes) ; « Fermer sans
    attendre » quitte tout de suite."""

    TIMEOUT_MS = 120_000

    def __init__(self, config: dict, parent=None) -> None:
        from PySide6.QtCore import QTimer

        super().__init__(parent)
        self.setMinimumWidth(420)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header("☁  Sauvegarde avant fermeture", height=54))
        body = QWidget()
        body.setObjectName("DialogBody")
        body.setStyleSheet("QWidget#DialogBody { background: white; }")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)
        text = QLabel("Envoi de la base vers le cloud… MobiDesk se ferme dès que c'est terminé.")
        text.setWordWrap(True)
        layout.addWidget(text)
        row = QHBoxLayout()
        row.addStretch()
        skip = QPushButton("Fermer sans attendre")
        skip.setObjectName("SecondaryButton")
        skip.clicked.connect(self.accept)
        row.addWidget(skip)
        layout.addLayout(row)
        root.addWidget(body)

        QTimer.singleShot(self.TIMEOUT_MS, self.accept)
        run_backup(lambda _ok, _msg: self.accept(), config=config)

