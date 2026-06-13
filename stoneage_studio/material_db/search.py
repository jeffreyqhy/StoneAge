from __future__ import annotations

from typing import Any

from .database import MaterialDatabase


def search_material_sources(db: MaterialDatabase, query: str, *, limit: int = 500) -> list[dict[str, Any]]:
    return db.search_sources(query, limit=limit)


def search_item_names(db: MaterialDatabase, query: str = "", *, limit: int = 2000) -> list[str]:
    return db.all_item_names(query, limit=limit)
