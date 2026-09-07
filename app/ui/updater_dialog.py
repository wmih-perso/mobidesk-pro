"""Boîte de dialogue de téléchargement d'une mise à jour."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout

from app.updater import UpdateInfo, download_update


class _DownloadThread(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(Path)
    failed = Signal(str)

    def __init__(self, info: UpdateInfo) -> None:
        super().__init__()
        self._info = info

    def run(self) -> None:
        try:
            path = download_update(self._info, progress_callback=self.progress.emit)
        except OSError as error:
            self.failed.emit(f"Échec du téléchargement : {error}")
            return
        self.finished_ok.emit(path)


class UpdaterDialog(QDialog):
    """Télécharge la mise à jour avec une barre de progression.

    À la fermeture réussie, `downloaded_path` contient le chemin du
    nouvel exécutable téléchargé.
    """

    def __init__(self, info: UpdateInfo) -> None:
        super().__init__()
        self.setWindowTitle("Téléchargement de la mise à jour")
        self.setMinimumWidth(380)
        self.setModal(True)
        self.downloaded_path: Path | None = None

        layout = QVBoxLayout(self)

        label = QLabel(f"Téléchargement de la version {info.version}...")
        layout.addWidget(label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self._thread = _DownloadThread(info)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_ok.connect(self._on_finished)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_progress(self, read: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setValue(int(read * 100 / total))

    def _on_finished(self, path: Path) -> None:
        self.downloaded_path = path
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.downloaded_path = None
        self._error_message = message
        self.reject()
