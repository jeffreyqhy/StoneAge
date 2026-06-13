from __future__ import annotations

from .database import MaterialDatabase


def get_diamond_per_rmb(db: MaterialDatabase) -> float:
    return db.diamond_per_rmb()


def set_diamond_per_rmb(db: MaterialDatabase, value: float) -> None:
    db.set_diamond_per_rmb(value)
