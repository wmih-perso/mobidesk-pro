"""Point d'entrée — MobiDesk Pro, gestion du stock des afficheurs."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app.backup import RESTART_FLAG, apply_pending_restore
from app.database import init_engine
from app.single_instance import notify_running_instance, start_listening
from app.ui.login_dialog import LoginDialog
from app.ui.main_window import MainWindow
from app.ui.widgets import install_wheel_guard

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
    app = QApplication(sys.argv)

    # Une seule instance : si l'app est déjà ouverte, on la remet au premier plan.
    # Après « Récupérer depuis le cloud », l'ancienne instance est en train
    # de se fermer : on lui laisse quelques secondes.
    if RESTART_FLAG in sys.argv:
        for _ in range(20):
            if not notify_running_instance():
                break
            time.sleep(0.5)
    if notify_running_instance():
        return 0
    instance_server = start_listening(app)  # noqa: F841 — à garder en vie

    restored_from = None
    try:
        restored_from = apply_pending_restore()
    except Exception as exc:  # la base actuelle reste en place
        QMessageBox.critical(None, "Récupération", f"La base récupérée n'a pas pu être installée :\n{exc}")
    init_engine()
    if restored_from is not None:
        QMessageBox.information(
            None, "Récupération",
            "La base récupérée depuis le cloud est installée.\n\n"
            f"L'ancienne base a été gardée ici :\n{restored_from}",
        )
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    # Le style natif Windows ("windowsvista") ignore une grande partie du QSS
    # (bordures arrondies, couleurs de survol, indicateurs de case à cocher...).
    # Fusion applique fidèlement toute la feuille de style sur toutes les
    # plateformes.
    app.setStyle("Fusion")
    install_wheel_guard(app)  # la molette ne change plus les prix / quantités
    app.setStyleSheet(_load_stylesheet())

    login = LoginDialog()
    if login.exec() != LoginDialog.DialogCode.Accepted:
        return 0

    window = MainWindow()
    window.showMaximized()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
