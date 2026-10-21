"""Connexion SQLite et gestion des sessions SQLAlchemy."""

from __future__ import annotations

import hashlib
import secrets
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
            "movement_batch_id": "INTEGER",
            "linked_batch_id": "INTEGER",
            "cashier_name": "VARCHAR(100) NOT NULL DEFAULT ''",
            "versement_cents": "INTEGER NOT NULL DEFAULT 0",
            "remise_cents": "INTEGER NOT NULL DEFAULT 0",
            "reseller_balance_before_cents": "INTEGER NOT NULL DEFAULT 0",
            "supplier_versement_cents": "INTEGER NOT NULL DEFAULT 0",
            "supplier_balance_before_cents": "INTEGER NOT NULL DEFAULT 0",
            "return_id": "INTEGER",
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


def _migrate_resellers_schema(engine) -> None:
    """Ajoute balance_cents à la table resellers si absent (installations antérieures)."""
    with engine.connect() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(resellers)").fetchall()
        }
        if "balance_cents" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE resellers ADD COLUMN balance_cents INTEGER NOT NULL DEFAULT 0"
            )
        connection.commit()


def _migrate_suppliers_schema(engine) -> None:
    """Ajoute balance_cents à la table suppliers si absent (installations antérieures)."""
    with engine.connect() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(suppliers)").fetchall()
        }
        if "balance_cents" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE suppliers ADD COLUMN balance_cents INTEGER NOT NULL DEFAULT 0"
            )
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


def _migrate_categories_schema(engine) -> None:
    """Ajoute la colonne `color` aux catégories si absente, puis donne une
    couleur de la palette à chaque catégorie qui n'en a pas encore (dans
    l'ordre de création). Idempotente : une couleur déjà choisie n'est
    jamais modifiée."""
    from app.services import CATEGORY_PALETTE

    with engine.connect() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(categories)").fetchall()
        }
        if "color" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE categories ADD COLUMN color VARCHAR(7) NOT NULL DEFAULT ''"
            )

        used = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT color FROM categories WHERE color != ''"
            ).fetchall()
        }
        uncolored = connection.exec_driver_sql(
            "SELECT id FROM categories WHERE color = '' ORDER BY id"
        ).fetchall()
        free = [color for color in CATEGORY_PALETTE if color not in used]
        for index, (category_id,) in enumerate(uncolored):
            color = free[index] if index < len(free) else CATEGORY_PALETTE[index % len(CATEGORY_PALETTE)]
            connection.exec_driver_sql(
                "UPDATE categories SET color = ? WHERE id = ?", (color, category_id)
            )
        connection.commit()


def _migrate_legacy_returns(engine) -> None:
    """Convertit les retours saisis avant les bons de retour (mouvements
    « Retour… » sans return_id) en ReturnDoc + ReturnLine, pour qu'ils
    apparaissent dans l'onglet Retours et comptent dans le bénéfice comme
    avant : prix et coût repris de la vente d'origine quand elle est
    connue, 0 sinon (même règle que l'ancien calcul). Les dettes ne sont
    pas modifiées — ces anciens retours ne les avaient jamais touchées.
    Idempotente : un mouvement converti reçoit son return_id.
    """
    with engine.connect() as connection:
        legacy = connection.exec_driver_sql(
            "SELECT id, display_id, change_quantity, reason, reseller_id, supplier_id,"
            " linked_batch_id, movement_batch_id, created_at, unit_purchase_price_cents"
            " FROM stock_movements"
            " WHERE reason LIKE 'Retour%' AND return_id IS NULL AND change_quantity != 0"
            " ORDER BY id"
        ).fetchall()

        for (movement_id, display_id, change, reason, reseller_id, supplier_id,
             linked_batch_id, movement_batch_id, created_at, movement_cost) in legacy:
            quantity = abs(change)
            note = reason.split(" — ", 1)[1] if " — " in reason else ""
            origin_id = None

            if change > 0:
                kind, settlement, status = "customer", "legacy", "done"
                price = cost = 0
                if linked_batch_id is not None:
                    origin = connection.exec_driver_sql(
                        "SELECT id, unit_sale_price_cents, unit_purchase_price_cents"
                        " FROM stock_movements WHERE movement_batch_id = ? AND display_id = ?"
                        " AND is_sale = 1 ORDER BY id LIMIT 1",
                        (linked_batch_id, display_id),
                    ).fetchone()
                    if origin is not None:
                        origin_id, price, cost = origin
            else:
                kind = "supplier"
                origin = None
                if linked_batch_id is not None:
                    origin = connection.exec_driver_sql(
                        "SELECT id, unit_purchase_price_cents FROM stock_movements"
                        " WHERE movement_batch_id = ? AND display_id = ? AND change_quantity > 0"
                        " ORDER BY id LIMIT 1",
                        (linked_batch_id, display_id),
                    ).fetchone()
                if origin is not None:
                    origin_id, cost = origin
                else:
                    cost = movement_cost or connection.exec_driver_sql(
                        "SELECT purchase_price_cents FROM displays WHERE id = ?", (display_id,)
                    ).fetchone()[0]
                price = cost
                # Ancienne résolution : mouvement « Remplacement… » ou
                # « Remboursement… » lié au retour (movement_batch_id ou id).
                resolution = connection.exec_driver_sql(
                    "SELECT reason FROM stock_movements WHERE linked_batch_id = ?"
                    " AND (reason LIKE 'Remplacement%' OR reason LIKE 'Remboursement%')"
                    " ORDER BY id LIMIT 1",
                    (movement_batch_id or movement_id,),
                ).fetchone()
                if resolution is None:
                    settlement, status = "exchange", "pending"
                elif resolution[0].startswith("Remplacement"):
                    settlement, status = "exchange", "done"
                else:
                    settlement, status = "cash", "done"

            cursor = connection.exec_driver_sql(
                "INSERT INTO returns (kind, reseller_id, supplier_id, source_batch_id, settlement,"
                " status, amount_cents, balance_before_cents, balance_after_cents, note,"
                " cashier_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, '', ?)",
                (kind, reseller_id if kind == "customer" else None,
                 supplier_id if kind == "supplier" else None, linked_batch_id, settlement,
                 status, price * quantity, note, created_at),
            )
            return_id = cursor.lastrowid
            connection.exec_driver_sql(
                "INSERT INTO return_lines (return_id, display_id, quantity, unit_price_cents,"
                " unit_cost_cents, condition, origin_movement_id)"
                " VALUES (?, ?, ?, ?, ?, 'good', ?)",
                (return_id, display_id, quantity, price, cost, origin_id),
            )
            connection.exec_driver_sql(
                "UPDATE stock_movements SET return_id = ? WHERE id = ?", (return_id, movement_id)
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


def _migrate_stock_batches_schema(engine) -> None:
    """Ajoute movement_batch_id à stock_batches si absent (installations antérieures)."""
    with engine.connect() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(stock_batches)").fetchall()
        }
        if "movement_batch_id" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE stock_batches ADD COLUMN movement_batch_id INTEGER"
            )
        connection.commit()


def _seed_stock_batches(engine) -> None:
    """Bootstrap FIFO : crée un lot unique par produit pour le stock existant.

    N'est exécuté qu'une seule fois (quand la table est vide). Les produits
    déjà en stock avant cette fonctionnalité sont regroupés en un lot unique
    daté au 01/01/2000 (le plus ancien possible) afin qu'ils soient toujours
    consommés en premiers lors des ventes suivantes.
    """
    with engine.connect() as connection:
        (count,) = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM stock_batches"
        ).fetchone()
        if count > 0:
            return

        rows = connection.exec_driver_sql(
            "SELECT id, quantity, purchase_price_cents FROM displays WHERE quantity > 0"
        ).fetchall()

        for display_id, quantity, purchase_price_cents in rows:
            connection.exec_driver_sql(
                """INSERT INTO stock_batches
                   (display_id, quantity_original, quantity_remaining,
                    unit_purchase_price_cents, created_at)
                   VALUES (?, ?, ?, ?, '2000-01-01 00:00:00')""",
                (display_id, quantity, quantity, purchase_price_cents),
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
        "CREATE INDEX IF NOT EXISTS ix_stock_batches_display_id ON stock_batches (display_id)",
        "CREATE INDEX IF NOT EXISTS ix_stock_batches_created_at ON stock_batches (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_stock_batches_movement_batch_id ON stock_batches (movement_batch_id)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_movement_batch_id ON stock_movements (movement_batch_id)",
        "CREATE INDEX IF NOT EXISTS ix_stock_movements_linked_batch_id ON stock_movements (linked_batch_id)",
    ]
    with engine.connect() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.commit()


COUNTER_RESELLER_NAME = "Client comptoir"


def _seed_counter_reseller(engine) -> None:
    """Crée le revendeur « Client comptoir » (clients de passage) : depuis
    la v2.3.0 toutes les ventes sont en gros, à un revendeur. Créé une seule
    fois : s'il est renommé ou supprimé, il n'est pas recréé."""
    with engine.connect() as connection:
        (already_seeded,) = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM resellers WHERE notes LIKE '%[comptoir]%' OR name = ?",
            (COUNTER_RESELLER_NAME,),
        ).fetchone()
        if not already_seeded:
            connection.exec_driver_sql(
                "INSERT INTO resellers (name, phone, address, notes, is_active, balance_cents,"
                " created_at, updated_at) VALUES (?, '', '',"
                " 'Clients de passage, payés sur place. [comptoir]', 1, 0,"
                " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (COUNTER_RESELLER_NAME,),
            )
        connection.commit()


def _seed_default_admin(engine) -> None:
    """Crée le compte admin par défaut si la table users est vide."""
    with engine.connect() as connection:
        (count,) = connection.exec_driver_sql("SELECT COUNT(*) FROM users").fetchone()
        if count == 0:
            salt = secrets.token_hex(16)
            pwd_hash = hashlib.sha256((salt + "admin123").encode("utf-8")).hexdigest()
            connection.exec_driver_sql(
                "INSERT INTO users (username, display_name, password_hash, password_salt, role, is_active, created_at)"
                " VALUES ('admin', 'Administrateur', ?, ?, 'admin', 1, CURRENT_TIMESTAMP)",
                (pwd_hash, salt),
            )
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
    _migrate_resellers_schema(_engine)
    _migrate_suppliers_schema(_engine)
    _seed_default_categories(_engine)
    _migrate_categories_schema(_engine)
    _migrate_stock_batches_schema(_engine)
    _ensure_indexes(_engine)
    _seed_stock_batches(_engine)
    _seed_default_admin(_engine)
    _seed_counter_reseller(_engine)
    _migrate_legacy_returns(_engine)
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
