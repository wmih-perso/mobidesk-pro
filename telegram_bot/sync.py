"""Récupère la dernière sauvegarde MobiDesk sur Backblaze (clé en LECTURE
SEULE) et l'ouvre pour le bot. La copie locale n'est jamais renvoyée au
cloud : le bot ne peut rien modifier au magasin."""

from __future__ import annotations

import datetime
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app import database
from app.backup import check_database_file, list_cloud_backups, _make_client

log = logging.getLogger("mobidesk.sync")


@dataclass
class Snapshot:
    name: str
    uploaded_at: datetime.datetime  # heure locale du dépôt (voir list_cloud_backups)


class BackupSync:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._db_path = data_dir / "current.db"
        self._state_path = data_dir / "sync_state.json"
        self._config = {
            "endpoint_url": os.environ["B2_ENDPOINT"],
            "access_key_id": os.environ["B2_KEY_ID"],
            "secret_access_key": os.environ["B2_APP_KEY"],
            "bucket_name": os.environ["B2_BUCKET"],
            "prefix": os.environ.get("B2_PREFIX", "mobidesk-backups/"),
        }
        self.snapshot: Snapshot | None = None
        if self._db_path.exists() and self._state_path.exists():
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.snapshot = Snapshot(state["name"], datetime.datetime.fromisoformat(state["uploaded_at"]))
            self._open()

    @property
    def ready(self) -> bool:
        return self.snapshot is not None

    def check(self) -> bool:
        """Télécharge la sauvegarde la plus récente si elle a changé.
        True si une nouvelle base vient d'être ouverte."""
        backups = list_cloud_backups(self._config)
        if not backups:
            return False
        latest = backups[0]
        if (self.snapshot and latest.name == self.snapshot.name
                and latest.uploaded_at == self.snapshot.uploaded_at):
            return False
        tmp = self._data_dir / "download.tmp"
        try:
            _make_client(self._config).download_file(self._config["bucket_name"], latest.key, str(tmp))
            check_database_file(tmp)  # fichier abîmé → on garde la base actuelle
            if database._engine is not None:
                database._engine.dispose()
            os.replace(tmp, self._db_path)
        finally:
            tmp.unlink(missing_ok=True)
        self.snapshot = Snapshot(latest.name, latest.uploaded_at)
        self._state_path.write_text(json.dumps(
            {"name": latest.name, "uploaded_at": latest.uploaded_at.isoformat()}), encoding="utf-8")
        self._open()
        log.info("Nouvelle sauvegarde : %s (%s)", latest.name, latest.uploaded_at)
        return True

    def _open(self) -> None:
        database.init_engine(self._db_path)
