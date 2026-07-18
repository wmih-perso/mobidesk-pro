"""Onglet « Bénéfices » — ventes réelles et bénéfice par période."""

from __future__ import annotations

import csv
import datetime

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database import session_scope
from app.money import format_da
from app.period import PERIOD_CHOICES, bounds_for_period
from app.services import list_sales, sum_profit_cents
from app.ui.widgets import EmptyState, apply_card_shadow

SALE_COLUMNS = [
    "Date",
    "Produit",
    "Type",
    "Quantité",
    "Prix d'achat unitaire",
    "Prix de vente unitaire",
    "Bénéfice unitaire",
    "Bénéfice total",
]

SALE_PRICE_TYPE_LABELS = {"retail": "Détail", "wholesale": "Gros"}


class ProfitTab(QWidget):
    """Liste des ventes réelles et leur bénéfice, filtrable par période."""

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        outer.addWidget(self._build_filters_card())

        table_card = QFrame()
        table_card.setObjectName("Card")
        apply_card_shadow(table_card)
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 14, 16, 12)
        table_layout.setSpacing(8)

        actions_row = QHBoxLayout()
        actions_row.addStretch()
        export_button = QPushButton("⬇  Exporter")
        export_button.setObjectName("SecondaryButton")
        export_button.clicked.connect(self._on_export_csv)
        actions_row.addWidget(export_button)
        table_layout.addLayout(actions_row)

        self.sales_table = QTableWidget(0, len(SALE_COLUMNS))
        self.sales_table.setHorizontalHeaderLabels(SALE_COLUMNS)
        self.sales_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sales_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sales_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sales_table.setAlternatingRowColors(True)
        self.sales_table.setShowGrid(False)
        self.sales_table.verticalHeader().setVisible(False)
        self.sales_table.verticalHeader().setDefaultSectionSize(40)
        self.sales_table.setFrameShape(QFrame.Shape.NoFrame)
        self.sales_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.sales_table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.sales_table, stretch=1)

        self.sales_empty_state = EmptyState(
            "💵",
            "Aucune vente enregistrée",
            "Les ventes réalisées via « Sortie de stock » (motif Vente) apparaîtront ici.",
        )
        table_layout.addWidget(self.sales_empty_state, stretch=1)

        outer.addWidget(table_card, stretch=1)

    def _build_filters_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        period_label = QLabel("Période")
        layout.addWidget(period_label)

        self.period_combo = QComboBox()
        self.period_combo.addItems(PERIOD_CHOICES)
        self.period_combo.setCurrentText("Ce mois-ci")
        layout.addWidget(self.period_combo)

        self.start_date_edit = QDateEdit(QDate.currentDate())
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setVisible(False)
        self.start_date_edit.dateChanged.connect(self.refresh)
        layout.addWidget(self.start_date_edit)

        to_label = QLabel("→")
        to_label.setVisible(False)
        self._to_label = to_label
        layout.addWidget(to_label)

        self.end_date_edit = QDateEdit(QDate.currentDate())
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setVisible(False)
        self.end_date_edit.dateChanged.connect(self.refresh)
        layout.addWidget(self.end_date_edit)

        layout.addStretch()

        subtitle = QLabel("Bénéfice de la période")
        subtitle.setStyleSheet("color: #8991ac; font-weight: 600;")
        layout.addWidget(subtitle)

        self.total_profit_label = QLabel("0 DA")
        self.total_profit_label.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #059669;"
        )
        layout.addWidget(self.total_profit_label)

        self.period_combo.currentTextChanged.connect(self._on_period_changed)

        return card

    def _on_period_changed(self, period: str) -> None:
        is_custom = period == "Plage personnalisée"
        self.start_date_edit.setVisible(is_custom)
        self._to_label.setVisible(is_custom)
        self.end_date_edit.setVisible(is_custom)
        self.refresh()

    def _resolve_bounds(self) -> tuple[datetime.datetime | None, datetime.datetime | None]:
        period = self.period_combo.currentText()
        if period == "Plage personnalisée":
            start = datetime.datetime.combine(
                self.start_date_edit.date().toPython(), datetime.time.min
            )
            end = datetime.datetime.combine(
                self.end_date_edit.date().toPython(), datetime.time.max
            )
            return start, end
        return bounds_for_period(period)

    def refresh(self) -> None:
        start, end = self._resolve_bounds()

        with session_scope() as session:
            sales = list_sales(session, start=start, end=end)
            self._current_rows = [
                (
                    m.created_at.strftime("%d/%m/%Y %H:%M"),
                    m.display.reference,
                    SALE_PRICE_TYPE_LABELS.get(m.sale_price_type, ""),
                    abs(m.change_quantity),
                    format_da(m.unit_purchase_price_cents),
                    format_da(m.unit_sale_price_cents),
                    format_da(m.unit_sale_price_cents - m.unit_purchase_price_cents),
                    format_da(m.profit_cents),
                )
                for m in sales
            ]
            total_profit_cents = sum(m.profit_cents for m in sales)

        self.total_profit_label.setText(format_da(total_profit_cents))
        self._render_table()

    def _render_table(self) -> None:
        rows = self._current_rows
        has_rows = len(rows) > 0
        self.sales_table.setVisible(has_rows)
        self.sales_empty_state.setVisible(not has_rows)

        self.sales_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                self.sales_table.setItem(
                    row_index, column_index, QTableWidgetItem(str(value))
                )

    def _on_export_csv(self) -> None:
        if not self._current_rows:
            QMessageBox.information(self, "Exporter", "Aucune vente à exporter.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les ventes", "ventes.csv", "Fichiers CSV (*.csv)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as csv_file:
                writer = csv.writer(csv_file, delimiter=";")
                writer.writerow(SALE_COLUMNS)
                writer.writerows(self._current_rows)
        except OSError as error:
            QMessageBox.warning(self, "Erreur d'export", f"Impossible d'écrire le fichier : {error}")
            return

        QMessageBox.information(self, "Export réussi", f"{len(self._current_rows)} vente(s) exportée(s).")
