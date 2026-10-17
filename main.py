"""Point d'entrée — MobiDesk Pro, gestion du stock des afficheurs."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.database import init_engine
from app.ui.login_dialog import LoginDialog
from app.ui.main_window import MainWindow

UI_DIR = Path(__file__).resolve().parent / "app" / "ui"
STYLE_PATH = UI_DIR / "style.qss"
ICONS_DIR = UI_DIR / "resources" / "icons"
APP_ICON_PATH = UI_DIR / "resources" / "app_icon.png"


def _load_stylesheet() -> str:
    # QSS n'accepte que des chemins avec des slashs (même sous Windows) —
    # le fichier .qss contient un jeton ICONS_DIR remplacé ici par le chemin
    # absolu réel du dossier d'icônes.
    text = STYLE_PATH.read_text(encoding="utf-8")
    return text.replace("ICONS_DIR", ICONS_DIR.as_posix())


def main() -> int:
    init_engine()

    app = QApplication(sys.argv)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    # Le style natif Windows ("windowsvista") ignore une grande partie du QSS
    # (bordures arrondies, couleurs de survol, indicateurs de case à cocher...).
    # Fusion applique fidèlement toute la feuille de style sur toutes les
    # plateformes.
    app.setStyle("Fusion")
    app.setStyleSheet(_load_stylesheet())

    login = LoginDialog()
    if login.exec() != LoginDialog.DialogCode.Accepted:
        return 0

    window = MainWindow()
    window.showMaximized()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
