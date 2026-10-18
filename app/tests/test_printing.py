import os

import pytest

from app import printer_config


@pytest.fixture()
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "printer_config.json"
    monkeypatch.setattr(printer_config, "_CONFIG_FILE", path)
    return path


def test_printer_config_defaults_and_roundtrip(config_file):
    assert printer_config.load_printer_config() == printer_config.DEFAULTS

    printer_config.save_printer_config({**printer_config.DEFAULTS, "label_height_mm": 80, "copies": 2})

    cfg = printer_config.load_printer_config()
    assert (cfg["label_width_mm"], cfg["label_height_mm"], cfg["copies"]) == (50, 80, 2)


@pytest.fixture()
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.path.isdir(r"C:\Windows\Fonts"):  # évite un long balayage des polices
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width, height", [(50, 30), (50, 80)])
def test_ticket_prints_on_one_label(qt_app, config_file, tmp_path, width, height):
    # QPdfWriter plutôt que QPrinter : même rendu, sans interroger les
    # imprimantes Windows (qui peut prendre plusieurs secondes).
    from PySide6.QtGui import QPdfWriter
    from PySide6.QtPdf import QPdfDocument

    from app.printing import _render_ticket, apply_label_layout, test_ticket_data

    pdf = tmp_path / "ticket.pdf"
    writer = QPdfWriter(str(pdf))
    writer.setResolution(203)  # résolution de la Niimbot B1
    apply_label_layout(writer, {**printer_config.DEFAULTS, "label_width_mm": width, "label_height_mm": height})

    assert _render_ticket(writer, test_ticket_data())
    del writer  # termine le fichier PDF

    document = QPdfDocument()
    document.load(str(pdf))
    assert document.pageCount() == 1  # tout le ticket sur une seule étiquette
    size_mm = document.pagePointSize(0) * 25.4 / 72
    assert (round(size_mm.width()), round(size_mm.height())) == (width, height)
