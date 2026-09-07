"""Panneau de configuration des sauvegardes automatiques (S3 / Backblaze B2 / Cloudflare R2)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.backup import is_configured, load_backup_config, save_backup_config, test_connection, upload_backup

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


class BackupPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._config = load_backup_config()
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Sélecteur fournisseur
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Fournisseur :"))
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(list(_PROVIDERS.keys()))
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self._provider_combo)

        guide_btn = QPushButton("Guide")
        guide_btn.setObjectName("SecondaryButton")
        guide_btn.clicked.connect(self._on_guide)
        provider_row.addWidget(guide_btn)
        provider_row.addStretch()
        layout.addLayout(provider_row)

        # Formulaire de configuration
        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._endpoint_input = QLineEdit(self._config.get("endpoint_url", ""))
        self._endpoint_input.setPlaceholderText("https://...")
        self._endpoint_input.textChanged.connect(self._save_config)
        form.addRow("Endpoint URL :", self._endpoint_input)

        self._key_input = QLineEdit(self._config.get("access_key_id", ""))
        self._key_input.textChanged.connect(self._save_config)
        form.addRow("Access Key ID :", self._key_input)

        self._secret_input = QLineEdit(self._config.get("secret_access_key", ""))
        self._secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret_input.textChanged.connect(self._save_config)
        form.addRow("Secret Key :", self._secret_input)

        self._bucket_input = QLineEdit(self._config.get("bucket_name", ""))
        self._bucket_input.setPlaceholderText("mobidesk-backups")
        self._bucket_input.textChanged.connect(self._save_config)
        form.addRow("Nom du bucket :", self._bucket_input)

        layout.addLayout(form)

        # Paramètres
        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("Conserver les"))
        self._keep_spin = QSpinBox()
        self._keep_spin.setRange(1, 365)
        self._keep_spin.setValue(self._config.get("keep_last", 30))
        self._keep_spin.setSuffix(" dernières sauvegardes")
        self._keep_spin.setFixedWidth(220)
        self._keep_spin.valueChanged.connect(self._save_config)
        params_row.addWidget(self._keep_spin)
        params_row.addStretch()
        layout.addLayout(params_row)

        hour_row = QHBoxLayout()
        hour_row.addWidget(QLabel("Heure de sauvegarde automatique :"))
        self._hour_spin = QSpinBox()
        self._hour_spin.setRange(0, 23)
        self._hour_spin.setValue(self._config.get("hour", 20))
        self._hour_spin.setSuffix("h00")
        self._hour_spin.setFixedWidth(90)
        self._hour_spin.valueChanged.connect(self._save_config)
        hour_row.addWidget(self._hour_spin)
        hour_row.addStretch()
        layout.addLayout(hour_row)

        # Test + statut
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Tester la connexion")
        self._test_btn.setObjectName("SecondaryButton")
        self._test_btn.clicked.connect(self._on_test_connection)
        test_row.addWidget(self._test_btn)

        self._backup_now_btn = QPushButton("Sauvegarder maintenant")
        self._backup_now_btn.clicked.connect(self._on_backup_now)
        test_row.addWidget(self._backup_now_btn)
        test_row.addStretch()
        layout.addLayout(test_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

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

    def _on_test_connection(self) -> None:
        self._status_label.setText("Test en cours…")
        self._status_label.setStyleSheet("color: #64748b;")
        config = self._current_config()
        import threading
        def _run():
            success, msg = test_connection(config)
            self._status_label.setText(msg)
            self._status_label.setStyleSheet(
                "color: #059669; font-weight:600;" if success else "color: #ef4444;"
            )
            if success:
                config["enabled"] = True
                save_backup_config(config)
        threading.Thread(target=_run, daemon=True).start()

    def _on_backup_now(self) -> None:
        self._status_label.setText("Sauvegarde en cours…")
        self._status_label.setStyleSheet("color: #64748b;")
        self._backup_now_btn.setEnabled(False)

        def _done(success: bool, message: str) -> None:
            self._status_label.setText(message)
            self._status_label.setStyleSheet(
                "color: #059669; font-weight:600;" if success else "color: #ef4444;"
            )
            self._backup_now_btn.setEnabled(True)

        upload_backup(_done, config=self._current_config())

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
        }

    def _save_config(self) -> None:
        self._config = self._current_config()
        save_backup_config(self._config)
