"""Connexion SQLite et gestion des sessions SQLAlchemy."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.paths import get_database_path


class Base(DeclarativeBase):
    """Classe de base pour tous les modèles ORM."""


_engine = None
_session_factory: sessionmaker | None = None


def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def _migrate_stock_movements_schema(engine) -> None:
    """Ajoute les colonnes de suivi du bénéfice si la table existait déjà
    sans elles (installations antérieures à cette fonctionnalité).

    Base.metadata.create_all() ne crée que les tables manquantes — il ne
    modifie jamais une table existante. Ce projet n'a pas d'outil de
    migration (pas d'Alembic), donc on gère ici un ALTER TABLE minimal et
    idempotent pour chaque colonne potentiellement manquante.
    """
    with engine.connect() as connection:
        existing_columns = {
            row[1]  # row = (cid, name, type, notnull, dflt_value, pk)
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(stock_movements)"
            ).fetchall()
        }

        columns_to_add = {
            "is_sale": "BOOLEAN NOT NULL DEFAULT 0",
            "unit_purchase_price_cents": "INTEGER NOT NULL DEFAULT 0",
            "unit_sale_price_cents": "INTEGER NOT NULL DEFAULT 0",
            "sale_price_type": "VARCHAR(10) NOT NULL DEFAULT ''",
            "repair_id": "INTEGER REFERENCES repairs(id)",
            "supplier_id": "INTEGER REFERENCES suppliers(id)",
            "reseller_id": "INTEGER REFERENCES resellers(id)",
        }

        for column_name, ddl_type in columns_to_add.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE stock_movements ADD COLUMN {column_name} {ddl_type}"
                )
        connection.commit()


def _migrate_displays_schema(engine) -> None:
    """Généralise la table `displays` d'un seul prix de vente à deux prix
    (détail/gros) et ajoute une catégorie — installations antérieures à
    cette fonctionnalité. Idempotente, comme _migrate_stock_movements_schema.
    """
    with engine.connect() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(displays)"
            ).fetchall()
        }

        if "sale_price_cents" in existing and "sale_price_retail_cents" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE displays RENAME COLUMN sale_price_cents TO sale_price_retail_cents"
            )
            existing.discard("sale_price_cents")
            existing.add("sale_price_retail_cents")

        if "category" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE displays ADD COLUMN category VARCHAR(50) NOT NULL DEFAULT 'Afficheur'"
            )

        if "sale_price_wholesale_cents" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE displays ADD COLUMN sale_price_wholesale_cents INTEGER NOT NULL DEFAULT 0"
            )
            # Bootstrap au prix détail existant, uniquement à la création de
            # la colonne — ne réécrase jamais un prix gros déjà personnalisé
            # par l'utilisateur lors d'un redémarrage suivant.
            connection.exec_driver_sql(
                "UPDATE displays SET sale_price_wholesale_cents = sale_price_retail_cents"
            )

        # "color" est une colonne NOT NULL héritée d'une version antérieure
        # au modèle actuel (retirée depuis). Le modèle ORM ne la renseigne
        # jamais à l'insertion, donc sa contrainte NOT NULL fait échouer
        # tout create_display() sur une base créée par cette ancienne
        # version — d'où un enregistrement de produit qui semble ne rien
        # faire (l'exception SQL n'est pas un StockError, elle échappe au
        # dialog silencieusement). On la supprime explicitement plutôt que
        # de la migrer, puisqu'aucune fonctionnalité actuelle ne l'utilise.
        if "color" in existing:
            connection.exec_driver_sql("ALTER TABLE displays DROP COLUMN color")

        connection.commit()


def _migrate_repairs_schema(engine) -> None:
    """Ajoute discount_cents (repairs) et unit_sale_price_cents (repair_items)
    si les tables existaient déjà sans ces colonnes. Idempotente, comme les
    autres migrations de ce module.
    """
    with engine.connect() as connection:
        repairs_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(repairs)"
            ).fetchall()
        }
        if repairs_columns and "discount_cents" not in repairs_columns:
            connection.exec_driver_sql(
                "ALTER TABLE repairs ADD COLUMN discount_cents INTEGER NOT NULL DEFAULT 0"
            )

        repair_items_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(repair_items)"
            ).fetchall()
        }
        if repair_items_columns and "unit_sale_price_cents" not in repair_items_columns:
            connection.exec_driver_sql(
                "ALTER TABLE repair_items ADD COLUMN unit_sale_price_cents INTEGER NOT NULL DEFAULT 0"
            )

        connection.commit()


_DEFAULT_CATEGORIES = ["Afficheur", "Batterie", "Autre"]


def _seed_default_categories(engine) -> None:
    """Peuple la table `categories` avec les catégories historiquement
    codées en dur dans display_dialog.py, uniquement si elle est encore
    vide (première exécution après cette fonctionnalité) — n'écrase jamais
    des catégories déjà personnalisées par l'utilisateur."""
    with engine.connect() as connection:
        (count,) = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM categories"
        ).fetchone()
        if count == 0:
            for name in _DEFAULT_CATEGORIES:
                connection.exec_driver_sql(
                    "INSERT INTO categories (name, created_at) VALUES (?, CURRENT_TIMESTAMP)",
                    (name,),
                )
        connection.commit()


def _ensure_indexes(engine) -> None:
    """Crée les index de performance manquants sur une base déjà existante.

    Comme pour _migrate_stock_movements_schema, create_all() ne modifie
    jamais une table déjà créée — les index ajoutés au modèle après coup
    doivent être créés explicitement ici. CREATE INDEX IF NOT EXISTS est
    naturellement idempotent (no-op sur une base neuve où create_all()
    les a déjà créés, et sur une base déjà migrée).
    """
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_displays_brand ON displays (brand)",
        "CREATE INDEX IF NOT EXISTS ix_displays_phone_model ON displays (phone_model)",
        "CREATE INDEX IF NOT EXISTS ix_displays_category ON displays (category)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_display_id ON stock_movements (display_id)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_created_at ON stock_movements (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_is_sale ON stock_movements (is_sale)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_repair_id ON stock_movements (repair_id)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_supplier_id ON stock_movements (supplier_id)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_reseller_id ON stock_movements (reseller_id)",
        "CREATE INDEX IF NOT EXISTS ix_repairs_created_at ON repairs (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_repair_items_repair_id ON repair_items (repair_id)",
        "CREATE INDEX IF NOT EXISTS ix_repair_items_display_id ON repair_items (display_id)",
        "CREATE INDEX IF NOT EXISTS ix_resellers_name ON resellers (name)",
        "CREATE INDEX IF NOT EXISTS ix_suppliers_name ON suppliers (name)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_categories_name ON categories (name)",
    ]
    with engine.connect() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.commit()


def init_engine(database_path: Path | str | None = None):
    """Crée le moteur, active les clés étrangères et crée les tables manquantes."""
    global _engine, _session_factory

    url = f"sqlite:///{database_path or get_database_path()}"
    _engine = create_engine(url, future=True)
    event.listen(_engine, "connect", _enable_foreign_keys)

    from app import models  # noqa: F401 — assure l'enregistrement des modèles

    Base.metadata.create_all(_engine)
    _migrate_stock_movements_schema(_engine)
    _migrate_displays_schema(_engine)
    _migrate_repairs_schema(_engine)
    _seed_default_categories(_engine)
    _ensure_indexes(_engine)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker:
    if _session_factory is None:
        init_engine()
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Fournit une session avec commit/rollback automatique.

    Une opération incomplète ne doit jamais laisser le stock dans un état
    incohérent : toute exception annule la transaction en entier.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
