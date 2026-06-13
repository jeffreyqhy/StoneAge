from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ACTOR_ALIASES: dict[str, str] = {
    "role": "role",
    "角色": "role",
    "人物": "role",
    "人": "role",
    "pet": "pet",
    "宠物": "pet",
    "宠": "pet",
    "item": "item",
    "道具": "item",
    "global": "global",
    "全局": "global",
}

ACTION_ALIASES: dict[str, str] = {
    "放风": "wind_spirit_l5",
    "风": "wind_spirit_l5",
    "风的精灵": "wind_spirit_l5",
    "风的精灵lv5": "wind_spirit_l5",
    "wind": "wind_spirit_l5",
    "wind_l5": "wind_spirit_l5",
    "真空刃": "vacuum_blade",
    "开口": "vacuum_blade",
    "vacuum": "vacuum_blade",
    "恩惠6": "grace_l6",
    "恩惠lv6": "grace_l6",
    "主治疗": "grace_l6",
    "grace6": "grace_l6",
    "恩惠5": "grace_l5",
    "恩惠lv5": "grace_l5",
    "备用治疗": "grace_l5",
    "grace5": "grace_l5",
    "神之撕裂": "divine_rip",
    "撕裂": "divine_rip",
    "divine": "divine_rip",
    "普通攻击": "normal_attack",
    "攻击": "normal_attack",
    "扔斧子": "normal_attack",
    "防御": "defend",
    "阿布的水": "abu_water",
    "补气": "abu_water",
    "补气道具": "abu_water",
    "生鱼片": "luxury_sashimi",
    "豪华船生鱼片": "luxury_sashimi",
    "pk用豪华船生鱼片": "luxury_sashimi",
}

ACTION_NAMES: dict[str, str] = {
    "wind_spirit_l5": "风的精灵 LV.5",
    "vacuum_blade": "真空刃",
    "grace_l6": "恩惠 LV.6",
    "grace_l5": "恩惠 LV.5",
    "divine_rip": "神之撕裂",
    "normal_attack": "普通攻击/扔斧子",
    "defend": "防御",
    "abu_water": "阿布的水/补气道具",
    "luxury_sashimi": "PK用豪华船生鱼片",
}

ACTION_STEP_SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "wind_spirit_l5": ("打开咒术", "滚动技能列表", "选择风的精灵", "选择目标/确认"),
    "vacuum_blade": ("打开咒术", "选择真空刃", "选择目标"),
    "grace_l6": ("打开咒术", "选择恩惠 LV.6", "选择治疗目标"),
    "grace_l5": ("打开咒术", "选择恩惠 LV.5", "选择治疗目标"),
    "divine_rip": ("打开宠物技能", "选择神之撕裂", "选择目标"),
    "normal_attack": ("选择攻击", "选择目标"),
    "defend": ("选择防御",),
    "abu_water": ("打开道具", "选择阿布的水", "选择目标/确认"),
    "luxury_sashimi": ("打开道具", "选择生鱼片", "选择治疗目标"),
}

ACTOR_PRESETS: tuple[str, ...] = (
    "1号人物",
    "2号人物",
    "3号人物",
    "4号人物",
    "5号人物",
    "1号宠物",
    "2号宠物",
    "3号宠物",
    "4号宠物",
    "5号宠物",
    "道具",
)

ACTION_PRESETS: tuple[str, ...] = (
    "放风",
    "真空刃",
    "恩惠6",
    "恩惠5",
    "神之撕裂",
    "普通攻击",
    "防御",
    "阿布的水",
    "生鱼片",
)

DEFAULT_REQUIRED_ACTIONS: tuple[tuple[str, int | None, str], ...] = (
    ("role", 1, "wind_spirit_l5"),
    ("role", 2, "wind_spirit_l5"),
    ("role", 3, "grace_l6"),
    ("role", 1, "vacuum_blade"),
    ("role", 2, "vacuum_blade"),
    ("role", 4, "vacuum_blade"),
    ("role", 5, "vacuum_blade"),
    ("pet", 1, "divine_rip"),
    ("pet", 2, "divine_rip"),
    ("pet", 3, "divine_rip"),
    ("pet", 4, "divine_rip"),
    ("item", None, "abu_water"),
    ("item", None, "luxury_sashimi"),
)


@dataclass(frozen=True)
class ActorRef:
    actor_type: str
    index: int | None = None

    @property
    def label(self) -> str:
        if self.actor_type == "role" and self.index is not None:
            return f"{self.index}号人物"
        if self.actor_type == "pet" and self.index is not None:
            return f"{self.index}号宠物"
        if self.actor_type == "item":
            return "道具"
        return self.actor_type


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace(" ", "").replace("_", "").replace("-", "")
    text = text.replace(".", "")
    return text


def normalize_action(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("动作不能为空。")
    if text in ACTION_NAMES:
        return text
    compact = normalize_key(text)
    if compact in ACTION_ALIASES:
        return ACTION_ALIASES[compact]
    for alias, key in ACTION_ALIASES.items():
        if normalize_key(alias) == compact:
            return key
    return compact


def normalize_actor(actor: str | ActorRef, index: int | None = None) -> ActorRef:
    if isinstance(actor, ActorRef):
        return actor
    text = str(actor or "").strip()
    if not text:
        raise ValueError("角色/宠物不能为空。")

    detected_index = index
    match = re.search(r"([1-5])\s*号?", text)
    if detected_index is None and match:
        detected_index = int(match.group(1))

    compact = normalize_key(re.sub(r"[1-5]\s*号?", "", text))
    actor_type = ACTOR_ALIASES.get(compact)
    if actor_type is None:
        if "宠" in text:
            actor_type = "pet"
        elif "道具" in text or "item" in compact:
            actor_type = "item"
        elif "全局" in text or "global" in compact:
            actor_type = "global"
        else:
            actor_type = "role"

    if actor_type in {"role", "pet"}:
        if detected_index is None:
            raise ValueError(f"{actor_type} 动作需要 1-5 号编号。")
        if detected_index < 1 or detected_index > 5:
            raise ValueError("人物/宠物编号必须在 1-5 之间。")
    else:
        detected_index = None

    return ActorRef(actor_type=actor_type, index=detected_index)


def action_id(actor: ActorRef, action_key: str) -> str:
    index = "all" if actor.index is None else str(actor.index)
    return f"{actor.actor_type}:{index}:{action_key}"


def display_action_name(action_key: str) -> str:
    return ACTION_NAMES.get(action_key, action_key)


def suggested_step_labels(action_key: str) -> tuple[str, ...]:
    return ACTION_STEP_SUGGESTIONS.get(normalize_action(action_key), ("步骤 1",))


def suggest_step_label(action_key: str, existing_labels: list[str] | tuple[str, ...]) -> str:
    existing = {str(label or "").strip() for label in existing_labels}
    suggestions = suggested_step_labels(action_key)
    for label in suggestions:
        if label not in existing:
            return label
    return f"步骤 {len(existing) + 1}"


class DeepSeaActionLibrary:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": 1,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "actions": {},
            }
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = self.path.with_suffix(f".broken_{int(datetime.now().timestamp())}.json")
            self.path.replace(backup)
            return {
                "version": 1,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "actions": {},
                "recovered_from": str(backup.name),
            }
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("created_at", now_iso())
        data.setdefault("updated_at", now_iso())
        data.setdefault("actions", {})
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = now_iso()
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_action(self, actor: str | ActorRef, action: str) -> dict[str, Any] | None:
        actor_ref = normalize_actor(actor)
        key = normalize_action(action)
        return self.data.get("actions", {}).get(action_id(actor_ref, key))

    def ensure_action(self, actor: str | ActorRef, action: str) -> dict[str, Any]:
        actor_ref = normalize_actor(actor)
        action_key = normalize_action(action)
        item_id = action_id(actor_ref, action_key)
        actions = self.data.setdefault("actions", {})
        item = actions.get(item_id)
        if item is None:
            item = {
                "id": item_id,
                "actor_type": actor_ref.actor_type,
                "actor_index": actor_ref.index,
                "actor_label": actor_ref.label,
                "action_key": action_key,
                "action_name": display_action_name(action_key),
                "steps": [],
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            actions[item_id] = item
        return item

    def add_step(
        self,
        *,
        actor: str | ActorRef,
        action: str,
        step_label: str,
        template_path: str | None = None,
        asset_id: str | None = None,
        bbox: list[int] | None = None,
        click_point: list[int] | None = None,
        click_offset: list[int] | None = None,
        search_bbox: list[int] | None = None,
        step_type: str = "template_click",
        swipe_start: list[int] | None = None,
        swipe_end: list[int] | None = None,
        duration_ms: int = 450,
        threshold: float = 0.85,
        wait_after: float = 0.4,
        note: str = "",
        replace_same_label: bool = True,
    ) -> dict[str, Any]:
        item = self.ensure_action(actor, action)
        label = str(step_label or "").strip() or f"步骤 {len(item['steps']) + 1}"
        step = {
            "id": f"deepsea_step_{uuid.uuid4().hex[:8]}",
            "label": label,
            "step_type": str(step_type or "template_click"),
            "template_path": template_path,
            "asset_id": asset_id,
            "bbox": list(bbox) if bbox else None,
            "click_point": list(click_point) if click_point else None,
            "click_offset": list(click_offset) if click_offset else None,
            "search_bbox": list(search_bbox) if search_bbox else None,
            "swipe_start": list(swipe_start) if swipe_start else None,
            "swipe_end": list(swipe_end) if swipe_end else None,
            "duration_ms": int(duration_ms),
            "threshold": float(threshold),
            "wait_after": float(wait_after),
            "note": str(note or ""),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        if replace_same_label:
            for index, old_step in enumerate(item["steps"]):
                if str(old_step.get("label") or "") == label:
                    step["id"] = old_step.get("id") or step["id"]
                    step["created_at"] = old_step.get("created_at") or step["created_at"]
                    item["steps"][index] = step
                    item["updated_at"] = now_iso()
                    return step
        item["steps"].append(step)
        item["updated_at"] = now_iso()
        return step

    def list_actions(self) -> list[dict[str, Any]]:
        actions = list((self.data.get("actions") or {}).values())
        return sorted(
            actions,
            key=lambda item: (
                str(item.get("actor_type") or ""),
                int(item.get("actor_index") or 0),
                str(item.get("action_key") or ""),
            ),
        )

    def missing_required_actions(
        self,
        required: tuple[tuple[str, int | None, str], ...] = DEFAULT_REQUIRED_ACTIONS,
    ) -> list[dict[str, Any]]:
        actions = self.data.get("actions") or {}
        missing: list[dict[str, Any]] = []
        for actor_type, actor_index, action_key in required:
            actor = ActorRef(actor_type, actor_index)
            item = actions.get(action_id(actor, action_key))
            if not item or not item.get("steps"):
                missing.append(
                    {
                        "actor_type": actor_type,
                        "actor_index": actor_index,
                        "actor_label": actor.label,
                        "action_key": action_key,
                        "action_name": display_action_name(action_key),
                    }
                )
        return missing

    def build_click_sequence(self, actor: str | ActorRef, action: str) -> list[dict[str, Any]]:
        item = self.get_action(actor, action)
        if not item:
            return []
        sequence: list[dict[str, Any]] = []
        for step in item.get("steps") or []:
            step_type = str(step.get("step_type") or "template_click")
            if step_type == "swipe":
                sequence.append(
                    {
                        "type": "swipe",
                        "name": step.get("label") or display_action_name(str(item.get("action_key") or "")),
                        "swipe_start": step.get("swipe_start"),
                        "swipe_end": step.get("swipe_end"),
                        "duration_ms": int(step.get("duration_ms") or 450),
                        "wait_after": float(step.get("wait_after") or 0.4),
                    }
                )
                continue
            if step_type in {"tap", "recorded_tap"}:
                sequence.append(
                    {
                        "type": "tap",
                        "name": step.get("label") or display_action_name(str(item.get("action_key") or "")),
                        "click_point": step.get("click_point"),
                        "wait_after": float(step.get("wait_after") or 0.4),
                    }
                )
                continue
            if not step.get("template_path"):
                continue
            sequence.append(
                {
                    "type": "template_click",
                    "name": step.get("label") or display_action_name(str(item.get("action_key") or "")),
                    "template_path": step.get("template_path"),
                    "threshold": float(step.get("threshold") or 0.85),
                    "search_bbox": step.get("search_bbox"),
                    "click_offset": step.get("click_offset"),
                    "click_point": step.get("click_point"),
                    "wait_after": float(step.get("wait_after") or 0.4),
                }
            )
        return sequence
