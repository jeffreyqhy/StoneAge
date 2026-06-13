from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


STEP_LABELS: dict[str, str] = {
    "click": "点击",
    "wait": "等待",
    "image_check": "识别图片",
    "ocr_text": "判断文字",
    "ocr_number": "判断数字",
    "verify_code": "输入验证码",
    "move_to_game_coord": "移动到游戏坐标",
    "find_target": "找目标",
    "click_target": "点击目标",
    "dialog": "对话处理",
    "question": "答题",
    "battle": "战斗",
    "condition": "条件分支",
    "loop": "循环",
    "jump": "跳转",
    "recovery": "异常恢复",
    "subflow": "调用子流程",
}


STEP_TYPES = list(STEP_LABELS)


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def create_flow(script_name: str = "副本_001") -> dict[str, Any]:
    return {
        "version": 1,
        "app": "StoneAge Script Studio",
        "script_name": script_name,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "settings": {
            "default_map": "map_001",
            "interactive_review": True,
            "stop_on_failure": True,
            "pre_dungeon_retry_limit": 2,
            "game_coord_region": [1720, 80, 200, 120],
            "emulator": {
                "name": "MuMu",
                "adb_endpoint": "127.0.0.1:16384",
                "expected_resolution": [1920, 1080],
            },
        },
        "steps": [],
    }


def create_step(step_type: str, name: str | None = None) -> dict[str, Any]:
    if step_type not in STEP_LABELS:
        raise ValueError(f"Unsupported step type: {step_type}")
    step_id = short_id("step")
    label = STEP_LABELS[step_type]
    step = {
        "id": step_id,
        "type": step_type,
        "name": name or f"{step_type}_001",
        "label": label,
        "enabled": True,
        "input": default_input(step_type),
        "output": {},
        "success_condition": {"mode": "default"},
        "failure_condition": {"mode": "default"},
        "retry_count": 0,
        "timeout": 10.0,
        "on_success": "next",
        "on_failure": "stop",
        "screenshots": {},
        "assets": [],
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    if step_type in {"loop", "condition"}:
        step["children"] = []
    return step


def clone_step(step: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(step)
    refresh_step_identity(cloned)
    return cloned


def refresh_step_identity(step: dict[str, Any]) -> None:
    step["id"] = short_id("step")
    step["name"] = f'{step.get("name", step.get("type", "step"))}_copy'
    step["created_at"] = utc_now_iso()
    step["updated_at"] = utc_now_iso()
    for child in step.get("children") or []:
        refresh_step_identity(child)


def default_input(step_type: str) -> dict[str, Any]:
    if step_type == "click":
        return {
            "click_type": "fixed_screen_coord",
            "screen_coord": [0, 0],
            "click_count": 1,
            "click_interval": 0.08,
            "wait_before": 0.0,
            "wait_after": 1.0,
            "confirm_success": False,
        }
    if step_type == "wait":
        return {"duration": 1.0}
    if step_type in {"image_check", "find_target", "click_target"}:
        return {
            "asset_id": None,
            "template_path": None,
            "bbox": None,
            "threshold": 0.85,
            "wait_until_found": True,
            "poll_interval": 0.4,
            "wait_after_found": 0.5,
            "instant_check": False,
            "click_mode": "center",
            "click_offset": None,
            "click_count": 1,
            "click_interval": 0.08,
            "action_on_found": "next",
            "action_on_missing": "fail",
        }
    if step_type in {"ocr_text", "ocr_number"}:
        return {
            "asset_id": None,
            "bbox": None,
            "last_result": "",
            "confidence": 0.0,
            "save_unknown_to_pending": True,
        }
    if step_type == "verify_code":
        return {
            "digit_bbox": None,
            "input_coord": [0, 0],
            "confirm_coord": [0, 0],
            "input_click_count": 1,
            "confirm_click_count": 1,
            "click_interval": 0.08,
            "expected_length": 4,
            "min_confidence": 0.50,
            "wait_before_capture": 1.0,
            "retry_count": 2,
            "retry_interval": 0.7,
            "wait_after_focus": 0.20,
            "wait_after_input": 0.20,
            "wait_after_confirm": 0.80,
            "last_result": "",
            "confidence": 0.0,
            "save_unknown_to_pending": True,
        }
    if step_type == "question":
        return {
            "target_correct_count": 1,
            "max_attempts": 1,
            "question_region": None,
            "option_regions": [],
            "confirm_region": None,
            "progress_region": None,
            "correct_region": None,
            "wrong_region": None,
            "unknown_policy": "choose_c",
            "answer_click_count": 1,
            "confirm_click_count": 1,
            "click_interval": 0.08,
            "wait_after_answer": 0.8,
            "use_question_visual_match": False,
            "question_visual_threshold": 0.90,
            "option_visual_threshold": 0.90,
        }
    if step_type == "dialog":
        return {
            "title": "脚本执行完成",
            "message": "脚本执行完成。",
            "log_message": True,
        }
    if step_type == "read_game_coord":
        return {"coord_region": None, "last_coord": None}
    if step_type == "move_to_game_coord":
        return {
            "target_coord": [0, 0],
            "tolerance": 0,
            "max_seconds": 60,
            "poll_interval": 0.35,
            "max_coord_jump": 12,
            "arrival_mode": "exact",
            "exact_target": True,
            "waypoint_lookahead": 5,
            "use_approach_points": False,
            "approach_radius": 1,
            "max_approach_radius": 3,
            "click_radii": [180, 220, 260, 300],
            "fine_click_radii": [80, 110, 140],
            "max_click_radius": 300,
            "fine_tune_distance": 3,
            "target_backoff_enabled": True,
            "target_backoff_distance": 3,
            "target_backoff_trigger_distance": 1,
            "target_backoff_max_attempts": 3,
            "exact_direct_click_enabled": True,
            "exact_direct_click_distance": 3,
            "exact_direct_click_attempts": 8,
            "exact_direct_click_min_radius": 70,
            "exact_direct_click_tile_radius": 54,
            "exact_direct_click_max_radius": 260,
            "stable_coord_frames": 1,
            "stable_coord_agreement": 1,
            "movement_settle_seconds": 0.8,
            "character_screen_position": None,
            "coord_recovery_enabled": True,
            "coord_recovery_missing_reads": 2,
            "coord_recovery_attempts": 3,
            "coord_recovery_wait_seconds": 0.45,
        }
    if step_type == "loop":
        return {
            "times": 1,
            "loop_mode": "fixed_count",
            "exit_condition": {
                "type": "none",
                "template_path": None,
                "asset_id": None,
                "threshold": 0.85,
            },
            "exit_check_timing": "before_each_step",
            "fail_when_max_reached": True,
            "body_step_ids": [],
            "body_start_id": None,
            "body_end_id": None,
            "break_on_failure": True,
            "first_step_skip_exits_loop": False,
        }
    if step_type == "condition":
        return {
            "branch_timeout": 0.4,
            "branch_threshold": 0.85,
            "branch_min_margin": 0.03,
            "on_no_match": "skip",
        }
    if step_type == "subflow":
        return {"flow_path": ""}
    return {}


def save_flow(path: Path, flow: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flow["updated_at"] = utc_now_iso()
    path.write_text(json.dumps(flow, ensure_ascii=False, indent=2), encoding="utf-8")


def load_flow(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
