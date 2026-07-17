from sqlalchemy import create_engine

from app.database import _migrate_stock_movements_schema


def _create_legacy_schema(engine) -> None:
    """Simule le schéma stock_movements d'avant le suivi du bénéfice."""
    with engine.connect() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE stock_movements (
                id INTEGER PRIMARY KEY,
                display_id INTEGER NOT NULL,
                change_quantity INTEGER NOT NULL,
                quantity_before INTEGER NOT NULL,
                quantity_after INTEGER NOT NULL,
                reason VARCHAR(255) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO stock_movements
                (id, display_id, change_quantity, quantity_before, quantity_after, reason, created_at)
            VALUES (1, 1, 5, 0, 5, 'Stock initial', '2024-01-01 00:00:00')
            """
        )
        connection.commit()


def test_migration_adds_missing_columns_without_losing_data(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_schema(engine)

    _migrate_stock_movements_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(stock_movements)"
            ).fetchall()
        }
        assert {"is_sale", "unit_purchase_price_cents", "unit_sale_price_cents"} <= columns

        row = connection.exec_driver_sql(
            "SELECT reason, is_sale, unit_purchase_price_cents, unit_sale_price_cents "
            "FROM stock_movements WHERE id = 1"
        ).fetchone()
        assert row[0] == "Stock initial"
        assert row[1] == 0
        assert row[2] == 0
        assert row[3] == 0


def test_migration_is_idempotent_on_already_migrated_schema(tmp_path):
    db_path = tmp_path / "migrated.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_schema(engine)

    _migrate_stock_movements_schema(engine)
    _migrate_stock_movements_schema(engine)  # ne doit pas lever d'erreur

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(stock_movements)"
            ).fetchall()
        }
        assert {"is_sale", "unit_purchase_price_cents", "unit_sale_price_cents"} <= columns
