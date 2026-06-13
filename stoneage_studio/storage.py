from __future__ import annotations

import json
import re
import shutil
import sqlite3
import math
import uuid
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Any


MAP_DIRS = [
    "raw",
    "annotated",
    "crops",
    "npc",
    "transition",
    "battle",
    "question",
    "loop_condition",
    "digit",
    "text",
    "unknown",
    "button",
    "coord",
]


TYPE_DIR = {
    "image": "crops",
    "digit": "digit",
    "text": "text",
    "npc": "npc",
    "target": "npc",
    "button": "button",
    "transition": "transition",
    "battle": "battle",
    "question": "question",
    "question_option": "question",
    "loop_condition": "loop_condition",
    "coord": "coord",
    "unknown": "unknown",
    "screenshot": "crops",
}

MAX_NORMAL_MOVEMENT_DELTA = 12


def now_compact() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def clean_single_digit_value(value: str) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d", text) else None


def movement_delta_is_plausible(delta: list[int] | tuple[int, int]) -> bool:
    """A normal StoneAge walking click should not look like a map jump or OCR glitch."""
    if len(delta) < 2:
        return False
    dx, dy = int(delta[0]), int(delta[1])
    return (dx != 0 or dy != 0) and abs(dx) + abs(dy) <= MAX_NORMAL_MOVEMENT_DELTA


def _json_list(value: Any, fallback: list[int]) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return list(fallback)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return [int(value[0]), int(value[1])]
        except (TypeError, ValueError):
            return list(fallback)
    return list(fallback)


def _angle_for_vector(vector: list[int]) -> float:
    dx, dy = int(vector[0]), int(vector[1])
    if dx == 0 and dy == 0:
        return 0.0
    return float(math.atan2(dy, dx))


def _direction_for_angle(angle: float | None) -> str:
    if angle is None:
        return "unknown"
    labels = ("E", "SE", "S", "SW", "W", "NW", "N", "NE")
    degrees = (math.degrees(float(angle)) + 360.0) % 360.0
    return labels[int((degrees + 22.5) // 45.0) % 8]


class ProjectStorage:
    def __init__(self, workspace: str | Path, project: str = "stoneage") -> None:
        self.workspace = Path(workspace)
        self.project = project
        self.root = self.workspace / "data" / "projects" / project
        self.db_path = self.root / "studio.sqlite3"
        self.ensure()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        maps_root = self.root / "assets" / "maps" / "map_001"
        for directory in MAP_DIRS:
            (maps_root / directory).mkdir(parents=True, exist_ok=True)

        for directory in [
            "templates/image",
            "templates/npc",
            "templates/buttons",
            "templates/transitions",
            "templates/battle_states",
            "ocr/digits/confirmed",
            "ocr/digits/pending_review",
            "ocr/text/confirmed",
            "ocr/text/pending_review",
            "ocr/scans",
            "questions/confirmed",
            "questions/pending_review",
            "deprecated",
            "unknown/pending_review",
            "unknown/rejected",
        ]:
            (self.root / "assets" / directory).mkdir(parents=True, exist_ok=True)
        for digit in "0123456789":
            (self.root / "assets" / "ocr" / "digits" / "confirmed" / digit).mkdir(
                parents=True,
                exist_ok=True,
            )
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    user_name TEXT,
                    auto_name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    map_id TEXT NOT NULL,
                    script_name TEXT,
                    step_id TEXT,
                    bbox TEXT,
                    raw_path TEXT,
                    crop_path TEXT,
                    annotated_path TEXT,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS pending_reviews (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    asset_id TEXT,
                    crop_path TEXT,
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS ocr_digits (
                    id TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    source_asset_id TEXT,
                    map_id TEXT,
                    source_ui TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ocr_text (
                    id TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    source_asset_id TEXT,
                    map_id TEXT,
                    source_ui TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    options TEXT NOT NULL DEFAULT '[]',
                    source_asset_id TEXT,
                    map_id TEXT,
                    question_bbox TEXT,
                    option_bboxes TEXT,
                    confirm_bbox TEXT,
                    progress_bbox TEXT,
                    raw_path TEXT,
                    annotated_path TEXT,
                    answer_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    success_check_path TEXT,
                    success_check_bbox TEXT,
                    failure_check_path TEXT,
                    failure_check_bbox TEXT,
                    result_check_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS bug_reports (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'runtime',
                    status TEXT NOT NULL DEFAULT 'open',
                    script_name TEXT,
                    step_id TEXT,
                    step_name TEXT,
                    step_type TEXT,
                    log_excerpt TEXT NOT NULL DEFAULT '',
                    screenshot_path TEXT,
                    report_text TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS script_run_stats (
                    script_name TEXT PRIMARY KEY,
                    loop_attempt_count INTEGER NOT NULL DEFAULT 0,
                    loop_completed_count INTEGER NOT NULL DEFAULT 0,
                    loop_failed_count INTEGER NOT NULL DEFAULT 0,
                    last_started_at TEXT,
                    last_completed_at TEXT,
                    last_failed_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS script_cycle_runs (
                    id TEXT PRIMARY KEY,
                    script_name TEXT NOT NULL,
                    cycle_number INTEGER NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 0,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_script_cycle_runs_script_time
                    ON script_cycle_runs(script_name, ended_at);

                CREATE TABLE IF NOT EXISTS movement_samples (
                    id TEXT PRIMARY KEY,
                    map_id TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT '',
                    screen_point TEXT NOT NULL DEFAULT '[0, 0]',
                    start_coord TEXT NOT NULL DEFAULT '[0, 0]',
                    end_coord TEXT NOT NULL DEFAULT '[0, 0]',
                    delta TEXT NOT NULL DEFAULT '[0, 0]',
                    before_game_coord TEXT,
                    after_game_coord TEXT,
                    click_relative_to_character TEXT,
                    click_angle REAL,
                    click_radius INTEGER,
                    actual_delta TEXT,
                    duration REAL NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 1,
                    stuck INTEGER NOT NULL DEFAULT 0,
                    progress_score REAL NOT NULL DEFAULT 0,
                    strategy TEXT,
                    script_name TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS movement_bad_events (
                    id TEXT PRIMARY KEY,
                    source_sample_id TEXT UNIQUE,
                    map_id TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT '',
                    before_game_coord TEXT NOT NULL DEFAULT '[0, 0]',
                    after_game_coord TEXT NOT NULL DEFAULT '[0, 0]',
                    click_relative_to_character TEXT NOT NULL DEFAULT '[0, 0]',
                    click_angle REAL,
                    click_radius INTEGER,
                    actual_delta TEXT NOT NULL DEFAULT '[0, 0]',
                    duration REAL NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 0,
                    stuck INTEGER NOT NULL DEFAULT 0,
                    progress_score REAL NOT NULL DEFAULT 0,
                    strategy TEXT,
                    script_name TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deepsea_chest_records (
                    id TEXT PRIMARY KEY,
                    chest_key TEXT NOT NULL DEFAULT 'deepsea_6f',
                    record_date TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_deepsea_chest_records_key_time
                    ON deepsea_chest_records(chest_key, created_at);
                CREATE INDEX IF NOT EXISTS idx_deepsea_chest_records_key_item
                    ON deepsea_chest_records(chest_key, item_name);
                """
            )
            self._ensure_question_columns(conn)
            self._ensure_bug_report_columns(conn)
            self._ensure_script_run_stats_columns(conn)
            self._ensure_movement_columns(conn)
            self._migrate_question_answers(conn)

    def _ensure_question_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(questions)")}
        added_answer_count = "answer_count" not in existing
        columns = {
            "map_id": "TEXT",
            "question_bbox": "TEXT",
            "option_bboxes": "TEXT",
            "confirm_bbox": "TEXT",
            "progress_bbox": "TEXT",
            "raw_path": "TEXT",
            "annotated_path": "TEXT",
            "answer_count": "INTEGER NOT NULL DEFAULT 0",
            "success_count": "INTEGER NOT NULL DEFAULT 0",
            "failure_count": "INTEGER NOT NULL DEFAULT 0",
            "success_check_path": "TEXT",
            "success_check_bbox": "TEXT",
            "failure_check_path": "TEXT",
            "failure_check_bbox": "TEXT",
            "result_check_note": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "updated_at": "TEXT",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE questions ADD COLUMN {name} {sql_type}")
        if added_answer_count:
            conn.execute(
                """
                UPDATE questions
                SET answer_count = COALESCE(success_count, 0),
                    success_count = 0,
                    failure_count = 0,
                    updated_at = COALESCE(updated_at, ?)
                """,
                (now_iso(),),
            )

    def _ensure_bug_report_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(bug_reports)")}
        columns = {
            "title": "TEXT NOT NULL DEFAULT ''",
            "kind": "TEXT NOT NULL DEFAULT 'runtime'",
            "status": "TEXT NOT NULL DEFAULT 'open'",
            "script_name": "TEXT",
            "step_id": "TEXT",
            "step_name": "TEXT",
            "step_type": "TEXT",
            "log_excerpt": "TEXT NOT NULL DEFAULT ''",
            "screenshot_path": "TEXT",
            "report_text": "TEXT NOT NULL DEFAULT ''",
            "metadata": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "TEXT",
            "updated_at": "TEXT",
            "resolved_at": "TEXT",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE bug_reports ADD COLUMN {name} {sql_type}")

    def _ensure_script_run_stats_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(script_run_stats)")}
        columns = {
            "loop_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "loop_completed_count": "INTEGER NOT NULL DEFAULT 0",
            "loop_failed_count": "INTEGER NOT NULL DEFAULT 0",
            "last_started_at": "TEXT",
            "last_completed_at": "TEXT",
            "last_failed_at": "TEXT",
            "updated_at": "TEXT",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE script_run_stats ADD COLUMN {name} {sql_type}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS script_cycle_runs (
                id TEXT PRIMARY KEY,
                script_name TEXT NOT NULL,
                cycle_number INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_script_cycle_runs_script_time
            ON script_cycle_runs(script_name, ended_at)
            """
        )

    def _migrate_question_answers(self, conn: sqlite3.Connection) -> None:
        for row in conn.execute("SELECT id, answer, options FROM questions"):
            answer = (row["answer"] or "").strip()
            if answer.upper() not in {"A", "B", "C", "D"}:
                continue
            try:
                options = json.loads(row["options"] or "[]")
            except json.JSONDecodeError:
                continue
            index = ord(answer.upper()) - ord("A")
            if index < len(options) and str(options[index]).strip():
                conn.execute(
                    "UPDATE questions SET answer = ?, updated_at = ? WHERE id = ?",
                    (str(options[index]).strip(), now_iso(), row["id"]),
                )

    def _ensure_movement_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(movement_samples)")}
        columns = {
            "before_game_coord": "TEXT",
            "after_game_coord": "TEXT",
            "click_relative_to_character": "TEXT",
            "click_angle": "REAL",
            "click_radius": "INTEGER",
            "actual_delta": "TEXT",
            "duration": "REAL NOT NULL DEFAULT 0",
            "success": "INTEGER NOT NULL DEFAULT 1",
            "stuck": "INTEGER NOT NULL DEFAULT 0",
            "progress_score": "REAL NOT NULL DEFAULT 0",
            "strategy": "TEXT",
            "script_name": "TEXT",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE movement_samples ADD COLUMN {name} {sql_type}")
        # Backfill legacy rows so aggregation can read movement samples without
        # consulting absolute screen clicks as navigation instructions.
        for row in conn.execute(
            """
            SELECT id, direction, screen_point, start_coord, end_coord, delta,
                   before_game_coord, after_game_coord, click_relative_to_character,
                   click_angle, click_radius, actual_delta, strategy
            FROM movement_samples
            """
        ):
            updates: dict[str, Any] = {}
            start = _json_list(row["start_coord"], [0, 0])
            end = _json_list(row["end_coord"], start)
            delta = _json_list(row["delta"], [int(end[0]) - int(start[0]), int(end[1]) - int(start[1])])
            if not row["before_game_coord"]:
                updates["before_game_coord"] = json.dumps(start, ensure_ascii=False)
            if not row["after_game_coord"]:
                updates["after_game_coord"] = json.dumps(end, ensure_ascii=False)
            if not row["actual_delta"]:
                updates["actual_delta"] = json.dumps(delta, ensure_ascii=False)
            legacy_backfill = not row["click_relative_to_character"]
            if legacy_backfill:
                point = _json_list(row["screen_point"], [960, 560])
                relative = [int(point[0]) - 960, int(point[1]) - 560]
                updates["click_relative_to_character"] = json.dumps(relative, ensure_ascii=False)
                radius = int(round((relative[0] ** 2 + relative[1] ** 2) ** 0.5))
                updates["click_radius"] = radius
                updates["click_angle"] = _angle_for_vector(relative)
            if not row["strategy"] and (legacy_backfill or _json_list(row["screen_point"], [0, 0]) != [0, 0]):
                updates["strategy"] = "legacy_screen_point_backfill"
            if not row["click_radius"]:
                relative = _json_list(updates.get("click_relative_to_character") or row["click_relative_to_character"], [0, 0])
                updates["click_radius"] = int(round((relative[0] ** 2 + relative[1] ** 2) ** 0.5))
            if row["click_angle"] is None:
                relative = _json_list(updates.get("click_relative_to_character") or row["click_relative_to_character"], [0, 0])
                updates["click_angle"] = _angle_for_vector(relative)
            if updates:
                assignments = ", ".join(f"{name} = ?" for name in updates)
                conn.execute(
                    f"UPDATE movement_samples SET {assignments} WHERE id = ?",
                    [*updates.values(), row["id"]],
                )

    def script_dir(self, script_name: str) -> Path:
        path = self.root / "scripts" / sanitize_filename(script_name)
        path.mkdir(parents=True, exist_ok=True)
        (path / "steps").mkdir(parents=True, exist_ok=True)
        return path

    def script_flow_file(self, script_name: str) -> Path:
        return self.root / "scripts" / sanitize_filename(script_name) / "flow.json"

    def script_order_file(self) -> Path:
        return self.root / "scripts" / "script_order.json"

    def studio_state_file(self) -> Path:
        return self.root / "studio_state.json"

    def load_studio_state(self) -> dict[str, Any]:
        path = self.studio_state_file()
        if not path.exists():
            return {"version": 1}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1}
        return data if isinstance(data, dict) else {"version": 1}

    def save_studio_state(self, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload["version"] = 1
        payload["updated_at"] = now_iso()
        path = self.studio_state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_last_flow_path(self, flow_path: str | Path) -> None:
        path = Path(flow_path)
        try:
            value = str(path.resolve().relative_to(self.root.resolve()))
        except (OSError, ValueError):
            value = str(path.expanduser())
        state = self.load_studio_state()
        state["last_flow_path"] = value
        self.save_studio_state(state)

    def load_last_flow_path(self) -> Path | None:
        state = self.load_studio_state()
        raw_path = str(state.get("last_flow_path") or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path if path.exists() else None

    def load_script_order(self) -> list[str]:
        path = self.script_order_file()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_order = data.get("order") if isinstance(data, dict) else data
        if not isinstance(raw_order, list):
            return []
        order: list[str] = []
        seen: set[str] = set()
        for value in raw_order:
            key = str(value or "").strip()
            if key and key not in seen:
                order.append(key)
                seen.add(key)
        return order

    def save_script_order(self, order_keys: list[str]) -> None:
        order: list[str] = []
        seen: set[str] = set()
        for value in order_keys:
            key = str(value or "").strip()
            if key and key not in seen:
                order.append(key)
                seen.add(key)
        path = self.script_order_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "order": order, "updated_at": now_iso()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def script_exists(self, script_name: str) -> bool:
        return self.script_flow_file(script_name).exists()

    def flow_path(self, script_name: str) -> Path:
        return self.script_dir(script_name) / "flow.json"

    def flow_backup_dir(self, script_name: str) -> Path:
        path = self.script_dir(script_name) / "backups"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def screenshots_dir(self) -> Path:
        path = self.root / "assets" / "screenshots"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def deepsea_dir(self) -> Path:
        path = self.root / "deepsea"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def deepsea_action_library_path(self) -> Path:
        return self.deepsea_dir() / "action_library.json"

    def deepsea_operation_records_dir(self) -> Path:
        path = self.deepsea_dir() / "operation_records"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_deepsea_chest_record(
        self,
        *,
        item_name: str,
        quantity: int,
        record_date: str | None = None,
        note: str = "",
        chest_key: str = "deepsea_6f",
    ) -> str:
        item = str(item_name or "").strip()
        if not item:
            raise ValueError("item_name is required")
        qty = int(quantity)
        if qty <= 0:
            raise ValueError("quantity must be greater than 0")
        date_text = str(record_date or "").strip() or now_iso().split("T", 1)[0]
        record_id = uuid.uuid4().hex
        timestamp = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deepsea_chest_records (
                    id, chest_key, record_date, item_name, quantity, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, chest_key, date_text, item, qty, str(note or "").strip(), timestamp, timestamp),
            )
        return record_id

    def update_deepsea_chest_record(
        self,
        record_id: str,
        *,
        item_name: str,
        quantity: int,
        record_date: str | None = None,
        note: str = "",
        chest_key: str = "deepsea_6f",
    ) -> None:
        item = str(item_name or "").strip()
        if not item:
            raise ValueError("item_name is required")
        qty = int(quantity)
        if qty <= 0:
            raise ValueError("quantity must be greater than 0")
        date_text = str(record_date or "").strip() or now_iso().split("T", 1)[0]
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE deepsea_chest_records
                SET chest_key = ?, record_date = ?, item_name = ?, quantity = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (chest_key, date_text, item, qty, str(note or "").strip(), now_iso(), record_id),
            )

    def delete_deepsea_chest_records(self, record_ids: list[str]) -> int:
        ids = [str(record_id).strip() for record_id in record_ids if str(record_id).strip()]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cursor = conn.execute(f"DELETE FROM deepsea_chest_records WHERE id IN ({placeholders})", ids)
            return int(cursor.rowcount or 0)

    def list_deepsea_chest_records(self, chest_key: str = "deepsea_6f") -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT *
                    FROM deepsea_chest_records
                    WHERE chest_key = ?
                    ORDER BY created_at DESC, updated_at DESC
                    """,
                    (chest_key,),
                )
            )

    def deepsea_chest_totals(self, chest_key: str = "deepsea_6f") -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT item_name, SUM(quantity) AS quantity
                FROM deepsea_chest_records
                WHERE chest_key = ?
                GROUP BY item_name
                """,
                (chest_key,),
            )
            return {str(row["item_name"]): int(row["quantity"] or 0) for row in rows}

    def bug_reports_dir(self) -> Path:
        path = self.root / "assets" / "bug_reports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def script_movement_db_file(self, script_name: str) -> Path:
        return self.script_dir(script_name) / "movement_coords.json"

    def script_route_plan_file(self, script_name: str) -> Path:
        return self.script_dir(script_name) / "route_plan.json"

    def script_route_training_file(self, script_name: str) -> Path:
        return self.script_dir(script_name) / "route_training.json"

    def script_battle_presets_file(self, script_name: str) -> Path:
        return self.script_dir(script_name) / "battle_presets.json"

    def script_presets_file(self, script_name: str) -> Path:
        return self.script_dir(script_name) / "presets.json"

    def project_presets_file(self) -> Path:
        return self.root / "presets.json"

    def load_script_route_plan(self, script_name: str) -> dict[str, Any]:
        path = self.script_route_plan_file(script_name)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        if not isinstance(payload.get("nodes"), list):
            payload["nodes"] = []
        if not isinstance(payload.get("training_edges"), list):
            payload["training_edges"] = []
        return payload

    def load_script_route_training(self, script_name: str) -> dict[str, Any]:
        path = self.script_route_training_file(script_name)
        if not path.exists():
            return {"version": 1, "script_name": script_name, "edges": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "script_name": script_name, "edges": {}}
        if not isinstance(payload.get("edges"), dict):
            payload["edges"] = {}
        payload.setdefault("version", 1)
        payload.setdefault("script_name", script_name)
        return payload

    def save_script_route_training(self, script_name: str, payload: dict[str, Any]) -> None:
        payload["version"] = 1
        payload["script_name"] = script_name
        self.script_route_training_file(script_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_script_battle_presets(self, script_name: str) -> dict[str, Any]:
        return self.load_script_presets(script_name)

    def _load_presets_payload(self, path: Path, *, script_name: str | None = None, scope: str) -> dict[str, Any]:
        fallback: dict[str, Any] = {"version": 1, "scope": scope, "presets": []}
        if script_name is not None:
            fallback["script_name"] = script_name
        if not path.exists():
            return fallback
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback
        if not isinstance(payload, dict):
            return fallback
        payload.setdefault("version", 1)
        payload["scope"] = scope
        if script_name is not None:
            payload["script_name"] = script_name
        if not isinstance(payload.get("presets"), list):
            payload["presets"] = []
        presets: list[dict[str, Any]] = []
        for preset in payload["presets"]:
            if not isinstance(preset, dict):
                continue
            normalized = self.normalize_script_preset(preset)
            normalized["scope"] = scope
            normalized["_preset_scope"] = scope
            if script_name is not None:
                normalized["script_name"] = script_name
            presets.append(normalized)
        payload["presets"] = presets
        return payload

    def _save_presets_payload(self, path: Path, payload: dict[str, Any], *, script_name: str | None = None, scope: str) -> None:
        payload["version"] = 1
        payload["scope"] = scope
        if script_name is not None:
            payload["script_name"] = script_name
        presets: list[dict[str, Any]] = []
        for preset in payload.get("presets") or []:
            if not isinstance(preset, dict):
                continue
            normalized = self.normalize_script_preset(preset)
            normalized["scope"] = scope
            if script_name is not None:
                normalized["script_name"] = script_name
            else:
                normalized.pop("script_name", None)
            normalized.pop("_preset_scope", None)
            presets.append(normalized)
        payload["presets"] = presets
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_project_presets(self) -> dict[str, Any]:
        return self._load_presets_payload(self.project_presets_file(), scope="global")

    def save_project_presets(self, payload: dict[str, Any]) -> None:
        self._save_presets_payload(self.project_presets_file(), payload, scope="global")

    def load_script_presets(self, script_name: str) -> dict[str, Any]:
        path = self.script_presets_file(script_name)
        if not path.exists():
            path = self.script_battle_presets_file(script_name)
        if not path.exists():
            return {"version": 1, "script_name": script_name, "presets": []}
        return self._load_presets_payload(path, script_name=script_name, scope="script")

    def load_combined_script_presets(self, script_name: str) -> dict[str, Any]:
        global_payload = self.load_project_presets()
        script_payload = self.load_script_presets(script_name)
        return {
            "version": 1,
            "script_name": script_name,
            "presets": [
                *[preset for preset in global_payload.get("presets") or [] if isinstance(preset, dict)],
                *[preset for preset in script_payload.get("presets") or [] if isinstance(preset, dict)],
            ],
        }

    def save_script_battle_presets(self, script_name: str, payload: dict[str, Any]) -> None:
        self.save_script_presets(script_name, payload)

    def save_script_presets(self, script_name: str, payload: dict[str, Any]) -> None:
        self._save_presets_payload(self.script_presets_file(script_name), payload, script_name=script_name, scope="script")

    def upsert_script_battle_preset(self, script_name: str, preset: dict[str, Any]) -> dict[str, Any]:
        return self.upsert_script_preset(script_name, preset)

    def upsert_project_preset(self, preset: dict[str, Any]) -> dict[str, Any]:
        payload = self.load_project_presets()
        presets = payload.setdefault("presets", [])
        preset = self.normalize_script_preset(dict(preset))
        preset_id = str(preset.get("id") or f"preset_{uuid.uuid4().hex[:8]}")
        preset["id"] = preset_id
        preset["scope"] = "global"
        preset.pop("script_name", None)
        preset.setdefault("created_at", now_iso())
        preset["updated_at"] = now_iso()
        replaced = False
        for index, item in enumerate(presets):
            if item.get("id") == preset_id:
                presets[index] = preset
                replaced = True
                break
        if not replaced:
            presets.append(preset)
        self.save_project_presets(payload)
        return preset

    def upsert_script_preset(self, script_name: str, preset: dict[str, Any]) -> dict[str, Any]:
        payload = self.load_script_presets(script_name)
        presets = payload.setdefault("presets", [])
        preset = self.normalize_script_preset(dict(preset))
        preset_id = str(preset.get("id") or f"preset_{uuid.uuid4().hex[:8]}")
        preset["id"] = preset_id
        preset["scope"] = "script"
        preset["script_name"] = script_name
        preset.setdefault("created_at", now_iso())
        preset["updated_at"] = now_iso()
        replaced = False
        for index, item in enumerate(presets):
            if item.get("id") == preset_id:
                presets[index] = preset
                replaced = True
                break
        if not replaced:
            presets.append(preset)
        self.save_script_presets(script_name, payload)
        return preset

    def delete_script_battle_preset(self, script_name: str, preset_id: str) -> bool:
        return self.delete_script_preset(script_name, preset_id)

    def delete_project_preset(self, preset_id: str) -> bool:
        payload = self.load_project_presets()
        presets = payload.setdefault("presets", [])
        kept = [preset for preset in presets if str(preset.get("id")) != str(preset_id)]
        if len(kept) == len(presets):
            return False
        payload["presets"] = kept
        self.save_project_presets(payload)
        return True

    def delete_script_preset(self, script_name: str, preset_id: str) -> bool:
        payload = self.load_script_presets(script_name)
        presets = payload.setdefault("presets", [])
        kept = [preset for preset in presets if str(preset.get("id")) != str(preset_id)]
        if len(kept) == len(presets):
            return False
        payload["presets"] = kept
        self.save_script_presets(script_name, payload)
        return True

    def normalize_script_preset(self, preset: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(preset)
        if "steps" not in normalized:
            loop_step = normalized.get("loop_step")
            if isinstance(loop_step, dict):
                normalized["steps"] = [loop_step]
                normalized.setdefault("kind", "battle")
        normalized.setdefault("kind", "general")
        normalized.setdefault("repeat_count", 1)
        try:
            normalized["repeat_count"] = max(1, int(normalized.get("repeat_count") or 1))
        except (TypeError, ValueError):
            normalized["repeat_count"] = 1
        steps = normalized.get("steps")
        if not isinstance(steps, list):
            normalized["steps"] = []
        normalized.setdefault("name", normalized.get("name") or "流程预设")
        if not normalized.get("note"):
            step_names = [
                str(step.get("name") or step.get("type") or "步骤")
                for step in normalized.get("steps") or []
                if isinstance(step, dict)
            ]
            normalized["note"] = " -> ".join(step_names[:5])
        return normalized

    def mark_script_route_edge_result(
        self,
        script_name: str,
        edge_id: str,
        *,
        from_node: str,
        to_node: str,
        success: bool,
        start_coord: list[int] | tuple[int, int] | None = None,
        end_coord: list[int] | tuple[int, int] | None = None,
    ) -> None:
        payload = self.load_script_route_training(script_name)
        edges = payload.setdefault("edges", {})
        edge = edges.setdefault(
            edge_id,
            {
                "edge_id": edge_id,
                "from": from_node,
                "to": to_node,
                "success": 0,
                "failure": 0,
            },
        )
        edge["from"] = from_node
        edge["to"] = to_node
        key = "success" if success else "failure"
        edge[key] = int(edge.get(key, 0) or 0) + 1
        if start_coord is not None:
            edge["last_start_coord"] = [int(start_coord[0]), int(start_coord[1])]
        if end_coord is not None:
            edge["last_end_coord"] = [int(end_coord[0]), int(end_coord[1])]
        edge["last_result"] = "success" if success else "failure"
        edge["updated_at"] = now_iso()
        self.save_script_route_training(script_name, payload)

    def load_script_movement_coords(self, script_name: str) -> dict[str, Any]:
        path = self.script_movement_db_file(script_name)
        if not path.exists():
            return {"version": 1, "script_name": script_name, "targets": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "script_name": script_name, "targets": []}
        if not isinstance(payload.get("targets"), list):
            payload["targets"] = []
        payload.setdefault("version", 1)
        payload.setdefault("script_name", script_name)
        return payload

    def save_script_movement_coords(self, script_name: str, payload: dict[str, Any]) -> None:
        payload["version"] = 1
        payload["script_name"] = script_name
        self.script_movement_db_file(script_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_script_movement_coord(
        self,
        script_name: str,
        *,
        map_id: str,
        coord: list[int] | tuple[int, int],
        label: str | None = None,
        tolerance: int = 0,
        exact: bool = True,
    ) -> bool:
        payload = self.load_script_movement_coords(script_name)
        target = [int(coord[0]), int(coord[1])]
        for item in payload["targets"]:
            if item.get("map_id") == map_id and item.get("coord") == target:
                item["label"] = label or item.get("label") or f"{target[0]},{target[1]}"
                item["tolerance"] = int(tolerance)
                item["exact"] = bool(exact)
                self.save_script_movement_coords(script_name, payload)
                return False
        payload["targets"].append(
            {
                "id": f"move_target_{uuid.uuid4().hex[:8]}",
                "label": label or f"{target[0]},{target[1]}",
                "map_id": map_id,
                "coord": target,
                "tolerance": int(tolerance),
                "exact": bool(exact),
                "practice_success": 0,
                "practice_failure": 0,
                "practice_routes": {},
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        self.save_script_movement_coords(script_name, payload)
        return True

    def update_script_movement_coord(
        self,
        script_name: str,
        target_id: str,
        *,
        map_id: str | None = None,
        coord: list[int] | tuple[int, int] | None = None,
        label: str | None = None,
        tolerance: int | None = None,
        exact: bool | None = None,
    ) -> bool:
        payload = self.load_script_movement_coords(script_name)
        for item in payload["targets"]:
            if item.get("id") != target_id:
                continue
            if map_id is not None:
                item["map_id"] = str(map_id)
            if coord is not None:
                item["coord"] = [int(coord[0]), int(coord[1])]
            if label is not None:
                item["label"] = label
            if tolerance is not None:
                item["tolerance"] = int(tolerance)
            if exact is not None:
                item["exact"] = bool(exact)
            item["updated_at"] = now_iso()
            self.save_script_movement_coords(script_name, payload)
            return True
        return False

    def delete_script_movement_coord(self, script_name: str, target_id: str) -> bool:
        payload = self.load_script_movement_coords(script_name)
        before = len(payload["targets"])
        payload["targets"] = [item for item in payload["targets"] if item.get("id") != target_id]
        changed = len(payload["targets"]) != before
        if changed:
            self.save_script_movement_coords(script_name, payload)
        return changed

    def mark_script_movement_coord_result(
        self,
        script_name: str,
        target_id: str,
        success: bool,
        *,
        origin_coord: list[int] | tuple[int, int] | None = None,
        origin_label: str | None = None,
    ) -> None:
        payload = self.load_script_movement_coords(script_name)
        for item in payload["targets"]:
            if item.get("id") != target_id:
                continue
            key = "practice_success" if success else "practice_failure"
            item[key] = int(item.get(key, 0) or 0) + 1
            if origin_coord is not None:
                origin = [int(origin_coord[0]), int(origin_coord[1])]
                target = item.get("coord") or []
                if len(target) >= 2:
                    target_coord = [int(target[0]), int(target[1])]
                    route_key = f"{origin[0]},{origin[1]}->{target_coord[0]},{target_coord[1]}"
                    routes = item.setdefault("practice_routes", {})
                    route = routes.setdefault(
                        route_key,
                        {
                            "origin_coord": origin,
                            "origin_label": origin_label or f"{origin[0]},{origin[1]}",
                            "target_coord": target_coord,
                            "success": 0,
                            "failure": 0,
                        },
                    )
                    route["origin_coord"] = origin
                    route["origin_label"] = origin_label or route.get("origin_label") or f"{origin[0]},{origin[1]}"
                    route["target_coord"] = target_coord
                    route_result_key = "success" if success else "failure"
                    route[route_result_key] = int(route.get(route_result_key, 0) or 0) + 1
                    route["updated_at"] = now_iso()
            item["updated_at"] = now_iso()
            break
        self.save_script_movement_coords(script_name, payload)

    def step_library_hidden_file(self) -> Path:
        path = self.root / "assets" / "step_library"
        path.mkdir(parents=True, exist_ok=True)
        return path / "hidden.json"

    def hidden_step_library_keys(self) -> set[str]:
        path = self.step_library_hidden_file()
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(data, list):
            return set()
        return {str(item) for item in data}

    def hide_step_library_keys(self, keys: list[str]) -> None:
        hidden = self.hidden_step_library_keys()
        hidden.update(key for key in keys if key)
        self.step_library_hidden_file().write_text(
            json.dumps(sorted(hidden), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear_hidden_step_library_keys(self) -> None:
        path = self.step_library_hidden_file()
        if path.exists():
            path.unlink()

    def backup_flow(self, script_name: str) -> Path | None:
        source = self.script_flow_file(script_name)
        if not source.exists():
            return None
        target = self.flow_backup_dir(script_name) / f"flow_{now_compact()}.json"
        if target.exists():
            target = self.flow_backup_dir(script_name) / f"flow_{now_compact()}_{uuid.uuid4().hex[:4]}.json"
        shutil.copy2(source, target)
        return target

    def list_script_flows(self) -> list[dict[str, Any]]:
        scripts_root = self.root / "scripts"
        if not scripts_root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for flow_path in scripts_root.glob("*/flow.json"):
            try:
                data = json.loads(flow_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            stat = flow_path.stat()
            steps = data.get("steps") if isinstance(data.get("steps"), list) else []
            rows.append(
                {
                    "script_name": data.get("script_name") or flow_path.parent.name,
                    "order_key": flow_path.parent.name,
                    "step_count": self._count_flow_steps(steps),
                    "updated_at": data.get("updated_at") or datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "path": str(flow_path),
                    "directory": str(flow_path.parent),
                }
            )
        rows.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        order_index = {key: index for index, key in enumerate(self.load_script_order())}
        rows.sort(key=lambda item: order_index.get(str(item.get("order_key") or ""), len(order_index)))
        return rows

    def delete_script_flow_path(self, flow_path: str | Path) -> Path:
        path = Path(flow_path).expanduser()
        scripts_root = (self.root / "scripts").resolve()
        try:
            resolved = path.resolve()
            resolved.relative_to(scripts_root)
        except (OSError, ValueError):
            raise ValueError("只能删除当前项目 scripts 目录内的副本流程。")
        if resolved.name != "flow.json" or not resolved.exists():
            raise ValueError("选中的副本流程文件不存在。")
        script_dir = resolved.parent
        deleted_root = self.root / "deleted_scripts"
        deleted_root.mkdir(parents=True, exist_ok=True)
        target = deleted_root / f"{script_dir.name}_{now_compact()}"
        if target.exists():
            target = deleted_root / f"{script_dir.name}_{now_compact()}_{uuid.uuid4().hex[:4]}"
        shutil.move(str(script_dir), str(target))
        order = self.load_script_order()
        if script_dir.name in order:
            self.save_script_order([key for key in order if key != script_dir.name])
        return target

    def _count_flow_steps(self, steps: list[dict[str, Any]]) -> int:
        total = 0
        for step in steps:
            total += 1
            children = step.get("children")
            if isinstance(children, list):
                total += self._count_flow_steps(children)
        return total

    def step_dir(self, script_name: str, step_id: str, step_type: str) -> Path:
        path = self.script_dir(script_name) / "steps" / f"{step_id}_{sanitize_filename(step_type)}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def map_dir(self, map_id: str, kind: str) -> Path:
        directory = TYPE_DIR.get(kind, "unknown")
        path = self.root / "assets" / "maps" / sanitize_filename(map_id) / directory
        path.mkdir(parents=True, exist_ok=True)
        return path

    def raw_dir(self, map_id: str) -> Path:
        path = self.root / "assets" / "maps" / sanitize_filename(map_id) / "raw"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def annotated_dir(self, map_id: str) -> Path:
        path = self.root / "assets" / "maps" / sanitize_filename(map_id) / "annotated"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def walkability_path(self, map_id: str) -> Path:
        path = self.root / "assets" / "maps" / sanitize_filename(map_id) / "walkability.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def make_asset_paths(
        self,
        map_id: str,
        asset_type: str,
        user_name: str | None = None,
    ) -> dict[str, Path | str]:
        asset_id = f"asset_{uuid.uuid4().hex[:8]}"
        safe_user = sanitize_filename(user_name or "")
        suffix = safe_user if safe_user != "unknown" else now_compact()
        auto_name = f"{sanitize_filename(map_id)}_{sanitize_filename(asset_type)}_{suffix}"
        raw_path = self.raw_dir(map_id) / f"{auto_name}_raw.png"
        crop_path = self.map_dir(map_id, asset_type) / f"{auto_name}_crop.png"
        annotated_path = self.annotated_dir(map_id) / f"{auto_name}_annotated.png"
        return {
            "asset_id": asset_id,
            "auto_name": auto_name,
            "raw_path": raw_path,
            "crop_path": crop_path,
            "annotated_path": annotated_path,
        }

    def rel(self, path: str | Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)

    def abs(self, path: str | Path | None) -> Path | None:
        if not path:
            return None
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return self.root / candidate

    def add_asset(
        self,
        *,
        asset_id: str,
        user_name: str | None,
        auto_name: str,
        asset_type: str,
        map_id: str,
        script_name: str,
        step_id: str | None,
        bbox: list[int],
        raw_path: str | Path,
        crop_path: str | Path,
        annotated_path: str | Path,
        confidence: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO assets (
                    id, user_name, auto_name, type, map_id, script_name, step_id,
                    bbox, raw_path, crop_path, annotated_path, created_at,
                    last_used_at, confidence, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    user_name or "",
                    auto_name,
                    asset_type,
                    map_id,
                    script_name,
                    step_id,
                    json.dumps(bbox, ensure_ascii=False),
                    self.rel(raw_path),
                    self.rel(crop_path),
                    self.rel(annotated_path),
                    now_iso(),
                    now_iso(),
                    float(confidence),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def list_assets(self, asset_type: str | None = None, status_filter: str | None = "active") -> list[sqlite3.Row]:
        sql = "SELECT * FROM assets WHERE status != 'deleted'"
        params: list[Any] = []
        if asset_type and asset_type != "全部":
            sql += " AND type = ?"
            params.append(asset_type)
        if status_filter and status_filter != "全部":
            sql += " AND status = ?"
            params.append(status_filter)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            return list(conn.execute(sql, params))

    def asset(self, asset_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()

    def rename_asset(self, asset_id: str, user_name: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE assets SET user_name = ? WHERE id = ?", (user_name, asset_id))

    def mark_asset_status(self, asset_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE assets SET status = ? WHERE id = ?", (status, asset_id))

    def deprecated_asset_dir(self, asset_id: str) -> Path:
        path = self.root / "assets" / "deprecated" / sanitize_filename(asset_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def move_assets_to_deprecated(self, asset_ids: list[str]) -> int:
        ids = [asset_id for asset_id in asset_ids if asset_id]
        moved = 0
        with self._connect() as conn:
            for asset_id in ids:
                row = conn.execute("SELECT * FROM assets WHERE id = ? AND status != 'deleted'", (asset_id,)).fetchone()
                if not row:
                    continue
                raw_path = self._move_asset_file(asset_id, row["raw_path"], "raw")
                crop_path = self._move_asset_file(asset_id, row["crop_path"], "crop")
                annotated_path = self._move_asset_file(asset_id, row["annotated_path"], "annotated")
                conn.execute(
                    """
                    UPDATE assets
                    SET status = 'deprecated',
                        raw_path = ?,
                        crop_path = ?,
                        annotated_path = ?,
                        last_used_at = ?
                    WHERE id = ?
                    """,
                    (raw_path, crop_path, annotated_path, now_iso(), asset_id),
                )
                moved += 1
        return moved

    def delete_assets(self, asset_ids: list[str]) -> int:
        ids = [asset_id for asset_id in asset_ids if asset_id]
        deleted = 0
        with self._connect() as conn:
            for asset_id in ids:
                row = conn.execute("SELECT * FROM assets WHERE id = ? AND status != 'deleted'", (asset_id,)).fetchone()
                if not row:
                    continue
                for key in ("raw_path", "crop_path", "annotated_path"):
                    self._unlink_asset_file(row[key])
                conn.execute(
                    """
                    UPDATE assets
                    SET status = 'deleted',
                        raw_path = NULL,
                        crop_path = NULL,
                        annotated_path = NULL,
                        last_used_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), asset_id),
                )
                deleted += 1
        return deleted

    def _move_asset_file(self, asset_id: str, rel_path: str | None, label: str) -> str | None:
        source = self.abs(rel_path)
        if source is None or not source.exists() or not source.is_file():
            return rel_path
        deprecated_root = (self.root / "assets" / "deprecated").resolve()
        try:
            if source.resolve().is_relative_to(deprecated_root):
                return self.rel(source)
        except AttributeError:
            try:
                source.resolve().relative_to(deprecated_root)
                return self.rel(source)
            except ValueError:
                pass
        suffix = source.suffix or ".png"
        target = self.deprecated_asset_dir(asset_id) / f"{label}{suffix}"
        if target.exists():
            target = self.deprecated_asset_dir(asset_id) / f"{label}_{now_compact()}{suffix}"
        shutil.move(str(source), str(target))
        return self.rel(target)

    def _unlink_asset_file(self, rel_path: str | None) -> None:
        path = self.abs(rel_path)
        if path is None or not path.exists() or not path.is_file():
            return
        try:
            path.unlink()
        except OSError:
            pass

    def add_pending_review(
        self,
        kind: str,
        asset_id: str | None,
        crop_path: str | Path | None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        review_id = f"review_{uuid.uuid4().hex[:8]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_reviews (
                    id, kind, asset_id, crop_path, payload, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    review_id,
                    kind,
                    asset_id,
                    self.rel(crop_path),
                    json.dumps(payload or {}, ensure_ascii=False),
                    now_iso(),
                ),
            )
        return review_id

    def list_pending_reviews(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM pending_reviews WHERE status = 'pending' ORDER BY created_at DESC"
                )
            )

    def resolve_pending_review(self, review_id: str, value: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM pending_reviews WHERE id = ?", (review_id,)).fetchone()
        if not row:
            return
        payload = json.loads(row["payload"] or "{}")
        crop_path = row["crop_path"] or ""
        asset_id = row["asset_id"]
        kind = row["kind"]
        if kind == "digit":
            self.add_public_ocr_sample(
                kind="digit",
                value=value,
                image_path=self.abs(crop_path) or crop_path,
                source_asset_id=asset_id,
                map_id=payload.get("map_id") or "map_001",
                source_ui=payload.get("source_ui") or "pending_review",
            )
        elif kind in {"text", "npc", "button"}:
            self.add_public_ocr_sample(
                kind="text",
                value=value,
                image_path=self.abs(crop_path) or crop_path,
                source_asset_id=asset_id,
                map_id=payload.get("map_id") or "map_001",
                source_ui=payload.get("source_ui") or "pending_review",
            )

        with self._connect() as conn:
            if kind == "question":
                conn.execute(
                    """
                    INSERT INTO questions (id, question, answer, options, source_asset_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"question_{uuid.uuid4().hex[:8]}",
                        payload.get("question", ""),
                        value,
                        json.dumps(payload.get("options", []), ensure_ascii=False),
                        asset_id,
                        now_iso(),
                    ),
                )
            conn.execute(
                "UPDATE pending_reviews SET status = 'resolved', reviewed_at = ? WHERE id = ?",
                (now_iso(), review_id),
            )

    def delete_pending_review(self, review_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE pending_reviews SET status = 'deleted', reviewed_at = ? WHERE id = ?",
                (now_iso(), review_id),
            )

    def insert_ocr_text(
        self,
        *,
        value: str,
        image_path: str | Path,
        source_asset_id: str | None,
        map_id: str,
        source_ui: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ocr_text (id, value, image_path, source_asset_id, map_id, source_ui, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"text_{uuid.uuid4().hex[:8]}",
                    value,
                    self.rel(image_path),
                    source_asset_id,
                    map_id,
                    source_ui,
                    now_iso(),
                ),
            )

    def ocr_scan_dir(self) -> Path:
        path = self.root / "assets" / "ocr" / "scans"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def public_ocr_dir(self, kind: str, value: str, status: str = "confirmed") -> Path:
        if kind == "digit":
            safe_value = sanitize_filename(value)[:60]
            path = self.root / "assets" / "ocr" / "digits" / status / safe_value
        else:
            safe_value = sanitize_filename(value)[:80]
            path = self.root / "assets" / "ocr" / "text" / status / safe_value
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_public_ocr_sample(
        self,
        *,
        kind: str,
        value: str,
        image_path: str | Path,
        map_id: str,
        source_ui: str,
        confidence: float = 0.0,
        source_asset_id: str | None = None,
    ) -> str:
        if kind == "digit":
            single_digit = clean_single_digit_value(value)
            if single_digit is None:
                return ""
            value = single_digit
        source = Path(image_path)
        target_dir = self.public_ocr_dir(kind, value, "confirmed")
        suffix = source.suffix or ".png"
        unique = uuid.uuid4().hex[:8]
        target = target_dir / f"{sanitize_filename(map_id)}_{sanitize_filename(source_ui)}_{now_compact()}_{unique}{suffix}"
        if source.exists():
            shutil.copy2(source, target)
        else:
            target.write_bytes(b"")
        if kind == "digit":
            self.insert_ocr_digit(
                value=value,
                image_path=target,
                source_asset_id=source_asset_id,
                map_id=map_id,
                source_ui=source_ui,
            )
        else:
            self.insert_ocr_text(
                value=value,
                image_path=target,
                source_asset_id=source_asset_id,
                map_id=map_id,
                source_ui=source_ui,
            )
        return self.rel(target) or str(target)

    def list_ocr_digit_samples(self, limit_per_digit: int = 20) -> list[sqlite3.Row]:
        limit = max(1, int(limit_per_digit))
        with self._connect() as conn:
            rows = list(
                conn.execute(
                    """
                    SELECT value, image_path, created_at
                    FROM ocr_digits
                    WHERE length(value) = 1 AND value GLOB '[0-9]'
                    ORDER BY created_at DESC
                    """
                )
            )
        counts: dict[str, int] = {}
        selected: list[sqlite3.Row] = []
        for row in rows:
            digit = str(row["value"])
            count = counts.get(digit, 0)
            if count >= limit:
                continue
            selected.append(row)
            counts[digit] = count + 1
        return selected

    def ocr_digit_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = list(
                conn.execute(
                    """
                    SELECT value, COUNT(1) AS total
                    FROM ocr_digits
                    WHERE length(value) = 1 AND value GLOB '[0-9]'
                    GROUP BY value
                    ORDER BY value
                    """
                )
            )
        return {str(row["value"]): int(row["total"]) for row in rows}

    def cleanup_invalid_ocr_digit_library(self) -> dict[str, Any]:
        removed_dirs: list[str] = []
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM ocr_digits
                WHERE NOT (length(value) = 1 AND value GLOB '[0-9]')
                """
            )
            deleted_rows = int(cursor.rowcount or 0)
            dedupe_cursor = conn.execute(
                """
                DELETE FROM ocr_digits
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM ocr_digits
                    GROUP BY value, image_path
                )
                """
            )
            deduped_rows = int(dedupe_cursor.rowcount or 0)

        root = self.root / "assets" / "ocr" / "digits" / "confirmed"
        if root.exists():
            for child in root.iterdir():
                if child.is_dir() and clean_single_digit_value(child.name) is None:
                    shutil.rmtree(child)
                    removed_dirs.append(self.rel(child) or str(child))
        return {
            "deleted_rows": deleted_rows,
            "deduped_rows": deduped_rows,
            "removed_dirs": removed_dirs,
        }

    def insert_ocr_digit(
        self,
        *,
        value: str,
        image_path: str | Path,
        source_asset_id: str | None,
        map_id: str,
        source_ui: str,
    ) -> None:
        single_digit = clean_single_digit_value(value)
        if single_digit is None:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ocr_digits (id, value, image_path, source_asset_id, map_id, source_ui, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"digit_{uuid.uuid4().hex[:8]}",
                    single_digit,
                    self.rel(image_path),
                    source_asset_id,
                    map_id,
                    source_ui,
                    now_iso(),
                ),
            )

    def questions_dir(self, status: str = "confirmed") -> Path:
        path = self.root / "assets" / "questions" / sanitize_filename(status)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def question_result_conditions_dir(self) -> Path:
        path = self.root / "assets" / "questions" / "result_conditions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_question(
        self,
        *,
        question: str,
        answer: str,
        options: list[str],
        map_id: str,
        question_bbox: list[int] | None,
        option_bboxes: list[list[int]],
        confirm_bbox: list[int] | None,
        progress_bbox: list[int] | None,
        raw_path: str | Path | None,
        annotated_path: str | Path | None,
        source_asset_id: str | None = None,
    ) -> str:
        question_id = f"question_{uuid.uuid4().hex[:8]}"
        created_at = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO questions (
                    id, question, answer, options, source_asset_id, map_id,
                    question_bbox, option_bboxes, confirm_bbox, progress_bbox,
                    raw_path, annotated_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question_id,
                    question,
                    answer,
                    json.dumps(options, ensure_ascii=False),
                    source_asset_id,
                    map_id,
                    json.dumps(question_bbox, ensure_ascii=False) if question_bbox else None,
                    json.dumps(option_bboxes, ensure_ascii=False),
                    json.dumps(confirm_bbox, ensure_ascii=False) if confirm_bbox else None,
                    json.dumps(progress_bbox, ensure_ascii=False) if progress_bbox else None,
                    self.rel(raw_path),
                    self.rel(annotated_path),
                    created_at,
                    created_at,
                ),
            )
        return question_id

    def list_questions(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM questions
                    WHERE COALESCE(status, 'active') != 'deleted'
                    ORDER BY created_at DESC
                    """
                )
            )

    def delete_questions(self, question_ids: list[str]) -> int:
        ids = [question_id for question_id in question_ids if question_id]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE questions
                SET status = 'deleted', updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [now_iso(), *ids],
            )
            return int(cursor.rowcount or 0)

    def update_question(
        self,
        question_id: str,
        *,
        question: str | None = None,
        answer: str | None = None,
        options: list[str] | None = None,
        result_check_note: str | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        if question is not None:
            assignments.append("question = ?")
            params.append(question)
        if answer is not None:
            assignments.append("answer = ?")
            params.append(answer)
        if options is not None:
            assignments.append("options = ?")
            params.append(json.dumps(options[:4], ensure_ascii=False))
        if result_check_note is not None:
            assignments.append("result_check_note = ?")
            params.append(result_check_note)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        params.append(now_iso())
        params.append(question_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE questions SET {', '.join(assignments)} WHERE id = ?",
                params,
            )

    def latest_question_layout(self) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM questions
                WHERE COALESCE(status, 'active') != 'deleted'
                  AND question_bbox IS NOT NULL
                  AND option_bboxes IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()

    def update_latest_question_layout(
        self,
        *,
        confirm_bbox: list[int] | None = None,
        progress_bbox: list[int] | None = None,
    ) -> None:
        row = self.latest_question_layout()
        if not row:
            return
        assignments: list[str] = []
        params: list[Any] = []
        if confirm_bbox is not None:
            assignments.append("confirm_bbox = ?")
            params.append(json.dumps(confirm_bbox, ensure_ascii=False))
        if progress_bbox is not None:
            assignments.append("progress_bbox = ?")
            params.append(json.dumps(progress_bbox, ensure_ascii=False))
        if not assignments:
            return
        assignments.append("updated_at = ?")
        params.append(now_iso())
        params.append(row["id"])
        with self._connect() as conn:
            conn.execute(
                f"UPDATE questions SET {', '.join(assignments)} WHERE id = ?",
                params,
            )

    def find_question(self, question_text: str, option_texts: list[str] | None = None) -> sqlite3.Row | None:
        needle = normalize_text(question_text)
        needle_markers = question_markers(question_text)
        normalized_options = [normalize_text(option) for option in (option_texts or []) if normalize_text(option)]
        best: tuple[float, sqlite3.Row] | None = None
        for row in self.list_questions():
            haystack = normalize_text(row["question"])
            if needle and haystack and needle == haystack:
                return row
            question_score = 0.0
            if needle and haystack:
                question_score = SequenceMatcher(None, needle, haystack).ratio()
                if needle in haystack or haystack in needle:
                    question_score = max(
                        question_score,
                        min(len(needle), len(haystack)) / max(len(needle), len(haystack)),
                    )
            haystack_markers = question_markers(row["question"])
            if not question_markers_compatible(needle_markers, haystack_markers, question_score):
                continue

            option_score = 0.0
            option_overlap = 0
            stored_normalized: list[str] = []
            if normalized_options:
                try:
                    stored_options = json.loads(row["options"] or "[]")
                except json.JSONDecodeError:
                    stored_options = []
                stored_normalized = [normalize_text(option) for option in stored_options if normalize_text(option)]
                option_matches: list[float] = []
                for option in normalized_options:
                    best_option = 0.0
                    for stored in stored_normalized:
                        if option == stored:
                            best_option = 1.0
                        elif option in stored or stored in option:
                            best_option = max(best_option, min(len(option), len(stored)) / max(len(option), len(stored)))
                        else:
                            best_option = max(best_option, SequenceMatcher(None, option, stored).ratio())
                    if best_option:
                        option_matches.append(best_option)
                    if best_option >= 0.86:
                        option_overlap += 1
                if option_matches:
                    option_score = sum(option_matches) / max(len(normalized_options), len(stored_normalized), 1)

            if stored_normalized:
                needed_overlap = 1 if question_score >= 0.90 else 2
                if option_overlap < min(needed_overlap, len(normalized_options), len(stored_normalized)):
                    continue
            if question_score < 0.68:
                strong_option_match = (
                    bool(stored_normalized)
                    and question_score >= 0.50
                    and option_overlap >= min(3, len(normalized_options), len(stored_normalized))
                )
                if not strong_option_match:
                    continue
            score = max(question_score, question_score * 0.82 + option_score * 0.18)
            if score > 0:
                if best is None or score > best[0]:
                    best = (score, row)
        return best[1] if best and best[0] >= 0.68 else None

    def find_existing_question(
        self,
        question_text: str,
        option_texts: list[str] | None = None,
        threshold: float = 0.92,
    ) -> sqlite3.Row | None:
        needle = normalize_text(question_text)
        if not needle:
            return None
        needle_markers = question_markers(question_text)
        normalized_options = [normalize_text(option) for option in (option_texts or []) if normalize_text(option)]
        best: tuple[float, sqlite3.Row] | None = None
        for row in self.list_questions():
            haystack = normalize_text(row["question"])
            if not haystack:
                continue
            question_score = SequenceMatcher(None, needle, haystack).ratio()
            if needle in haystack or haystack in needle:
                question_score = max(
                    question_score,
                    min(len(needle), len(haystack)) / max(len(needle), len(haystack)),
                )
            haystack_markers = question_markers(row["question"])
            if not question_markers_compatible(needle_markers, haystack_markers, question_score):
                continue

            option_overlap = 0
            if normalized_options:
                try:
                    stored_options = json.loads(row["options"] or "[]")
                except json.JSONDecodeError:
                    stored_options = []
                stored_normalized = [normalize_text(option) for option in stored_options if normalize_text(option)]
                for option in normalized_options:
                    for stored in stored_normalized:
                        option_score = SequenceMatcher(None, option, stored).ratio()
                        if option == stored or option in stored or stored in option or option_score >= 0.86:
                            option_overlap += 1
                            break
                if stored_normalized:
                    needed_overlap = 1 if question_score >= 0.98 else min(2, len(normalized_options), len(stored_normalized))
                    if option_overlap < needed_overlap:
                        continue
                if question_score < threshold and option_overlap < min(3, len(normalized_options), len(stored_normalized) if stored_normalized else 4):
                    continue
            elif needle == haystack:
                return row
            elif question_score < threshold:
                continue

            if best is None or question_score > best[0]:
                best = (question_score, row)
        return best[1] if best else None

    def mark_question_answered(self, question_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE questions
                SET answer_count = COALESCE(answer_count, 0) + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_iso(), question_id),
            )

    def mark_question_result(self, question_id: str, success: bool) -> None:
        column = "success_count" if success else "failure_count"
        with self._connect() as conn:
            conn.execute(
                f"UPDATE questions SET {column} = COALESCE({column}, 0) + 1, updated_at = ? WHERE id = ?",
                (now_iso(), question_id),
            )

    def update_question_answer(
        self,
        question_id: str,
        *,
        answer: str,
        options: list[str] | None = None,
    ) -> None:
        assignments = ["answer = ?", "updated_at = ?"]
        params: list[Any] = [answer, now_iso()]
        if options and len(options) >= 4:
            assignments.insert(1, "options = ?")
            params.insert(1, json.dumps(options[:4], ensure_ascii=False))
        params.append(question_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE questions SET {', '.join(assignments)} WHERE id = ?",
                params,
            )

    def update_question_result_condition(
        self,
        question_id: str,
        *,
        kind: str,
        image_path: str | Path,
        bbox: list[int] | None,
    ) -> None:
        if kind not in {"success", "failure"}:
            raise ValueError("kind must be success or failure")
        path_column = "success_check_path" if kind == "success" else "failure_check_path"
        bbox_column = "success_check_bbox" if kind == "success" else "failure_check_bbox"
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE questions
                SET {path_column} = ?,
                    {bbox_column} = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    self.rel(image_path),
                    json.dumps(bbox, ensure_ascii=False) if bbox else None,
                    now_iso(),
                    question_id,
                ),
            )

    def add_bug_report(
        self,
        *,
        title: str,
        kind: str,
        script_name: str | None = None,
        step_id: str | None = None,
        step_name: str | None = None,
        step_type: str | None = None,
        log_excerpt: str = "",
        screenshot_path: str | Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        report_id = f"bug_{uuid.uuid4().hex[:8]}"
        created_at = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bug_reports (
                    id, title, kind, status, script_name, step_id, step_name,
                    step_type, log_excerpt, screenshot_path, report_text,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    report_id,
                    title,
                    kind,
                    script_name or "",
                    step_id or "",
                    step_name or "",
                    step_type or "",
                    log_excerpt,
                    self.rel(screenshot_path),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    created_at,
                    created_at,
                ),
            )
        return report_id

    def list_bug_reports(self, status_filter: str | None = "open") -> list[sqlite3.Row]:
        sql = "SELECT * FROM bug_reports WHERE COALESCE(status, 'open') != 'deleted'"
        params: list[Any] = []
        if status_filter and status_filter != "全部":
            sql += " AND status = ?"
            params.append(status_filter)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            return list(conn.execute(sql, params))

    def update_bug_report_status(
        self,
        report_id: str,
        status: str,
        *,
        report_text: str | None = None,
    ) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now_iso()]
        if status == "fixed":
            assignments.append("resolved_at = ?")
            params.append(now_iso())
        if status == "open":
            assignments.append("resolved_at = NULL")
        if report_text is not None:
            assignments.append("report_text = ?")
            params.append(report_text)
        params.append(report_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE bug_reports SET {', '.join(assignments)} WHERE id = ?",
                params,
            )

    def delete_bug_report(self, report_id: str) -> None:
        self.update_bug_report_status(report_id, "deleted")

    def script_loop_stats(self, script_name: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM script_run_stats WHERE script_name = ?",
                (script_name,),
            ).fetchone()
            timing = conn.execute(
                """
                SELECT
                    COUNT(1) AS timed_count,
                    SUM(CASE WHEN success THEN 1 ELSE 0 END) AS timed_success_count,
                    AVG(CASE WHEN success THEN duration_seconds END) AS avg_success_duration_seconds,
                    MIN(CASE WHEN success THEN duration_seconds END) AS best_success_duration_seconds,
                    MAX(CASE WHEN success THEN duration_seconds END) AS worst_success_duration_seconds
                FROM script_cycle_runs
                WHERE script_name = ?
                """,
                (script_name,),
            ).fetchone()
            latest = conn.execute(
                """
                SELECT duration_seconds, success, cycle_number, started_at, ended_at
                FROM script_cycle_runs
                WHERE script_name = ?
                ORDER BY ended_at DESC, id DESC
                LIMIT 1
                """,
                (script_name,),
            ).fetchone()
        if not row:
            stats = {
                "script_name": script_name,
                "loop_attempt_count": 0,
                "loop_completed_count": 0,
                "loop_failed_count": 0,
                "last_started_at": "",
                "last_completed_at": "",
                "last_failed_at": "",
                "updated_at": "",
            }
        else:
            stats = dict(row)
        timing_data = dict(timing) if timing else {}
        latest_data = dict(latest) if latest else {}
        stats.update(
            {
                "timed_count": int(timing_data.get("timed_count") or 0),
                "timed_success_count": int(timing_data.get("timed_success_count") or 0),
                "avg_success_duration_seconds": float(timing_data.get("avg_success_duration_seconds") or 0.0),
                "best_success_duration_seconds": float(timing_data.get("best_success_duration_seconds") or 0.0),
                "worst_success_duration_seconds": float(timing_data.get("worst_success_duration_seconds") or 0.0),
                "last_duration_seconds": float(latest_data.get("duration_seconds") or 0.0),
                "last_duration_success": bool(latest_data.get("success")) if latest else False,
                "last_duration_cycle_number": int(latest_data.get("cycle_number") or 0),
                "last_duration_started_at": str(latest_data.get("started_at") or ""),
                "last_duration_ended_at": str(latest_data.get("ended_at") or ""),
            }
        )
        return stats

    def record_script_loop_cycle(
        self,
        script_name: str,
        *,
        success: bool,
        duration_seconds: float | None = None,
        cycle_number: int | None = None,
        started_at: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        stats = self.script_loop_stats(script_name)
        duration = max(0.0, float(duration_seconds or 0.0))
        if stats.get("updated_at"):
            completed = int(stats.get("loop_completed_count") or 0)
            failed = int(stats.get("loop_failed_count") or 0)
            attempted = int(stats.get("loop_attempt_count") or 0)
            if success:
                completed += 1
            else:
                failed += 1
            attempted += 1
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE script_run_stats
                    SET loop_attempt_count = ?,
                        loop_completed_count = ?,
                        loop_failed_count = ?,
                        last_started_at = ?,
                        last_completed_at = CASE WHEN ? THEN ? ELSE last_completed_at END,
                        last_failed_at = CASE WHEN ? THEN last_failed_at ELSE ? END,
                        updated_at = ?
                    WHERE script_name = ?
                    """,
                    (
                        attempted,
                        completed,
                        failed,
                        now,
                        1 if success else 0,
                        now,
                        1 if success else 0,
                        now,
                        now,
                        script_name,
                    ),
                )
        else:
            attempted = 1
            completed = 1 if success else 0
            failed = 0 if success else 1
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO script_run_stats (
                        script_name, loop_attempt_count, loop_completed_count,
                        loop_failed_count, last_started_at, last_completed_at,
                        last_failed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        script_name,
                        attempted,
                        completed,
                        failed,
                        now,
                        now if success else None,
                        None if success else now,
                        now,
                    ),
                )
        if duration_seconds is not None:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO script_cycle_runs (
                        id, script_name, cycle_number, success, duration_seconds,
                        started_at, ended_at, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"cycle_{uuid.uuid4().hex[:8]}",
                        script_name,
                        int(cycle_number or attempted),
                        1 if success else 0,
                        duration,
                        started_at or now,
                        now,
                        notes,
                    ),
                )
        return self.script_loop_stats(script_name)

    def list_script_cycle_runs(self, script_name: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM script_cycle_runs
                WHERE script_name = ?
                ORDER BY ended_at DESC, id DESC
                LIMIT ?
                """,
                (script_name, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_movement_sample(
        self,
        *,
        map_id: str,
        before_game_coord: list[int] | tuple[int, int] | None = None,
        after_game_coord: list[int] | tuple[int, int] | None = None,
        click_relative_to_character: list[int] | tuple[int, int] | None = None,
        click_angle: float | None = None,
        click_radius: int | None = None,
        actual_delta: list[int] | tuple[int, int] | None = None,
        duration: float = 0.0,
        success: bool = True,
        stuck: bool = False,
        progress_score: float = 0.0,
        direction: str | None = None,
        script_name: str | None = None,
        screen_point: list[int] | tuple[int, int] | None = None,
        start_coord: list[int] | tuple[int, int] | None = None,
        end_coord: list[int] | tuple[int, int] | None = None,
        strategy: str | None = None,
        dedupe: bool = False,
    ) -> bool:
        before = _json_list(before_game_coord, _json_list(start_coord, [0, 0]))
        after = _json_list(after_game_coord, _json_list(end_coord, before))
        delta = _json_list(
            actual_delta,
            [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])],
        )
        relative = _json_list(click_relative_to_character, [0, 0])
        if click_radius is None:
            click_radius = int(round((relative[0] ** 2 + relative[1] ** 2) ** 0.5))
        if click_angle is None:
            click_angle = _angle_for_vector(relative)
        direction = direction or _direction_for_angle(click_angle)
        plausible = movement_delta_is_plausible(delta)
        if (not plausible and delta != [0, 0]) or (not success) or stuck or float(progress_score) <= 0:
            if not plausible and delta != [0, 0]:
                reason = "implausible_delta"
            elif stuck:
                reason = "stuck"
            elif not success:
                reason = "failure"
            else:
                reason = "no_progress"
            self.record_bad_movement_event(
                map_id=map_id,
                before_game_coord=before,
                after_game_coord=after,
                click_relative_to_character=relative,
                click_angle=click_angle,
                click_radius=click_radius,
                actual_delta=delta,
                duration=duration,
                success=success,
                stuck=stuck,
                progress_score=progress_score,
                direction=direction,
                strategy=strategy,
                script_name=script_name,
                reason=reason,
            )
            return False
        compat_point = _json_list(screen_point, [0, 0])
        screen_point_json = json.dumps(compat_point, ensure_ascii=False)
        delta_json = json.dumps(delta, ensure_ascii=False)
        before_json = json.dumps(before, ensure_ascii=False)
        after_json = json.dumps(after, ensure_ascii=False)
        relative_json = json.dumps(relative, ensure_ascii=False)
        with self._connect() as conn:
            if dedupe:
                exists = conn.execute(
                    """
                    SELECT 1 FROM movement_samples
                    WHERE map_id = ?
                      AND click_relative_to_character = ?
                      AND actual_delta = ?
                      AND COALESCE(strategy, '') = COALESCE(?, '')
                    LIMIT 1
                    """,
                    (map_id, relative_json, delta_json, strategy),
                ).fetchone()
                if exists:
                    return False
            conn.execute(
                """
                INSERT INTO movement_samples (
                    id, map_id, direction, screen_point, start_coord, end_coord, delta,
                    before_game_coord, after_game_coord, click_relative_to_character,
                    click_angle, click_radius, actual_delta, duration, success, stuck,
                    progress_score, strategy, script_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"move_{uuid.uuid4().hex[:8]}",
                    map_id,
                    direction,
                    screen_point_json,
                    before_json,
                    after_json,
                    delta_json,
                    before_json,
                    after_json,
                    relative_json,
                    float(click_angle),
                    int(click_radius),
                    delta_json,
                    float(duration),
                    1 if success else 0,
                    1 if stuck else 0,
                    float(progress_score),
                    strategy,
                    script_name,
                    now_iso(),
                ),
            )
        return True

    def record_bad_movement_event(
        self,
        *,
        map_id: str,
        before_game_coord: list[int] | tuple[int, int],
        after_game_coord: list[int] | tuple[int, int],
        click_relative_to_character: list[int] | tuple[int, int],
        click_angle: float | None,
        click_radius: int | None,
        actual_delta: list[int] | tuple[int, int],
        duration: float,
        success: bool,
        stuck: bool,
        progress_score: float,
        direction: str | None = None,
        strategy: str | None = None,
        script_name: str | None = None,
        reason: str | None = None,
        source_sample_id: str | None = None,
    ) -> bool:
        before = _json_list(before_game_coord, [0, 0])
        after = _json_list(after_game_coord, before)
        delta = _json_list(actual_delta, [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])])
        relative = _json_list(click_relative_to_character, [0, 0])
        if click_radius is None:
            click_radius = int(round((relative[0] ** 2 + relative[1] ** 2) ** 0.5))
        if click_angle is None:
            click_angle = _angle_for_vector(relative)
        direction = direction or _direction_for_angle(click_angle)
        if reason is None:
            if stuck:
                reason = "stuck"
            elif not success:
                reason = "failure"
            elif float(progress_score) <= 0:
                reason = "no_progress"
            else:
                reason = "bad_sample"
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO movement_bad_events (
                    id, source_sample_id, map_id, direction, before_game_coord, after_game_coord,
                    click_relative_to_character, click_angle, click_radius, actual_delta,
                    duration, success, stuck, progress_score, strategy, script_name, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"badmove_{uuid.uuid4().hex[:8]}",
                    source_sample_id,
                    map_id,
                    direction,
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(after, ensure_ascii=False),
                    json.dumps(relative, ensure_ascii=False),
                    float(click_angle),
                    int(click_radius),
                    json.dumps(delta, ensure_ascii=False),
                    float(duration),
                    1 if success else 0,
                    1 if stuck else 0,
                    float(progress_score),
                    strategy,
                    script_name,
                    reason,
                    now_iso(),
                ),
            )
            return int(cursor.rowcount or 0) > 0

    def list_movement_samples(
        self,
        map_id: str | None = None,
        limit: int = 200,
        script_name: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM movement_samples"
        params: list[Any] = []
        if map_id:
            sql += " WHERE map_id = ?"
            params.append(map_id)
        if script_name:
            sql += " AND script_name = ?" if params else " WHERE script_name = ?"
            params.append(script_name)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return list(conn.execute(sql, params))

    def movement_sample_health(
        self,
        *,
        map_id: str | None = None,
        script_name: str | None = None,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if map_id:
            where.append("map_id = ?")
            params.append(map_id)
        if script_name:
            where.append("script_name = ?")
            params.append(script_name)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failure_count,
                    SUM(CASE WHEN stuck = 1 THEN 1 ELSE 0 END) AS stuck_count,
                    SUM(CASE WHEN progress_score <= 0 THEN 1 ELSE 0 END) AS no_progress_count,
                    SUM(CASE WHEN success = 0 OR stuck = 1 OR progress_score <= 0 THEN 1 ELSE 0 END) AS cleanup_candidates
                FROM movement_samples
                {clause}
                """,
                params,
            ).fetchone()
            archived_row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failure_count,
                    SUM(CASE WHEN stuck = 1 THEN 1 ELSE 0 END) AS stuck_count,
                    SUM(CASE WHEN progress_score <= 0 THEN 1 ELSE 0 END) AS no_progress_count
                FROM movement_bad_events
                {clause}
                """,
                params,
            ).fetchone()
        total = int(row["total"] or 0)
        success_count = int(row["success_count"] or 0)
        failure_count = int(row["failure_count"] or 0)
        stuck_count = int(row["stuck_count"] or 0)
        no_progress_count = int(row["no_progress_count"] or 0)
        cleanup_candidates = int(row["cleanup_candidates"] or 0)
        archived_bad = int(archived_row["total"] or 0)
        return {
            "total": total,
            "success": success_count,
            "failure": failure_count,
            "stuck": stuck_count,
            "no_progress": no_progress_count,
            "cleanup_candidates": cleanup_candidates,
            "archived_bad": archived_bad,
            "archived_failure": int(archived_row["failure_count"] or 0),
            "archived_stuck": int(archived_row["stuck_count"] or 0),
            "archived_no_progress": int(archived_row["no_progress_count"] or 0),
            "success_rate": success_count / total if total else 0.0,
        }

    def cleanup_bad_movement_samples(
        self,
        *,
        map_id: str | None = None,
        script_name: str | None = None,
    ) -> int:
        where = ["(success = 0 OR stuck = 1 OR progress_score <= 0)"]
        params: list[Any] = []
        if map_id:
            where.append("map_id = ?")
            params.append(map_id)
        if script_name:
            where.append("script_name = ?")
            params.append(script_name)
        with self._connect() as conn:
            rows = list(conn.execute(f"SELECT * FROM movement_samples WHERE {' AND '.join(where)}", params))
            for row in rows:
                before = _json_list(row["before_game_coord"] or row["start_coord"], [0, 0])
                after = _json_list(row["after_game_coord"] or row["end_coord"], before)
                relative = _json_list(row["click_relative_to_character"], [0, 0])
                delta = _json_list(row["actual_delta"] or row["delta"], [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])])
                progress_score = float(row["progress_score"] or 0)
                stuck = bool(row["stuck"])
                success = bool(row["success"])
                if stuck:
                    reason = "stuck"
                elif not success:
                    reason = "failure"
                elif progress_score <= 0:
                    reason = "no_progress"
                else:
                    reason = "cleanup"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO movement_bad_events (
                        id, source_sample_id, map_id, direction, before_game_coord, after_game_coord,
                        click_relative_to_character, click_angle, click_radius, actual_delta,
                        duration, success, stuck, progress_score, strategy, script_name, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"badmove_{uuid.uuid4().hex[:8]}",
                        row["id"],
                        row["map_id"],
                        row["direction"] or "",
                        json.dumps(before, ensure_ascii=False),
                        json.dumps(after, ensure_ascii=False),
                        json.dumps(relative, ensure_ascii=False),
                        row["click_angle"],
                        row["click_radius"],
                        json.dumps(delta, ensure_ascii=False),
                        float(row["duration"] or 0),
                        1 if success else 0,
                        1 if stuck else 0,
                        progress_score,
                        row["strategy"],
                        row["script_name"],
                        reason,
                        now_iso(),
                    ),
                )
            cursor = conn.execute(
                f"DELETE FROM movement_samples WHERE {' AND '.join(where)}",
                params,
            )
            return int(cursor.rowcount or 0)

    def aggregate_movement_samples(
        self,
        map_id: str | None = None,
        limit: int = 2000,
        script_name: str | None = None,
    ) -> dict[str, Any]:
        rows = [
            row
            for row in self.list_movement_samples(map_id, limit=limit, script_name=script_name)
            if not str(row["strategy"] or "").startswith("legacy_")
            and str(row["strategy"] or "") != "legacy_screen_point_backfill"
        ]
        direction_counts: dict[str, list[int]] = {}
        radius_counts: dict[str, list[int]] = {}
        angle_counts: dict[str, list[int]] = {}
        map_radius_counts: dict[str, list[int]] = {}
        stuck_areas: dict[str, int] = {}
        for row in rows:
            success = bool(row["success"]) and not bool(row["stuck"])
            click_angle = row["click_angle"]
            direction = _direction_for_angle(float(click_angle)) if click_angle is not None else str(row["direction"] or "unknown")
            radius = row["click_radius"]
            if radius is None:
                relative = _json_list(row["click_relative_to_character"], [0, 0])
                radius = int(round((relative[0] ** 2 + relative[1] ** 2) ** 0.5))
            radius_key = str(int(radius))
            for bucket, key in (
                (direction_counts, direction),
                (angle_counts, direction),
                (radius_counts, radius_key),
                (map_radius_counts, radius_key),
            ):
                bucket.setdefault(key, [0, 0])
                bucket[key][1] += 1
                if success:
                    bucket[key][0] += 1
            if bool(row["stuck"]):
                before = _json_list(row["before_game_coord"] or row["start_coord"], [0, 0])
                key = f"{before[0]},{before[1]}"
                stuck_areas[key] = stuck_areas.get(key, 0) + 1

        def rates(counts: dict[str, list[int]]) -> dict[str, float]:
            return {
                key: round(successes / attempts, 4)
                for key, (successes, attempts) in counts.items()
                if attempts > 0
            }

        best_radius: int | None = None
        best_score = -1.0
        for key, (successes, attempts) in map_radius_counts.items():
            if attempts <= 0:
                continue
            score = (successes / attempts) * min(1.0, attempts / 8.0)
            if score > best_score:
                best_score = score
                best_radius = int(key)
        stuck_rows = sorted(stuck_areas.items(), key=lambda item: item[1], reverse=True)
        return {
            "direction_success_rates": rates(direction_counts),
            "radius_success_rates": rates(radius_counts),
            "map_best_radius": best_radius,
            "stuck_areas": [
                {"coord": [int(key.split(",")[0]), int(key.split(",")[1])], "stuck_count": count}
                for key, count in stuck_rows[:20]
            ],
            "approach_angle_success": rates(angle_counts),
            "sample_count": len(rows),
        }

    def import_legacy_movement_samples(self, path: str | Path, map_id: str = "map_001") -> int:
        """Import calibrated walking taps from the older sqsd_ai project."""
        source = Path(path)
        if not source.exists():
            return 0
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            return 0
        moves = payload.get("moves") if isinstance(payload, dict) else []
        imported = 0
        for item in moves or []:
            if not isinstance(item, dict):
                continue
            try:
                tap_x = int(item.get("tap_x", 0) or 0)
                tap_y = int(item.get("tap_y", 0) or 0)
                dx = int(item.get("dx", 0) or 0)
                dy = int(item.get("dy", 0) or 0)
            except (TypeError, ValueError):
                continue
            if tap_x <= 0 or tap_y <= 0 or not movement_delta_is_plausible([dx, dy]):
                continue
            relative = [tap_x - 960, tap_y - 560]
            if self.add_movement_sample(
                map_id=map_id,
                before_game_coord=[0, 0],
                after_game_coord=[dx, dy],
                click_relative_to_character=relative,
                click_angle=_angle_for_vector(relative),
                click_radius=int(round((relative[0] ** 2 + relative[1] ** 2) ** 0.5)),
                actual_delta=[dx, dy],
                duration=0.0,
                success=True,
                stuck=False,
                progress_score=abs(dx) + abs(dy),
                direction=f"legacy:{str(item.get('label') or f'{dx:+d},{dy:+d}')}",
                screen_point=[tap_x, tap_y],
                start_coord=[0, 0],
                end_coord=[dx, dy],
                strategy="legacy_relative_import",
                dedupe=True,
            ):
                imported += 1
        return imported


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    compact = re.sub(r"\s+", "", value).strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", compact)


def question_markers(value: str | None) -> list[str]:
    if not value:
        return []
    compact = normalize_text(value)
    markers: set[str] = set(re.findall(r"第[一二三四五六七八九十\d]+|[一二三四五六七八九十\d]+块", value))
    for pattern, label in (
        (r"请问(.+?)族长是谁", "族长"),
        (r"(.+?)族长是谁", "族长"),
        (r"请问(.+?)叫什么名字", "名字"),
        (r"(.+?)叫什么名字", "名字"),
    ):
        match = re.search(pattern, compact)
        if match:
            subject = match.group(1).strip()
            subject = re.sub(r"^(原版|在|请问)", "", subject)
            subject = re.sub(r"的$", "", subject)
            if subject:
                markers.add(f"{label}:{subject}")
            break
    return sorted(markers)


def question_markers_compatible(left: list[str], right: list[str], question_score: float) -> bool:
    left_set = set(left)
    right_set = set(right)
    if left_set and right_set:
        return left_set == right_set or question_score >= 0.995
    if left_set or right_set:
        return question_score >= 0.96
    return True
