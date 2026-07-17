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
        }

        for column_name, ddl_type in columns_to_add.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE stock_movements ADD COLUMN {column_name} {ddl_type}"
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
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_display_id ON stock_movements (display_id)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_created_at ON stock_movements (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_is_sale ON stock_movements (is_sale)",
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
