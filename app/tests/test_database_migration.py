from sqlalchemy import create_engine

from app.database import (
    Base,
    _ensure_indexes,
    _migrate_displays_schema,
    _migrate_repairs_schema,
    _migrate_stock_movements_schema,
    _seed_default_categories,
)
from app import models  # noqa: F401 — enregistre les modèles


def _create_legacy_displays_table(engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE displays (
                id INTEGER PRIMARY KEY,
                reference VARCHAR(50) UNIQUE,
                brand VARCHAR(100) NOT NULL DEFAULT '',
                phone_model VARCHAR(150) NOT NULL DEFAULT '',
                purchase_price_cents INTEGER NOT NULL DEFAULT 0,
                sale_price_cents INTEGER NOT NULL DEFAULT 0,
                quantity INTEGER NOT NULL DEFAULT 0,
                min_stock INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.commit()


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


def _create_repairs_table(engine) -> None:
    """Les colonnes repair_id/supplier_id/reseller_id de stock_movements
    référencent repairs(id)/suppliers(id)/resellers(id) — ces tables
    doivent exister avant que la migration ajoute ces colonnes."""
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE repairs (id INTEGER PRIMARY KEY, created_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name VARCHAR(150))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE resellers (id INTEGER PRIMARY KEY, name VARCHAR(150))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE categories (id INTEGER PRIMARY KEY, name VARCHAR(50))"
        )
        connection.commit()


def test_migration_adds_missing_columns_without_losing_data(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_repairs_table(engine)
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


def test_ensure_indexes_creates_expected_indexes_on_legacy_schema(tmp_path):
    db_path = tmp_path / "legacy_indexes.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_displays_table(engine)
    _create_repairs_table(engine)
    _create_legacy_schema(engine)
    _migrate_stock_movements_schema(engine)  # ajoute is_sale avant les index
    _migrate_displays_schema(engine)  # ajoute category avant les index

    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE repair_items (id INTEGER PRIMARY KEY, repair_id INTEGER, display_id INTEGER)"
        )
        connection.commit()

    _ensure_indexes(engine)

    with engine.connect() as connection:
        index_names = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA index_list(stock_movements)").fetchall()
        } | {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA index_list(displays)").fetchall()
        }

    expected = {
        "ix_displays_brand",
        "ix_displays_phone_model",
        "ix_displays_category",
        "ix_stock_movements_display_id",
        "ix_stock_movements_created_at",
        "ix_stock_movements_is_sale",
        "ix_stock_movements_repair_id",
        "ix_stock_movements_supplier_id",
        "ix_stock_movements_reseller_id",
    }
    assert expected <= index_names


def test_ensure_indexes_is_idempotent(tmp_path):
    db_path = tmp_path / "legacy_indexes_twice.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_displays_table(engine)
    _create_repairs_table(engine)
    _create_legacy_schema(engine)
    _migrate_stock_movements_schema(engine)
    _migrate_displays_schema(engine)

    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE repair_items (id INTEGER PRIMARY KEY, repair_id INTEGER, display_id INTEGER)"
        )
        connection.commit()

    _ensure_indexes(engine)
    _ensure_indexes(engine)  # ne doit pas lever d'erreur


def test_migration_is_idempotent_on_already_migrated_schema(tmp_path):
    db_path = tmp_path / "migrated.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_repairs_table(engine)
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


def test_migrate_displays_schema_adds_category_and_splits_prices(tmp_path):
    db_path = tmp_path / "legacy_displays.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_displays_table(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO displays
                (id, reference, brand, phone_model, purchase_price_cents, sale_price_cents, quantity, min_stock)
            VALUES (1, 'AFF-001', 'Samsung', 'Galaxy A12', 150000, 250000, 10, 3)
            """
        )
        connection.commit()

    _migrate_displays_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(displays)"
            ).fetchall()
        }
        assert {"category", "sale_price_retail_cents", "sale_price_wholesale_cents"} <= columns
        assert "sale_price_cents" not in columns

        row = connection.exec_driver_sql(
            "SELECT category, sale_price_retail_cents, sale_price_wholesale_cents "
            "FROM displays WHERE id = 1"
        ).fetchone()
        assert row[0] == "Afficheur"
        assert row[1] == 250000
        assert row[2] == 250000  # bootstrap au prix détail existant


def test_migrate_displays_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "displays_twice.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_displays_table(engine)

    _migrate_displays_schema(engine)
    _migrate_displays_schema(engine)  # ne doit pas lever d'erreur


def test_migrate_displays_schema_drops_legacy_not_null_color_column(tmp_path):
    """La colonne `color` (NOT NULL, sans défaut) d'une version antérieure
    au modèle actuel fait échouer tout INSERT généré par l'ORM, puisque
    celui-ci ne la renseigne jamais — reproduit un bug réel où "Enregistrer"
    semblait ne rien faire sur une base créée par cette ancienne version."""
    db_path = tmp_path / "displays_legacy_color.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_displays_table(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("ALTER TABLE displays ADD COLUMN color VARCHAR(50) NOT NULL DEFAULT ''")
        connection.exec_driver_sql(
            """
            INSERT INTO displays
                (id, reference, brand, phone_model, color, purchase_price_cents, sale_price_cents, quantity, min_stock)
            VALUES (1, 'AFF-001', 'Samsung', 'Galaxy A12', 'Noir', 150000, 250000, 10, 3)
            """
        )
        connection.commit()

    _migrate_displays_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(displays)"
            ).fetchall()
        }
        assert "color" not in columns

        row = connection.exec_driver_sql(
            "SELECT reference FROM displays WHERE id = 1"
        ).fetchone()
        assert row[0] == "AFF-001"  # la ligne existante n'est pas perdue


def test_migrate_displays_schema_drops_color_column_is_idempotent(tmp_path):
    db_path = tmp_path / "displays_legacy_color_twice.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_displays_table(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("ALTER TABLE displays ADD COLUMN color VARCHAR(50) NOT NULL DEFAULT ''")
        connection.commit()

    _migrate_displays_schema(engine)
    _migrate_displays_schema(engine)  # ne doit pas lever d'erreur


def test_migrate_displays_schema_preserves_customized_wholesale_price(tmp_path):
    db_path = tmp_path / "displays_custom_wholesale.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_displays_table(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO displays
                (id, reference, brand, phone_model, purchase_price_cents, sale_price_cents, quantity, min_stock)
            VALUES (1, 'AFF-001', 'Samsung', 'Galaxy A12', 150000, 250000, 10, 3)
            """
        )
        connection.commit()

    _migrate_displays_schema(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "UPDATE displays SET sale_price_wholesale_cents = 220000 WHERE id = 1"
        )
        connection.commit()

    _migrate_displays_schema(engine)  # rerun ne doit pas réécraser le prix gros personnalisé

    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT sale_price_wholesale_cents FROM displays WHERE id = 1"
        ).fetchone()
        assert row[0] == 220000


def test_migration_adds_repair_id_column_to_stock_movements(tmp_path):
    db_path = tmp_path / "legacy_repair_id.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_repairs_table(engine)
    _create_legacy_schema(engine)

    _migrate_stock_movements_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(stock_movements)"
            ).fetchall()
        }
        assert {"repair_id", "supplier_id", "reseller_id"} <= columns

        row = connection.exec_driver_sql(
            "SELECT repair_id, supplier_id, reseller_id FROM stock_movements WHERE id = 1"
        ).fetchone()
        assert row[0] is None
        assert row[1] is None
        assert row[2] is None


def test_migration_adds_repair_id_column_is_idempotent(tmp_path):
    db_path = tmp_path / "legacy_repair_id_twice.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_repairs_table(engine)
    _create_legacy_schema(engine)

    _migrate_stock_movements_schema(engine)
    _migrate_stock_movements_schema(engine)  # ne doit pas lever d'erreur


def _create_legacy_repairs_tables(engine) -> None:
    """Simule repairs/repair_items d'avant discount_cents/unit_sale_price_cents."""
    with engine.connect() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE repairs (
                id INTEGER PRIMARY KEY,
                client_name VARCHAR(150) NOT NULL DEFAULT '',
                charged_price_cents INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE repair_items (
                id INTEGER PRIMARY KEY,
                repair_id INTEGER NOT NULL,
                display_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_purchase_price_cents INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO repairs (id, client_name, charged_price_cents, created_at) "
            "VALUES (1, 'Karim', 300000, '2024-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO repair_items (id, repair_id, display_id, quantity, unit_purchase_price_cents) "
            "VALUES (1, 1, 1, 2, 150000)"
        )
        connection.commit()


def test_migrate_repairs_schema_adds_missing_columns(tmp_path):
    db_path = tmp_path / "legacy_repairs.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_repairs_tables(engine)

    _migrate_repairs_schema(engine)

    with engine.connect() as connection:
        repairs_columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(repairs)"
            ).fetchall()
        }
        repair_items_columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(repair_items)"
            ).fetchall()
        }
        assert "discount_cents" in repairs_columns
        assert "unit_sale_price_cents" in repair_items_columns

        row = connection.exec_driver_sql(
            "SELECT discount_cents FROM repairs WHERE id = 1"
        ).fetchone()
        assert row[0] == 0


def test_migrate_repairs_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "legacy_repairs_twice.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _create_legacy_repairs_tables(engine)

    _migrate_repairs_schema(engine)
    _migrate_repairs_schema(engine)  # ne doit pas lever d'erreur


def test_migrate_repairs_schema_noop_when_tables_absent(tmp_path):
    db_path = tmp_path / "no_repairs_yet.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    _migrate_repairs_schema(engine)  # ne doit pas lever d'erreur si les tables n'existent pas encore


def test_seed_default_categories_populates_empty_table(tmp_path):
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    _seed_default_categories(engine)

    with engine.connect() as connection:
        names = {
            row[0] for row in connection.exec_driver_sql("SELECT name FROM categories").fetchall()
        }
    assert names == {"Afficheur", "Batterie", "Autre"}


def test_seed_default_categories_does_not_duplicate_on_rerun(tmp_path):
    db_path = tmp_path / "fresh_twice.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    _seed_default_categories(engine)
    _seed_default_categories(engine)

    with engine.connect() as connection:
        (count,) = connection.exec_driver_sql("SELECT COUNT(*) FROM categories").fetchone()
    assert count == 3


def test_seed_default_categories_preserves_user_customizations(tmp_path):
    db_path = tmp_path / "customized.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    with engine.connect() as connection:
        connection.exec_driver_sql(
            "INSERT INTO categories (name, created_at) VALUES ('Coque', CURRENT_TIMESTAMP)"
        )
        connection.commit()

    _seed_default_categories(engine)  # ne doit pas ajouter les catégories par défaut ici

    with engine.connect() as connection:
        names = {
            row[0] for row in connection.exec_driver_sql("SELECT name FROM categories").fetchall()
        }
    assert names == {"Coque"}


