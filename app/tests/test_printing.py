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


def _print_to_pdf(tmp_path, cfg, data=None):
    """Imprime dans un PDF (QPdfWriter : même rendu qu'un QPrinter, sans
    interroger les imprimantes Windows). Renvoie (nb de pages, taille mm)."""
    from PySide6.QtGui import QPdfWriter
    from PySide6.QtPdf import QPdfDocument

    from app.printing import _render_ticket, _ticket_height, apply_page_layout, test_ticket_data

    data = data or test_ticket_data()
    pdf = tmp_path / "ticket.pdf"
    writer = QPdfWriter(str(pdf))
    writer.setResolution(203)
    apply_page_layout(writer, cfg, _ticket_height(data))
    assert _render_ticket(writer, data, cfg)
    del writer  # termine le fichier PDF

    document = QPdfDocument()
    document.load(str(pdf))
    size_mm = document.pagePointSize(0) * 25.4 / 72
    return document.pageCount(), (round(size_mm.width()), round(size_mm.height()))


@pytest.mark.parametrize("width, height", [(50, 30), (50, 80)])
def test_ticket_prints_on_one_label(qt_app, config_file, tmp_path, width, height):
    cfg = {**printer_config.DEFAULTS, "label_width_mm": width, "label_height_mm": height}
    assert _print_to_pdf(tmp_path, cfg) == (1, (width, height))


def test_ticket_on_a4_sheet(qt_app, config_file, tmp_path):
    cfg = {**printer_config.DEFAULTS, "paper": printer_config.PAPER_SHEET, "ticket_width_mm": 80}
    assert _print_to_pdf(tmp_path, cfg) == (1, (210, 297))


def test_roll_page_is_as_long_as_the_ticket(qt_app, config_file, tmp_path):
    from app.printing import test_ticket_data

    cfg = {**printer_config.DEFAULTS, "paper": printer_config.PAPER_ROLL, "ticket_width_mm": 80}
    short = test_ticket_data()
    long = {**short, "lines": short["lines"] * 10, "nb_articles": 20}

    pages, (width, short_height) = _print_to_pdf(tmp_path, cfg, short)
    _, (_, long_height) = _print_to_pdf(tmp_path, cfg, long)

    assert pages == 1 and width == 80
    assert long_height > short_height + 50


def test_missing_printer_is_reported_instead_of_printing_elsewhere(qt_app, config_file, monkeypatch):
    from app import printing

    warnings = []
    monkeypatch.setattr(printing.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(printing, "make_printer", lambda *a: pytest.fail("ne doit pas imprimer"))
    cfg = {**printer_config.DEFAULTS, "printer_name": "Imprimante qui n'existe pas"}

    assert printing.print_ticket_data(printing.test_ticket_data(), cfg=cfg) is False
    assert "introuvable" in warnings[0]


@pytest.mark.parametrize("printed, closed", [(True, True), (False, False)])
def test_preview_closes_after_printing(qt_app, config_file, monkeypatch, printed, closed):
    from app import printing
    from app.ui.ticket_dialog import TicketPreviewDialog

    calls = []
    monkeypatch.setattr(printing, "print_ticket_data", lambda data, parent: calls.append(1) or printed)
    dialog = TicketPreviewDialog.__new__(TicketPreviewDialog)
    TicketPreviewDialog.__init__(dialog, batch_id=None, movement_id=None)
    dialog._data = printing.test_ticket_data()
    dialog._print_btn.setEnabled(True)

    dialog._on_print()

    assert calls == [1]
    assert (dialog.result() == TicketPreviewDialog.DialogCode.Accepted) is closed
    assert dialog._print_btn.isEnabled() is not closed


def test_roll_width_and_offset_settings(qt_app, config_file):
    from app.printing import _roll_dots, roll_print_width_mm

    cfg = {**printer_config.DEFAULTS, "paper": printer_config.PAPER_ROLL, "ticket_width_mm": 80}
    assert roll_print_width_mm(cfg) == 72 and _roll_dots(cfg) == 576
    assert roll_print_width_mm({**cfg, "ticket_width_mm": 58}) == 48
    assert roll_print_width_mm({**cfg, "roll_print_width_mm": 66}) == 66
    assert printer_config.paper_description({**cfg, "roll_offset_mm": -10}) == \
        "Rouleau de ticket 80 mm (décalage -10 mm)"


def test_thermal_ticket_is_pure_black_and_white(qt_app, config_file):
    from PySide6.QtGui import QImage

    from app.printing import test_ticket_data, thermal_ticket_image

    image = thermal_ticket_image(test_ticket_data(), 576)
    assert image.format() == QImage.Format.Format_Mono and image.width() == 576
