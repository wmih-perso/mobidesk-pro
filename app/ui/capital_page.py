"""Onglet « Capital » (compte admin) — voir app/capital.py."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import session as _session
from app.timeutil import to_local
from app.capital import compute_capital, current_stock_value_cents, start_capital_tracking
from app.database import session_scope
from app.money import da_to_cents, format_da
from app.services import StockError
from app.ui.frameless_dialog import FramelessDialog
from app.ui.widgets import ModernDoubleSpinBox, apply_card_shadow

_TITLE_STYLE = "font-size: 13px; font-weight: 600; color: #64748b;"
_VALUE_STYLE = "font-size: 24px; font-weight: 800; color: {color};"
_HINT_STYLE = "font-size: 12px; color: #94a3b8;"


def _card(title: str, value: str, hint: str = "", color: str = "#0f172a", accent: str = "#e2e8f0") -> QFrame:
    card = QFrame()
    card.setObjectName("Card")
    card.setStyleSheet(f"QFrame#Card {{ border-left: 5px solid {accent}; }}")
    apply_card_shadow(card)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(4)
    title_label = QLabel(title)
    title_label.setStyleSheet(_TITLE_STYLE)
    layout.addWidget(title_label)
    value_label = QLabel(value)
    value_label.setStyleSheet(_VALUE_STYLE.format(color=color))
    layout.addWidget(value_label)
    if hint:
        hint_label = QLabel(hint)
        hint_label.setStyleSheet(_HINT_STYLE)
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
    return card


class CapitalPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 24)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("💼  Capital")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        header.addWidget(title)
        header.addStretch()
        self.restart_btn = QPushButton("🔄  Redémarrer le suivi")
        self.restart_btn.setObjectName("SecondaryButton")
        self.restart_btn.clicked.connect(self._on_start)
        header.addWidget(self.restart_btn)
        outer.addLayout(header)

        subtitle = QLabel(
            "Sans compter le bénéfice, le stock (au prix d'achat) et les espèces du capital "
            "doivent toujours redonner le capital de départ. Le bénéfice est affiché à part : "
            "c'est ce que vous pouvez retirer."
        )
        subtitle.setStyleSheet(_HINT_STYLE)
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 4, 4, 4)
        self._content_layout.setSpacing(14)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------------

    def _clear(self) -> None:
        self._clear_layout(self._content_layout)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Détacher tout de suite : sinon l'ancien contenu reste visible
                # jusqu'à sa destruction différée.
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    def refresh(self) -> None:
        self._clear()
        with session_scope() as session:
            report = compute_capital(session)
            start_date = to_local(report.start.started_at).strftime("%d/%m/%Y") if report else ""
            created_by = report.start.created_by if report else ""
        self.restart_btn.setVisible(report is not None)
        if report is None:
            self._show_not_started()
        else:
            self._show_report(report, start_date, created_by)
        self._content_layout.addStretch()

    def _show_not_started(self) -> None:
        card = QFrame()
        card.setObjectName("Card")
        apply_card_shadow(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        text = QLabel(
            "<b>Le suivi du capital n'est pas encore démarré.</b><br><br>"
            "Au démarrage, l'application prend une photo de votre capital : la valeur du stock "
            "au prix d'achat (calculée automatiquement) + les espèces que vous avez en caisse "
            "ce jour-là. Ensuite, chaque achat, vente et retour est suivi pour vérifier que "
            "le capital reste le même."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        button = QPushButton("▶  Démarrer le suivi du capital")
        button.setMinimumHeight(44)
        button.clicked.connect(self._on_start)
        row = QHBoxLayout()
        row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self._content_layout.addWidget(card)

    def _show_report(self, report, start_date: str, created_by: str) -> None:
        gap = report.gap_cents
        balanced = abs(gap) < 100  # moins de 1 DA : arrondis
        top = QGridLayout()
        top.setHorizontalSpacing(14)
        top.setVerticalSpacing(14)
        top.addWidget(_card(
            "Capital de départ", format_da(report.start.capital_cents),
            f"Le {start_date}" + (f" — par {created_by}" if created_by else "")
            + f" : stock {format_da(report.start.stock_value_cents)} + espèces {format_da(report.start.cash_cents)}",
            accent="#2563eb",
        ), 0, 0)
        top.addWidget(_card(
            "Capital aujourd'hui (stock + espèces du capital)", format_da(report.total_cents),
            "✓  Identique au capital de départ" if balanced else
            f"Écart : {'+' if gap > 0 else '−'} {format_da(abs(gap))} — pièces perdues, cassées ou "
            "corrigées (ajustements de stock), ou produits supprimés avec du stock.",
            color="#059669" if balanced else "#b45309",
            accent="#059669" if balanced else "#f59e0b",
        ), 0, 1)
        top.addWidget(_card(
            "💰  Bénéfice à retirer", format_da(report.profit_cents),
            f"Depuis le {start_date}, ventes − prix d'achat − retours. "
            f"Dettes des revendeurs aujourd'hui : {format_da(report.reseller_debts_cents)} "
            "(argent des ventes pas encore encaissé).",
            color="#1d4ed8", accent="#1d4ed8",
        ), 0, 2)
        self._content_layout.addLayout(top)

        parts = QGridLayout()
        parts.setHorizontalSpacing(14)
        parts.addWidget(_card("📦  Stock (au prix d'achat)", format_da(report.stock_cents),
                              "Pièces en stock × leur prix d'achat réel."), 0, 0)
        parts.addWidget(_card("💵  Espèces du capital", format_da(report.cash_cents),
                              "Ce qui doit être en caisse pour le capital, bénéfice non compris."), 0, 1)
        column = 2
        if report.defective_cents:
            parts.addWidget(_card("⚠  Défectueux à renvoyer", format_da(report.defective_cents),
                                  "Rendus par des clients, pas encore renvoyés au fournisseur."), 0, column)
            column += 1
        if report.exchange_cents:
            parts.addWidget(_card("🔁  Échanges en attente", format_da(report.exchange_cents),
                                  "Renvoyés au fournisseur, remplacement pas encore reçu."), 0, column)
        self._content_layout.addLayout(parts)

        detail = QFrame()
        detail.setObjectName("Card")
        apply_card_shadow(detail)
        grid = QGridLayout(detail)
        grid.setContentsMargins(20, 16, 20, 16)
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(8)
        heading = QLabel("Comment sont calculées les espèces du capital")
        heading.setStyleSheet("font-weight: 700; color: #1e3a8a;")
        grid.addWidget(heading, 0, 0, 1, 2)
        rows = [
            ("Espèces au départ", report.start.cash_cents),
            ("− Achats (fournisseurs, stock initial)", -report.purchases_cents),
            ("+ Pièces vendues (au prix d'achat)", report.sold_cost_cents),
            ("− Retours clients (au prix d'achat)", -report.customer_returns_cost_cents),
            ("+ Retours fournisseur remboursés", report.supplier_refunds_cents),
        ]
        for index, (label, cents) in enumerate(rows, start=1):
            grid.addWidget(QLabel(label), index, 0)
            value = QLabel(("− " if cents < 0 else "") + format_da(abs(cents)))
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(value, index, 1)
        total_label = QLabel("= Espèces du capital")
        total_label.setStyleSheet("font-weight: 700;")
        grid.addWidget(total_label, len(rows) + 1, 0)
        total_value = QLabel(format_da(report.cash_cents))
        total_value.setStyleSheet("font-weight: 700;")
        total_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(total_value, len(rows) + 1, 1)
        grid.setColumnStretch(2, 1)
        self._content_layout.addWidget(detail)

    def _on_start(self) -> None:
        dialog = StartCapitalDialog(restart=self.restart_btn.isVisible(), parent=self)
        if dialog.exec() == StartCapitalDialog.DialogCode.Accepted:
            self.refresh()


class StartCapitalDialog(FramelessDialog):
    """Photo du capital de départ : stock actuel (calculé) + espèces saisies."""

    def __init__(self, restart: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(480)
        self.setModal(True)
        with session_scope() as session:
            self._stock_cents = current_stock_value_cents(session)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header(
            "🔄  Redémarrer le suivi" if restart else "▶  Démarrer le suivi du capital", height=54
        ))
        body = QWidget()
        # Fond blanc limité au corps : sans sélecteur, il s'appliquerait aussi
        # aux boutons (bouton principal blanc sur blanc, invisible).
        body.setObjectName("DialogBody")
        body.setStyleSheet("QWidget#DialogBody { background: white; }")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        if restart:
            warning = QLabel(
                "Le suivi repart d'aujourd'hui : le capital de départ et le bénéfice sont "
                "recalculés à partir de maintenant."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #b45309;")
            layout.addWidget(warning)

        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.addWidget(QLabel("Stock actuel (au prix d'achat) :"), 0, 0)
        stock = QLabel(f"<b>{format_da(self._stock_cents)}</b>")
        stock.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(stock, 0, 1)
        grid.addWidget(QLabel("Espèces en caisse aujourd'hui :"), 1, 0)
        self._cash_spin = ModernDoubleSpinBox()
        self._cash_spin.setRange(0, 999_999_999)
        self._cash_spin.setDecimals(0)
        self._cash_spin.setSuffix(" DA")
        self._cash_spin.setMinimumHeight(36)
        self._cash_spin.valueChanged.connect(self._update_total)
        grid.addWidget(self._cash_spin, 1, 1)
        grid.addWidget(QLabel("= Capital de départ :"), 2, 0)
        self._total = QLabel("")
        self._total.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._total, 2, 1)
        layout.addLayout(grid)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #dc2626;")
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("✕  Annuler")
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("✔  Démarrer")
        ok.clicked.connect(self._on_save)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        root.addWidget(body)
        self._update_total()

    def _update_total(self) -> None:
        total = self._stock_cents + da_to_cents(self._cash_spin.value())
        self._total.setText(f"<b style='font-size:16px; color:#1d4ed8;'>{format_da(total)}</b>")

    def _on_save(self) -> None:
        try:
            with session_scope() as session:
                start_capital_tracking(
                    session,
                    cash_cents=da_to_cents(self._cash_spin.value()),
                    created_by=_session.get_current_cashier_name(),
                )
        except StockError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.accept()

