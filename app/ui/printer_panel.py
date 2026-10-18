"""Paramètres > Imprimante : résumé, « Configurer l'imprimante » (admin) et
« Imprimer un test ». Les réglages sont dans app/printer_config.py."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtPrintSupport import QPrinterInfo
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app import session as _session
from app.printer_config import LABEL_PRESETS, load_printer_config, save_printer_config
from app.ui.frameless_dialog import FramelessDialog



def _printer_label(cfg: dict) -> str:
    return cfg.get("printer_name") or f"par défaut de Windows ({QPrinterInfo.defaultPrinterName() or 'aucune'})"


class PrinterPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        buttons = QHBoxLayout()
        self._configure_btn = QPushButton("⚙  Configurer l'imprimante")
        self._configure_btn.clicked.connect(self._on_configure)
        self._configure_btn.setVisible(_session.is_admin())
        buttons.addWidget(self._configure_btn)
        test_btn = QPushButton("🧾  Imprimer un test")
        test_btn.setObjectName("SecondaryButton")
        test_btn.clicked.connect(self._on_test)
        buttons.addWidget(test_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        self._refresh()

    def _refresh(self) -> None:
        cfg = load_printer_config()
        name = cfg.get("printer_name")
        missing = bool(name) and name not in QPrinterInfo.availablePrinterNames()
        printer = (
            f"<b style='color:#ef4444;'>{name} (introuvable — branchée et allumée ?)</b>"
            if missing else f"<b>{_printer_label(cfg)}</b>"
        )
        self._summary_label.setText(
            f"Imprimante : {printer}  ·  Étiquette : <b>{cfg['label_width_mm']} × {cfg['label_height_mm']} mm</b>"
            f"  ·  Exemplaires : <b>{cfg['copies']}</b>"
            f"  ·  Impression directe : <b>{'oui' if cfg.get('direct') else 'non'}</b>"
        )

    def _set_status(self, text: str, color: str) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _on_configure(self) -> None:
        dialog = PrinterConfigDialog(parent=self)
        if dialog.exec() == PrinterConfigDialog.DialogCode.Accepted:
            self._refresh()
            self._set_status("✓  Réglages de l'imprimante enregistrés.", "#059669")

    def _on_test(self) -> None:
        from app.printing import print_test_ticket

        if print_test_ticket(self):
            self._set_status("✓  Ticket de test envoyé à l'imprimante.", "#059669")


class PrinterConfigDialog(FramelessDialog):
    """Choix de l'imprimante, de la taille d'étiquette, du nombre
    d'exemplaires et de l'impression directe. Rien n'est enregistré avant
    « Enregistrer »."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(560)
        self.setModal(True)
        cfg = load_printer_config()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header("🖨  Configurer l'imprimante"))

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

        self._printer_combo = QComboBox()
        self._printer_combo.setMinimumHeight(36)
        self._printer_combo.addItem(
            f"Imprimante par défaut de Windows ({QPrinterInfo.defaultPrinterName() or 'aucune'})", ""
        )
        for name in QPrinterInfo.availablePrinterNames():
            self._printer_combo.addItem(name, name)
        current = cfg.get("printer_name") or ""
        if current and self._printer_combo.findData(current) < 0:
            self._printer_combo.addItem(f"{current} (introuvable)", current)
        self._printer_combo.setCurrentIndex(max(self._printer_combo.findData(current), 0))
        grid.addWidget(_lbl("Imprimante"), 0, 0)
        grid.addWidget(self._printer_combo, 0, 1)

        size_row = QHBoxLayout()
        self._size_combo = QComboBox()
        self._size_combo.setMinimumHeight(36)
        for width, height in LABEL_PRESETS:
            self._size_combo.addItem(f"{width} × {height} mm")
        self._size_combo.addItem("Personnalisée…")
        size_row.addWidget(self._size_combo)
        self._width_spin = self._mm_spin(cfg["label_width_mm"], 10, 120)
        self._height_spin = self._mm_spin(cfg["label_height_mm"], 10, 300)
        size_row.addWidget(self._width_spin)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self._height_spin)
        size_row.addStretch()
        grid.addWidget(_lbl("Taille d'étiquette"), 1, 0)
        grid.addLayout(size_row, 1, 1)
        # findData ne reconnaît pas les tuples Python : on cherche l'index nous-mêmes.
        preset = (int(cfg["label_width_mm"]), int(cfg["label_height_mm"]))
        index = LABEL_PRESETS.index(preset) if preset in LABEL_PRESETS else self._size_combo.count() - 1
        self._size_combo.setCurrentIndex(index)
        self._size_combo.currentIndexChanged.connect(self._on_size_changed)
        self._on_size_changed()

        hint = QLabel(
            "La taille est écrite sur le rouleau d'étiquettes, ou affichée dans l'application "
            "Niimbot du téléphone quand le rouleau est dans l'imprimante. Le ticket est réduit "
            "pour tenir sur une seule étiquette."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 12px;")
        grid.addWidget(hint, 2, 1)

        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 3)
        self._copies_spin.setValue(int(cfg.get("copies", 1)))
        self._copies_spin.setMinimumHeight(36)
        self._copies_spin.setFixedWidth(80)
        grid.addWidget(_lbl("Exemplaires"), 3, 0)
        grid.addWidget(self._copies_spin, 3, 1)

        self._direct_check = QCheckBox("Impression directe (sans la fenêtre de choix de Windows)")
        self._direct_check.setChecked(bool(cfg.get("direct", True)))
        grid.addWidget(self._direct_check, 4, 1)
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
        test_btn = QPushButton("🧾  Imprimer un test")
        test_btn.setObjectName("SecondaryButton")
        test_btn.clicked.connect(self._on_test)
        footer_layout.addWidget(test_btn)
        save_btn = QPushButton("💾  Enregistrer")
        save_btn.clicked.connect(self._on_save)
        footer_layout.addWidget(save_btn)
        root.addWidget(footer)

    @staticmethod
    def _mm_spin(value: int, minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(int(value))
        spin.setSuffix(" mm")
        spin.setMinimumHeight(36)
        spin.setFixedWidth(100)
        return spin

    def _on_size_changed(self) -> None:
        index = self._size_combo.currentIndex()
        custom = index >= len(LABEL_PRESETS)
        if not custom:
            width, height = LABEL_PRESETS[index]
            self._width_spin.setValue(width)
            self._height_spin.setValue(height)
        self._width_spin.setEnabled(custom)
        self._height_spin.setEnabled(custom)

    def _form_config(self) -> dict:
        return {
            **load_printer_config(),
            "printer_name": self._printer_combo.currentData(),
            "label_width_mm": self._width_spin.value(),
            "label_height_mm": self._height_spin.value(),
            "copies": self._copies_spin.value(),
            "direct": self._direct_check.isChecked(),
        }

    def _on_test(self) -> None:
        from app.printing import print_test_ticket

        if print_test_ticket(self, cfg=self._form_config()):
            self._status_label.setText("✓  Ticket de test envoyé (réglages non encore enregistrés).")
            self._status_label.setStyleSheet("color: #059669; font-weight: 600;")

    def _on_save(self) -> None:
        save_printer_config(self._form_config())
        self.accept()
