"""Doublons probables : un même produit créé par plusieurs fournisseurs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.merge import (
    MergeSuggestion,
    ProductInfo,
    backup_database_file,
    list_lots,
    merge_displays,
    suggest_merges,
)
from app.money import format_da
from app.paths import get_data_dir, get_database_path
from app.services import StockError
from app.ui.frameless_dialog import FramelessDialog


class MergeDialog(FramelessDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.changed = False  # vrai si au moins une fusion a été faite
        self._suggestions: list[MergeSuggestion] = []
        self._ignored: set[tuple[int, int]] = set()
        self.setMinimumSize(900, 540)
        self.setModal(True)
        self._build_ui()
        self._reload()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header("Doublons probables"))

        body = QWidget()
        body.setStyleSheet("background: white;")
        layout = QHBoxLayout(body)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(18)

        left = QVBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #64748b; font-size: 12px;")
        left.addWidget(self.count_label)
        self.list = QListWidget()
        self.list.setMinimumWidth(360)
        self.list.currentRowChanged.connect(self._on_selected)
        left.addWidget(self.list, stretch=1)
        layout.addLayout(left, stretch=1)

        right = QVBoxLayout()
        right.setSpacing(10)
        self.detail_label = QLabel("")
        self.detail_label.setTextFormat(Qt.TextFormat.RichText)
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail_label.setStyleSheet("font-size: 13px; color: #1f2937;")
        right.addWidget(self.detail_label, stretch=1)

        right.addWidget(QLabel("Produit à garder :"))
        self.keep_combo = QComboBox()
        right.addWidget(self.keep_combo)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626; font-size: 12px;")
        self.error_label.setWordWrap(True)
        right.addWidget(self.error_label)

        buttons = QHBoxLayout()
        self.ignore_btn = QPushButton("Pas un doublon")
        self.ignore_btn.setObjectName("SecondaryButton")
        self.ignore_btn.clicked.connect(self._on_ignore)
        buttons.addWidget(self.ignore_btn)
        buttons.addStretch()
        self.merge_btn = QPushButton("🔗  Fusionner")
        self.merge_btn.clicked.connect(self._on_merge)
        buttons.addWidget(self.merge_btn)
        right.addLayout(buttons)
        layout.addLayout(right, stretch=1)

        root.addWidget(body, stretch=1)

        footer = QFrame()
        footer.setStyleSheet("QFrame { background: #f0f9ff; border-top: 1px solid #bae6fd; }")
        flay = QHBoxLayout(footer)
        flay.setContentsMargins(24, 10, 24, 10)
        note = QLabel(
            "Une copie de la base est faite avant chaque fusion. Les lots (fournisseur et prix) "
            "sont conservés."
        )
        note.setStyleSheet("color: #475569; font-size: 12px; background: transparent; border: none;")
        flay.addWidget(note)
        flay.addStretch()
        close = QPushButton("Fermer")
        close.setObjectName("SecondaryButton")
        close.clicked.connect(self.accept)
        flay.addWidget(close)
        root.addWidget(footer)

    # ------------------------------------------------------------------

    @staticmethod
    def _pair_key(s: MergeSuggestion) -> tuple[int, int]:
        return (min(s.a.id, s.b.id), max(s.a.id, s.b.id))

    def _reload(self) -> None:
        with session_scope() as session:
            all_suggestions = suggest_merges(session)
        self._suggestions = [s for s in all_suggestions if self._pair_key(s) not in self._ignored]
        self.list.blockSignals(True)
        self.list.clear()
        for s in self._suggestions:
            tag = "identique" if s.score >= 0.999 else f"{round(s.score * 100)} %"
            self.list.addItem(QListWidgetItem(
                f"{s.a.category} · {s.a.brand}\n"
                f"   {s.a.phone_model}   ⇄   {s.b.phone_model}   ({tag})"
            ))
        self.list.blockSignals(False)
        n = len(self._suggestions)
        self.count_label.setText(f"{n} doublon(s) probable(s)" if n else "Aucun doublon probable.")
        has = n > 0
        self.merge_btn.setEnabled(has)
        self.ignore_btn.setEnabled(has)
        if has:
            self.list.setCurrentRow(0)
            self._on_selected(0)
        else:
            self.detail_label.setText("")
            self.keep_combo.clear()

    def _describe(self, p: ProductInfo, lots) -> str:
        lines = "".join(
            f"<br>&nbsp;&nbsp;•&nbsp;{lot.supplier_name} — {lot.quantity_remaining} pièce(s) "
            f"à {format_da(lot.unit_price_cents)}"
            for lot in lots
        ) or "<br>&nbsp;&nbsp;aucun lot en stock"
        return (
            f"<p><b>{p.reference}</b><br>{p.category} · {p.brand} · {p.phone_model}<br>"
            f"Stock : <b>{p.quantity}</b> — prix d'achat {format_da(p.purchase_price_cents)}"
            f"<br>Lots :{lines}</p>"
        )

    def _on_selected(self, row: int) -> None:
        self.error_label.setText("")
        if not 0 <= row < len(self._suggestions):
            return
        s = self._suggestions[row]
        with session_scope() as session:
            lots_a = list_lots(session, s.a.id)
            lots_b = list_lots(session, s.b.id)
        self.detail_label.setText(self._describe(s.a, lots_a) + self._describe(s.b, lots_b))
        # Par défaut on garde le produit qui a le plus de stock.
        first, second = (s.a, s.b) if s.a.quantity >= s.b.quantity else (s.b, s.a)
        self.keep_combo.clear()
        for p in (first, second):
            self.keep_combo.addItem(f"{p.reference} — {p.phone_model}", p.id)

    def _on_ignore(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._suggestions):
            self._ignored.add(self._pair_key(self._suggestions[row]))
            self._reload()

    def _on_merge(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self._suggestions):
            return
        s = self._suggestions[row]
        keep_id = self.keep_combo.currentData()
        keep, drop = (s.a, s.b) if keep_id == s.a.id else (s.b, s.a)
        answer = QMessageBox.question(
            self, "Fusionner",
            f"Garder « {keep.reference} » ({keep.phone_model}) et y fusionner "
            f"« {drop.reference} » ({drop.phone_model}) ?\n\n"
            f"Stock après fusion : {keep.quantity + drop.quantity} pièce(s).",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup_database_file(get_database_path(), get_data_dir() / "backups")
            with session_scope() as session:
                merge_displays(session, keep.id, drop.id)
        except (StockError, OSError) as error:
            self.error_label.setText(str(error))
            return
        self.changed = True
        self._reload()
