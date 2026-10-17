"""Panneau de configuration des sauvegardes automatiques (S3 / Backblaze B2 / Cloudflare R2)."""

from __future__ import annotations

import datetime

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.backup import load_backup_config, save_backup_config, test_connection, upload_backup

# Endpoints pré-remplis selon le fournisseur
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
            "6. Entrez ces valeurs dans les champs ci-dessous et cliquez «Tester»"
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
            "6. Entrez ces valeurs dans les champs ci-dessous et cliquez «Tester»"
        ),
    },
}


class BackupResult(QObject):
    """Ramène le résultat d'une sauvegarde (calculé dans un thread) sur le
    thread de l'interface : Qt interdit de toucher aux widgets, ou
    d'ouvrir une boîte de dialogue, depuis un autre thread."""

    finished = Signal(bool, str)


# Garde une référence aux ponts en cours, jusqu'à leur signal.
_pending: set[BackupResult] = set()


def run_backup(on_done, config: dict | None = None) -> None:
    """upload_backup, avec `on_done(succès, message)` appelé sur le thread
    de l'interface."""
    bridge = BackupResult()
    _pending.add(bridge)

    def _finish(success: bool, message: str) -> None:
        _pending.discard(bridge)
        bridge.deleteLater()
        on_done(success, message)

    bridge.finished.connect(_finish)
    upload_backup(bridge.finished.emit, config=config)


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
    """Configuration de la sauvegarde cloud. Rien n'est enregistré tant
    qu'on ne clique pas sur « Enregistrer » : un champ effacé par erreur
    ne fait pas perdre les clés."""

    def __init__(self) -> None:
        super().__init__()
        self._config = load_backup_config()
        self._build_ui()
        self._refresh_last_backup()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Sélecteur fournisseur
        provider_row = QHBoxLayout()
        provider_row.setSpacing(8)
        prov_lbl = QLabel("Fournisseur :")
        prov_lbl.setFixedWidth(140)
        prov_lbl.setFixedHeight(38)
        prov_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        provider_row.addWidget(prov_lbl)
        self._provider_combo = QComboBox()
        self._provider_combo.setFixedHeight(38)
        self._provider_combo.addItems(list(_PROVIDERS.keys()))
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self._provider_combo)
        guide_btn = QPushButton("Guide")
        guide_btn.setFixedHeight(38)
        guide_btn.setObjectName("SecondaryButton")
        guide_btn.clicked.connect(self._on_guide)
        provider_row.addWidget(guide_btn)
        provider_row.addStretch()
        layout.addLayout(provider_row)

        # Formulaire — QGridLayout avec espacement vertical garanti
        _inp_style = (
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 6px;"
            " padding: 7px 10px; font-size: 13px; background: #ffffff; }"
            "QLineEdit:focus { border: 2px solid #2563eb; }"
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnMinimumWidth(0, 140)
        grid.setColumnStretch(1, 1)

        def _lbl(text):
            l = QLabel(text + " :")
            l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            l.setMinimumHeight(36)
            return l

        def _line(key: str, placeholder: str = "", secret: bool = False) -> QLineEdit:
            field = QLineEdit(self._config.get(key, ""))
            field.setPlaceholderText(placeholder)
            field.setMinimumHeight(36)
            field.setStyleSheet(_inp_style)
            if secret:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            field.textChanged.connect(self._mark_unsaved)
            return field

        self._endpoint_input = _line("endpoint_url", "https://...")
        grid.addWidget(_lbl("Endpoint URL"), 0, 0)
        grid.addWidget(self._endpoint_input, 0, 1)

        self._key_input = _line("access_key_id")
        grid.addWidget(_lbl("Access Key ID"), 1, 0)
        grid.addWidget(self._key_input, 1, 1)

        self._secret_input = _line("secret_access_key", secret=True)
        grid.addWidget(_lbl("Secret Key"), 2, 0)
        grid.addWidget(self._secret_input, 2, 1)

        self._bucket_input = _line("bucket_name", "mobidesk-backups")
        grid.addWidget(_lbl("Nom du bucket"), 3, 0)
        grid.addWidget(self._bucket_input, 3, 1)

        self._keep_spin = QSpinBox()
        self._keep_spin.setRange(1, 365)
        self._keep_spin.setValue(self._config.get("keep_last", 30))
        self._keep_spin.setSuffix(" dernières sauvegardes")
        self._keep_spin.setMinimumHeight(36)
        self._keep_spin.setFixedWidth(230)
        self._keep_spin.valueChanged.connect(self._mark_unsaved)
        grid.addWidget(_lbl("Conserver les"), 4, 0)
        grid.addWidget(self._keep_spin, 4, 1)

        self._hour_spin = QSpinBox()
        self._hour_spin.setRange(0, 23)
        self._hour_spin.setValue(self._config.get("hour", 20))
        self._hour_spin.setSuffix("h00")
        self._hour_spin.setMinimumHeight(36)
        self._hour_spin.setFixedWidth(90)
        self._hour_spin.valueChanged.connect(self._mark_unsaved)
        grid.addWidget(_lbl("Heure auto"), 5, 0)
        grid.addWidget(self._hour_spin, 5, 1)

        self._enabled_check = QCheckBox(
            "Activer la sauvegarde automatique (chaque jour à l'heure choisie, "
            "ou dès l'ouverture de l'application si l'heure est passée)"
        )
        self._enabled_check.setChecked(bool(self._config.get("enabled")))
        self._enabled_check.toggled.connect(self._mark_unsaved)
        grid.addWidget(self._enabled_check, 6, 1)

        layout.addLayout(grid)

        # Boutons + statut
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
        layout.addLayout(buttons_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._last_backup_label = QLabel("")
        self._last_backup_label.setWordWrap(True)
        layout.addWidget(self._last_backup_label)

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

    def _on_provider_changed(self, provider: str) -> None:
        info = _PROVIDERS.get(provider, {})
        self._endpoint_input.setPlaceholderText(info.get("hint_endpoint", ""))
        self._key_input.setPlaceholderText(info.get("hint_key", ""))
        self._secret_input.setPlaceholderText(info.get("hint_secret", ""))

    def _on_guide(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        provider = self._provider_combo.currentText()
        guide = _PROVIDERS.get(provider, {}).get("guide", "")
        QMessageBox.information(self, f"Guide — {provider}", guide)

    def _on_save(self) -> None:
        config = self._current_config()
        missing = [
            label for key, label in (
                ("endpoint_url", "Endpoint URL"), ("access_key_id", "Access Key ID"),
                ("secret_access_key", "Secret Key"), ("bucket_name", "Nom du bucket"),
            ) if not config[key]
        ]
        if config["enabled"] and missing:
            self._set_status(
                "Impossible d'activer la sauvegarde : champ(s) manquant(s) — " + ", ".join(missing) + ".",
                "#ef4444",
            )
            return
        # Les résultats de sauvegarde peuvent avoir été mis à jour entre-temps.
        self._config = {**load_backup_config(), **config}
        save_backup_config(self._config)
        if config["enabled"]:
            self._set_status(
                f"✓  Enregistré. Sauvegarde automatique activée chaque jour à {config['hour']}h00.",
                "#059669", bold=True,
            )
        else:
            self._set_status("✓  Enregistré. Sauvegarde automatique désactivée.", "#059669", bold=True)

    def _on_test_connection(self) -> None:
        self._set_status("Test en cours…", "#64748b")
        self._test_btn.setEnabled(False)
        config = self._current_config()

        import threading

        bridge = BackupResult()
        _pending.add(bridge)

        def _finish(success: bool, msg: str) -> None:
            _pending.discard(bridge)
            bridge.deleteLater()
            self._test_btn.setEnabled(True)
            self._set_status(msg, "#059669" if success else "#ef4444", bold=success)

        bridge.finished.connect(_finish)
        threading.Thread(
            target=lambda: bridge.finished.emit(*test_connection(config)), daemon=True
        ).start()

    def _on_backup_now(self) -> None:
        self._set_status("Sauvegarde en cours…", "#64748b")
        self._backup_now_btn.setEnabled(False)

        def _done(success: bool, message: str) -> None:
            self._set_status(message, "#059669" if success else "#ef4444", bold=success)
            self._backup_now_btn.setEnabled(True)
            self._refresh_last_backup()

        run_backup(_done, config=self._current_config())

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _current_config(self) -> dict:
        return {
            **self._config,
            "endpoint_url": self._endpoint_input.text().strip(),
            "access_key_id": self._key_input.text().strip(),
            "secret_access_key": self._secret_input.text().strip(),
            "bucket_name": self._bucket_input.text().strip(),
            "keep_last": self._keep_spin.value(),
            "hour": self._hour_spin.value(),
            "enabled": self._enabled_check.isChecked(),
        }
