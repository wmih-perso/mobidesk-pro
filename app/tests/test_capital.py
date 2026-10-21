from app.capital import compute_capital, start_capital_tracking
from app.returns import create_customer_return, create_supplier_return, list_purchases_for_return, list_sales_for_return
from app.services import apply_stock_batch, create_reseller, create_supplier
from app.tests.test_services import _create_sample

DA = 100  # centimes


def _buy(session, display, supplier, quantity, unit_da=1500):
    apply_stock_batch(session, direction=1, reason="Achat fournisseur",
                      lines=[(display.id, quantity, unit_da * DA)], supplier_id=supplier.id)


def _sell(session, display, reseller, quantity, unit_da=2000):
    apply_stock_batch(session, direction=-1, reason="Vente", lines=[(display.id, quantity, unit_da * DA)],
                      is_sale=True, sale_price_type="wholesale", reseller_id=reseller.id,
                      versement_cents=quantity * unit_da * DA)


def test_capital_stays_equal_to_start_with_profit_apart(db_session):
    # L'exemple du cahier des charges.
    display = _create_sample(db_session, quantity=0, purchase_price_cents=1500 * DA)
    supplier = create_supplier(db_session, name="F")
    reseller = create_reseller(db_session, name="Client comptoir")
    start_capital_tracking(db_session, cash_cents=100_000 * DA)

    _buy(db_session, display, supplier, 10)
    report = compute_capital(db_session)
    assert (report.stock_cents, report.cash_cents) == (15_000 * DA, 85_000 * DA)

    _sell(db_session, display, reseller, 4)
    report = compute_capital(db_session)
    assert (report.stock_cents, report.cash_cents) == (9_000 * DA, 91_000 * DA)
    assert report.profit_cents == 2_000 * DA
    assert report.total_cents == 100_000 * DA and report.gap_cents == 0

    _buy(db_session, display, supplier, 5)
    report = compute_capital(db_session)
    assert (report.stock_cents, report.cash_cents) == (16_500 * DA, 83_500 * DA)
    assert report.gap_cents == 0


def test_start_includes_existing_stock(db_session):
    _create_sample(db_session, quantity=4, purchase_price_cents=1000 * DA)  # 4 000 DA de stock

    start = start_capital_tracking(db_session, cash_cents=6_000 * DA)

    assert start.capital_cents == 10_000 * DA
    assert compute_capital(db_session).gap_cents == 0


def test_returns_keep_capital_balanced(db_session):
    display = _create_sample(db_session, quantity=0)
    supplier = create_supplier(db_session, name="F")
    reseller = create_reseller(db_session, name="R")
    start_capital_tracking(db_session, cash_cents=50_000 * DA)
    _buy(db_session, display, supplier, 10)
    _sell(db_session, display, reseller, 3)

    [sale] = list_sales_for_return(db_session, reseller_id=reseller.id)
    create_customer_return(db_session, lines=[(sale.lines[0].movement_id, 1, "good")], settlement="cash")
    create_customer_return(db_session, lines=[(sale.lines[0].movement_id, 1, "defective")], settlement="cash")
    report = compute_capital(db_session)
    assert report.defective_cents == 1500 * DA
    assert report.gap_cents == 0

    [purchase] = list_purchases_for_return(db_session, supplier_id=supplier.id)
    create_supplier_return(db_session, supplier_id=supplier.id,
                           purchase_lines=[(purchase.lines[0].movement_id, 1)], settlement="exchange")
    report = compute_capital(db_session)
    assert report.exchange_cents == 1500 * DA
    assert report.gap_cents == 0
    assert report.profit_cents == 500 * DA  # 3 vendus − 2 rendus


def test_lost_pieces_show_as_gap(db_session):
    display = _create_sample(db_session, quantity=0)
    supplier = create_supplier(db_session, name="F")
    start_capital_tracking(db_session, cash_cents=20_000 * DA)
    _buy(db_session, display, supplier, 4)

    apply_stock_batch(db_session, direction=-1, reason="Produit perdu", lines=[(display.id, 1, None)])

    assert compute_capital(db_session).gap_cents == -1500 * DA
