from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .normalizer import normalize_item_name


TABLES = [
    "items",
    "item_aliases",
    "sources",
    "source_items",
    "recipes",
    "recipe_materials",
    "upgrade_steps",
    "upgrade_step_materials",
    "item_prices",
    "item_price_history",
    "official_site_content",
    "app_settings",
    "import_batches",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_db_path(workspace: str | Path) -> Path:
    return Path(workspace) / "data" / "stoneage_materials.db"


class MaterialDatabase:
    def __init__(self, workspace: str | Path, db_path: str | Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.db_path = Path(db_path) if db_path else default_db_path(self.workspace)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    category TEXT,
                    notes TEXT,
                    icon_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS item_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL DEFAULT 'excel',
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS source_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    item_name TEXT NOT NULL,
                    normalized_item_name TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    parsed_quantity REAL,
                    source_id INTEGER NOT NULL,
                    sheet_name TEXT,
                    row_index INTEGER,
                    col_index INTEGER,
                    import_batch_id INTEGER,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL,
                    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
                    FOREIGN KEY(import_batch_id) REFERENCES import_batches(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_item_id INTEGER,
                    product_name TEXT NOT NULL,
                    normalized_product_name TEXT NOT NULL,
                    recipe_type TEXT,
                    category TEXT,
                    success_rate REAL NOT NULL DEFAULT 1,
                    output_quantity REAL NOT NULL DEFAULT 1,
                    diamond_cost REAL NOT NULL DEFAULT 0,
                    coin_cost REAL NOT NULL DEFAULT 0,
                    failure_consumes_materials INTEGER NOT NULL DEFAULT 1,
                    failure_consumes_diamonds INTEGER NOT NULL DEFAULT 1,
                    failure_consumes_coin INTEGER NOT NULL DEFAULT 1,
                    screenshot_path TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(product_item_id) REFERENCES items(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS recipe_materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipe_id INTEGER NOT NULL,
                    material_item_id INTEGER,
                    material_name TEXT NOT NULL,
                    normalized_material_name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
                    FOREIGN KEY(material_item_id) REFERENCES items(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS upgrade_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    equipment_item_id INTEGER,
                    equipment_name TEXT NOT NULL,
                    normalized_equipment_name TEXT NOT NULL,
                    from_level INTEGER NOT NULL,
                    to_level INTEGER NOT NULL,
                    success_rate REAL NOT NULL DEFAULT 1,
                    diamond_cost REAL NOT NULL DEFAULT 0,
                    coin_cost REAL NOT NULL DEFAULT 0,
                    failure_consumes_materials INTEGER NOT NULL DEFAULT 1,
                    failure_consumes_diamonds INTEGER NOT NULL DEFAULT 1,
                    failure_downgrades_level INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(equipment_item_id) REFERENCES items(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS upgrade_step_materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    upgrade_step_id INTEGER NOT NULL,
                    material_item_id INTEGER,
                    material_name TEXT NOT NULL,
                    normalized_material_name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    notes TEXT,
                    FOREIGN KEY(upgrade_step_id) REFERENCES upgrade_steps(id) ON DELETE CASCADE,
                    FOREIGN KEY(material_item_id) REFERENCES items(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS item_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    item_name TEXT NOT NULL,
                    normalized_item_name TEXT NOT NULL UNIQUE,
                    price_diamonds REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    price_source TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS item_price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    item_name TEXT NOT NULL,
                    normalized_item_name TEXT NOT NULL,
                    old_price_diamonds REAL,
                    new_price_diamonds REAL NOT NULL,
                    price_source TEXT,
                    notes TEXT,
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS official_site_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL,
                    subtitle TEXT,
                    body TEXT,
                    url TEXT,
                    badge TEXT,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(section, item_key)
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_source_items_norm ON source_items(normalized_item_name);
                CREATE INDEX IF NOT EXISTS idx_source_items_source ON source_items(source_id);
                CREATE INDEX IF NOT EXISTS idx_recipes_product ON recipes(normalized_product_name);
                CREATE INDEX IF NOT EXISTS idx_upgrade_steps_equipment ON upgrade_steps(normalized_equipment_name, from_level, to_level);
                CREATE INDEX IF NOT EXISTS idx_price_norm ON item_prices(normalized_item_name);
                CREATE INDEX IF NOT EXISTS idx_official_site_content_section ON official_site_content(section, sort_order, id);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO app_settings(key, value, updated_at)
                VALUES('diamond_per_rmb', '500', ?)
                """,
                (now_iso(),),
            )

    def clear_excel_imports(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM source_items WHERE import_batch_id IS NOT NULL")
            conn.execute(
                """
                DELETE FROM sources
                WHERE source_type = 'excel'
                  AND id NOT IN (SELECT DISTINCT source_id FROM source_items)
                """
            )

    def create_import_batch(self, file_path: str | Path, notes: str = "") -> int:
        path = Path(file_path)
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO import_batches(file_path, file_name, imported_at, notes) VALUES(?, ?, ?, ?)",
                (str(path), path.name, now_iso(), notes),
            )
            return int(cur.lastrowid)

    def ensure_item(self, name: str, *, category: str = "", notes: str = "", icon_path: str | None = None) -> int:
        item_name = normalize_item_name(name)
        if not item_name:
            raise ValueError("物品名不能为空")
        normalized = normalize_item_name(item_name)
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM items WHERE normalized_name = ?", (normalized,)).fetchone()
            if row:
                updates: dict[str, Any] = {"updated_at": now}
                if category:
                    updates["category"] = category
                if notes:
                    updates["notes"] = notes
                if icon_path:
                    updates["icon_path"] = icon_path
                if updates:
                    assignments = ", ".join(f"{key} = ?" for key in updates)
                    conn.execute(f"UPDATE items SET {assignments} WHERE id = ?", [*updates.values(), int(row["id"])])
                return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO items(name, normalized_name, category, notes, icon_path, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (item_name, normalized, category, notes, icon_path, now, now),
            )
            return int(cur.lastrowid)

    def resolve_item(self, name: str) -> sqlite3.Row | None:
        normalized = normalize_item_name(name)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE normalized_name = ?", (normalized,)).fetchone()
            if row:
                return row
            alias = conn.execute(
                """
                SELECT items.*
                FROM item_aliases
                JOIN items ON items.id = item_aliases.item_id
                WHERE item_aliases.normalized_alias = ?
                """,
                (normalized,),
            ).fetchone()
            return alias

    def canonical_name(self, name: str) -> str:
        row = self.resolve_item(name)
        return str(row["name"]) if row else normalize_item_name(name)

    def ensure_source(self, name: str, *, source_type: str = "excel", notes: str = "") -> int:
        source_name = normalize_item_name(name)
        if not source_name:
            raise ValueError("出处名称不能为空")
        normalized = normalize_item_name(source_name)
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM sources WHERE normalized_name = ?", (normalized,)).fetchone()
            if row:
                conn.execute("UPDATE sources SET updated_at = ? WHERE id = ?", (now, int(row["id"])))
                return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO sources(name, normalized_name, source_type, notes, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (source_name, normalized, source_type, notes, now, now),
            )
            return int(cur.lastrowid)

    def source_item_exists(
        self,
        *,
        normalized_item_name: str,
        raw_text: str,
        source_id: int,
        sheet_name: str,
        row_index: int,
        col_index: int,
    ) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM source_items
                WHERE normalized_item_name = ? AND raw_text = ? AND source_id = ?
                  AND COALESCE(sheet_name, '') = ? AND COALESCE(row_index, 0) = ?
                  AND COALESCE(col_index, 0) = ?
                LIMIT 1
                """,
                (normalized_item_name, raw_text, int(source_id), sheet_name, int(row_index), int(col_index)),
            ).fetchone()
            return row is not None

    def add_source_item(
        self,
        *,
        item_name: str,
        raw_text: str,
        source_name: str,
        parsed_quantity: float | int | None = None,
        sheet_name: str = "",
        row_index: int = 0,
        col_index: int = 0,
        import_batch_id: int | None = None,
        source_type: str = "excel",
        notes: str = "",
        skip_duplicate: bool = True,
    ) -> int | None:
        clean_item = normalize_item_name(item_name)
        if not clean_item:
            return None
        item_id = self.ensure_item(clean_item)
        source_id = self.ensure_source(source_name, source_type=source_type)
        normalized = normalize_item_name(clean_item)
        if skip_duplicate and self.source_item_exists(
            normalized_item_name=normalized,
            raw_text=str(raw_text),
            source_id=source_id,
            sheet_name=sheet_name,
            row_index=row_index,
            col_index=col_index,
        ):
            return None
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO source_items(
                    item_id, item_name, normalized_item_name, raw_text, parsed_quantity, source_id,
                    sheet_name, row_index, col_index, import_batch_id, notes, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    clean_item,
                    normalized,
                    str(raw_text),
                    parsed_quantity,
                    source_id,
                    sheet_name,
                    int(row_index),
                    int(col_index),
                    import_batch_id,
                    notes,
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def search_sources(self, query: str = "", *, limit: int = 500) -> list[dict[str, Any]]:
        normalized = normalize_item_name(query)
        canonical = self.canonical_name(query) if normalized else ""
        canonical_norm = normalize_item_name(canonical)
        params: list[Any] = []
        where = ""
        if normalized:
            like = f"%{normalized}%"
            raw_like = f"%{query.strip()}%"
            where = """
                WHERE source_items.normalized_item_name LIKE ?
                   OR source_items.raw_text LIKE ?
                   OR sources.name LIKE ?
                   OR source_items.normalized_item_name = ?
            """
            params = [like, raw_like, raw_like, canonical_norm]
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT source_items.*, sources.name AS source_name, sources.source_type
                FROM source_items
                JOIN sources ON sources.id = source_items.source_id
                {where}
                ORDER BY source_items.normalized_item_name, sources.name, sheet_name, row_index, col_index
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_source_items_by_source(self, query: str = "", *, limit: int = 500) -> list[dict[str, Any]]:
        normalized = normalize_item_name(query)
        params: list[Any] = []
        where = ""
        if normalized:
            where = """
                WHERE sources.normalized_name LIKE ?
                   OR sources.name LIKE ?
            """
            params = [f"%{normalized}%", f"%{query.strip()}%"]
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT source_items.*, sources.name AS source_name, sources.source_type
                FROM source_items
                JOIN sources ON sources.id = source_items.source_id
                {where}
                ORDER BY sources.name, source_items.normalized_item_name, sheet_name, row_index, col_index
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def all_source_names(self, query: str = "", *, limit: int = 2000) -> list[str]:
        normalized = normalize_item_name(query)
        where = ""
        params: list[Any] = []
        if normalized:
            where = "WHERE normalized_name LIKE ? OR name LIKE ?"
            params = [f"%{normalized}%", f"%{query.strip()}%"]
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT name FROM sources {where} ORDER BY name LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def list_source_drops(self, source_query: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        normalized = normalize_item_name(source_query)
        if not normalized:
            return []
        search_text = source_query.strip()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    MIN(source_items.id) AS id,
                    COALESCE(items.name, source_items.item_name) AS item_name,
                    source_items.normalized_item_name,
                    sources.name AS source_name,
                    GROUP_CONCAT(DISTINCT source_items.parsed_quantity) AS quantities,
                    COUNT(source_items.id) AS record_count,
                    COALESCE(items.icon_path, '') AS icon_path,
                    COALESCE(items.notes, '') AS item_notes,
                    item_prices.price_diamonds,
                    item_prices.is_active,
                    item_prices.price_source,
                    item_prices.updated_at AS price_updated_at
                FROM source_items
                JOIN sources ON sources.id = source_items.source_id
                LEFT JOIN items ON items.normalized_name = source_items.normalized_item_name
                LEFT JOIN item_prices ON item_prices.normalized_item_name = source_items.normalized_item_name
                WHERE sources.normalized_name LIKE ?
                   OR sources.name LIKE ?
                GROUP BY source_items.normalized_item_name, sources.id
                ORDER BY sources.name, item_name
                LIMIT ?
                """,
                (f"%{normalized}%", f"%{search_text}%", int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_materials(self, query: str = "", *, limit: int = 1000) -> list[dict[str, Any]]:
        normalized = normalize_item_name(query)
        search_text = query.strip()
        where = ""
        params: list[Any] = []
        if normalized:
            where = """
                WHERE items.normalized_name LIKE ?
                   OR items.name LIKE ?
                   OR COALESCE(items.notes, '') LIKE ?
                   OR COALESCE(item_prices.notes, '') LIKE ?
            """
            like_norm = f"%{normalized}%"
            like_text = f"%{search_text}%"
            params = [like_norm, like_text, like_text, like_text]
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                WITH source_summary AS (
                    SELECT
                        source_items.normalized_item_name,
                        GROUP_CONCAT(DISTINCT sources.name) AS source_names,
                        COUNT(source_items.id) AS source_count
                    FROM source_items
                    JOIN sources ON sources.id = source_items.source_id
                    GROUP BY source_items.normalized_item_name
                )
                SELECT
                    items.*,
                    COALESCE(source_summary.source_names, '') AS source_names,
                    COALESCE(source_summary.source_count, 0) AS source_count,
                    item_prices.id AS price_id,
                    item_prices.price_diamonds,
                    item_prices.is_active,
                    item_prices.price_source,
                    item_prices.notes AS price_notes,
                    item_prices.updated_at AS price_updated_at
                FROM items
                LEFT JOIN source_summary ON source_summary.normalized_item_name = items.normalized_name
                LEFT JOIN item_prices ON item_prices.normalized_item_name = items.normalized_name
                {where}
                ORDER BY items.name
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def item_sources(self, item_name: str, *, limit: int = 500) -> list[dict[str, Any]]:
        normalized = normalize_item_name(self.canonical_name(item_name))
        if not normalized:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT source_items.*, sources.name AS source_name, sources.source_type
                FROM source_items
                JOIN sources ON sources.id = source_items.source_id
                WHERE source_items.normalized_item_name = ?
                ORDER BY sources.name, source_items.raw_text, source_items.created_at DESC
                LIMIT ?
                """,
                (normalized, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_item_details(
        self,
        item_name: str,
        *,
        category: str | None = None,
        notes: str | None = None,
        icon_path: str | None = None,
    ) -> int:
        clean_item = normalize_item_name(item_name)
        if not clean_item:
            raise ValueError("材料名不能为空")
        item_id = self.ensure_item(clean_item)
        fields: dict[str, Any] = {"updated_at": now_iso()}
        if category is not None:
            fields["category"] = str(category)
        if notes is not None:
            fields["notes"] = str(notes)
        if icon_path is not None:
            fields["icon_path"] = str(icon_path)
        with self.connect() as conn:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            conn.execute(f"UPDATE items SET {assignments} WHERE id = ?", [*fields.values(), item_id])
        return item_id

    def delete_source_items(self, ids: Iterable[int]) -> int:
        id_list = [int(item) for item in ids]
        if not id_list:
            return 0
        placeholders = ",".join("?" for _ in id_list)
        with self.connect() as conn:
            cur = conn.execute(f"DELETE FROM source_items WHERE id IN ({placeholders})", id_list)
            return int(cur.rowcount)

    def add_alias(self, item_name: str, alias: str) -> int:
        item_id = self.ensure_item(item_name)
        alias_text = normalize_item_name(alias)
        if not alias_text:
            raise ValueError("别名不能为空")
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO item_aliases(item_id, alias, normalized_alias, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (item_id, alias_text, normalize_item_name(alias_text), now_iso()),
            )
            return int(cur.lastrowid)

    def list_aliases(self, query: str = "") -> list[dict[str, Any]]:
        normalized = normalize_item_name(query)
        params: list[Any] = []
        where = ""
        if normalized:
            like = f"%{normalized}%"
            where = "WHERE items.normalized_name LIKE ? OR item_aliases.normalized_alias LIKE ?"
            params = [like, like]
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT item_aliases.id, items.name AS item_name, item_aliases.alias, item_aliases.created_at
                FROM item_aliases
                JOIN items ON items.id = item_aliases.item_id
                {where}
                ORDER BY items.name, item_aliases.alias
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_alias(self, alias_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM item_aliases WHERE id = ?", (int(alias_id),))
            return cur.rowcount > 0

    def set_price(
        self,
        item_name: str,
        price_diamonds: float,
        *,
        price_source: str = "手动录入",
        notes: str = "",
        is_active: bool = True,
    ) -> int:
        clean_item = normalize_item_name(item_name)
        if not clean_item:
            raise ValueError("物品名不能为空")
        price = float(price_diamonds)
        if price < 0:
            raise ValueError("市场价格不能为负数")
        item_id = self.ensure_item(clean_item)
        normalized = normalize_item_name(clean_item)
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM item_prices WHERE normalized_item_name = ?", (normalized,)).fetchone()
            old_price = float(row["price_diamonds"]) if row else None
            if row:
                conn.execute(
                    """
                    UPDATE item_prices
                    SET item_id = ?, item_name = ?, price_diamonds = ?, is_active = ?,
                        price_source = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (item_id, clean_item, price, int(is_active), price_source, notes, now, int(row["id"])),
                )
                price_id = int(row["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO item_prices(
                        item_id, item_name, normalized_item_name, price_diamonds, is_active,
                        price_source, notes, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (item_id, clean_item, normalized, price, int(is_active), price_source, notes, now, now),
                )
                price_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO item_price_history(
                    item_id, item_name, normalized_item_name, old_price_diamonds, new_price_diamonds,
                    price_source, notes, changed_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, clean_item, normalized, old_price, price, price_source, notes, now),
            )
            return price_id

    def get_price(self, item_name: str) -> dict[str, Any] | None:
        canonical = self.canonical_name(item_name)
        normalized = normalize_item_name(canonical)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM item_prices WHERE normalized_item_name = ? AND is_active = 1",
                (normalized,),
            ).fetchone()
            if row:
                return dict(row)
            if canonical != normalize_item_name(item_name):
                row = conn.execute(
                    "SELECT * FROM item_prices WHERE normalized_item_name = ? AND is_active = 1",
                    (normalize_item_name(item_name),),
                ).fetchone()
                return dict(row) if row else None
        return None

    def list_prices(self, query: str = "") -> list[dict[str, Any]]:
        normalized = normalize_item_name(query)
        params: list[Any] = []
        where = ""
        if normalized:
            like = f"%{normalized}%"
            where = "WHERE normalized_item_name LIKE ? OR item_name LIKE ? OR notes LIKE ?"
            params = [like, f"%{query.strip()}%", f"%{query.strip()}%"]
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM item_prices
                {where}
                ORDER BY item_name
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_price(self, price_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM item_prices WHERE id = ?", (int(price_id),))
            return cur.rowcount > 0

    def price_history(self, item_name: str) -> list[dict[str, Any]]:
        normalized = normalize_item_name(self.canonical_name(item_name))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM item_price_history
                WHERE normalized_item_name = ?
                ORDER BY changed_at DESC, id DESC
                """,
                (normalized,),
            ).fetchall()
        return [dict(row) for row in rows]

    def diamond_per_rmb(self) -> float:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'diamond_per_rmb'").fetchone()
        try:
            value = float(row["value"]) if row else 500.0
        except (TypeError, ValueError):
            value = 500.0
        return value if value > 0 else 500.0

    def set_diamond_per_rmb(self, value: float) -> None:
        ratio = float(value)
        if ratio <= 0:
            raise ValueError("钻石兑换比例必须大于 0")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES('diamond_per_rmb', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(ratio), now_iso()),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (str(key),)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(key), str(value), now_iso()),
            )

    def official_site_content_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM official_site_content").fetchone()
        return int(row["count"] if row else 0)

    def upsert_official_site_content(self, entries: Iterable[dict[str, Any]]) -> None:
        now = now_iso()
        with self.connect() as conn:
            for entry in entries:
                section = str(entry.get("section") or "").strip()
                item_key = str(entry.get("item_key") or "").strip()
                title = str(entry.get("title") or "").strip()
                if not section or not item_key or not title:
                    continue
                meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
                conn.execute(
                    """
                    INSERT INTO official_site_content(
                        section, item_key, sort_order, title, subtitle, body, url, badge,
                        meta_json, is_active, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(section, item_key) DO UPDATE SET
                        sort_order = excluded.sort_order,
                        title = excluded.title,
                        subtitle = excluded.subtitle,
                        body = excluded.body,
                        url = excluded.url,
                        badge = excluded.badge,
                        meta_json = excluded.meta_json,
                        is_active = excluded.is_active,
                        updated_at = excluded.updated_at
                    """,
                    (
                        section,
                        item_key,
                        int(entry.get("sort_order") or 0),
                        title,
                        str(entry.get("subtitle") or ""),
                        str(entry.get("body") or ""),
                        str(entry.get("url") or ""),
                        str(entry.get("badge") or ""),
                        json.dumps(meta, ensure_ascii=False, sort_keys=True),
                        1 if entry.get("is_active", True) else 0,
                        now,
                        now,
                    ),
                )

    def list_official_site_content(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        where = "WHERE is_active = 1" if active_only else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM official_site_content
                {where}
                ORDER BY section, sort_order, id
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["meta"] = json.loads(str(item.pop("meta_json") or "{}"))
            except json.JSONDecodeError:
                item["meta"] = {}
            result.append(item)
        return result

    def diamonds_to_rmb(self, diamonds: float | int | None) -> float:
        if diamonds is None:
            return 0.0
        return float(diamonds) / self.diamond_per_rmb()

    def all_item_names(self, query: str = "", *, limit: int = 2000) -> list[str]:
        normalized = normalize_item_name(query)
        params: list[Any] = []
        where = ""
        if normalized:
            like = f"%{normalized}%"
            where = "WHERE normalized_name LIKE ? OR name LIKE ?"
            params = [like, f"%{query.strip()}%"]
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT name FROM items {where} ORDER BY name LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def save_recipe(self, recipe: dict[str, Any], materials: list[dict[str, Any]], recipe_id: int | None = None) -> int:
        product_name = normalize_item_name(recipe.get("product_name"))
        if not product_name:
            raise ValueError("成品名称不能为空")
        success_rate = _rate_value(recipe.get("success_rate", 1))
        output_quantity = max(0.000001, float(recipe.get("output_quantity") or 1))
        product_item_id = self.ensure_item(product_name, category=str(recipe.get("category") or ""))
        now = now_iso()
        fields = {
            "product_item_id": product_item_id,
            "product_name": product_name,
            "normalized_product_name": normalize_item_name(product_name),
            "recipe_type": str(recipe.get("recipe_type") or "打造"),
            "category": str(recipe.get("category") or "其他"),
            "success_rate": success_rate,
            "output_quantity": output_quantity,
            "diamond_cost": max(0.0, float(recipe.get("diamond_cost") or 0)),
            "coin_cost": max(0.0, float(recipe.get("coin_cost") or 0)),
            "failure_consumes_materials": int(bool(recipe.get("failure_consumes_materials", True))),
            "failure_consumes_diamonds": int(bool(recipe.get("failure_consumes_diamonds", True))),
            "failure_consumes_coin": int(bool(recipe.get("failure_consumes_coin", True))),
            "screenshot_path": str(recipe.get("screenshot_path") or ""),
            "notes": str(recipe.get("notes") or ""),
            "updated_at": now,
        }
        prepared_materials: list[dict[str, Any]] = []
        for material in materials:
            material_name = normalize_item_name(material.get("material_name"))
            if not material_name:
                continue
            quantity = float(material.get("quantity") or 0)
            if quantity <= 0:
                continue
            prepared_materials.append(
                {
                    "material_item_id": self.ensure_item(material_name),
                    "material_name": material_name,
                    "quantity": quantity,
                    "notes": str(material.get("notes") or ""),
                }
            )
        with self.connect() as conn:
            if recipe_id:
                assignments = ", ".join(f"{key} = ?" for key in fields)
                conn.execute(f"UPDATE recipes SET {assignments} WHERE id = ?", [*fields.values(), int(recipe_id)])
                saved_id = int(recipe_id)
                conn.execute("DELETE FROM recipe_materials WHERE recipe_id = ?", (saved_id,))
            else:
                cur = conn.execute(
                    f"""
                    INSERT INTO recipes({', '.join([*fields.keys(), 'created_at'])})
                    VALUES({', '.join('?' for _ in [*fields.keys(), 'created_at'])})
                    """,
                    [*fields.values(), now],
                )
                saved_id = int(cur.lastrowid)
            for material in prepared_materials:
                conn.execute(
                    """
                    INSERT INTO recipe_materials(
                        recipe_id, material_item_id, material_name, normalized_material_name,
                        quantity, notes, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        saved_id,
                        material["material_item_id"],
                        material["material_name"],
                        normalize_item_name(material["material_name"]),
                        material["quantity"],
                        material["notes"],
                        now,
                        now,
                    ),
                )
            return saved_id

    def list_recipes(self, query: str = "", category: str = "全部", recipe_type: str = "全部") -> list[dict[str, Any]]:
        normalized = normalize_item_name(query)
        where: list[str] = []
        params: list[Any] = []
        if normalized:
            where.append("(recipes.normalized_product_name LIKE ? OR recipes.product_name LIKE ? OR recipes.notes LIKE ?)")
            params.extend([f"%{normalized}%", f"%{query.strip()}%", f"%{query.strip()}%"])
        if category and category != "全部":
            where.append("category = ?")
            params.append(category)
        if recipe_type and recipe_type != "全部":
            where.append("recipe_type = ?")
            params.append(recipe_type)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT recipes.*, COUNT(recipe_materials.id) AS material_count
                FROM recipes
                LEFT JOIN recipe_materials ON recipe_materials.recipe_id = recipes.id
                {where_sql}
                GROUP BY recipes.id
                ORDER BY recipes.product_name
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_recipe(self, recipe_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (int(recipe_id),)).fetchone()
            if not row:
                return None
            materials = conn.execute(
                "SELECT * FROM recipe_materials WHERE recipe_id = ? ORDER BY id",
                (int(recipe_id),),
            ).fetchall()
        data = dict(row)
        data["materials"] = [dict(item) for item in materials]
        return data

    def find_recipe(self, product_name: str) -> dict[str, Any] | None:
        normalized = normalize_item_name(self.canonical_name(product_name))
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM recipes
                WHERE normalized_product_name = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        return self.get_recipe(int(row["id"])) if row else None

    def delete_recipe(self, recipe_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM recipes WHERE id = ?", (int(recipe_id),))
            return cur.rowcount > 0

    def save_upgrade_step(
        self,
        step: dict[str, Any],
        materials: list[dict[str, Any]],
        step_id: int | None = None,
    ) -> int:
        equipment_name = normalize_item_name(step.get("equipment_name"))
        if not equipment_name:
            raise ValueError("装备名称不能为空")
        from_level = int(step.get("from_level") or 0)
        to_level = int(step.get("to_level") or 0)
        if to_level <= from_level:
            raise ValueError("目标等级必须大于当前等级")
        equipment_item_id = self.ensure_item(equipment_name, category="装备")
        now = now_iso()
        fields = {
            "equipment_item_id": equipment_item_id,
            "equipment_name": equipment_name,
            "normalized_equipment_name": normalize_item_name(equipment_name),
            "from_level": from_level,
            "to_level": to_level,
            "success_rate": _rate_value(step.get("success_rate", 1)),
            "diamond_cost": max(0.0, float(step.get("diamond_cost") or 0)),
            "coin_cost": max(0.0, float(step.get("coin_cost") or 0)),
            "failure_consumes_materials": int(bool(step.get("failure_consumes_materials", True))),
            "failure_consumes_diamonds": int(bool(step.get("failure_consumes_diamonds", True))),
            "failure_downgrades_level": int(bool(step.get("failure_downgrades_level", False))),
            "notes": str(step.get("notes") or ""),
            "updated_at": now,
        }
        prepared_materials: list[dict[str, Any]] = []
        for material in materials:
            material_name = normalize_item_name(material.get("material_name"))
            if not material_name:
                continue
            quantity = float(material.get("quantity") or 0)
            if quantity <= 0:
                continue
            prepared_materials.append(
                {
                    "material_item_id": self.ensure_item(material_name),
                    "material_name": material_name,
                    "quantity": quantity,
                    "notes": str(material.get("notes") or ""),
                }
            )
        with self.connect() as conn:
            if step_id:
                assignments = ", ".join(f"{key} = ?" for key in fields)
                conn.execute(f"UPDATE upgrade_steps SET {assignments} WHERE id = ?", [*fields.values(), int(step_id)])
                saved_id = int(step_id)
                conn.execute("DELETE FROM upgrade_step_materials WHERE upgrade_step_id = ?", (saved_id,))
            else:
                cur = conn.execute(
                    f"""
                    INSERT INTO upgrade_steps({', '.join([*fields.keys(), 'created_at'])})
                    VALUES({', '.join('?' for _ in [*fields.keys(), 'created_at'])})
                    """,
                    [*fields.values(), now],
                )
                saved_id = int(cur.lastrowid)
            for material in prepared_materials:
                conn.execute(
                    """
                    INSERT INTO upgrade_step_materials(
                        upgrade_step_id, material_item_id, material_name, normalized_material_name,
                        quantity, notes
                    )
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        saved_id,
                        material["material_item_id"],
                        material["material_name"],
                        normalize_item_name(material["material_name"]),
                        material["quantity"],
                        material["notes"],
                    ),
                )
            return saved_id

    def list_upgrade_steps(self, query: str = "") -> list[dict[str, Any]]:
        normalized = normalize_item_name(query)
        where = ""
        params: list[Any] = []
        if normalized:
            where = "WHERE upgrade_steps.normalized_equipment_name LIKE ? OR upgrade_steps.equipment_name LIKE ? OR upgrade_steps.notes LIKE ?"
            params = [f"%{normalized}%", f"%{query.strip()}%", f"%{query.strip()}%"]
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT upgrade_steps.*, COUNT(upgrade_step_materials.id) AS material_count
                FROM upgrade_steps
                LEFT JOIN upgrade_step_materials ON upgrade_step_materials.upgrade_step_id = upgrade_steps.id
                {where}
                GROUP BY upgrade_steps.id
                ORDER BY equipment_name, from_level, to_level
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_upgrade_step(self, step_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM upgrade_steps WHERE id = ?", (int(step_id),)).fetchone()
            if not row:
                return None
            materials = conn.execute(
                "SELECT * FROM upgrade_step_materials WHERE upgrade_step_id = ? ORDER BY id",
                (int(step_id),),
            ).fetchall()
        data = dict(row)
        data["materials"] = [dict(item) for item in materials]
        return data

    def find_upgrade_step(self, equipment_name: str, from_level: int, to_level: int) -> dict[str, Any] | None:
        normalized = normalize_item_name(self.canonical_name(equipment_name))
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM upgrade_steps
                WHERE normalized_equipment_name = ? AND from_level = ? AND to_level = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (normalized, int(from_level), int(to_level)),
            ).fetchone()
        return self.get_upgrade_step(int(row["id"])) if row else None

    def delete_upgrade_step(self, step_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM upgrade_steps WHERE id = ?", (int(step_id),))
            return cur.rowcount > 0

    def export_json(self, path: str | Path) -> None:
        payload: dict[str, Any] = {"version": 1, "exported_at": now_iso(), "tables": {}}
        with self.connect() as conn:
            for table in TABLES:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                payload["tables"][table] = [dict(row) for row in rows]
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_json(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        tables = payload.get("tables") or {}
        if not isinstance(tables, dict):
            raise ValueError("JSON 备份格式不正确")
        self.ensure_schema()
        with self.connect() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            for table in reversed(TABLES):
                conn.execute(f"DELETE FROM {table}")
            for table in TABLES:
                rows = tables.get(table) or []
                if not rows:
                    continue
                columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    keys = [key for key in columns if key in row]
                    if not keys:
                        continue
                    conn.execute(
                        f"INSERT INTO {table}({', '.join(keys)}) VALUES({', '.join('?' for _ in keys)})",
                        [row[key] for key in keys],
                    )
            conn.execute("PRAGMA foreign_keys = ON")
        self.ensure_schema()

    def export_prices_csv(self, path: str | Path) -> None:
        rows = self.list_prices()
        _write_csv(path, rows, ["item_name", "price_diamonds", "is_active", "price_source", "notes", "updated_at"])

    def import_prices_csv(self, path: str | Path) -> int:
        count = 0
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = row.get("item_name") or row.get("物品名") or row.get("name") or ""
                price_text = row.get("price_diamonds") or row.get("钻石价格") or row.get("price") or ""
                if not name.strip() or not str(price_text).strip():
                    continue
                self.set_price(
                    name,
                    float(price_text),
                    price_source=row.get("price_source") or row.get("价格来源") or "CSV导入",
                    notes=row.get("notes") or row.get("备注") or "",
                    is_active=str(row.get("is_active", "1")).strip() not in {"0", "false", "False", "否"},
                )
                count += 1
        return count

    def export_sources_csv(self, path: str | Path) -> None:
        rows = self.search_sources("", limit=1000000)
        _write_csv(
            path,
            rows,
            ["item_name", "source_name", "raw_text", "parsed_quantity", "sheet_name", "row_index", "col_index", "notes"],
        )

    def export_recipes_csv(self, path: str | Path) -> None:
        rows: list[dict[str, Any]] = []
        for recipe in self.list_recipes():
            full = self.get_recipe(int(recipe["id"])) or recipe
            rows.append(
                {
                    "product_name": full.get("product_name"),
                    "category": full.get("category"),
                    "recipe_type": full.get("recipe_type"),
                    "success_rate": full.get("success_rate"),
                    "output_quantity": full.get("output_quantity"),
                    "diamond_cost": full.get("diamond_cost"),
                    "materials_json": json.dumps(full.get("materials") or [], ensure_ascii=False),
                    "notes": full.get("notes"),
                }
            )
        _write_csv(path, rows, ["product_name", "category", "recipe_type", "success_rate", "output_quantity", "diamond_cost", "materials_json", "notes"])

    def export_upgrades_csv(self, path: str | Path) -> None:
        rows: list[dict[str, Any]] = []
        for step in self.list_upgrade_steps():
            full = self.get_upgrade_step(int(step["id"])) or step
            rows.append(
                {
                    "equipment_name": full.get("equipment_name"),
                    "from_level": full.get("from_level"),
                    "to_level": full.get("to_level"),
                    "success_rate": full.get("success_rate"),
                    "diamond_cost": full.get("diamond_cost"),
                    "materials_json": json.dumps(full.get("materials") or [], ensure_ascii=False),
                    "notes": full.get("notes"),
                }
            )
        _write_csv(path, rows, ["equipment_name", "from_level", "to_level", "success_rate", "diamond_cost", "materials_json", "notes"])


def _write_csv(path: str | Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _rate_value(value: Any) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        rate = 1.0
    if rate > 1:
        rate = rate / 100.0
    if rate <= 0:
        raise ValueError("成功率必须大于 0")
    if rate > 1:
        raise ValueError("成功率不能超过 100%")
    return rate
