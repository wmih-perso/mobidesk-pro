"""Panneau de sauvegarde automatique vers le cloud (Backblaze B2 / S3).

Les clés du stockage ne s'affichent jamais : elles sont importées une fois
par PC (compte admin) depuis un fichier JSON gardé hors de l'application,
puis conservées dans la configuration locale (voir app/backup.py). L'écran
ne propose que l'activation, l'heure, le test et la sauvegarde immédiate.
"""

from __future__ import annotations

import datetime
import json

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app import session as _session
from app.backup import (
    CREDENTIALS_TEMPLATE,
    import_credentials,
    is_configured,
    load_backup_config,
    save_backup_config,
    test_connection,
    upload_backup,
)

_TEMPLATE_GUIDE = (
    "Remplissez le fichier avec les informations de votre compte Backblaze B2 :\n\n"
    "• endpoint_url : dans « Buckets », champ « Endpoint » du bucket,\n"
    "  précédé de https://  (ex : https://s3.eu-central-003.backblazeb2.com)\n"
    "• access_key_id : le « keyID » de votre clé (App Keys)\n"
    "• secret_access_key : l'« applicationKey » (affichée une seule fois\n"
    "  à la création de la clé)\n"
    "• bucket_name : le nom du bucket\n\n"
    "Puis cliquez sur « Importer les clés… ». Gardez ce fichier en lieu sûr\n"
    "(clé USB) : il sert à configurer les autres PC."
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


def _run_test(on_done, config: dict) -> None:
    """test_connection dans un thread, résultat sur le thread de l'interface."""
    import threading

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


class BackupPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self._load()
        self._hour_spin.valueChanged.connect(self._mark_unsaved)
        self._keep_spin.valueChanged.connect(self._mark_unsaved)
        self._enabled_check.toggled.connect(self._mark_unsaved)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._account_label = QLabel("")
        self._account_label.setWordWrap(True)
        layout.addWidget(self._account_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnMinimumWidth(0, 140)

        def _lbl(text):
            label = QLabel(text + " :")
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setMinimumHeight(36)
            return label

        self._hour_spin = QSpinBox()
        self._hour_spin.setRange(0, 23)
        self._hour_spin.setSuffix("h00")
        self._hour_spin.setMinimumHeight(36)
        self._hour_spin.setFixedWidth(90)
        grid.addWidget(_lbl("Heure auto"), 0, 0)
        grid.addWidget(self._hour_spin, 0, 1)

        self._keep_spin = QSpinBox()
        self._keep_spin.setRange(1, 365)
        self._keep_spin.setSuffix(" dernières sauvegardes")
        self._keep_spin.setMinimumHeight(36)
        self._keep_spin.setFixedWidth(230)
        grid.addWidget(_lbl("Conserver les"), 1, 0)
        grid.addWidget(self._keep_spin, 1, 1)

        self._enabled_check = QCheckBox(
            "Activer la sauvegarde automatique (chaque jour à l'heure choisie, "
            "ou dès l'ouverture de l'application si l'heure est passée)"
        )
        grid.addWidget(self._enabled_check, 2, 1)
        grid_row = QHBoxLayout()
        grid_row.addLayout(grid)
        grid_row.addStretch()
        layout.addLayout(grid_row)

        buttons_row = QHBoxLayout()
        self._save_btn = QPushButton("💾  Enregistrer")
        self._save_btn.clicked.connect(self._on_save)
        buttons_row.addWidget(self._save_btn)

        self._test_btn = QPushButton("Tester la connexion")
        self._test_btn.setObjectName("SecondaryButton")
        self._test_btn.clicked.connect(self._on_test_connection)
        buttons_row.addWidget(self._test_btn)

        self._backup_now_btn = QPushButton("Sauvegarder maintenant")
        self._backup_now_btn.setObjectName("SecondaryButton")
        self._backup_now_btn.clicked.connect(self._on_backup_now)
        buttons_row.addWidget(self._backup_now_btn)
        buttons_row.addStretch()

        # Gestion des clés : compte admin uniquement.
        self._import_btn = QPushButton("🔑  Importer les clés…")
        self._import_btn.setObjectName("SecondaryButton")
        self._import_btn.clicked.connect(self._on_import)
        buttons_row.addWidget(self._import_btn)
        self._template_btn = QPushButton("📄  Créer un modèle de fichier…")
        self._template_btn.setObjectName("SecondaryButton")
        self._template_btn.clicked.connect(self._on_create_template)
        buttons_row.addWidget(self._template_btn)
        is_admin = _session.is_admin()
        self._import_btn.setVisible(is_admin)
        self._template_btn.setVisible(is_admin)
        layout.addLayout(buttons_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._last_backup_label = QLabel("")
        self._last_backup_label.setWordWrap(True)
        layout.addWidget(self._last_backup_label)

    def _load(self) -> None:
        """(Re)charge l'écran depuis la configuration enregistrée."""
        config = load_backup_config()
        widgets = (self._hour_spin, self._keep_spin, self._enabled_check)
        for widget in widgets:
            widget.blockSignals(True)
        self._hour_spin.setValue(config.get("hour", 20))
        self._keep_spin.setValue(config.get("keep_last", 30))
        self._enabled_check.setChecked(bool(config.get("enabled")))
        for widget in widgets:
            widget.blockSignals(False)

        configured = is_configured()
        if configured:
            self._account_label.setText(
                "Compte cloud : <b style='color:#059669;'>configuré ✓</b>"
                f"  —  bucket « {config.get('bucket_name')} »"
            )
        else:
            self._account_label.setText(
                "Compte cloud : <b style='color:#ef4444;'>non configuré</b>  —  "
                + ("importez le fichier de clés avec « Importer les clés… »."
                   if _session.is_admin() else
                   "demandez à l'administrateur d'importer les clés sur ce PC.")
            )
        self._test_btn.setEnabled(configured)
        self._backup_now_btn.setEnabled(configured)
        self._refresh_last_backup()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _set_status(self, text: str, color: str, bold: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color};" + (" font-weight: 600;" if bold else ""))

    def _mark_unsaved(self, *_args) -> None:
        self._set_status("⚠  Modifications non enregistrées — cliquez sur « Enregistrer ».", "#b45309")

    def _refresh_last_backup(self) -> None:
        text, color = format_last_backup(load_backup_config())
        self._last_backup_label.setText(text)
        self._last_backup_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _on_save(self) -> None:
        enabled = self._enabled_check.isChecked()
        if enabled and not is_configured():
            self._set_status(
                "Impossible d'activer la sauvegarde : les clés du compte cloud ne sont pas importées.",
                "#ef4444",
            )
            return
        config = {
            **load_backup_config(),
            "hour": self._hour_spin.value(),
            "keep_last": self._keep_spin.value(),
            "enabled": enabled,
        }
        save_backup_config(config)
        if enabled:
            self._set_status(
                f"✓  Enregistré. Sauvegarde automatique activée chaque jour à {config['hour']}h00.",
                "#059669", bold=True,
            )
        else:
            self._set_status("✓  Enregistré. Sauvegarde automatique désactivée.", "#059669", bold=True)

    def _on_test_connection(self) -> None:
        self._set_status("Test en cours…", "#64748b")
        self._test_btn.setEnabled(False)

        def _done(success: bool, message: str) -> None:
            self._test_btn.setEnabled(True)
            self._set_status(message, "#059669" if success else "#ef4444", bold=success)

        _run_test(_done, load_backup_config())

    def _on_backup_now(self) -> None:
        self._set_status("Sauvegarde en cours…", "#64748b")
        self._backup_now_btn.setEnabled(False)

        def _done(success: bool, message: str) -> None:
            self._set_status(message, "#059669" if success else "#ef4444", bold=success)
            self._backup_now_btn.setEnabled(True)
            self._refresh_last_backup()

        run_backup(_done, config={**load_backup_config(), "keep_last": self._keep_spin.value()})

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer les clés du compte cloud", "", "Fichier de clés (*.json)"
        )
        if not path:
            return
        try:
            import_credentials(path)
        except ValueError as error:
            self._set_status(str(error), "#ef4444")
            return
        self._load()
        self._on_test_connection()

    def _on_create_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Créer un modèle de fichier de clés", "mobidesk_cles_cloud.json",
            "Fichier de clés (*.json)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(CREDENTIALS_TEMPLATE, file, ensure_ascii=False, indent=2)
        except OSError as error:
            self._set_status(f"Impossible de créer le fichier : {error}", "#ef4444")
            return
        QMessageBox.information(self, "Modèle créé", f"Fichier créé :\n{path}\n\n{_TEMPLATE_GUIDE}")
