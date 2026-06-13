from __future__ import annotations

from pathlib import Path

from .database import MaterialDatabase


def export_database_json(db: MaterialDatabase, path: str | Path) -> None:
    db.export_json(path)


def import_database_json(db: MaterialDatabase, path: str | Path) -> None:
    db.import_json(path)


def export_price_csv(db: MaterialDatabase, path: str | Path) -> None:
    db.export_prices_csv(path)


def import_price_csv(db: MaterialDatabase, path: str | Path) -> int:
    return db.import_prices_csv(path)
