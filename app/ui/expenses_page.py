"""Page « Frais » (compte admin) — livraison, transport… déduits du bénéfice net."""

from __future__ import annotations

import datetime

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import cents_to_da, da_to_cents, format_da
from app.period import PERIOD_CHOICES, bounds_for_period
from app.services import (
    EXPENSE_KINDS,
    StockError,
    create_expense,
    delete_expense,
    list_expenses,
    update_expense,
)
from app.ui.widgets import ALIGN_RIGHT, EmptyState, ModernDoubleSpinBox, apply_card_shadow, right_align_headers

COLUMNS = ["Date", "Motif", "Montant", "Note", "Saisi par"]


class ExpensesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._editing_id: int | None = None
        self._ids: list[int] = []
        self._rows: list[tuple] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 24)
        outer.setSpacing(12)

        title = QLabel("🧾  Frais")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        outer.addWidget(title)
        hint = QLabel(
            "Livraison, transport… Ces frais sont retirés du bénéfice net de la période "
            "(onglet Bénéfices)."
        )
        hint.setStyleSheet("font-size: 12px; color: #94a3b8;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # Formulaire
        form_card = QFrame()
        form_card.setObjectName("Card")
        apply_card_shadow(form_card)
        form = QHBoxLayout(form_card)
        form.setContentsMargins(18, 14, 18, 14)
        form.setSpacing(10)

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        form.addWidget(self.date_edit)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(EXPENSE_KINDS)
        form.addWidget(self.kind_combo)

        self.amount_spin = ModernDoubleSpinBox()
        self.amount_spin.setRange(0, 99_999_999)
        self.amount_spin.setDecimals(0)
        self.amount_spin.setSuffix(" DA")
        self.amount_spin.setMinimumWidth(170)
        form.addWidget(self.amount_spin)

        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Note (facultatif) — ex : livraison commande Alger")
        self.note_input.setMaxLength(255)
        form.addWidget(self.note_input, stretch=1)

        self.save_btn = QPushButton("＋  Ajouter")
        self.save_btn.clicked.connect(self._on_save)
        form.addWidget(self.save_btn)

        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setObjectName("SecondaryButton")
        self.cancel_btn.clicked.connect(self._reset_form)
        self.cancel_btn.setVisible(False)
        form.addWidget(self.cancel_btn)
        outer.addWidget(form_card)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626; font-size: 12px;")
        outer.addWidget(self.error_label)

        # Filtre + total
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Période"))
        self.period_combo = QComboBox()
        self.period_combo.addItems([p for p in PERIOD_CHOICES if p != "Plage personnalisée"])
        self.period_combo.setCurrentText("Ce mois-ci")
        self.period_combo.currentTextChanged.connect(lambda _t: self.refresh())
        filter_row.addWidget(self.period_combo)
        filter_row.addStretch()
        filter_row.addWidget(QLabel("Total des frais :"))
        self.total_label = QLabel("0 DA")
        self.total_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #ef4444;")
        filter_row.addWidget(self.total_label)
        outer.addLayout(filter_row)

        # Liste
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right_align_headers(self.table, [2])
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        outer.addWidget(self.table, stretch=1)

        self.empty_state = EmptyState(
            "🧾", "Aucun frais sur cette période", "Ajoutez un frais avec le formulaire ci-dessus."
        )
        outer.addWidget(self.empty_state, stretch=1)

        actions = QHBoxLayout()
        actions.addStretch()
        self.edit_btn = QPushButton("✏  Modifier")
        self.edit_btn.setObjectName("SecondaryButton")
        self.edit_btn.clicked.connect(self._on_edit)
        actions.addWidget(self.edit_btn)
        self.delete_btn = QPushButton("🗑  Supprimer")
        self.delete_btn.setObjectName("SecondaryButton")
        self.delete_btn.clicked.connect(self._on_delete)
        actions.addWidget(self.delete_btn)
        outer.addLayout(actions)
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Données
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        start, end = bounds_for_period(self.period_combo.currentText())
        with session_scope() as session:
            expenses = list_expenses(session, start=start, end=end)
            self._ids = [e.id for e in expenses]
            self._rows = [
                (
                    e.expense_date, e.kind, e.amount_cents, e.note, e.cashier_name,
                )
                for e in expenses
            ]
        total = sum(r[2] for r in self._rows)
        self.total_label.setText(format_da(total))

        has_rows = bool(self._rows)
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        self.table.setRowCount(len(self._rows))
        for r, (when, kind, cents, note, who) in enumerate(self._rows):
            values = [when.strftime("%d/%m/%Y"), kind, format_da(cents), note, who]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 2:
                    item.setTextAlignment(ALIGN_RIGHT)
                self.table.setItem(r, c, item)
        self._on_selection_changed()

    def _selected_index(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else None

    def _on_selection_changed(self) -> None:
        has = self._selected_index() is not None
        self.edit_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _reset_form(self) -> None:
        self._editing_id = None
        self.date_edit.setDate(QDate.currentDate())
        self.kind_combo.setCurrentIndex(0)
        self.amount_spin.setValue(0)
        self.note_input.clear()
        self.save_btn.setText("＋  Ajouter")
        self.cancel_btn.setVisible(False)
        self.error_label.setText("")

    def _on_save(self) -> None:
        self.error_label.setText("")
        when = datetime.datetime.combine(self.date_edit.date().toPython(), datetime.time.min)
        data = dict(
            expense_date=when,
            kind=self.kind_combo.currentText(),
            amount_cents=da_to_cents(self.amount_spin.value()),
            note=self.note_input.text(),
        )
        try:
            with session_scope() as session:
                if self._editing_id is None:
                    create_expense(session, **data)
                else:
                    update_expense(session, self._editing_id, **data)
        except StockError as error:
            self.error_label.setText(str(error))
            return
        self._reset_form()
        self.refresh()

    def _on_edit(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        when, kind, cents, note, _who = self._rows[index]
        self._editing_id = self._ids[index]
        self.date_edit.setDate(QDate(when.year, when.month, when.day))
        position = self.kind_combo.findText(kind)
        if position < 0:
            self.kind_combo.addItem(kind)
            position = self.kind_combo.findText(kind)
        self.kind_combo.setCurrentIndex(position)
        self.amount_spin.setValue(cents_to_da(cents))
        self.note_input.setText(note)
        self.save_btn.setText("💾  Enregistrer")
        self.cancel_btn.setVisible(True)

    def _on_delete(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        answer = QMessageBox.question(
            self, "Supprimer le frais",
            f"Supprimer ce frais de {format_da(self._rows[index][2])} ?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            with session_scope() as session:
                delete_expense(session, self._ids[index])
        except StockError as error:
            self.error_label.setText(str(error))
        if self._editing_id == self._ids[index]:
            self._reset_form()
        self.refresh()
