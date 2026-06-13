from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QButtonGroup,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QPlainTextEdit,
    QProgressBar,
)

from .adb import AdbClient, AdbError
from .coords.coordinate_mapper import CoordinateMapper
from .deepsea_action_library import (
    ACTION_PRESETS,
    ACTOR_PRESETS,
    DeepSeaActionLibrary,
    display_action_name,
    normalize_action,
    normalize_actor,
    suggest_step_label,
    suggested_step_labels,
)
from .deepsea_operation_recorder import (
    TouchDeviceInfo,
    classify_operation,
    map_raw_point,
    parse_getevent_line,
    parse_touch_device,
)
from .deepsea_chest import (
    DEEPSEA_6F_CHEST_ITEMS,
    DEEPSEA_6F_CHEST_KEY,
    build_item_stats,
    read_deepsea_matrix_excel_records,
)
from .coords.game_coord_reader import GameCoordReader
from .flow import STEP_LABELS, STEP_TYPES, clone_step, create_flow, create_step, load_flow, refresh_step_identity, save_flow, short_id
from .maps.walkability_grid import WalkabilityGrid
from .navigation.approach_point_selector import ApproachPointSelector
from .navigation.local_movement_controller import LocalMovementController
from .navigation.path_planner import PathPlanner
from .navigation.stuck_detector import StuckDetector
from .ocr import OCREngine, parse_coord_candidate
from .storage import MAX_NORMAL_MOVEMENT_DELTA, ProjectStorage, movement_delta_is_plausible, normalize_text, now_iso
from .vision import match_template_qimage


DEFAULT_BATTLE_END_TEMPLATE = "assets/maps/map_001/battle/map_001_battle_战斗结束_crop.png"
DEFAULT_SWITCH_BUTTON_TEMPLATE = "assets/maps/map_001/button/map_001_button_切换_crop.png"
DEFAULT_SWITCH_CLOSE_TEMPLATE = "assets/maps/map_001/button/map_001_button_关闭切换角色_crop.png"
DEFAULT_PRE_DUNGEON_RETRY_LIMIT = 2
DEFAULT_VERIFY_CODE_MIN_CONFIDENCE = 0.50


def normalize_adb_endpoint(value: str) -> str:
    endpoint = value.strip()
    if endpoint == "127.0.0.16384":
        return "127.0.0.1:16384"
    if ":" not in endpoint and endpoint.startswith("127.0.0."):
        prefix, port = endpoint.rsplit(".", 1)
        if port.isdigit():
            return f"{prefix}:{port}"
    return endpoint


def bbox_from_rect(rect: QRect) -> list[int]:
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def rect_from_bbox(bbox: list[int] | tuple[int, int, int, int] | None) -> QRect | None:
    if not bbox or len(bbox) < 4:
        return None
    return QRect(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))


def bbox_center(bbox: list[int] | tuple[int, int, int, int]) -> tuple[int, int]:
    return (int(bbox[0] + bbox[2] / 2), int(bbox[1] + bbox[3] / 2))


def clean_verification_digits(text: str) -> str:
    replacements = str.maketrans({
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
    })
    return re.sub(r"\D", "", str(text or "").translate(replacements))


def format_duration(seconds: float | int | None) -> str:
    total = max(0, int(round(float(seconds or 0))))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def is_single_digit_value(value: str) -> bool:
    return bool(re.fullmatch(r"\d", str(value or "").strip()))


VERIFY_DIGIT_MASK_SIZE = (32, 48)
VERIFY_AMBIGUOUS_DIGITS = {"0", "3", "8", "9"}


def verification_digit_mask_from_path(path: Path) -> list[int] | None:
    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return None
    try:
        source = Image.open(path).convert("RGB")
    except Exception:
        return None
    mask = Image.new("L", source.size, 0)
    source_pixels = source.load()
    mask_pixels = mask.load()
    for y in range(source.height):
        for x in range(source.width):
            red, green, blue = source_pixels[x, y]
            if red > 90 and green > 85 and blue > 55:
                mask_pixels[x, y] = 255
    bbox = mask.getbbox()
    if bbox is None:
        return None
    cropped = mask.crop(bbox)
    padded = ImageOps.expand(cropped, border=4, fill=0)
    resized = padded.resize(VERIFY_DIGIT_MASK_SIZE, Image.Resampling.LANCZOS)
    return [1 if value > 32 else 0 for value in resized.getdata()]


def verification_digit_mask_similarity(a: list[int] | None, b: list[int] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    intersection = 0
    union = 0
    for left, right in zip(a, b):
        if left or right:
            union += 1
            if left and right:
                intersection += 1
    if union <= 0:
        return 0.0
    return intersection / union


def tesseract_single_digit_candidates(path: Path) -> list[dict[str, Any]]:
    executable = shutil.which("tesseract")
    if not executable:
        return []
    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return []
    try:
        source = Image.open(path).convert("RGB")
    except Exception:
        return []

    def cream_digit_mask(image: Any) -> Any:
        mask = Image.new("L", image.size, 255)
        source_pixels = image.load()
        mask_pixels = mask.load()
        for y in range(image.height):
            for x in range(image.width):
                red, green, blue = source_pixels[x, y]
                if red > 90 and green > 85 and blue > 55:
                    mask_pixels[x, y] = 0
        return mask

    variants = [
        ("raw", source),
        ("gray", ImageOps.grayscale(source)),
        ("cream", cream_digit_mask(source)),
    ]
    candidates: list[dict[str, Any]] = []
    temp_paths: list[Path] = []
    try:
        for variant_name, image in variants:
            resized = image.resize((max(1, image.width * 6), max(1, image.height * 6)), Image.Resampling.LANCZOS)
            border_fill: int | tuple[int, int, int] = 255 if resized.mode == "L" else (255, 255, 255)
            prepared = ImageOps.expand(resized, border=40, fill=border_fill)
            handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            handle.close()
            temp_path = Path(handle.name)
            prepared.save(temp_path)
            temp_paths.append(temp_path)
            for psm in ("10", "13", "8", "7"):
                try:
                    result = subprocess.run(
                        [
                            executable,
                            str(temp_path),
                            "stdout",
                            "-l",
                            "eng",
                            "--psm",
                            psm,
                            "-c",
                            "tessedit_char_whitelist=0123456789",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                except Exception:
                    continue
                text = clean_verification_digits(result.stdout)
                if len(text) != 1:
                    continue
                base_confidence = 0.78 if variant_name == "cream" else 0.74
                if psm in {"10", "13"}:
                    base_confidence += 0.02
                candidates.append(
                    {
                        "digit": text,
                        "confidence": min(0.86, base_confidence),
                        "backend": f"tesseract-cli-{variant_name}-psm{psm}",
                    }
                )
    finally:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
    return candidates


def digit_component_rects(image: QImage, expected_length: int) -> list[tuple[QRect, QRect]]:
    if image.isNull() or expected_length <= 0:
        return []
    width = int(image.width())
    height = int(image.height())
    if width <= 0 or height <= 0:
        return []

    def is_digit_fill(x: int, y: int) -> bool:
        color = image.pixelColor(x, y)
        return color.red() > 90 and color.green() > 85 and color.blue() > 55

    min_col_pixels = max(2, min(14, height // 8))
    col_counts: list[int] = []
    for x in range(width):
        count = 0
        for y in range(height):
            if is_digit_fill(x, y):
                count += 1
        col_counts.append(count)

    runs: list[tuple[int, int]] = []
    in_run = False
    start = 0
    for x, count in enumerate(col_counts):
        if count >= min_col_pixels and not in_run:
            start = x
            in_run = True
        at_end = x == width - 1
        if in_run and (count < min_col_pixels or at_end):
            end = x if count < min_col_pixels else x + 1
            if end > start:
                runs.append((start, end))
            in_run = False

    merge_gap = max(3, width // 90)
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    min_width = max(4, width // max(20, expected_length * 10))
    merged = [(start, end) for start, end in merged if end - start >= min_width]
    if len(merged) > expected_length:
        merged = sorted(merged, key=lambda item: item[1] - item[0], reverse=True)[:expected_length]
        merged.sort()

    if len(merged) != expected_length:
        active_columns = [index for index, count in enumerate(col_counts) if count >= min_col_pixels]
        if active_columns:
            left = min(active_columns)
            right = max(active_columns) + 1
        else:
            left = 0
            right = width
        if right <= left:
            left = 0
            right = width
        merged = []
        for index in range(expected_length):
            start = round(left + (right - left) * index / expected_length)
            end = round(left + (right - left) * (index + 1) / expected_length)
            merged.append((start, max(start + 1, end)))

    padding_x = max(12, width // 24)
    padding_y = max(6, height // 12)
    rects: list[tuple[QRect, QRect]] = []
    for start, end in merged:
        left = max(0, int(start) - padding_x)
        right = min(width, int(end) + padding_x)
        ys: list[int] = []
        for y in range(height):
            for x in range(left, right):
                if is_digit_fill(x, y):
                    ys.append(y)
                    break
        if ys:
            top = max(0, min(ys) - padding_y)
            bottom = min(height, max(ys) + padding_y + 1)
        else:
            top = 0
            bottom = height
        full_rect = QRect(left, 0, max(1, right - left), height)
        tight_rect = QRect(left, top, max(1, right - left), max(1, bottom - top))
        rects.append((full_rect, tight_rect))
    return rects


def parse_progress_text(text: str, fallback_total: int) -> tuple[int, int] | None:
    cleaned = text.strip()
    match = re.search(r"(\d+)\s*/\s*(\d+)", cleaned)
    if match:
        return int(match.group(1)), int(match.group(2))
    digits = re.findall(r"\d+", cleaned)
    if len(digits) >= 2:
        return int(digits[0]), int(digits[1])
    if len(digits) == 1 and len(digits[0]) >= 2:
        value = digits[0]
        return int(value[:-1] or "0"), int(value[-1])
    if len(digits) == 1:
        return int(digits[0]), fallback_total
    return None


def parse_game_coord_text(text: str, previous: list[int] | None = None) -> list[int] | None:
    coord = parse_coord_candidate(text, previous=previous)
    return [coord[0], coord[1]] if coord else None


def coord_distance(a: list[int] | tuple[int, int], b: list[int] | tuple[int, int]) -> int:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def coord_chebyshev_distance(a: list[int] | tuple[int, int], b: list[int] | tuple[int, int]) -> int:
    return max(abs(int(a[0]) - int(b[0])), abs(int(a[1]) - int(b[1])))


def coord_within_tolerance(a: list[int] | tuple[int, int], b: list[int] | tuple[int, int], tolerance: int) -> bool:
    return abs(int(a[0]) - int(b[0])) <= tolerance and abs(int(a[1]) - int(b[1])) <= tolerance


def coord_sign(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def normalize_click_radii(
    values: list[int] | tuple[int, ...] | None,
    defaults: list[int],
    *,
    max_radius: int,
) -> list[int]:
    radii: list[int] = []
    source = values if values else defaults
    for value in source:
        try:
            radius = int(value)
        except (TypeError, ValueError):
            continue
        if 60 <= radius <= int(max_radius):
            radii.append(radius)
    return sorted(set(radii)) or list(defaults)


def coord_jump_is_plausible(
    before: list[int] | tuple[int, int] | None,
    after: list[int] | tuple[int, int] | None,
    max_jump: int = MAX_NORMAL_MOVEMENT_DELTA,
) -> bool:
    if before is None or after is None:
        return True
    return coord_distance(before, after) <= max(1, int(max_jump))


def coord_component_repair_candidates(value: int, anchors: list[int]) -> list[int]:
    value = int(value)
    candidates = [value]
    ones = value % 10
    for anchor in anchors:
        anchor = int(anchor)
        for base in {
            (anchor // 10) * 10,
            ((anchor // 10) - 1) * 10,
            ((anchor // 10) + 1) * 10,
        }:
            candidate = base + ones
            if 0 <= candidate <= 99 and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def repair_implausible_coord(
    coord: list[int],
    previous: list[int],
    *,
    target_hint: list[int] | None,
    max_jump: int,
) -> list[int] | None:
    anchors_x = [int(previous[0])]
    anchors_y = [int(previous[1])]
    if target_hint:
        anchors_x.append(int(target_hint[0]))
        anchors_y.append(int(target_hint[1]))
    candidates: list[list[int]] = []
    for x in coord_component_repair_candidates(int(coord[0]), anchors_x):
        for y in coord_component_repair_candidates(int(coord[1]), anchors_y):
            candidate = [x, y]
            if candidate == coord:
                continue
            if coord_jump_is_plausible(previous, candidate, max_jump=max_jump):
                candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            coord_distance(item, target_hint) if target_hint else coord_distance(item, previous),
            coord_distance(item, previous),
        )
    )
    return candidates[0]


def next_step_has_image_target(flow: dict[str, Any], index: int | None) -> bool:
    if index is None:
        return False
    steps = flow.get("steps") or []
    next_pos = int(index)
    if next_pos < 0 or next_pos >= len(steps):
        return False
    next_step = steps[next_pos]
    if next_step.get("type") not in {"image_check", "find_target", "click_target"}:
        return False
    return bool((next_step.get("input") or {}).get("template_path"))


def repair_coord_against_target_hint(
    coord: list[int],
    target_hint: list[int] | None,
    *,
    max_jump: int,
) -> list[int] | None:
    if not target_hint:
        return None
    raw_distance = coord_distance(coord, target_hint)
    if raw_distance <= max(18, int(max_jump) * 2):
        return None
    axis_gap = [abs(int(coord[0]) - int(target_hint[0])), abs(int(coord[1]) - int(target_hint[1]))]
    axis_near = [axis_gap[0] <= max(3, int(max_jump)), axis_gap[1] <= max(3, int(max_jump))]
    axis_looks_ocr_bad = [
        axis_gap[0] >= 35 and (int(coord[0]) >= 70 or int(target_hint[0]) >= 70),
        axis_gap[1] >= 35 and (int(coord[1]) >= 70 or int(target_hint[1]) >= 70),
    ]
    if not any(axis_near) or not any(axis_looks_ocr_bad):
        return None

    candidates: list[list[int]] = []
    for x in coord_component_repair_candidates(int(coord[0]), [int(target_hint[0])]):
        for y in coord_component_repair_candidates(int(coord[1]), [int(target_hint[1])]):
            candidate = [x, y]
            if candidate == coord:
                continue
            if coord_distance(candidate, target_hint) < raw_distance - 20:
                candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda item: coord_distance(item, target_hint))
    return candidates[0]


def answer_to_index(answer: str, options: list[str]) -> int | None:
    value = answer.strip()
    if not value:
        return None
    letter_match = re.match(r"^([A-Da-d])(?:$|[\s.。．、:：)])", value)
    if letter_match:
        return ord(letter_match.group(1).upper()) - ord("A")

    def normalized_option_text(text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^[A-Da-d]\s*[\.\。．、:：)]\s*", "", cleaned)
        return re.sub(r"\s+", "", cleaned).lower()

    normalized_value = normalized_option_text(value)
    normalized_options = [normalized_option_text(option) for option in options]
    for index, normalized_option in enumerate(normalized_options[:4]):
        if normalized_value and normalized_value == normalized_option:
            return index

    best: tuple[float, int] | None = None
    for index, normalized_option in enumerate(normalized_options[:4]):
        if not normalized_value or not normalized_option:
            continue
        score = SequenceMatcher(None, normalized_value, normalized_option).ratio()
        if normalized_value in normalized_option or normalized_option in normalized_value:
            overlap = min(len(normalized_value), len(normalized_option)) / max(len(normalized_value), len(normalized_option))
            score = max(score, overlap)
        if best is None or score > best[0]:
            best = (score, index)
    return best[1] if best and best[0] >= 0.80 else None


REGION_CHOICES: list[tuple[str, str]] = [
    ("图片识别", "image"),
    ("数字识别", "digit"),
    ("文字识别", "text"),
    ("NPC/目标识别并点击", "npc"),
    ("截图定位点击", "target"),
    ("按钮识别并点击", "button"),
    ("传送点识别", "transition"),
    ("战斗状态识别", "battle"),
    ("问答题题目区域", "question"),
    ("问答题选项区域", "question_option"),
    ("答题进度区域 0/5", "question_progress"),
    ("答题确定按钮区域", "question_confirm"),
    ("普通截图素材", "screenshot"),
]

VISIBLE_STEP_TYPES = [
    "click",
    "wait",
    "image_check",
    "find_target",
    "click_target",
    "ocr_text",
    "ocr_number",
    "verify_code",
    "dialog",
    "question",
    "battle",
    "condition",
    "loop",
]

OLD_DEFAULT_GAME_COORD_REGION = [1760, 118, 150, 75]
DEFAULT_GAME_COORD_REGION = [1720, 80, 200, 120]

STEP_LIST_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
STEP_LIST_DEPTH_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class StepListWidget(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setAutoScroll(True)
        self.setAutoScrollMargin(72)

    def dragMoveEvent(self, event: Any) -> None:  # noqa: N802
        super().dragMoveEvent(event)
        try:
            y = event.position().toPoint().y()
        except AttributeError:
            y = event.pos().y()
        margin = 84
        scroll = self.verticalScrollBar()
        if y < margin:
            scroll.setValue(scroll.value() - max(4, (margin - y) // 3))
        elif y > self.viewport().height() - margin:
            scroll.setValue(scroll.value() + max(4, (y - (self.viewport().height() - margin)) // 3))


class ScreenWorker(QThread):
    frame_ready = Signal(QImage)
    status = Signal(str)

    def __init__(self, adb: AdbClient, interval: float = 0.7) -> None:
        super().__init__()
        self.adb = adb
        self.interval = interval
        self._running = True
        self._last_error = ""

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            try:
                image = QImage.fromData(self.adb.screencap_png(), "PNG")
                if not image.isNull():
                    self.frame_ready.emit(image)
                    self._last_error = ""
                else:
                    self._emit_error("ADB 截图解码失败")
            except Exception as exc:  # noqa: BLE001 - worker should not crash GUI
                self._emit_error(str(exc))
            self.msleep(max(100, int(self.interval * 1000)))

    def _emit_error(self, message: str) -> None:
        if message != self._last_error:
            self.status.emit(message)
            self._last_error = message


class DeepSeaAutoRecorderWorker(QThread):
    operation_recorded = Signal(str)
    status = Signal(str)

    def __init__(
        self,
        adb: AdbClient,
        output_root: Path,
        *,
        screen_size: tuple[int, int],
    ) -> None:
        super().__init__()
        self.adb = adb
        self.output_root = Path(output_root)
        self.screen_size = screen_size
        self._running = True
        self._proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self._running = False
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()

    def adb_base(self) -> list[str]:
        cmd = [self.adb.executable]
        if self.adb.serial:
            cmd.extend(["-s", self.adb.serial])
        return cmd

    def run_adb_text(self, args: list[str], timeout: float = 8.0) -> str:
        result = subprocess.run(
            self.adb_base() + args,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout or result.stderr or ""

    def save_screencap(self, path: Path) -> bool:
        try:
            data = self.adb.screencap_png()
        except Exception as exc:  # noqa: BLE001
            self.status.emit(f"自动记录截图失败：{exc}")
            return False
        path.write_bytes(data)
        return True

    def run(self) -> None:
        session_dir = self.output_root / time.strftime("session_%Y%m%d_%H%M%S")
        session_dir.mkdir(parents=True, exist_ok=True)
        try:
            getevent_lp = self.run_adb_text(["shell", "getevent", "-lp"])
            dumpsys_input = self.run_adb_text(["shell", "dumpsys", "input"], timeout=10.0)
            device = parse_touch_device(getevent_lp, dumpsys_input)
            if device is None:
                self.status.emit("深海自动记录：没有找到触摸输入设备。")
                return
            meta = {
                "started_at": now_iso(),
                "screen_size": list(self.screen_size),
                "device": {
                    "path": device.path,
                    "name": device.name,
                    "max_x": device.max_x,
                    "max_y": device.max_y,
                    "orientation": device.orientation,
                },
            }
            (session_dir / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.emit(f"深海自动记录已开始：{device.path} -> {session_dir.name}")
            self.record_events(session_dir, device)
        except Exception as exc:  # noqa: BLE001
            if self._running:
                self.status.emit(f"深海自动记录失败：{exc}")
        finally:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
            self.status.emit("深海自动记录已停止。")

    def record_events(self, session_dir: Path, device: TouchDeviceInfo) -> None:
        cmd = self.adb_base() + ["shell", "getevent", "-lt", device.path]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if self._proc.stdout is None:
            raise RuntimeError("getevent 没有输出。")

        active = False
        raw_x: int | None = None
        raw_y: int | None = None
        raw_points: list[list[int]] = []
        screen_points: list[list[int]] = []
        started_at = 0.0
        before_path: Path | None = None
        operation_index = 0
        last_point: list[int] | None = None

        for line in self._proc.stdout:
            if not self._running:
                break
            parsed = parse_getevent_line(line)
            if parsed is None:
                continue
            kind, value = parsed
            if kind == "x":
                raw_x = value
            elif kind == "y":
                raw_y = value
            elif kind == "touch" and value == 1 and not active:
                active = True
                started_at = time.monotonic()
                raw_points = []
                screen_points = []
                last_point = None
                operation_index += 1
                before_path = session_dir / f"operation_{operation_index:04d}_before.png"
                self.save_screencap(before_path)
            elif kind == "tracking_id" and value >= 0 and not active:
                active = True
                started_at = time.monotonic()
                raw_points = []
                screen_points = []
                last_point = None
                operation_index += 1
                before_path = session_dir / f"operation_{operation_index:04d}_before.png"
                self.save_screencap(before_path)
            elif kind in {"touch", "tracking_id"} and active and value in {0, -1}:
                self.finish_operation(
                    session_dir,
                    device,
                    operation_index,
                    started_at,
                    before_path,
                    raw_points,
                    screen_points,
                )
                active = False
                before_path = None
            elif kind == "syn" and active and raw_x is not None and raw_y is not None:
                raw_point = [int(raw_x), int(raw_y)]
                screen_point = map_raw_point(raw_x, raw_y, device=device, screen_size=self.screen_size)
                if last_point != raw_point:
                    raw_points.append(raw_point)
                    screen_points.append(screen_point)
                    last_point = raw_point

    def finish_operation(
        self,
        session_dir: Path,
        device: TouchDeviceInfo,
        operation_index: int,
        started_at: float,
        before_path: Path | None,
        raw_points: list[list[int]],
        screen_points: list[list[int]],
    ) -> None:
        if not raw_points or not screen_points:
            return
        duration = max(0.0, time.monotonic() - started_at)
        time.sleep(0.16)
        after_path = session_dir / f"operation_{operation_index:04d}_after.png"
        self.save_screencap(after_path)
        operation_type = classify_operation(screen_points, duration)
        payload = {
            "id": f"operation_{operation_index:04d}",
            "created_at": now_iso(),
            "type": operation_type,
            "duration_seconds": duration,
            "screen_size": list(self.screen_size),
            "device": {
                "path": device.path,
                "name": device.name,
                "max_x": device.max_x,
                "max_y": device.max_y,
                "orientation": device.orientation,
            },
            "raw_points": raw_points,
            "points": screen_points,
            "start": screen_points[0],
            "end": screen_points[-1],
            "before": before_path.name if before_path else None,
            "after": after_path.name,
            "label": "",
            "actor": "",
            "action": "",
            "step_label": "",
        }
        json_path = session_dir / f"operation_{operation_index:04d}.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.operation_recorded.emit(
            f"{payload['id']} {operation_type}: {payload['start']} -> {payload['end']}"
        )


class RuntimeWorker(QThread):
    failed = Signal(str)

    def __init__(self, name: str, callback: Callable[[], Any]) -> None:
        super().__init__()
        self.name = name
        self.callback = callback

    def run(self) -> None:
        try:
            self.callback()
        except Exception as exc:  # noqa: BLE001 - keep the UI process alive
            self.failed.emit(f"{self.name} 异常停止：{exc}")


class GameView(QWidget):
    clicked_on_frame = Signal(QPoint)
    region_selected = Signal(QRect)
    coordinate_changed = Signal(QPoint)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(420, 240)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.frame: QImage | None = None
        self._display_rect = QRect()
        self._press_pos: QPoint | None = None
        self._drag_pos: QPoint | None = None
        self._hover_image_pos: QPoint | None = None
        self._pinned_rect: QRect | None = None
        self._pinned_point: QPoint | None = None
        self.zoom = 0.0
        self.focus_rect: QRect | None = None

    def set_zoom(self, zoom: float) -> None:
        self.zoom = zoom
        self.updateGeometry()
        self.update()

    def set_focus_rect(self, rect: QRect) -> None:
        if self.frame is None:
            return
        bounded = rect.normalized().intersected(QRect(0, 0, self.frame.width(), self.frame.height()))
        if bounded.width() < 10 or bounded.height() < 10:
            return
        self.focus_rect = bounded
        self.updateGeometry()
        self.update()

    def clear_focus_rect(self) -> None:
        self.focus_rect = None
        self.updateGeometry()
        self.update()

    def visible_source_rect(self) -> QRect:
        if self.frame is None:
            return QRect()
        if self.focus_rect is not None:
            return self.focus_rect
        return QRect(0, 0, self.frame.width(), self.frame.height())

    def sizeHint(self) -> QSize:  # noqa: N802
        if self.frame is not None and self.zoom > 0:
            source = self.visible_source_rect()
            return QSize(
                max(1, int(source.width() * self.zoom)),
                max(1, int(source.height() * self.zoom)),
            )
        return QSize(960, 540)

    def set_frame(self, image: QImage) -> None:
        self.frame = image.copy()
        self.updateGeometry()
        self.update()

    def image_size(self) -> QSize:
        if self.frame is None:
            return QSize(0, 0)
        return self.frame.size()

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101418"))

        if self.frame is None or self.frame.isNull():
            painter.setPen(QColor("#9aa8b4"))
            painter.setFont(QFont("Arial", 18, QFont.Weight.Medium))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "未连接模拟器\n点击“连接ADB”开始映射游戏画面")
            self._display_rect = QRect()
            return

        source_rect = self.visible_source_rect()
        source_image = self.frame.copy(source_rect) if self.focus_rect is not None else self.frame

        if self.zoom > 0:
            width = max(1, int(source_rect.width() * self.zoom))
            height = max(1, int(source_rect.height() * self.zoom))
            scaled = source_image.scaled(
                width,
                height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._display_rect = QRect(0, 0, scaled.width(), scaled.height())
            painter.drawImage(self._display_rect, scaled)
        else:
            scaled = source_image.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            self._display_rect = QRect(x, y, scaled.width(), scaled.height())
            painter.drawImage(self._display_rect, scaled)

        painter.setPen(QPen(QColor("#28313a"), 1))
        painter.drawRect(self._display_rect.adjusted(0, 0, -1, -1))

        if self._press_pos and self._drag_pos:
            selection = QRect(self._press_pos, self._drag_pos).normalized()
            painter.setPen(QPen(QColor("#35d0a5"), 2))
            painter.setBrush(QColor(53, 208, 165, 45))
            painter.drawRect(selection)

        if self._pinned_rect is not None:
            view_rect = self._map_image_rect_to_view(self._pinned_rect)
            if view_rect is not None:
                painter.setPen(QPen(QColor("#ffb020"), 2))
                painter.setBrush(QColor(255, 176, 32, 35))
                painter.drawRect(view_rect)

        if self._pinned_point is not None:
            view_point = self._map_image_point_to_view(self._pinned_point)
            if view_point is not None:
                painter.setPen(QPen(QColor("#00d2ff"), 3))
                painter.drawLine(view_point.x() - 10, view_point.y(), view_point.x() + 10, view_point.y())
                painter.drawLine(view_point.x(), view_point.y() - 10, view_point.x(), view_point.y() + 10)
                painter.setBrush(QColor(0, 210, 255, 70))
                painter.drawEllipse(view_point, 8, 8)

        if self._hover_image_pos:
            self.coordinate_changed.emit(self._hover_image_pos)
            text = f"{self._hover_image_pos.x()}, {self._hover_image_pos.y()}"
            painter.setPen(QColor("#d8e1e8"))
            painter.setBrush(QColor(16, 20, 24, 180))
            painter.drawRect(12, 12, 112, 28)
            painter.drawText(20, 31, text)

        if self.focus_rect is not None:
            text = f"局部放大 {self.focus_rect.x()},{self.focus_rect.y()} {self.focus_rect.width()}x{self.focus_rect.height()}"
            painter.setPen(QColor("#d8e1e8"))
            painter.setBrush(QColor(16, 20, 24, 190))
            painter.drawRect(12, 46, min(330, max(170, len(text) * 8)), 28)
            painter.drawText(20, 65, text)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.frame is None:
            return
        if not self._display_rect.contains(event.position().toPoint()):
            return
        self._press_pos = event.position().toPoint()
        self._drag_pos = self._press_pos
        self.update()

    def set_pinned_rect(self, rect: QRect | None) -> None:
        self._pinned_rect = rect
        self.update()

    def set_pinned_point(self, point: QPoint | None) -> None:
        self._pinned_point = point
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = event.position().toPoint()
        self._hover_image_pos = self._map_view_to_image(point)
        if self._press_pos:
            self._drag_pos = point
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        release_pos = event.position().toPoint()
        start = self._press_pos
        self._press_pos = None
        self._drag_pos = None

        if (release_pos - start).manhattanLength() < 6:
            image_point = self._map_view_to_image(release_pos)
            if image_point:
                self.clicked_on_frame.emit(image_point)
        else:
            image_rect = self._map_view_rect_to_image(QRect(start, release_pos).normalized())
            if image_rect and image_rect.width() > 2 and image_rect.height() > 2:
                self.region_selected.emit(image_rect)
        self.update()

    def _map_view_to_image(self, point: QPoint) -> QPoint | None:
        if self.frame is None or self._display_rect.isNull() or not self._display_rect.contains(point):
            return None
        source_rect = self.visible_source_rect()
        x_ratio = source_rect.width() / self._display_rect.width()
        y_ratio = source_rect.height() / self._display_rect.height()
        x = int(source_rect.x() + (point.x() - self._display_rect.x()) * x_ratio)
        y = int(source_rect.y() + (point.y() - self._display_rect.y()) * y_ratio)
        return QPoint(max(0, min(self.frame.width() - 1, x)), max(0, min(self.frame.height() - 1, y)))

    def _map_view_rect_to_image(self, rect: QRect) -> QRect | None:
        top_left = self._map_view_to_image(rect.topLeft())
        bottom_right = self._map_view_to_image(rect.bottomRight())
        if top_left is None or bottom_right is None:
            clipped = rect.intersected(self._display_rect)
            if clipped.isNull():
                return None
            top_left = self._map_view_to_image(clipped.topLeft())
            bottom_right = self._map_view_to_image(clipped.bottomRight())
        if top_left is None or bottom_right is None:
            return None
        return QRect(top_left, bottom_right).normalized()

    def _map_image_rect_to_view(self, rect: QRect) -> QRect | None:
        if self.frame is None or self._display_rect.isNull():
            return None
        source_rect = self.visible_source_rect()
        if source_rect.width() <= 0 or source_rect.height() <= 0:
            return None
        x_ratio = self._display_rect.width() / source_rect.width()
        y_ratio = self._display_rect.height() / source_rect.height()
        x1 = self._display_rect.x() + int((rect.x() - source_rect.x()) * x_ratio)
        y1 = self._display_rect.y() + int((rect.y() - source_rect.y()) * y_ratio)
        x2 = self._display_rect.x() + int((rect.right() - source_rect.x()) * x_ratio)
        y2 = self._display_rect.y() + int((rect.bottom() - source_rect.y()) * y_ratio)
        view_rect = QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized()
        return view_rect.intersected(self._display_rect)

    def _map_image_point_to_view(self, point: QPoint) -> QPoint | None:
        if self.frame is None or self._display_rect.isNull():
            return None
        source_rect = self.visible_source_rect()
        if not source_rect.contains(point):
            return None
        x_ratio = self._display_rect.width() / source_rect.width()
        y_ratio = self._display_rect.height() / source_rect.height()
        x = self._display_rect.x() + int((point.x() - source_rect.x()) * x_ratio)
        y = self._display_rect.y() + int((point.y() - source_rect.y()) * y_ratio)
        return QPoint(x, y)


class LargeRegionDialog(QDialog):
    def __init__(
        self,
        image: QImage,
        *,
        title: str = "当前画面操作",
        fixed_kind: str | None = None,
        hint: str = "这是刚截取的当前游戏画面。单击可插入点击位置移动步骤；拖拽框选后选择素材用途，或直接保存整张截图。",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(available.width() - 24, 1900), min(available.height() - 24, 1120))
            if available.width() >= 1500 and available.height() >= 900:
                self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        else:
            self.resize(1800, 1040)
        self.selected_rect: QRect | None = None
        self.selected_point: QPoint | None = None
        self.fixed_kind = fixed_kind
        self._region_menu_open = False

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(hint))
        top.addStretch(1)
        top.addWidget(QLabel("缩放"))
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["适应", "100%", "125%", "150%", "200%", "300%"])
        self.zoom_combo.setCurrentText("适应")
        top.addWidget(self.zoom_combo)
        self.zoom_selected_button = QPushButton("放大所选")
        self.zoom_selected_button.setEnabled(False)
        top.addWidget(self.zoom_selected_button)
        self.full_image_button = QPushButton("返回全图")
        top.addWidget(self.full_image_button)
        self.full_screenshot_button = QPushButton("保存整张截图")
        self.full_screenshot_button.setVisible(fixed_kind is None)
        top.addWidget(self.full_screenshot_button)
        layout.addLayout(top)

        self.view = GameView()
        self.view.setMinimumSize(1, 1)
        self.view.set_frame(image)
        self.view.set_zoom(0.0)
        self.view.region_selected.connect(self.on_region_selected)
        self.view.clicked_on_frame.connect(self.on_point_clicked)
        self.view.coordinate_changed.connect(self.on_coordinate_changed)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.view)
        layout.addWidget(self.scroll, 1)

        bottom = QHBoxLayout()
        self.coord_label = QLabel("坐标：-")
        self.coord_label.setMinimumWidth(420)
        bottom.addWidget(self.coord_label)
        bottom.addStretch(1)
        self.kind_combo = QComboBox()
        for label, kind in REGION_CHOICES:
            self.kind_combo.addItem(label, kind)
        if fixed_kind is not None:
            self.kind_combo.setEnabled(False)
            index = self.kind_combo.findData(fixed_kind)
            if index >= 0:
                self.kind_combo.setCurrentIndex(index)
        bottom.addWidget(QLabel("用途"))
        bottom.addWidget(self.kind_combo)
        layout.addLayout(bottom)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.zoom_combo.currentTextChanged.connect(self.on_zoom_changed)
        self.zoom_selected_button.clicked.connect(self.zoom_selected_region)
        self.full_image_button.clicked.connect(self.return_full_image)
        self.full_screenshot_button.clicked.connect(self.accept_full_screenshot)

    def on_zoom_changed(self, value: str) -> None:
        if value == "适应":
            self.scroll.setWidgetResizable(True)
            self.view.set_zoom(0.0)
            return
        zoom = int(value.rstrip("%")) / 100.0
        self.scroll.setWidgetResizable(False)
        self.view.set_zoom(zoom)
        self.view.resize(self.view.sizeHint())

    def on_coordinate_changed(self, point: QPoint) -> None:
        if self.selected_point is not None:
            self.coord_label.setText(f"点击位置：x={self.selected_point.x()}, y={self.selected_point.y()}  鼠标：{point.x()}, {point.y()}")
        elif self.selected_rect is None:
            self.coord_label.setText(f"坐标：{point.x()}, {point.y()}")
        else:
            self.coord_label.setText(self.region_text(self.selected_rect, point))

    def on_region_selected(self, rect: QRect) -> None:
        self.selected_point = None
        self.selected_rect = rect
        self.view.set_pinned_rect(rect)
        self.view.set_pinned_point(None)
        self.ok_button.setEnabled(True)
        self.zoom_selected_button.setEnabled(True)
        self.coord_label.setText(self.region_text(rect))
        QTimer.singleShot(0, self.show_region_action_menu)

    def on_point_clicked(self, point: QPoint) -> None:
        if self.fixed_kind is not None:
            return
        self.selected_point = point
        self.selected_rect = None
        self.view.set_pinned_rect(None)
        self.view.set_pinned_point(point)
        self.ok_button.setEnabled(True)
        self.zoom_selected_button.setEnabled(False)
        self.coord_label.setText(f"点击位置：x={point.x()}, y={point.y()}")
        QTimer.singleShot(0, self.show_point_action_menu)

    def show_point_action_menu(self) -> None:
        if self.selected_point is None or self._region_menu_open:
            return
        self._region_menu_open = True
        menu = QMenu(self)
        confirm_action = menu.addAction("插入点击位置移动步骤")
        redo_action = menu.addAction("重新选择")
        action = menu.exec(QCursor.pos())
        self._region_menu_open = False
        if action == confirm_action:
            self.accept()
        elif action == redo_action:
            self.view.set_pinned_point(None)
            self.selected_point = None
            self.ok_button.setEnabled(False)
            self.coord_label.setText("请重新单击要点击的位置，或拖拽框选素材。")

    def show_region_action_menu(self) -> None:
        if self.selected_rect is None or self._region_menu_open:
            return
        self._region_menu_open = True
        menu = QMenu(self)
        if self.fixed_kind is None:
            current_label = self.kind_combo.currentText()
            confirm_action = menu.addAction(f"按当前用途确认：{current_label}")
        else:
            confirm_action = menu.addAction("确认此区域")
        zoom_action = menu.addAction("放大所选")
        redo_action = menu.addAction("重新框选")
        kind_actions: dict[QAction, str] = {}
        if self.fixed_kind is None:
            menu.addSeparator()
            for label, kind in REGION_CHOICES:
                action = menu.addAction(label)
                kind_actions[action] = kind
        action = menu.exec(QCursor.pos())
        self._region_menu_open = False
        if action == confirm_action:
            self.accept()
        elif action == zoom_action:
            self.zoom_selected_region()
        elif action == redo_action:
            self.view.set_pinned_rect(None)
            self.selected_rect = None
            self.selected_point = None
            self.ok_button.setEnabled(False)
            self.zoom_selected_button.setEnabled(False)
            self.coord_label.setText("请重新框选区域。")
        elif action in kind_actions:
            index = self.kind_combo.findData(kind_actions[action])
            if index >= 0:
                self.kind_combo.setCurrentIndex(index)
            self.accept()

    def zoom_selected_region(self) -> None:
        if self.selected_rect is None:
            return
        self.view.set_focus_rect(self.selected_rect)
        self.view.set_pinned_rect(None)
        self.view.set_pinned_point(None)
        self.selected_rect = None
        self.selected_point = None
        self.ok_button.setEnabled(False)
        self.zoom_selected_button.setEnabled(False)
        self.zoom_combo.setCurrentText("适应")
        self.scroll.setWidgetResizable(True)
        self.coord_label.setText("已放大所选区域，请在放大图中重新精确框选。")

    def return_full_image(self) -> None:
        self.view.clear_focus_rect()
        self.view.set_pinned_rect(None)
        self.view.set_pinned_point(None)
        self.selected_rect = None
        self.selected_point = None
        self.ok_button.setEnabled(False)
        self.zoom_selected_button.setEnabled(False)
        self.zoom_combo.setCurrentText("适应")
        self.scroll.setWidgetResizable(True)
        self.coord_label.setText("已返回全图。")

    def accept_full_screenshot(self) -> None:
        if self.view.frame is None:
            return
        self.selected_point = None
        self.selected_rect = QRect(0, 0, self.view.frame.width(), self.view.frame.height())
        if self.fixed_kind is None:
            index = self.kind_combo.findData("screenshot")
            if index >= 0:
                self.kind_combo.setCurrentIndex(index)
        self.accept()

    def region_text(self, rect: QRect, point: QPoint | None = None) -> str:
        coord = f"鼠标：{point.x()}, {point.y()}  " if point is not None else ""
        return (
            f"{coord}区域：x={rect.x()}, y={rect.y()}, "
            f"w={rect.width()}, h={rect.height()}, "
            f"中心={rect.x() + rect.width() // 2},{rect.y() + rect.height() // 2}"
        )

    def selected_kind(self) -> str:
        if self.fixed_kind is not None:
            return self.fixed_kind
        return str(self.kind_combo.currentData())


class TemplateClickPointDialog(QDialog):
    def __init__(
        self,
        image: QImage,
        *,
        title: str = "设置模板内点击点",
        initial_point: list[int] | tuple[int, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 620)
        self.selected_point: QPoint | None = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("在模板图里点一下真正要点击的位置。运行时会点：匹配框左上角 + 这个点。"))
        top.addStretch(1)
        top.addWidget(QLabel("缩放"))
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["适应", "200%", "300%", "400%", "600%", "800%"])
        self.zoom_combo.setCurrentText("300%")
        top.addWidget(self.zoom_combo)
        layout.addLayout(top)

        self.view = GameView()
        self.view.setMinimumSize(1, 1)
        self.view.set_frame(image)
        self.view.set_zoom(3.0)
        self.view.clicked_on_frame.connect(self.on_point_clicked)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.view)
        self.view.resize(self.view.sizeHint())
        layout.addWidget(self.scroll, 1)

        bottom = QHBoxLayout()
        self.coord_label = QLabel("点击点：未设置")
        bottom.addWidget(self.coord_label)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.zoom_combo.currentTextChanged.connect(self.on_zoom_changed)
        if initial_point and len(initial_point) >= 2:
            self.on_point_clicked(QPoint(int(initial_point[0]), int(initial_point[1])))

    def on_zoom_changed(self, value: str) -> None:
        if value == "适应":
            self.scroll.setWidgetResizable(True)
            self.view.set_zoom(0.0)
            return
        zoom = int(value.rstrip("%")) / 100.0
        self.scroll.setWidgetResizable(False)
        self.view.set_zoom(zoom)
        self.view.resize(self.view.sizeHint())

    def on_point_clicked(self, point: QPoint) -> None:
        if self.view.frame is None:
            return
        x = max(0, min(self.view.frame.width() - 1, int(point.x())))
        y = max(0, min(self.view.frame.height() - 1, int(point.y())))
        self.selected_point = QPoint(x, y)
        self.view.set_pinned_point(self.selected_point)
        self.coord_label.setText(f"点击点：x={x}, y={y}")
        self.ok_button.setEnabled(True)


class DeepSeaActionCaptureSetupDialog(QDialog):
    def __init__(
        self,
        library: DeepSeaActionLibrary,
        *,
        last_actor: str = "1号人物",
        last_action: str = "放风",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("深海动作截图")
        self.library = library
        self.resize(520, 260)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.actor_combo = QComboBox()
        self.actor_combo.setEditable(True)
        self.actor_combo.addItems(list(ACTOR_PRESETS))
        self.actor_combo.setCurrentText(last_actor or "1号人物")
        form.addRow("行动者", self.actor_combo)

        self.action_combo = QComboBox()
        self.action_combo.setEditable(True)
        self.action_combo.addItems(list(ACTION_PRESETS))
        self.action_combo.setCurrentText(last_action or "放风")
        form.addRow("动作", self.action_combo)

        self.step_combo = QComboBox()
        self.step_combo.setEditable(True)
        form.addRow("步骤", self.step_combo)

        self.step_type_combo = QComboBox()
        self.step_type_combo.addItem("点击图片", "template_click")
        self.step_type_combo.addItem("滑动列表", "swipe")
        form.addRow("类型", self.step_type_combo)

        layout.addLayout(form)

        self.center_click = QCheckBox("模板中心就是点击点")
        self.center_click.setChecked(True)
        self.tap_after_capture = QCheckBox("保存后立刻点击这一步")
        self.tap_after_capture.setChecked(True)
        layout.addWidget(self.center_click)
        layout.addWidget(self.tap_after_capture)

        hint = QLabel("建议只框选当前要点的按钮/技能/道具文字，采完自动点下去，下一张图继续采下一步。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.action_combo.currentTextChanged.connect(self.refresh_step_suggestions)
        self.actor_combo.currentTextChanged.connect(self.refresh_step_suggestions)
        self.step_combo.currentTextChanged.connect(self.update_step_type_from_label)
        self.refresh_step_suggestions()

    def refresh_step_suggestions(self) -> None:
        actor_text = self.actor_combo.currentText()
        action_text = self.action_combo.currentText()
        try:
            actor_ref = normalize_actor(actor_text)
            action_key = normalize_action(action_text)
        except ValueError:
            action_key = "unknown"
            existing_labels: list[str] = []
        else:
            action = self.library.get_action(actor_ref, action_key)
            existing_labels = [str(step.get("label") or "") for step in (action or {}).get("steps") or []]

        current = self.step_combo.currentText().strip()
        suggestions = list(suggested_step_labels(action_key))
        next_label = suggest_step_label(action_key, existing_labels)
        if next_label not in suggestions:
            suggestions.insert(0, next_label)

        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        self.step_combo.addItems(suggestions)
        keep_custom = bool(current and current not in suggestions and not re.fullmatch(r"步骤\s*\d+", current))
        self.step_combo.setCurrentText(current if keep_custom else next_label)
        self.step_combo.blockSignals(False)
        self.update_step_type_from_label(self.step_combo.currentText())

    def update_step_type_from_label(self, value: str) -> None:
        text = str(value or "")
        desired = "swipe" if any(word in text for word in ("滚动", "滑动", "下拉", "上拉")) else "template_click"
        index = self.step_type_combo.findData(desired)
        if index >= 0:
            self.step_type_combo.setCurrentIndex(index)

    def accept(self) -> None:
        try:
            normalize_actor(self.actor_combo.currentText())
            normalize_action(self.action_combo.currentText())
        except ValueError as exc:
            QMessageBox.warning(self, "深海动作截图", str(exc))
            return
        if not self.step_combo.currentText().strip():
            QMessageBox.warning(self, "深海动作截图", "步骤名不能为空。")
            return
        super().accept()

    def capture_config(self) -> dict[str, Any]:
        return {
            "actor_text": self.actor_combo.currentText().strip(),
            "action_text": self.action_combo.currentText().strip(),
            "step_label": self.step_combo.currentText().strip(),
            "step_type": self.step_type_combo.currentData(),
            "manual_click_offset": not self.center_click.isChecked(),
            "tap_after_capture": self.tap_after_capture.isChecked(),
        }


class DeepSeaSwipeCaptureDialog(QDialog):
    def __init__(
        self,
        image: QImage,
        *,
        title: str = "记录滑动",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1200, 820)
        self.selected_start: QPoint | None = None
        self.selected_end: QPoint | None = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.hint_label = QLabel("第一下点滑动起点，第二下点滑动终点。比如技能列表翻下去：从列表下方点到列表上方。")
        top.addWidget(self.hint_label)
        top.addStretch(1)
        self.reset_button = QPushButton("重选")
        top.addWidget(self.reset_button)
        layout.addLayout(top)

        self.view = GameView()
        self.view.setMinimumSize(1, 1)
        self.view.set_frame(image)
        self.view.set_zoom(0.0)
        self.view.clicked_on_frame.connect(self.on_point_clicked)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.view)
        layout.addWidget(self.scroll, 1)

        self.coord_label = QLabel("滑动：未设置")
        layout.addWidget(self.coord_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reset_button.clicked.connect(self.reset_points)

    def reset_points(self) -> None:
        self.selected_start = None
        self.selected_end = None
        self.view.set_pinned_point(None)
        self.ok_button.setEnabled(False)
        self.coord_label.setText("滑动：未设置")
        self.hint_label.setText("第一下点滑动起点，第二下点滑动终点。")

    def on_point_clicked(self, point: QPoint) -> None:
        if self.view.frame is None:
            return
        x = max(0, min(self.view.frame.width() - 1, int(point.x())))
        y = max(0, min(self.view.frame.height() - 1, int(point.y())))
        selected = QPoint(x, y)
        if self.selected_start is None or self.selected_end is not None:
            self.selected_start = selected
            self.selected_end = None
            self.view.set_pinned_point(selected)
            self.ok_button.setEnabled(False)
            self.coord_label.setText(f"滑动起点：{x}, {y}；请点终点")
            self.hint_label.setText("现在点滑动终点。")
            return
        self.selected_end = selected
        self.view.set_pinned_point(selected)
        self.ok_button.setEnabled(True)
        self.coord_label.setText(
            f"滑动：{self.selected_start.x()}, {self.selected_start.y()} -> "
            f"{self.selected_end.x()}, {self.selected_end.y()}"
        )
        self.hint_label.setText("滑动已设置，点 OK 保存。")


class DeepSeaOperationReviewDialog(QDialog):
    def __init__(
        self,
        storage: ProjectStorage,
        library: DeepSeaActionLibrary,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("深海记录回放 / 标注")
        self.storage = storage
        self.library = library
        self.sessions: list[Path] = []
        self.operations: list[dict[str, Any]] = []
        self.current_session: Path | None = None
        self.resize(1500, 900)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("记录"))
        self.session_combo = QComboBox()
        self.session_combo.setMinimumWidth(420)
        top.addWidget(self.session_combo)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.load_sessions)
        top.addWidget(refresh_button)
        top.addStretch(1)
        layout.addLayout(top)

        body = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["序号", "类型", "起点", "终点", "标注", "动作", "步骤"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.show_selected_operation)
        body.addWidget(self.table)

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_row = QHBoxLayout()
        self.before_label = QLabel("before")
        self.after_label = QLabel("after")
        for label in (self.before_label, self.after_label):
            label.setMinimumSize(520, 292)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("PreviewFrame")
            preview_row.addWidget(label, 1)
        preview_layout.addLayout(preview_row)
        self.detail_label = QLabel("请选择一条记录。")
        self.detail_label.setWordWrap(True)
        preview_layout.addWidget(self.detail_label)

        form = QFormLayout()
        self.actor_combo = QComboBox()
        self.actor_combo.setEditable(True)
        self.actor_combo.addItems(list(ACTOR_PRESETS))
        self.actor_combo.setCurrentText("1号人物")
        form.addRow("行动者", self.actor_combo)

        self.action_combo = QComboBox()
        self.action_combo.setEditable(True)
        self.action_combo.addItems(list(ACTION_PRESETS))
        self.action_combo.setCurrentText("放风")
        form.addRow("动作", self.action_combo)

        self.step_combo = QComboBox()
        self.step_combo.setEditable(True)
        form.addRow("步骤", self.step_combo)
        preview_layout.addLayout(form)

        buttons = QHBoxLayout()
        annotate_button = QPushButton("标注选中并加入动作库")
        annotate_button.clicked.connect(self.annotate_selected_as_action)
        exception_button = QPushButton("标注选中为异常")
        exception_button.clicked.connect(self.annotate_selected_as_exception)
        buttons.addWidget(annotate_button)
        buttons.addWidget(exception_button)
        buttons.addStretch(1)
        preview_layout.addLayout(buttons)
        body.addWidget(preview)
        body.setSizes([620, 860])
        layout.addWidget(body, 1)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.rejected.connect(self.reject)
        layout.addWidget(close_buttons)

        self.session_combo.currentIndexChanged.connect(self.load_selected_session)
        self.action_combo.currentTextChanged.connect(self.refresh_step_suggestions)
        self.actor_combo.currentTextChanged.connect(self.refresh_step_suggestions)
        self.load_sessions()

    def load_sessions(self) -> None:
        root = self.storage.deepsea_operation_records_dir()
        self.sessions = sorted(root.glob("session_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        current = self.session_combo.currentData()
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for session in self.sessions:
            count = len(list(session.glob("operation_*.json")))
            self.session_combo.addItem(f"{session.name} ({count} 步)", str(session))
        if current:
            index = self.session_combo.findData(current)
            if index >= 0:
                self.session_combo.setCurrentIndex(index)
        self.session_combo.blockSignals(False)
        self.load_selected_session()

    def load_selected_session(self) -> None:
        path_text = self.session_combo.currentData()
        self.current_session = Path(path_text) if path_text else None
        self.operations = []
        if self.current_session and self.current_session.exists():
            for path in sorted(self.current_session.glob("operation_*.json")):
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                item["_json_path"] = str(path)
                self.operations.append(item)
        self.refresh_table()
        self.refresh_step_suggestions()

    def refresh_table(self) -> None:
        self.table.setRowCount(0)
        for row_index, operation in enumerate(self.operations):
            self.table.insertRow(row_index)
            values = [
                operation.get("id", ""),
                operation.get("type", ""),
                str(operation.get("start", "")),
                str(operation.get("end", "")),
                operation.get("label", ""),
                operation.get("action", ""),
                operation.get("step_label", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.ItemDataRole.UserRole, operation.get("_json_path"))
                self.table.setItem(row_index, col, item)
        if self.operations:
            self.table.selectRow(0)

    def selected_operations(self) -> list[dict[str, Any]]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self.operations[row] for row in rows if 0 <= row < len(self.operations)]

    def show_selected_operation(self) -> None:
        selected = self.selected_operations()
        if not selected:
            self.before_label.setPixmap(QPixmap())
            self.after_label.setPixmap(QPixmap())
            self.detail_label.setText("请选择一条记录。")
            return
        operation = selected[0]
        self.set_preview_pixmap(self.before_label, operation.get("before"))
        self.set_preview_pixmap(self.after_label, operation.get("after"))
        self.detail_label.setText(
            f"{operation.get('id')}  {operation.get('type')}  "
            f"{operation.get('start')} -> {operation.get('end')}  "
            f"已选 {len(selected)} 步"
        )

    def set_preview_pixmap(self, label: QLabel, rel_name: str | None) -> None:
        if self.current_session is None or not rel_name:
            label.setPixmap(QPixmap())
            return
        path = self.current_session / str(rel_name)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            label.setPixmap(QPixmap())
            label.setText("无预览")
            return
        label.setPixmap(
            pixmap.scaled(
                label.width(),
                label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def refresh_step_suggestions(self) -> None:
        actor_text = self.actor_combo.currentText()
        action_text = self.action_combo.currentText()
        try:
            actor_ref = normalize_actor(actor_text)
            action_key = normalize_action(action_text)
        except ValueError:
            action_key = "unknown"
            existing_labels: list[str] = []
        else:
            action = self.library.get_action(actor_ref, action_key)
            existing_labels = [str(step.get("label") or "") for step in (action or {}).get("steps") or []]
        current = self.step_combo.currentText().strip()
        suggestions = list(suggested_step_labels(action_key))
        next_label = suggest_step_label(action_key, existing_labels)
        if next_label not in suggestions:
            suggestions.insert(0, next_label)
        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        self.step_combo.addItems(suggestions)
        keep_custom = bool(current and current not in suggestions and not re.fullmatch(r"步骤\s*\d+", current))
        self.step_combo.setCurrentText(current if keep_custom else next_label)
        self.step_combo.blockSignals(False)

    def annotate_selected_as_action(self) -> None:
        selected = self.selected_operations()
        if not selected:
            QMessageBox.information(self, "深海记录回放", "请先选中要标注的记录。")
            return
        try:
            actor_ref = normalize_actor(self.actor_combo.currentText())
            action_key = normalize_action(self.action_combo.currentText())
        except ValueError as exc:
            QMessageBox.warning(self, "深海记录回放", str(exc))
            return
        base_label = self.step_combo.currentText().strip() or "记录步骤"
        multi = len(selected) > 1
        for index, operation in enumerate(selected, start=1):
            step_label = f"{base_label} {index}" if multi else base_label
            self.update_operation_label(operation, actor_ref.label, action_key, step_label, "action")
            self.add_operation_to_action_library(operation, actor_ref, action_key, step_label)
        self.library.save()
        self.load_selected_session()

    def annotate_selected_as_exception(self) -> None:
        selected = self.selected_operations()
        if not selected:
            QMessageBox.information(self, "深海记录回放", "请先选中要标注的记录。")
            return
        label, ok = QInputDialog.getText(self, "标注异常", "异常标签", text="人物被飞")
        if not ok:
            return
        for operation in selected:
            self.update_operation_label(operation, "", "", "", f"异常：{label.strip() or '未命名'}")
        self.load_selected_session()

    def update_operation_label(
        self,
        operation: dict[str, Any],
        actor: str,
        action: str,
        step_label: str,
        label: str,
    ) -> None:
        path = Path(str(operation.get("_json_path") or ""))
        if not path.exists():
            return
        operation["actor"] = actor
        operation["action"] = action
        operation["step_label"] = step_label
        operation["label"] = label
        operation["reviewed_at"] = now_iso()
        clean = dict(operation)
        clean.pop("_json_path", None)
        path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_operation_to_action_library(
        self,
        operation: dict[str, Any],
        actor_ref: Any,
        action_key: str,
        step_label: str,
    ) -> None:
        operation_type = str(operation.get("type") or "tap")
        source_note = f"{self.current_session.name if self.current_session else ''}/{operation.get('id')}"
        if operation_type == "swipe":
            self.library.add_step(
                actor=actor_ref,
                action=action_key,
                step_label=step_label,
                step_type="swipe",
                swipe_start=operation.get("start"),
                swipe_end=operation.get("end"),
                duration_ms=int(float(operation.get("duration_seconds") or 0.45) * 1000),
                wait_after=0.4,
                note=source_note,
                replace_same_label=False,
            )
            return
        self.library.add_step(
            actor=actor_ref,
            action=action_key,
            step_label=step_label,
            step_type="recorded_tap",
            click_point=operation.get("start"),
            wait_after=0.4,
            note=source_note,
            replace_same_label=False,
        )


class StepCard(QWidget):
    toggled = Signal(str, bool)

    def __init__(self, index: int, step: dict[str, Any], *, depth: int = 0, display_index: str | None = None) -> None:
        super().__init__()
        self.step = step
        self.depth = depth
        self.setObjectName("StepCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8 + depth * 20, 6, 8, 6)
        layout.setSpacing(6)

        index_label = QLabel(display_index or f"{index:02d}")
        index_label.setObjectName("StepIndex")
        index_label.setFixedWidth(36)
        index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_box = QVBoxLayout()
        self.name_label = QLabel()
        self.name_label.setObjectName("StepName")
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.refresh_name()
        step_input = step.get("input") or {}
        if step.get("type") == "loop" and step_input.get("condition_branch"):
            kind_text = "默认分支" if step_input.get("condition_default_branch") else "条件分支"
        else:
            kind_text = STEP_LABELS.get(step.get("type", ""), step.get("type", ""))
        kind = QLabel(kind_text)
        kind.setObjectName("StepKind")
        text_box.addWidget(self.name_label)
        text_box.addWidget(kind)

        badges = QHBoxLayout()
        for label, present in (
            ("图", bool(step.get("assets"))),
            ("子", bool(step.get("children"))),
            ("OCR", step.get("type") in {"ocr_text", "ocr_number"}),
            ("失败", step.get("on_failure") not in {None, "", "stop"}),
        ):
            badge = QLabel(label)
            badge.setObjectName("StepBadgeOn" if present else "StepBadgeOff")
            badge.setFixedHeight(22)
            badges.addWidget(badge)
        badges.addStretch(1)

        enabled = QCheckBox()
        enabled.setChecked(bool(step.get("enabled", True)))
        enabled.setToolTip("启用/禁用")
        enabled.toggled.connect(lambda value: self.toggled.emit(step["id"], value))

        layout.addWidget(index_label)
        layout.addLayout(text_box, 1)
        layout.addLayout(badges)
        layout.addWidget(enabled)

    def refresh_name(self) -> None:
        text = str(self.step.get("name") or "step")
        if self.depth:
            text = f"↳ {text}"
        self.name_label.setText(text)


class FlowLoadDialog(QDialog):
    def __init__(self, storage: ProjectStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.rows: list[dict[str, Any]] = []
        self.selected_flow_path: str | None = None
        self.setWindowTitle("加载副本流程")
        self.resize(920, 520)

        layout = QVBoxLayout(self)
        hint = QLabel("选择要加载的副本流程。这里加载的是左侧流程步骤，不需要手动选择 flow.json。可用上移/下移调整顺序，启动时默认加载第一个。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["副本脚本", "步骤数", "更新时间", "保存位置"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _index: self.accept_selected())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        refresh = QPushButton("刷新")
        self.move_up_button = QPushButton("上移")
        self.move_down_button = QPushButton("下移")
        load = QPushButton("加载选中")
        delete = QPushButton("删除选中")
        cancel = QPushButton("取消")
        buttons.addWidget(refresh)
        buttons.addWidget(self.move_up_button)
        buttons.addWidget(self.move_down_button)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        buttons.addWidget(load)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        refresh.clicked.connect(lambda: self.load())
        self.move_up_button.clicked.connect(lambda: self.move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self.move_selected(1))
        load.clicked.connect(self.accept_selected)
        delete.clicked.connect(self.delete_selected)
        cancel.clicked.connect(self.reject)
        self.table.itemSelectionChanged.connect(self.update_order_buttons)
        self.load()

    def load(self, select_key: str | None = None) -> None:
        self.rows = self.storage.list_script_flows()
        self.table.setRowCount(len(self.rows))
        for row_idx, row in enumerate(self.rows):
            values = [
                row.get("script_name", ""),
                row.get("step_count", 0),
                row.get("updated_at", ""),
                row.get("directory", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.get("path"))
                if col == 3:
                    item.setToolTip(str(row.get("path", "")))
                self.table.setItem(row_idx, col, item)
        if self.rows:
            selected_row = 0
            if select_key:
                for row_idx, row in enumerate(self.rows):
                    if self.row_order_key(row) == select_key:
                        selected_row = row_idx
                        break
            self.table.selectRow(selected_row)
            self.table.scrollToItem(self.table.item(selected_row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
        self.update_order_buttons()

    def row_order_key(self, row: dict[str, Any]) -> str:
        return str(row.get("order_key") or Path(str(row.get("directory") or "")).name or row.get("script_name") or "")

    def update_order_buttons(self) -> None:
        row = self.table.currentRow()
        has_rows = bool(self.rows)
        self.move_up_button.setEnabled(has_rows and row > 0)
        self.move_down_button.setEnabled(has_rows and 0 <= row < len(self.rows) - 1)

    def move_selected(self, delta: int) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows):
            QMessageBox.information(self, "调整副本顺序", "请先选中一个副本流程。")
            return
        target = row + delta
        if target < 0 or target >= len(self.rows):
            return
        self.rows[row], self.rows[target] = self.rows[target], self.rows[row]
        moved_key = self.row_order_key(self.rows[target])
        self.storage.save_script_order([self.row_order_key(item) for item in self.rows])
        self.load(moved_key)

    def accept_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows):
            QMessageBox.information(self, "加载副本流程", "请先选中一个副本流程。")
            return
        self.selected_flow_path = str(self.rows[row]["path"])
        self.accept()

    def delete_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows):
            QMessageBox.information(self, "删除副本流程", "请先选中一个副本流程。")
            return
        item = self.rows[row]
        script_name = str(item.get("script_name") or "")
        reply = QMessageBox.question(
            self,
            "删除副本流程",
            f"确定删除副本流程“{script_name}”吗？\n"
            "会移动到 deleted_scripts 备份目录，不会立刻永久清空。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            target = self.storage.delete_script_flow_path(item.get("path") or "")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除副本流程", str(exc))
            return
        next_key = None
        if len(self.rows) > 1:
            next_key = self.row_order_key(self.rows[row + 1] if row + 1 < len(self.rows) else self.rows[row - 1])
        self.selected_flow_path = None
        self.load(next_key)
        QMessageBox.information(self, "删除副本流程", f"已删除并备份到：{target}")


class AssetManagerDialog(QDialog):
    def __init__(
        self,
        storage: ProjectStorage,
        parent: QWidget | None = None,
        use_callback: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.use_callback = use_callback
        self.rows: list[Any] = []
        self.setWindowTitle("素材管理器")
        self.resize(1320, 720)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.type_filter = QComboBox()
        self.type_filter.addItems(["全部", "image", "digit", "text", "npc", "button", "battle", "question", "loop_condition", "unknown"])
        self.status_filter = QComboBox()
        self.status_filter.addItem("可用", "active")
        self.status_filter.addItem("废弃", "deprecated")
        self.status_filter.addItem("全部", "全部")
        refresh = QPushButton("刷新")
        rename = QPushButton("重命名")
        insert_step = QPushButton("插入为步骤")
        select_duplicates = QPushButton("选出重复")
        deprecate_duplicates = QPushButton("废弃重复")
        deprecate = QPushButton("移到废弃")
        delete = QPushButton("永久删除")
        self.summary_label = QLabel("素材：-")
        top.addWidget(QLabel("类型"))
        top.addWidget(self.type_filter)
        top.addWidget(QLabel("状态"))
        top.addWidget(self.status_filter)
        top.addStretch(1)
        top.addWidget(self.summary_label)
        top.addWidget(refresh)
        top.addWidget(rename)
        top.addWidget(insert_step)
        top.addWidget(select_duplicates)
        top.addWidget(deprecate_duplicates)
        top.addWidget(deprecate)
        top.addWidget(delete)
        layout.addLayout(top)

        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels(["预览", "ID", "用户名", "自动名", "类型", "地图", "脚本", "步骤", "bbox", "raw", "crop", "annotated", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(72)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        refresh.clicked.connect(self.load)
        self.type_filter.currentTextChanged.connect(lambda _value: self.load())
        self.status_filter.currentTextChanged.connect(lambda _value: self.load())
        rename.clicked.connect(self.rename_selected)
        insert_step.clicked.connect(self.insert_selected_as_step)
        select_duplicates.clicked.connect(self.select_duplicate_assets)
        deprecate_duplicates.clicked.connect(self.deprecate_duplicate_assets)
        deprecate.clicked.connect(self.deprecate_selected)
        delete.clicked.connect(self.delete_selected)
        self.load()

    def load(self) -> None:
        self.rows = [
            row
            for row in self.storage.list_assets(self.type_filter.currentText(), self.status_filter.currentData())
            if str(row["type"]) != "coord"
        ]
        self.table.setRowCount(len(self.rows))
        for row_idx, row in enumerate(self.rows):
            self.table.setRowHeight(row_idx, 72)
            self.table.setCellWidget(row_idx, 0, self.preview_widget(row["crop_path"] or row["annotated_path"] or row["raw_path"]))
            values = [
                row["id"],
                row["user_name"],
                row["auto_name"],
                row["type"],
                row["map_id"],
                row["script_name"],
                row["step_id"],
                row["bbox"],
                row["raw_path"],
                row["crop_path"],
                row["annotated_path"],
                row["status"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if col in {8, 9, 10}:
                    item.setToolTip(str(value or ""))
                self.table.setItem(row_idx, col + 1, item)
        self.summary_label.setText(f"素材：{len(self.rows)} 条")

    def preview_widget(self, preview_path: str | None) -> QWidget:
        label = QLabel("无")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(86, 62)
        if preview_path:
            path = self.storage.abs(preview_path)
            if path and path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    label.setText("")
                    label.setPixmap(
                        pixmap.scaled(
                            84,
                            58,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    label.setToolTip(str(path))
        return label

    def selected_asset_id(self) -> str | None:
        ids = self.selected_asset_ids()
        return ids[0] if ids else None

    def selected_asset_ids(self) -> list[str]:
        items = self.table.selectedItems()
        if not items:
            return []
        rows = sorted({item.row() for item in items})
        ids: list[str] = []
        for row in rows:
            item = self.table.item(row, 1)
            if item and item.text():
                ids.append(item.text())
        return ids

    def rename_selected(self) -> None:
        asset_id = self.selected_asset_id()
        if not asset_id:
            return
        name, ok = QInputDialog.getText(self, "重命名素材", "新名称")
        if ok and name.strip():
            self.storage.rename_asset(asset_id, name.strip())
            self.load()

    def selected_asset_row(self) -> Any | None:
        asset_id = self.selected_asset_id()
        return self.storage.asset(asset_id) if asset_id else None

    def insert_selected_as_step(self) -> None:
        if not self.use_callback:
            QMessageBox.information(self, "插入为步骤", "当前入口没有连接流程编辑器。")
            return
        row = self.selected_asset_row()
        if not row:
            QMessageBox.information(self, "插入为步骤", "请先选中一个素材。")
            return
        self.use_callback(row)

    def asset_fingerprint(self, row: Any) -> tuple[str, str] | None:
        path_text = row["crop_path"] or row["annotated_path"] or row["raw_path"]
        path = self.storage.abs(path_text)
        if not path or not path.exists():
            bbox = str(row["bbox"] or "")
            if not bbox:
                return None
            return (str(row["type"] or "unknown"), f"bbox:{bbox}")
        try:
            digest = hashlib.sha1(path.read_bytes()).hexdigest()
        except OSError:
            return None
        return (str(row["type"] or "unknown"), digest)

    def duplicate_asset_groups(self) -> list[list[Any]]:
        groups: dict[tuple[str, str], list[Any]] = {}
        for row in self.storage.list_assets(status_filter="active"):
            key = self.asset_fingerprint(row)
            if key is None:
                continue
            groups.setdefault(key, []).append(row)
        return [group for group in groups.values() if len(group) > 1]

    def duplicate_asset_ids(self) -> list[str]:
        ids: list[str] = []
        for group in self.duplicate_asset_groups():
            for row in group[1:]:
                ids.append(str(row["id"]))
        return ids

    def select_duplicate_assets(self) -> None:
        duplicate_ids = set(self.duplicate_asset_ids())
        self.table.clearSelection()
        if not duplicate_ids:
            QMessageBox.information(self, "选出重复", "当前没有发现重复素材。")
            return
        visible = 0
        for row_idx, row in enumerate(self.rows):
            if str(row["id"]) not in duplicate_ids:
                continue
            visible += 1
            for col in range(self.table.columnCount()):
                item = self.table.item(row_idx, col)
                if item:
                    item.setSelected(True)
        self.summary_label.setText(f"重复素材：可见 {visible} / 总计 {len(duplicate_ids)}")
        if visible == 0:
            QMessageBox.information(self, "选出重复", f"发现 {len(duplicate_ids)} 个重复素材，但当前筛选条件下不可见。")

    def deprecate_duplicate_assets(self) -> None:
        ids = self.duplicate_asset_ids()
        if not ids:
            QMessageBox.information(self, "废弃重复", "当前没有发现重复素材。")
            return
        reply = QMessageBox.question(
            self,
            "废弃重复",
            f"将保留每组最新素材，把 {len(ids)} 个重复素材移到废弃目录。确定继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        moved = self.storage.move_assets_to_deprecated(ids)
        self.load()
        QMessageBox.information(self, "废弃重复", f"已移到废弃：{moved} 个。")

    def deprecate_selected(self) -> None:
        ids = self.selected_asset_ids()
        if not ids:
            QMessageBox.information(self, "移到废弃", "请先选中要废弃的素材。")
            return
        reply = QMessageBox.question(
            self,
            "移到废弃",
            f"确认把 {len(ids)} 个素材移到废弃目录？\nraw/crop/annotated 会移动到 assets/deprecated/<asset_id>/。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        moved = self.storage.move_assets_to_deprecated(ids)
        self.load()
        QMessageBox.information(self, "移到废弃", f"已移动 {moved} 个素材到废弃目录。")

    def delete_selected(self) -> None:
        ids = self.selected_asset_ids()
        if not ids:
            QMessageBox.information(self, "永久删除", "请先选中要删除的素材。")
            return
        reply = QMessageBox.question(
            self,
            "永久删除",
            f"确认永久删除 {len(ids)} 个素材？\n数据库记录会隐藏，raw/crop/annotated 文件会从磁盘删除。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = self.storage.delete_assets(ids)
        self.load()
        QMessageBox.information(self, "永久删除", f"已永久删除 {deleted} 个素材文件。")


class MovementLibraryDialog(QDialog):
    def __init__(
        self,
        storage: ProjectStorage,
        script_name: str,
        map_id: str,
        parent: QWidget | None = None,
        practice_callback: Callable[[], None] | None = None,
        route_practice_callback: Callable[[], None] | None = None,
        map_explore_callback: Callable[[], None] | None = None,
        map_report_callback: Callable[..., None] | None = None,
        report_callback: Callable[..., None] | None = None,
        cleanup_callback: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.script_name = script_name
        self.map_id = map_id
        self.practice_callback = practice_callback
        self.route_practice_callback = route_practice_callback
        self.map_explore_callback = map_explore_callback
        self.map_report_callback = map_report_callback
        self.report_callback = report_callback
        self.cleanup_callback = cleanup_callback
        self.targets: list[dict[str, Any]] = []
        self.setWindowTitle(f"移动库管理 - {script_name}")
        self.resize(1040, 680)

        layout = QVBoxLayout(self)
        form = QGridLayout()
        self.label_edit = QLineEdit()
        self.x_spin = QSpinBox()
        self.y_spin = QSpinBox()
        self.tolerance_spin = QSpinBox()
        for spin in (self.x_spin, self.y_spin):
            spin.setRange(0, 999)
        self.tolerance_spin.setRange(0, 20)
        self.exact_check = QCheckBox("必须精准到达")
        self.exact_check.setChecked(True)
        form.addWidget(QLabel("名称"), 0, 0)
        form.addWidget(self.label_edit, 0, 1, 1, 3)
        form.addWidget(QLabel("X"), 0, 4)
        form.addWidget(self.x_spin, 0, 5)
        form.addWidget(QLabel("Y"), 0, 6)
        form.addWidget(self.y_spin, 0, 7)
        form.addWidget(QLabel("容差"), 1, 0)
        form.addWidget(self.tolerance_spin, 1, 1)
        form.addWidget(self.exact_check, 1, 2, 1, 2)
        layout.addLayout(form)

        actions = QHBoxLayout()
        add_button = QPushButton("新增目标")
        update_button = QPushButton("更新选中")
        delete_button = QPushButton("删除选中")
        practice_button = QPushButton("自由练移动库")
        route_practice_button = QPushButton("按线路图训练")
        explore_map_button = QPushButton("探索地图")
        map_report_button = QPushButton("地图报告")
        report_button = QPushButton("训练报告")
        cleanup_button = QPushButton("清理垃圾样本")
        refresh_button = QPushButton("刷新")
        actions.addWidget(add_button)
        actions.addWidget(update_button)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        actions.addWidget(report_button)
        actions.addWidget(map_report_button)
        actions.addWidget(cleanup_button)
        actions.addWidget(explore_map_button)
        actions.addWidget(route_practice_button)
        actions.addWidget(practice_button)
        actions.addWidget(refresh_button)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(["ID", "名称", "X", "Y", "精准", "容差", "成功", "失败", "路线", "更新时间"])
        self.table.setColumnHidden(0, True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        log_label = QLabel("移动库日志 / 训练报告")
        log_label.setObjectName("StepName")
        layout.addWidget(log_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setMinimumHeight(130)
        layout.addWidget(self.log_view)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        add_button.clicked.connect(self.add_target)
        update_button.clicked.connect(self.update_selected)
        delete_button.clicked.connect(self.delete_selected)
        refresh_button.clicked.connect(self.load)
        practice_button.clicked.connect(self.practice)
        route_practice_button.clicked.connect(self.practice_route_plan)
        explore_map_button.clicked.connect(self.explore_map)
        map_report_button.clicked.connect(self.show_map_report)
        report_button.clicked.connect(self.show_report)
        cleanup_button.clicked.connect(self.cleanup_samples)
        self.table.itemSelectionChanged.connect(self.populate_fields_from_selection)
        self.load()

    def load(self) -> None:
        payload = self.storage.load_script_movement_coords(self.script_name)
        self.targets = [
            target
            for target in payload.get("targets") or []
            if target.get("map_id", self.map_id) == self.map_id and isinstance(target.get("coord"), list)
        ]
        self.table.setRowCount(len(self.targets))
        for row_idx, target in enumerate(self.targets):
            coord = target.get("coord") or [0, 0]
            routes = target.get("practice_routes") or {}
            values = [
                target.get("id", ""),
                target.get("label", ""),
                int(coord[0]),
                int(coord[1]),
                "是" if target.get("exact", True) else "否",
                int(target.get("tolerance", 0) or 0),
                int(target.get("practice_success", 0) or 0),
                int(target.get("practice_failure", 0) or 0),
                len(routes),
                target.get("updated_at", ""),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row_idx, col, QTableWidgetItem(str(value)))

    def selected_target_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.text() if item else None

    def field_values(self) -> tuple[str, list[int], int, bool]:
        coord = [int(self.x_spin.value()), int(self.y_spin.value())]
        label = self.label_edit.text().strip() or f"{coord[0]},{coord[1]}"
        return label, coord, int(self.tolerance_spin.value()), bool(self.exact_check.isChecked())

    def populate_fields_from_selection(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.targets):
            return
        target = self.targets[row]
        coord = target.get("coord") or [0, 0]
        self.label_edit.setText(str(target.get("label") or f"{coord[0]},{coord[1]}"))
        self.x_spin.setValue(int(coord[0]))
        self.y_spin.setValue(int(coord[1]))
        self.tolerance_spin.setValue(int(target.get("tolerance", 0) or 0))
        self.exact_check.setChecked(bool(target.get("exact", True)))

    def add_target(self) -> None:
        label, coord, tolerance, exact = self.field_values()
        self.storage.add_script_movement_coord(
            self.script_name,
            map_id=self.map_id,
            coord=coord,
            label=label,
            tolerance=tolerance,
            exact=exact,
        )
        self.load()

    def update_selected(self) -> None:
        target_id = self.selected_target_id()
        if not target_id:
            QMessageBox.information(self, "移动库管理", "请先选中一个目标点。")
            return
        label, coord, tolerance, exact = self.field_values()
        self.storage.update_script_movement_coord(
            self.script_name,
            target_id,
            map_id=self.map_id,
            coord=coord,
            label=label,
            tolerance=tolerance,
            exact=exact,
        )
        self.load()

    def delete_selected(self) -> None:
        target_id = self.selected_target_id()
        if not target_id:
            QMessageBox.information(self, "移动库管理", "请先选中一个目标点。")
            return
        if QMessageBox.question(self, "删除目标", "确定删除选中的移动目标点吗？") != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_script_movement_coord(self.script_name, target_id)
        self.load()

    def practice(self) -> None:
        if self.practice_callback is not None:
            QTimer.singleShot(0, self.practice_callback)

    def practice_route_plan(self) -> None:
        if self.route_practice_callback is not None:
            QTimer.singleShot(0, self.route_practice_callback)

    def explore_map(self) -> None:
        if self.map_explore_callback is not None:
            QTimer.singleShot(0, self.map_explore_callback)

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def show_report(self) -> None:
        if self.report_callback is not None:
            self.report_callback(self.append_log)

    def show_map_report(self) -> None:
        if self.map_report_callback is not None:
            self.map_report_callback(self.append_log)

    def cleanup_samples(self) -> None:
        if self.cleanup_callback is not None:
            self.cleanup_callback(self.append_log)
            self.load()


class PendingReviewDialog(QDialog):
    def __init__(self, storage: ProjectStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Pending Review")
        self.resize(1180, 680)
        self.rows: list[Any] = []

        layout = QVBoxLayout(self)

        body = QHBoxLayout()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "类型", "素材", "裁剪图", "创建时间"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        body.addWidget(self.table, 2)

        side = QVBoxLayout()
        self.preview_label = QLabel("选中一行查看裁剪图")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(360, 260)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_label.setScaledContents(False)
        side.addWidget(self.preview_label)

        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        side.addWidget(self.path_label)

        self.payload_view = QPlainTextEdit()
        self.payload_view.setReadOnly(True)
        self.payload_view.setMaximumHeight(120)
        side.addWidget(self.payload_view)

        form = QFormLayout()
        self.result_input = QLineEdit()
        self.result_input.setPlaceholderText("选中一行后输入正确结果")
        form.addRow("正确结果", self.result_input)
        side.addLayout(form)
        body.addLayout(side, 1)
        layout.addLayout(body, 1)

        buttons = QHBoxLayout()
        refresh = QPushButton("刷新")
        resolve = QPushButton("输入结果并入库")
        delete = QPushButton("删除选中")
        close = QPushButton("关闭")
        buttons.addWidget(refresh)
        self.confirm_review = QCheckBox("确认入公共库")
        self.confirm_review.setChecked(True)
        buttons.addWidget(self.confirm_review)
        buttons.addStretch(1)
        buttons.addWidget(resolve)
        buttons.addWidget(delete)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        refresh.clicked.connect(self.load)
        resolve.clicked.connect(self.resolve_selected)
        delete.clicked.connect(self.delete_selected)
        close.clicked.connect(self.reject)
        self.table.itemSelectionChanged.connect(self.show_selected_review)
        self.load()

    def load(self) -> None:
        self.rows = self.storage.list_pending_reviews()
        self.table.setRowCount(len(self.rows))
        for row_idx, row in enumerate(self.rows):
            values = [row["id"], row["kind"], row["asset_id"], row["crop_path"], row["created_at"]]
            for col, value in enumerate(values):
                self.table.setItem(row_idx, col, QTableWidgetItem(str(value or "")))
        if self.rows:
            self.table.selectRow(0)
        else:
            self.show_selected_review()

    def selected_row(self) -> Any | None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None
        row_index = selected[0].row()
        if row_index < 0 or row_index >= len(self.rows):
            return None
        return self.rows[row_index]

    def show_selected_review(self) -> None:
        row = self.selected_row()
        if row is None:
            self.preview_label.setText("没有待复核项目")
            self.preview_label.setPixmap(QPixmap())
            self.path_label.setText("")
            self.payload_view.setPlainText("")
            self.result_input.clear()
            self.result_input.setMaxLength(32767)
            return

        crop_path = row["crop_path"] or ""
        abs_path = self.storage.abs(crop_path)
        self.path_label.setText(str(abs_path or crop_path))
        self.payload_view.setPlainText(row["payload"] or "")
        self.result_input.clear()
        self.result_input.setMaxLength(1 if row["kind"] == "digit" else 32767)

        if abs_path is None or not abs_path.exists():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("裁剪图不存在")
            return
        pixmap = QPixmap(str(abs_path))
        if pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("裁剪图无法打开")
            return
        size = self.preview_label.size()
        target = QSize(max(1, size.width() - 16), max(1, size.height() - 16))
        self.preview_label.setText("")
        self.preview_label.setPixmap(
            pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resolve_selected(self) -> None:
        row = self.selected_row()
        if row is None:
            return
        review_id = row["id"]
        kind = row["kind"]
        value = self.result_input.text().strip()
        if not value:
            value, ok = QInputDialog.getText(self, "确认识别结果", f"请输入 {kind} 的正确结果")
            if not ok:
                return
            value = value.strip()
        if not value:
            return
        if kind == "digit" and not is_single_digit_value(value):
            QMessageBox.information(self, "Pending Review", "digit 只能输入单个数字 0-9。")
            return
        if self.confirm_review.isChecked():
            self.storage.resolve_pending_review(review_id, value)
            self.load()
        else:
            QMessageBox.information(self, "Pending Review", "请勾选“确认入公共库”后再入库。")

    def delete_selected(self) -> None:
        row = self.selected_row()
        if row is None:
            return
        review_id = row["id"]
        self.storage.delete_pending_review(review_id)
        self.load()


class QuestionEntryDialog(QDialog):
    def __init__(
        self,
        *,
        question_text: str,
        option_texts: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加题库")
        self.resize(780, 560)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.question_edit = QPlainTextEdit()
        self.question_edit.setPlainText(question_text)
        self.question_edit.setFixedHeight(112)
        form.addRow("题目", self.question_edit)

        self.option_edits: list[QLineEdit] = []
        self.answer_group = QButtonGroup(self)
        self.answer_buttons: list[QRadioButton] = []
        for index in range(4):
            edit = QLineEdit(option_texts[index] if index < len(option_texts) else "")
            edit.setMinimumWidth(360)
            self.option_edits.append(edit)
            row = QHBoxLayout()
            radio = QRadioButton("正确")
            self.answer_group.addButton(radio, index)
            self.answer_buttons.append(radio)
            row.addWidget(edit, 1)
            row.addWidget(radio)
            form.addRow(f"选项 {chr(ord('A') + index)}", row)
        self.answer_buttons[0].setChecked(True)

        hint = QLabel("选择正确答案时直接点对应选项右侧的“正确”。保存后会把该选项文字写入题库。")
        hint.setWordWrap(True)

        layout.addLayout(form)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        answer_index = self.answer_group.checkedId()
        if answer_index < 0:
            answer_index = 0
        answer_letter = chr(ord("A") + answer_index)
        answer_text = self.option_edits[answer_index].text().strip() if 0 <= answer_index < len(self.option_edits) else ""
        return {
            "question": self.question_edit.toPlainText().strip(),
            "options": [edit.text().strip() for edit in self.option_edits],
            "answer": answer_text or answer_letter,
        }


class QuestionEditDialog(QDialog):
    def __init__(self, row: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = row
        self.setWindowTitle("编辑题库")
        self.resize(820, 640)

        try:
            options = json.loads(row["options"] or "[]")
        except json.JSONDecodeError:
            options = []
        while len(options) < 4:
            options.append("")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.question_edit = QPlainTextEdit()
        self.question_edit.setPlainText(str(row["question"] or ""))
        self.question_edit.setFixedHeight(126)
        form.addRow("题目", self.question_edit)

        self.option_edits: list[QLineEdit] = []
        self.answer_group = QButtonGroup(self)
        self.answer_buttons: list[QRadioButton] = []
        answer = str(row["answer"] or "").strip()
        checked_index = 0
        if answer.upper() in {"A", "B", "C", "D"}:
            checked_index = ord(answer.upper()) - ord("A")
        else:
            for index, option in enumerate(options[:4]):
                if str(option).strip() == answer:
                    checked_index = index
                    break

        for index in range(4):
            edit = QLineEdit(str(options[index] or ""))
            edit.setMinimumWidth(420)
            self.option_edits.append(edit)
            row_layout = QHBoxLayout()
            radio = QRadioButton("正确")
            self.answer_group.addButton(radio, index)
            self.answer_buttons.append(radio)
            row_layout.addWidget(edit, 1)
            row_layout.addWidget(radio)
            form.addRow(f"选项 {chr(ord('A') + index)}", row_layout)
        if 0 <= checked_index < len(self.answer_buttons):
            self.answer_buttons[checked_index].setChecked(True)

        layout.addLayout(form)
        hint = QLabel("这里直接维护题目文字、四个选项和正确答案。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        answer_index = self.answer_group.checkedId()
        if answer_index < 0:
            answer_index = 0
        options = [edit.text().strip() for edit in self.option_edits]
        answer_text = options[answer_index].strip() if 0 <= answer_index < len(options) else ""
        return {
            "question": self.question_edit.toPlainText().strip(),
            "options": options,
            "answer": answer_text or chr(ord("A") + answer_index),
        }


class QuestionBankDialog(QDialog):
    def __init__(
        self,
        storage: ProjectStorage,
        parent: QWidget | None = None,
        result_capture_callback: Callable[[str, str], bool] | None = None,
        add_question_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.result_capture_callback = result_capture_callback
        self.add_question_callback = add_question_callback
        self.current_map = str(getattr(parent, "current_map", "map_001") or "map_001")
        self.rows: list[Any] = []
        self.setWindowTitle("题库管理")
        self.resize(1360, 760)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        refresh = QPushButton("刷新")
        add_question = QPushButton("添加题库")
        edit_selected = QPushButton("编辑选中")
        select_duplicates = QPushButton("选择重复项")
        delete_duplicates = QPushButton("删除重复项")
        delete_selected = QPushButton("删除选中")
        clear_selection = QPushButton("清除选择")
        self.summary_label = QLabel("题库：-")
        top.addWidget(self.summary_label)
        top.addStretch(1)
        top.addWidget(refresh)
        top.addWidget(add_question)
        top.addWidget(edit_selected)
        top.addWidget(select_duplicates)
        top.addWidget(delete_duplicates)
        top.addWidget(delete_selected)
        top.addWidget(clear_selection)
        layout.addLayout(top)

        body = QHBoxLayout()
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["截图", "ID", "题目", "答案选项", "答案内容", "选项", "答题次数", "题目区域", "选项区域", "创建时间"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(72)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _index: self.edit_selected_question())
        body.addWidget(self.table, 3)

        side = QVBoxLayout()
        self.preview_label = QLabel("选中题目查看截图")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(360, 260)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        side.addWidget(self.preview_label)

        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setMaximumBlockCount(300)
        side.addWidget(self.detail_view, 1)
        body.addLayout(side, 1)
        layout.addLayout(body, 1)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        refresh.clicked.connect(self.load)
        add_question.clicked.connect(self.add_question_from_capture)
        edit_selected.clicked.connect(self.edit_selected_question)
        select_duplicates.clicked.connect(self.select_duplicate_questions)
        delete_duplicates.clicked.connect(self.delete_duplicate_questions)
        delete_selected.clicked.connect(self.delete_selected_questions)
        clear_selection.clicked.connect(lambda: self.table.clearSelection())
        self.table.itemSelectionChanged.connect(self.show_selected_question)
        self.load()

    def load(self) -> None:
        self.rows = self.storage.list_questions()
        self.table.setRowCount(len(self.rows))
        for row_idx, row in enumerate(self.rows):
            self.table.setRowHeight(row_idx, 72)
            self.table.setCellWidget(row_idx, 0, self.preview_widget(row["annotated_path"] or row["raw_path"]))
            values = [
                row["id"],
                row["question"],
                self.answer_option_label(row),
                row["answer"],
                self.readable_options(row["options"]),
                row["answer_count"],
                row["question_bbox"],
                row["option_bboxes"],
                row["created_at"],
            ]
            for col, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value or ""))
                if col in {2, 4, 5}:
                    item.setToolTip(str(value or ""))
                self.table.setItem(row_idx, col, item)
        groups = self.duplicate_groups()
        duplicate_count = sum(max(0, len(indices) - 1) for indices in groups.values())
        answered = sum(int(row["answer_count"] or 0) for row in self.rows)
        self.summary_label.setText(f"题库：{len(self.rows)} 条，答题 {answered} 次，重复候选：{duplicate_count} 条")
        if self.rows:
            self.table.selectRow(0)
        else:
            self.show_selected_question()

    def readable_options(self, value: str | None) -> str:
        try:
            options = json.loads(value or "[]")
        except json.JSONDecodeError:
            return str(value or "")
        return " / ".join(f"{chr(ord('A') + index)}. {option}" for index, option in enumerate(options[:4]))

    def options_for_row(self, row: Any) -> list[str]:
        try:
            options = json.loads(row["options"] or "[]")
        except json.JSONDecodeError:
            return []
        return [str(option or "") for option in options]

    def answer_option_label(self, row: Any) -> str:
        options = self.options_for_row(row)
        index = answer_to_index(str(row["answer"] or ""), options)
        if index is None or index < 0 or index >= 4:
            return "未匹配"
        return chr(ord("A") + index)

    def preview_widget(self, preview_path: str | None) -> QWidget:
        label = QLabel("无")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(92, 62)
        if preview_path:
            path = self.storage.abs(preview_path)
            if path and path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    label.setText("")
                    label.setPixmap(
                        pixmap.scaled(
                            90,
                            60,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    label.setToolTip(str(path))
        return label

    def selected_question_ids(self) -> list[str]:
        selected_rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        ids: list[str] = []
        for row in selected_rows:
            if 0 <= row < len(self.rows):
                ids.append(str(self.rows[row]["id"]))
        return ids

    def selected_row(self) -> Any | None:
        selected = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not selected:
            return None
        row_index = selected[0].row()
        if row_index < 0 or row_index >= len(self.rows):
            return None
        return self.rows[row_index]

    def show_selected_question(self) -> None:
        row = self.selected_row()
        if row is None:
            self.preview_label.setText("没有题库记录")
            self.preview_label.setPixmap(QPixmap())
            self.detail_view.setPlainText("")
            return
        preview_path = row["annotated_path"] or row["raw_path"]
        abs_path = self.storage.abs(preview_path)
        if abs_path and abs_path.exists():
            pixmap = QPixmap(str(abs_path))
            if not pixmap.isNull():
                target = QSize(max(1, self.preview_label.width() - 16), max(1, self.preview_label.height() - 16))
                self.preview_label.setText("")
                self.preview_label.setPixmap(
                    pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            else:
                self.preview_label.setText("截图无法打开")
                self.preview_label.setPixmap(QPixmap())
        else:
            self.preview_label.setText("没有截图")
            self.preview_label.setPixmap(QPixmap())

        detail = [
            f"ID: {row['id']}",
            f"题目: {row['question']}",
            f"答案选项: {self.answer_option_label(row)}",
            f"答案内容: {row['answer']}",
            f"选项: {self.readable_options(row['options'])}",
            f"答题次数: {row['answer_count'] or 0}",
        ]
        self.detail_view.setPlainText("\n".join(detail))

    def edit_selected_question(self) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "编辑题库", "请先选中一道题。")
            return
        dialog = QuestionEditDialog(row, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["question"]:
            QMessageBox.warning(self, "编辑题库", "题目不能为空。")
            return
        self.storage.update_question(
            row["id"],
            question=values["question"],
            answer=values["answer"],
            options=values["options"],
        )
        self.load()

    def capture_result_condition(self, kind: str) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "题库判定截图", "请先选中一道题。")
            return
        if not self.result_capture_callback:
            QMessageBox.information(self, "题库判定截图", "当前窗口没有可用的截图回调。")
            return
        if self.result_capture_callback(str(row["id"]), kind):
            self.load()

    def add_question_from_capture(self) -> None:
        if not self.add_question_callback:
            QMessageBox.information(self, "添加题库", "当前窗口没有可用的截图添加回调。")
            return
        self.add_question_callback()
        self.load()

    def duplicate_groups(self) -> dict[str, list[int]]:
        groups: dict[str, list[int]] = {}
        for row_idx, row in enumerate(self.rows):
            key = normalize_text(row["question"])
            if not key:
                continue
            groups.setdefault(key, []).append(row_idx)
        return {key: indices for key, indices in groups.items() if len(indices) > 1}

    def duplicate_delete_rows(self) -> list[int]:
        delete_rows: list[int] = []
        for indices in self.duplicate_groups().values():
            keep = max(indices, key=self.question_keep_score)
            delete_rows.extend(index for index in indices if index != keep)
        return sorted(delete_rows)

    def question_keep_score(self, row_idx: int) -> tuple[int, int, str]:
        row = self.rows[row_idx]
        answer_count = int(row["answer_count"] or 0)
        condition_count = int(bool(row["success_check_path"])) + int(bool(row["failure_check_path"]))
        created_at = str(row["created_at"] or "")
        return (answer_count, condition_count, created_at)

    def select_duplicate_questions(self) -> None:
        self.table.clearSelection()
        rows = self.duplicate_delete_rows()
        for row in rows:
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setSelected(True)
        QMessageBox.information(self, "选择重复项", f"已选中 {len(rows)} 条重复候选。\n每组会保留答题次数最多、判定截图更完整、较新的那条。")

    def delete_selected_questions(self) -> None:
        ids = self.selected_question_ids()
        if not ids:
            QMessageBox.information(self, "删除题库", "请先选中要删除的题目。")
            return
        reply = QMessageBox.question(
            self,
            "删除题库",
            f"确认删除选中的 {len(ids)} 条题库记录？\n这是软删除，截图文件会保留。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = self.storage.delete_questions(ids)
        self.load()
        QMessageBox.information(self, "删除题库", f"已删除 {deleted} 条。")

    def delete_duplicate_questions(self) -> None:
        rows = self.duplicate_delete_rows()
        if not rows:
            QMessageBox.information(self, "删除重复项", "没有发现重复候选。")
            return
        ids = [str(self.rows[row]["id"]) for row in rows if 0 <= row < len(self.rows)]
        reply = QMessageBox.question(
            self,
            "删除重复项",
            f"将删除 {len(ids)} 条重复候选。\n每组保留答题次数最多、判定截图更完整、较新的那条。\n确认继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = self.storage.delete_questions(ids)
        self.load()
        QMessageBox.information(self, "删除重复项", f"已删除 {deleted} 条重复记录。")


class BugReportDialog(QDialog):
    STATUS_LABELS = {
        "open": "待修复",
        "reported": "已报告",
        "fixed": "已修复",
        "deleted": "已删除",
    }

    def __init__(self, storage: ProjectStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.rows: list[Any] = []
        self.setWindowTitle("Bug 待修复")
        self.resize(1280, 760)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("状态"))
        self.status_filter = QComboBox()
        for label, value in (("待修复", "open"), ("已报告", "reported"), ("已修复", "fixed"), ("全部", "全部")):
            self.status_filter.addItem(label, value)
        refresh = QPushButton("刷新")
        mark_fixed = QPushButton("已修复")
        make_report = QPushButton("错误报告")
        reopen = QPushButton("重新打开")
        delete = QPushButton("删除")
        self.summary_label = QLabel("Bug：-")
        top.addWidget(self.status_filter)
        top.addStretch(1)
        top.addWidget(self.summary_label)
        top.addWidget(refresh)
        top.addWidget(mark_fixed)
        top.addWidget(make_report)
        top.addWidget(reopen)
        top.addWidget(delete)
        layout.addLayout(top)

        body = QHBoxLayout()
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "状态", "类型", "脚本", "步骤", "标题", "创建时间"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        body.addWidget(self.table, 2)

        side = QVBoxLayout()
        self.preview_label = QLabel("选中记录查看错误截图")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(420, 260)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        side.addWidget(self.preview_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(300)
        side.addWidget(QLabel("日志片段"))
        side.addWidget(self.log_view, 1)

        self.report_edit = QPlainTextEdit()
        self.report_edit.setMaximumBlockCount(300)
        side.addWidget(QLabel("错误报告 / 处理备注"))
        side.addWidget(self.report_edit, 1)
        body.addLayout(side, 1)
        layout.addLayout(body, 1)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        self.status_filter.currentIndexChanged.connect(lambda _index: self.load())
        refresh.clicked.connect(self.load)
        mark_fixed.clicked.connect(lambda: self.set_selected_status("fixed"))
        make_report.clicked.connect(self.mark_selected_reported)
        reopen.clicked.connect(lambda: self.set_selected_status("open"))
        delete.clicked.connect(self.delete_selected)
        self.table.itemSelectionChanged.connect(self.show_selected_report)
        self.load()

    def load(self) -> None:
        self.rows = self.storage.list_bug_reports(self.status_filter.currentData())
        self.table.setRowCount(len(self.rows))
        for row_idx, row in enumerate(self.rows):
            values = [
                row["id"],
                self.STATUS_LABELS.get(row["status"], row["status"]),
                row["kind"],
                row["script_name"],
                row["step_name"] or row["step_id"],
                row["title"],
                row["created_at"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if col == 6:
                    item.setToolTip(str(value or ""))
                self.table.setItem(row_idx, col, item)
        self.summary_label.setText(f"Bug：{len(self.rows)} 条")
        if self.rows:
            self.table.selectRow(0)
        else:
            self.show_selected_report()

    def selected_row(self) -> Any | None:
        selected = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not selected:
            return None
        row_index = selected[0].row()
        if row_index < 0 or row_index >= len(self.rows):
            return None
        return self.rows[row_index]

    def show_selected_report(self) -> None:
        row = self.selected_row()
        if row is None:
            self.preview_label.setText("没有 Bug 记录")
            self.preview_label.setPixmap(QPixmap())
            self.log_view.setPlainText("")
            self.report_edit.setPlainText("")
            return

        screenshot_path = row["screenshot_path"] or ""
        abs_path = self.storage.abs(screenshot_path)
        if abs_path and abs_path.exists():
            pixmap = QPixmap(str(abs_path))
            if not pixmap.isNull():
                target = QSize(max(1, self.preview_label.width() - 16), max(1, self.preview_label.height() - 16))
                self.preview_label.setText("")
                self.preview_label.setPixmap(
                    pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            else:
                self.preview_label.setText("截图无法打开")
                self.preview_label.setPixmap(QPixmap())
        else:
            self.preview_label.setText("没有错误截图")
            self.preview_label.setPixmap(QPixmap())
        self.log_view.setPlainText(row["log_excerpt"] or "")
        self.report_edit.setPlainText(row["report_text"] or self.default_report_text(row))

    def default_report_text(self, row: Any) -> str:
        return "\n".join(
            [
                f"标题：{row['title']}",
                f"类型：{row['kind']}",
                f"脚本：{row['script_name'] or '-'}",
                f"步骤：{row['step_name'] or row['step_id'] or '-'}",
                f"截图：{row['screenshot_path'] or '无'}",
                "",
                "日志：",
                row["log_excerpt"] or "",
            ]
        )

    def set_selected_status(self, status: str) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "Bug 待修复", "请先选中一条记录。")
            return
        self.storage.update_bug_report_status(row["id"], status, report_text=self.report_edit.toPlainText().strip())
        self.load()

    def mark_selected_reported(self) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "错误报告", "请先选中一条记录。")
            return
        report_text = self.report_edit.toPlainText().strip() or self.default_report_text(row)
        self.storage.update_bug_report_status(row["id"], "reported", report_text=report_text)
        self.load()
        QMessageBox.information(self, "错误报告", "已保存错误报告并标记为已报告。")

    def delete_selected(self) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "Bug 待修复", "请先选中一条记录。")
            return
        reply = QMessageBox.question(self, "删除 Bug", f"确定删除“{row['title']}”吗？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.storage.delete_bug_report(row["id"])
        self.load()


class RunnerPanel(QWidget):
    def __init__(self, controller: "MainWindow") -> None:
        super().__init__(controller)
        self.controller = controller
        self.storage = controller.storage
        self._loading_scripts = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("运行模式")
        title.setObjectName("PanelTitle")
        hint = QLabel("暂停制作界面的截图刷新，专注执行脚本")
        hint.setObjectName("StatusPill")
        self.loop_stats_label = QLabel("循环统计：-")
        self.loop_stats_label.setObjectName("StatusPill")
        self.timing_stats_label = QLabel("耗时：-")
        self.timing_stats_label.setObjectName("StatusPill")
        timing_detail = QPushButton("耗时数据")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.timing_stats_label)
        header.addWidget(timing_detail)
        header.addWidget(self.loop_stats_label)
        header.addWidget(hint)
        layout.addLayout(header)

        controls = QFrame()
        controls.setObjectName("StepCard")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(10, 10, 10, 10)
        controls_layout.setSpacing(8)

        script_row = QHBoxLayout()
        script_row.setSpacing(8)
        self.script_combo = QComboBox()
        refresh = QPushButton("刷新脚本")
        load = QPushButton("加载脚本")
        delete = QPushButton("删除脚本")
        script_row.addWidget(QLabel("脚本"))
        script_row.addWidget(self.script_combo, 1)
        script_row.addWidget(refresh)
        script_row.addWidget(load)
        script_row.addWidget(delete)
        controls_layout.addLayout(script_row)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        run_all = QPushButton("从头运行")
        run_selected = QPushButton("从选中运行")
        run_loop = QPushButton("循环运行")
        mini = QPushButton("迷你运行窗")
        stop = QPushButton("停止")
        self.loop_count = QSpinBox()
        self.loop_count.setRange(0, 999999)
        self.loop_count.setSpecialValueText("一直循环")
        self.loop_count.setValue(0)
        self.loop_count.setToolTip("设为 0 时会一直循环，直到点击停止。")
        run_row.addWidget(QLabel("运行"))
        run_row.addWidget(run_all)
        run_row.addWidget(run_selected)
        run_row.addSpacing(14)
        run_row.addWidget(QLabel("循环次数"))
        run_row.addWidget(self.loop_count)
        run_row.addWidget(run_loop)
        run_row.addStretch(1)
        run_row.addWidget(mini)
        run_row.addWidget(stop)
        controls_layout.addLayout(run_row)
        layout.addWidget(controls)

        self.step_table = QTableWidget(0, 4)
        self.step_table.setHorizontalHeaderLabels(["序号", "步骤名", "类型", "状态"])
        self.step_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.step_table.horizontalHeader().setStretchLastSection(True)
        self.step_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.step_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.step_table, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1200)
        layout.addWidget(self.log_view, 1)

        refresh.clicked.connect(self.load_scripts)
        load.clicked.connect(self.load_selected_script)
        self.script_combo.currentIndexChanged.connect(self.load_selected_script_from_combo)
        delete.clicked.connect(self.delete_selected_script)
        run_all.clicked.connect(self.run_all)
        run_selected.clicked.connect(self.run_from_selected)
        run_loop.clicked.connect(self.run_loop)
        mini.clicked.connect(self.controller.open_mini_runner)
        stop.clicked.connect(self.controller.stop_run)
        self.loop_count.valueChanged.connect(lambda _value: self.refresh_loop_stats())
        timing_detail.clicked.connect(self.open_timing_history)
        self.controller.loop_stats_changed.connect(self.update_loop_stats)
        self.load_scripts()
        self.refresh_steps()
        self.refresh_loop_stats()

    def activate(self) -> None:
        self.load_scripts()
        self.auto_load_first_script_if_needed()
        self.refresh_steps()
        self.refresh_loop_stats()

    def append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def load_scripts(self) -> None:
        current = self.controller.flow.get("script_name")
        self._loading_scripts = True
        try:
            self.script_combo.clear()
            matched_current = False
            for row in self.storage.list_script_flows():
                self.script_combo.addItem(
                    f"{row.get('script_name')}  ({row.get('step_count')}步)",
                    row.get("path"),
                )
                if row.get("script_name") == current:
                    self.script_combo.setCurrentIndex(self.script_combo.count() - 1)
                    matched_current = True
            if not matched_current and self.script_combo.count() > 0:
                self.script_combo.setCurrentIndex(0)
        finally:
            self._loading_scripts = False

    def auto_load_first_script_if_needed(self) -> bool:
        if self.controller.flow.get("steps"):
            return False
        if self.script_combo.count() <= 0:
            return False
        if self.script_combo.currentIndex() < 0:
            self.script_combo.setCurrentIndex(0)
        path = self.script_combo.currentData()
        if not path:
            return False
        self.controller.load_flow_path(Path(path))
        return True

    def load_selected_script_from_combo(self, _index: int) -> None:
        if self._loading_scripts:
            return
        self.load_selected_script(auto=True)

    def selected_script_is_current(self, path: str | Path) -> bool:
        current_path = self.controller.current_flow_path
        if current_path is None:
            return False
        try:
            return Path(path).resolve() == Path(current_path).resolve()
        except OSError:
            return Path(path) == Path(current_path)

    def load_selected_script(self, checked: bool = False, *, auto: bool = False) -> None:
        del checked
        path = self.script_combo.currentData()
        if not path:
            if not auto:
                QMessageBox.information(self, "运行脚本", "没有可加载的副本流程。")
            return
        if self.selected_script_is_current(path):
            self.refresh_steps()
            self.refresh_loop_stats()
            return
        if not self.controller.confirm_save_if_dirty("运行脚本", "当前副本流程有未保存更改。加载运行脚本前要保存吗？"):
            self.load_scripts()
            return
        self.controller.load_flow_path(Path(path))
        self.refresh_steps()
        self.refresh_loop_stats()

    def delete_selected_script(self) -> None:
        path = self.script_combo.currentData()
        script_label = self.script_combo.currentText().split("  (", 1)[0]
        if not path:
            QMessageBox.information(self, "删除脚本", "没有可删除的副本流程。")
            return
        reply = QMessageBox.question(
            self,
            "删除脚本",
            f"确定删除副本流程“{script_label}”吗？\n会移动到 deleted_scripts 备份目录。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            target = self.storage.delete_script_flow_path(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除脚本", str(exc))
            return
        self.load_scripts()
        self.refresh_steps()
        self.append_log(f"[{time.strftime('%H:%M:%S')}] 已删除脚本并备份到：{target}")

    def refresh_steps(self) -> None:
        rows: list[tuple[str, dict[str, Any]]] = []
        self.collect_step_rows(self.controller.flow.get("steps") or [], "", rows)
        self.step_table.setRowCount(len(rows))
        for row_index, (number, step) in enumerate(rows):
            values = [
                number,
                step.get("name", ""),
                STEP_LABELS.get(step.get("type", ""), step.get("type", "")),
                "启用" if step.get("enabled", True) else "禁用",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, step.get("id"))
                self.step_table.setItem(row_index, col, item)
        if rows:
            self.step_table.selectRow(0)

    def collect_step_rows(self, steps: list[dict[str, Any]], prefix: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
        for index, step in enumerate(steps, start=1):
            number = f"{prefix}.{index}" if prefix else f"{index:02d}"
            rows.append((number, step))
            children = step.get("children") or []
            if children:
                self.collect_step_rows(children, number, rows)

    def selected_step_id(self) -> str | None:
        row = self.step_table.currentRow()
        if row < 0:
            return None
        item = self.step_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item and item.data(Qt.ItemDataRole.UserRole) else None

    def run_all(self) -> None:
        self.controller.pause_preview()
        self.controller.run_flow_from_index(0, "运行模式：从头运行。", "运行模式：运行结束。", reset_stop=True)

    def run_from_selected(self) -> None:
        step_id = self.selected_step_id()
        if not step_id:
            QMessageBox.information(self, "从选中运行", "请先选中一个步骤。")
            return
        self.controller.select_step_by_id(step_id)
        self.controller.pause_preview()
        self.controller.run_from_selected_step()

    def run_loop(self) -> None:
        cycles = None if self.loop_count.value() == 0 else int(self.loop_count.value())
        self.controller.pause_preview()
        self.controller.run_flow_repeated(cycles)

    def refresh_loop_stats(self) -> None:
        script_name = str(self.controller.flow.get("script_name") or "")
        stats = self.storage.script_loop_stats(script_name) if script_name else {}
        self.update_loop_stats(
            {
                "script_name": script_name,
                "current_attempt": 0,
                "current_completed": 0,
                "target": self.current_loop_target(),
                "history_completed": int(stats.get("loop_completed_count") or 0),
                "history_failed": int(stats.get("loop_failed_count") or 0),
                "last_duration_seconds": float(stats.get("last_duration_seconds") or 0.0),
                "avg_success_duration_seconds": float(stats.get("avg_success_duration_seconds") or 0.0),
                "best_success_duration_seconds": float(stats.get("best_success_duration_seconds") or 0.0),
                "worst_success_duration_seconds": float(stats.get("worst_success_duration_seconds") or 0.0),
                "timed_success_count": int(stats.get("timed_success_count") or 0),
            }
        )

    def current_loop_target(self) -> int | None:
        return None if self.loop_count.value() == 0 else int(self.loop_count.value())

    def update_loop_stats(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        script_name = str(payload.get("script_name") or "")
        if script_name and script_name != str(self.controller.flow.get("script_name") or ""):
            return
        target = payload.get("target")
        target_text = "∞" if target is None else str(target)
        current_completed = int(payload.get("current_completed") or 0)
        current_attempt = int(payload.get("current_attempt") or 0)
        history_completed = int(payload.get("history_completed") or 0)
        history_failed = int(payload.get("history_failed") or 0)
        self.loop_stats_label.setText(
            f"循环统计：本次 {current_completed}/{target_text}"
            f"（尝试 {current_attempt}），历史完成 {history_completed}，失败 {history_failed}"
        )
        self.timing_stats_label.setText(self.timing_summary_text(payload))

    def timing_summary_text(self, payload: dict[str, Any]) -> str:
        last_duration = float(payload.get("last_duration_seconds") or 0.0)
        avg_duration = float(payload.get("avg_success_duration_seconds") or 0.0)
        best_duration = float(payload.get("best_success_duration_seconds") or 0.0)
        timed_success_count = int(payload.get("timed_success_count") or 0)
        if timed_success_count <= 0 and last_duration <= 0:
            return "耗时：暂无"
        bits = []
        if last_duration > 0:
            bits.append(f"上次 {format_duration(last_duration)}")
        if timed_success_count > 0:
            bits.append(f"均 {format_duration(avg_duration)}")
            bits.append(f"快 {format_duration(best_duration)}")
        return "耗时：" + " / ".join(bits)

    def open_timing_history(self) -> None:
        script_name = str(self.controller.flow.get("script_name") or "")
        if not script_name:
            QMessageBox.information(self, "耗时数据", "请先加载一个脚本。")
            return
        rows = self.storage.list_script_cycle_runs(script_name, limit=300)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"耗时数据 - {script_name}")
        dialog.resize(860, 520)
        layout = QVBoxLayout(dialog)
        stats = self.storage.script_loop_stats(script_name)
        summary = QLabel(
            " / ".join(
                [
                    f"成功样本 {int(stats.get('timed_success_count') or 0)}",
                    f"平均 {format_duration(stats.get('avg_success_duration_seconds'))}",
                    f"最快 {format_duration(stats.get('best_success_duration_seconds'))}",
                    f"最慢 {format_duration(stats.get('worst_success_duration_seconds'))}",
                ]
            )
        )
        summary.setObjectName("StatusPill")
        layout.addWidget(summary)
        table = QTableWidget(len(rows), 6)
        table.setHorizontalHeaderLabels(["结束时间", "轮次", "结果", "耗时", "秒", "备注"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row_index, row in enumerate(rows):
            duration = float(row.get("duration_seconds") or 0)
            values = [
                row.get("ended_at", ""),
                row.get("cycle_number", ""),
                "成功" if row.get("success") else "失败",
                format_duration(duration),
                f"{duration:.1f}",
                row.get("notes", ""),
            ]
            for col, value in enumerate(values):
                table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()


class MiniRunnerWindow(QWidget):
    def __init__(self, controller: "MainWindow") -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.controller = controller
        self._alerted_errors: set[str] = set()
        self._syncing_loop_count = False
        self.setWindowTitle("StoneAge 运行")
        self.setMinimumWidth(360)
        self.resize(380, 250)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        top = QHBoxLayout()
        title = QLabel("运行监控")
        title.setObjectName("PanelTitle")
        self.pin_check = QCheckBox("置顶")
        self.pin_check.toggled.connect(self.set_stay_on_top)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.pin_check)
        layout.addLayout(top)

        self.status_label = QLabel("空闲")
        self.status_label.setObjectName("StatusPill")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.script_label = QLabel("脚本：-")
        self.script_label.setWordWrap(True)
        layout.addWidget(self.script_label)

        self.stats_label = QLabel("轮次：-")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

        self.timing_label = QLabel("耗时：-")
        self.timing_label.setWordWrap(True)
        layout.addWidget(self.timing_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(80)
        self.log_view.setFixedHeight(72)
        layout.addWidget(self.log_view)

        run_row = QHBoxLayout()
        run_all = QPushButton("从头")
        run_loop = QPushButton("循环")
        self.loop_count = QSpinBox()
        self.loop_count.setRange(0, 999999)
        self.loop_count.setSpecialValueText("∞")
        self.loop_count.setValue(0)
        self.loop_count.setFixedWidth(74)
        stop = QPushButton("停止")
        run_row.addWidget(run_all)
        run_row.addWidget(QLabel("次数"))
        run_row.addWidget(self.loop_count)
        run_row.addWidget(run_loop)
        run_row.addWidget(stop)
        layout.addLayout(run_row)

        window_row = QHBoxLayout()
        show_main = QPushButton("主窗口")
        hide_main = QPushButton("隐藏")
        bug_reports = QPushButton("Bug")
        window_row.addWidget(show_main)
        window_row.addWidget(hide_main)
        window_row.addWidget(bug_reports)
        layout.addLayout(window_row)

        run_all.clicked.connect(self.run_all)
        run_loop.clicked.connect(self.run_loop)
        stop.clicked.connect(self.controller.stop_run)
        show_main.clicked.connect(self.show_main_window)
        hide_main.clicked.connect(self.controller.hide)
        bug_reports.clicked.connect(self.open_bug_reports)
        self.loop_count.valueChanged.connect(self.sync_loop_count_to_runner)
        self.controller.runner_panel.loop_count.valueChanged.connect(self.sync_loop_count_from_runner)

        self.controller.log_line_ready.connect(self.append_log)
        self.controller.loop_stats_changed.connect(self.update_loop_stats)
        self.controller.runtime_status_changed.connect(self.update_runtime_status)
        self.controller.runtime_error_alert.connect(self.show_error_alert)

    def set_stay_on_top(self, enabled: bool) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if was_visible:
            self.show()
            self.raise_()

    def sync_from_controller(self) -> None:
        script_name = str(self.controller.flow.get("script_name") or "-")
        self.script_label.setText(f"脚本：{script_name}")
        self.sync_loop_count_from_runner(int(self.controller.runner_panel.loop_count.value()))
        stats = self.controller.storage.script_loop_stats(script_name) if script_name and script_name != "-" else {}
        self.update_loop_stats(
            {
                "script_name": script_name,
                "current_attempt": 0,
                "current_completed": 0,
                "target": self.current_loop_target(),
                "history_completed": int(stats.get("loop_completed_count") or 0),
                "history_failed": int(stats.get("loop_failed_count") or 0),
                "last_duration_seconds": float(stats.get("last_duration_seconds") or 0.0),
                "avg_success_duration_seconds": float(stats.get("avg_success_duration_seconds") or 0.0),
                "best_success_duration_seconds": float(stats.get("best_success_duration_seconds") or 0.0),
                "timed_success_count": int(stats.get("timed_success_count") or 0),
            }
        )
        self.update_runtime_status(self.controller.current_runtime_status())
        self.controller.runner_panel.refresh_loop_stats()
        self.log_view.setPlainText(self.controller.recent_log_excerpt(limit=10))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def current_loop_target(self) -> int | None:
        return None if self.loop_count.value() == 0 else int(self.loop_count.value())

    def sync_loop_count_from_runner(self, value: int) -> None:
        if self._syncing_loop_count:
            return
        self._syncing_loop_count = True
        self.loop_count.setValue(int(value))
        self._syncing_loop_count = False

    def sync_loop_count_to_runner(self, value: int) -> None:
        if self._syncing_loop_count:
            return
        self._syncing_loop_count = True
        self.controller.runner_panel.loop_count.setValue(int(value))
        self._syncing_loop_count = False

    def append_log(self, line: str) -> None:
        self.log_view.appendPlainText(str(line))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def update_loop_stats(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        script_name = str(payload.get("script_name") or "")
        if script_name:
            self.script_label.setText(f"脚本：{script_name}")
        target = payload.get("target")
        target_text = "∞" if target is None else str(target)
        current_completed = int(payload.get("current_completed") or 0)
        current_attempt = int(payload.get("current_attempt") or 0)
        active_round = payload.get("active_round")
        history_completed = int(payload.get("history_completed") or 0)
        history_failed = int(payload.get("history_failed") or 0)
        if active_round:
            prefix = f"正在第 {int(active_round)}/{target_text} 轮"
        else:
            prefix = f"本次 {current_completed}/{target_text}"
        self.stats_label.setText(
            f"轮次：{prefix}，尝试 {current_attempt}，历史完成 {history_completed}，失败 {history_failed}"
        )
        self.timing_label.setText(self.controller.runner_panel.timing_summary_text(payload))

    def update_runtime_status(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        state = str(payload.get("state") or "idle")
        task = str(payload.get("task") or "")
        detail = str(payload.get("detail") or "")
        if state == "running":
            text = f"正常运行：{task or '脚本'}"
            color = "#0f7b3a"
        elif state == "stopping":
            text = "正在停止..."
            color = "#9a6700"
        elif state == "error":
            text = f"出错：{detail or task or '请查看 Bug'}"
            color = "#b42318"
        else:
            text = "空闲"
            color = "#425466"
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 700;")

    def ensure_script_loaded(self) -> bool:
        self.controller.runner_panel.load_scripts()
        self.controller.runner_panel.auto_load_first_script_if_needed()
        if not self.controller.flow.get("steps"):
            QMessageBox.information(self, "迷你运行窗", "没有可运行的脚本。请先在主窗口保存或加载脚本。")
            return False
        self.sync_from_controller()
        return True

    def run_all(self) -> None:
        if not self.ensure_script_loaded():
            return
        self.controller.pause_preview()
        self.controller.run_flow_from_index(0, "迷你运行窗：从头运行。", "迷你运行窗：运行结束。", reset_stop=True)

    def run_loop(self) -> None:
        if not self.ensure_script_loaded():
            return
        self.sync_loop_count_to_runner(int(self.loop_count.value()))
        cycles = None if self.loop_count.value() == 0 else int(self.loop_count.value())
        self.controller.pause_preview()
        self.controller.run_flow_repeated(cycles)

    def show_main_window(self) -> None:
        self.controller.restore_main_window()
        self.hide()

    def open_bug_reports(self) -> None:
        self.controller.restore_main_window()
        self.controller.open_bug_reports()

    def show_error_alert(self, payload: object) -> None:
        if not isinstance(payload, dict):
            message = str(payload)
            report_id = ""
        else:
            message = str(payload.get("title") or payload.get("message") or "运行出错")
            report_id = str(payload.get("report_id") or "")
        key = f"{report_id}:{message}"
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.update_runtime_status({"state": "error", "detail": message})
        if key not in self._alerted_errors:
            self._alerted_errors.add(key)
            QMessageBox.warning(self, "运行出错", f"{message}\n\n已加入 Bug 待修复：{report_id or '-'}")

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        if not self.controller.isVisible():
            self.controller.restore_main_window()
        super().closeEvent(event)


class PresetManagerDialog(QDialog):
    def __init__(
        self,
        storage: ProjectStorage,
        script_name: str,
        parent: QWidget | None = None,
        insert_callback: Callable[[dict[str, Any]], None] | None = None,
        save_current_callback: Callable[[], bool] | None = None,
        create_default_callback: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.script_name = script_name
        self.insert_callback = insert_callback
        self.save_current_callback = save_current_callback
        self.create_default_callback = create_default_callback
        self.presets: list[dict[str, Any]] = []
        self.setWindowTitle(f"预设管理 - {script_name}")
        self.resize(1080, 660)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "预设可以保存一组连续步骤，例如更改设置、打开菜单、战斗切换循环。"
            "通用预设所有副本都能用；当前副本预设只跟随这个副本。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        actions = QHBoxLayout()
        create_default = QPushButton("新建战斗切换循环")
        save_current = QPushButton("保存选中步骤为预设")
        insert = QPushButton("插入选中预设")
        rename = QPushButton("重命名")
        change_scope = QPushButton("改范围")
        delete = QPushButton("删除")
        refresh = QPushButton("刷新")
        actions.addWidget(create_default)
        actions.addWidget(save_current)
        actions.addStretch(1)
        actions.addWidget(insert)
        actions.addWidget(rename)
        actions.addWidget(change_scope)
        actions.addWidget(delete)
        actions.addWidget(refresh)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["ID", "范围", "名称", "用途", "默认次数", "步骤数", "更新时间", "说明"])
        self.table.setColumnHidden(0, True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _index: self.insert_selected())
        layout.addWidget(self.table, 1)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)

        create_default.clicked.connect(self.create_default_loop)
        save_current.clicked.connect(self.save_current_loop)
        insert.clicked.connect(self.insert_selected)
        rename.clicked.connect(self.rename_selected)
        change_scope.clicked.connect(self.change_selected_scope)
        delete.clicked.connect(self.delete_selected)
        refresh.clicked.connect(self.load)
        self.load()

    def load(self) -> None:
        payload = self.storage.load_combined_script_presets(self.script_name)
        self.presets = [preset for preset in payload.get("presets") or [] if isinstance(preset, dict)]
        self.table.setRowCount(len(self.presets))
        for row_idx, preset in enumerate(self.presets):
            steps = [step for step in preset.get("steps") or [] if isinstance(step, dict)]
            scope = self.preset_scope(preset)
            values = [
                preset.get("id", ""),
                "通用" if scope == "global" else "当前副本",
                preset.get("name") or "流程预设",
                "战斗" if preset.get("kind") == "battle" else "普通",
                preset.get("repeat_count", 1),
                sum(self.storage._count_flow_steps([step]) for step in steps),
                preset.get("updated_at", ""),
                preset.get("note", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if col == 6:
                    item.setToolTip(str(value or ""))
                self.table.setItem(row_idx, col, item)
        if self.presets:
            self.table.selectRow(0)

    def preset_scope(self, preset: dict[str, Any]) -> str:
        scope = str(preset.get("_preset_scope") or preset.get("scope") or "")
        return "global" if scope == "global" else "script"

    def save_preset_to_own_scope(self, preset: dict[str, Any]) -> None:
        updated = copy.deepcopy(preset)
        updated.pop("_preset_scope", None)
        if self.preset_scope(preset) == "global":
            self.storage.upsert_project_preset(updated)
        else:
            self.storage.upsert_script_preset(self.script_name, updated)

    def delete_preset_from_own_scope(self, preset: dict[str, Any]) -> bool:
        preset_id = str(preset.get("id") or "")
        if self.preset_scope(preset) == "global":
            return self.storage.delete_project_preset(preset_id)
        return self.storage.delete_script_preset(self.script_name, preset_id)

    def selected_preset(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.presets):
            return None
        return self.presets[row]

    def create_default_loop(self) -> None:
        if not self.create_default_callback:
            return
        if self.create_default_callback():
            self.accept()

    def save_current_loop(self) -> None:
        if not self.save_current_callback:
            return
        if self.save_current_callback():
            self.load()

    def insert_selected(self) -> None:
        preset = self.selected_preset()
        if not preset:
            QMessageBox.information(self, "插入预设", "请先选中一个预设。")
            return
        if self.insert_callback:
            self.insert_callback(preset)
            self.accept()

    def rename_selected(self) -> None:
        preset = self.selected_preset()
        if not preset:
            QMessageBox.information(self, "重命名预设", "请先选中一个预设。")
            return
        current_name = str(preset.get("name") or "流程预设")
        name, ok = QInputDialog.getText(self, "重命名预设", "新名称", text=current_name)
        if not ok or not name.strip():
            return
        updated = copy.deepcopy(preset)
        updated["name"] = name.strip()
        self.save_preset_to_own_scope(updated)
        self.load()

    def change_selected_scope(self) -> None:
        preset = self.selected_preset()
        if not preset:
            QMessageBox.information(self, "更改预设范围", "请先选中一个预设。")
            return
        current_scope = self.preset_scope(preset)
        choices = [
            "通用预设（所有副本可用）",
            f"当前副本预设（仅 {self.script_name}）",
        ]
        default_index = 0 if current_scope == "global" else 1
        choice, ok = QInputDialog.getItem(self, "更改预设范围", "保存范围", choices, default_index, False)
        if not ok or not choice:
            return
        target_scope = "global" if choice.startswith("通用") else "script"
        if target_scope == current_scope:
            return
        updated = copy.deepcopy(preset)
        preset_id = str(updated.get("id") or "")
        updated.pop("_preset_scope", None)
        if target_scope == "global":
            self.storage.upsert_project_preset(updated)
            if preset_id:
                self.storage.delete_script_preset(self.script_name, preset_id)
        else:
            self.storage.upsert_script_preset(self.script_name, updated)
            if preset_id:
                self.storage.delete_project_preset(preset_id)
        self.load()

    def delete_selected(self) -> None:
        preset = self.selected_preset()
        if not preset:
            QMessageBox.information(self, "删除预设", "请先选中一个预设。")
            return
        name = str(preset.get("name") or "流程预设")
        scope_text = "通用预设" if self.preset_scope(preset) == "global" else "当前副本预设"
        reply = QMessageBox.question(self, "删除预设", f"确定删除“{name}”吗？\n范围：{scope_text}")
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.delete_preset_from_own_scope(preset)
        self.load()


class StepReuseDialog(QDialog):
    def __init__(self, storage: ProjectStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.rows: list[dict[str, Any]] = []
        self.selected_step: dict[str, Any] | None = None
        self.setWindowTitle("历史步骤库")
        self.resize(1280, 720)

        layout = QVBoxLayout(self)
        hint = QLabel("这里会整理已保存流程、已有素材生成的可复用步骤，以及旧步骤截图记录。能插入的步骤会复用同一份素材；删掉只是从步骤库隐藏，不会删除素材文件。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("类型"))
        self.type_filter = QComboBox()
        self.type_filter.addItems(["全部", "点击目标", "识别图片", "判断文字", "判断数字", "点击", "答题", "旧截图记录"])
        self.type_filter.currentTextChanged.connect(lambda _value: self.load())
        filter_row.addWidget(self.type_filter)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["预览", "来源", "名称", "类型", "地图", "脚本/位置", "可插入", "更新时间", "引用"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(66)
        self.table.setIconSize(QSize(80, 54))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _index: self.accept_selected())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        refresh = QPushButton("刷新")
        hide = QPushButton("删除选中")
        unhide = QPushButton("显示已隐藏")
        insert = QPushButton("插入选中")
        cancel = QPushButton("取消")
        buttons.addWidget(refresh)
        buttons.addWidget(hide)
        buttons.addWidget(unhide)
        buttons.addStretch(1)
        buttons.addWidget(insert)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        refresh.clicked.connect(self.load)
        hide.clicked.connect(self.hide_selected)
        unhide.clicked.connect(self.clear_hidden)
        insert.clicked.connect(self.accept_selected)
        cancel.clicked.connect(self.reject)
        self.load()

    def load(self) -> None:
        hidden = self.storage.hidden_step_library_keys()
        self.rows = []
        selected_filter = self.type_filter.currentText() if hasattr(self, "type_filter") else "全部"
        for script in self.storage.list_script_flows():
            path = Path(str(script.get("path")))
            try:
                flow = load_flow(path)
            except Exception:  # noqa: BLE001
                continue
            self.collect_flow_steps(
                str(script.get("script_name") or flow.get("script_name") or path.parent.name),
                path,
                flow.get("steps") or [],
                hidden,
            )
        self.collect_asset_steps(hidden)
        self.collect_step_folders(hidden)
        if selected_filter != "全部":
            self.rows = [row for row in self.rows if row["type"] == selected_filter]
        self.rows.sort(key=lambda row: (row["insertable"], row["updated_at"]), reverse=True)
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            self.table.setRowHeight(row_index, 66)
            self.table.setCellWidget(row_index, 0, self.preview_widget(row.get("preview_path")))
            values = [
                row["source"],
                row["name"],
                row["type"],
                row["map_id"],
                row["location"],
                "是" if row["insertable"] else "否",
                row["updated_at"],
                row["key"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row_index, col + 1, item)
        if self.rows:
            self.table.selectRow(0)

    def preview_widget(self, preview_path: str | None) -> QWidget:
        label = QLabel("无")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(82, 58)
        if preview_path:
            path = self.storage.abs(preview_path)
            if path and path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    label.setText("")
                    label.setPixmap(
                        pixmap.scaled(
                            80,
                            54,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    label.setToolTip(str(path))
        return label

    def preview_path_for_step(self, step: dict[str, Any]) -> str:
        template_path = step.get("input", {}).get("template_path")
        if template_path:
            path = self.storage.abs(template_path)
            if path and path.exists():
                return str(template_path)
        for asset_id in step.get("assets") or []:
            asset = self.storage.asset(str(asset_id))
            if asset and asset["crop_path"]:
                return str(asset["crop_path"])
        return ""

    def collect_flow_steps(
        self,
        script_name: str,
        path: Path,
        steps: list[dict[str, Any]],
        hidden: set[str],
        prefix: str = "",
    ) -> None:
        for index, step in enumerate(steps, start=1):
            number = f"{prefix}{index}" if not prefix else f"{prefix}.{index}"
            if step.get("type") in {"read_game_coord", "move_to_game_coord"}:
                continue
            key = f"flow:{path}:{step.get('id', number)}"
            if key not in hidden:
                self.rows.append(
                    {
                        "key": key,
                        "source": "流程",
                        "name": step.get("name", ""),
                        "type": STEP_LABELS.get(step.get("type", ""), step.get("type", "")),
                        "map_id": "",
                        "location": f"{script_name} #{number}",
                        "updated_at": step.get("updated_at") or "",
                        "insertable": True,
                        "step": copy.deepcopy(step),
                        "preview_path": self.preview_path_for_step(step),
                    }
                )
            children = step.get("children") or []
            if children:
                self.collect_flow_steps(script_name, path, children, hidden, number)

    def collect_asset_steps(self, hidden: set[str]) -> None:
        for asset in self.storage.list_assets(status_filter="active"):
            key = f"asset:{asset['id']}"
            if key in hidden:
                continue
            if str(asset["type"]) == "coord":
                continue
            step = self.step_from_asset(asset)
            if step is None:
                step_type = "坐标区域配置" if str(asset["type"]) == "coord" else "旧截图记录"
                insertable = False
            else:
                step_type = STEP_LABELS.get(step["type"], step["type"])
                insertable = True
            name = asset["user_name"] or asset["auto_name"] or asset["id"]
            self.rows.append(
                {
                    "key": key,
                    "source": "素材步骤",
                    "name": name,
                    "type": step_type,
                    "map_id": asset["map_id"] or "",
                    "location": asset["crop_path"] or "",
                    "updated_at": asset["created_at"] or "",
                    "insertable": insertable,
                    "step": step,
                    "preview_path": asset["crop_path"] or "",
                }
            )

    def collect_step_folders(self, hidden: set[str]) -> None:
        scripts_root = self.storage.root / "scripts"
        for steps_root in scripts_root.glob("*/steps"):
            for folder in steps_root.iterdir():
                if not folder.is_dir():
                    continue
                key = f"folder:{folder}"
                if key in hidden:
                    continue
                if any(row["key"].endswith(folder.name.split("_", 2)[1] if folder.name.startswith("step_") else folder.name) for row in self.rows):
                    continue
                parts = folder.name.split("_")
                raw_type = parts[-1] if parts else "unknown"
                stat = folder.stat()
                preview_path = ""
                for pattern in ("*crop*.png", "*.png"):
                    found = next(folder.glob(pattern), None)
                    if found:
                        preview_path = str(found)
                        break
                self.rows.append(
                    {
                        "key": key,
                        "source": "旧步骤截图",
                        "name": folder.name,
                        "type": "旧截图记录",
                        "map_id": "",
                        "location": str(folder),
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                        "insertable": False,
                        "step": None,
                        "preview_path": preview_path,
                    }
                )

    def step_from_asset(self, asset: Any) -> dict[str, Any] | None:
        kind = str(asset["type"])
        step_type = {
            "npc": "click_target",
            "target": "click_target",
            "button": "click_target",
            "image": "image_check",
            "transition": "image_check",
            "battle": "image_check",
            "loop_condition": "image_check",
            "text": "ocr_text",
            "digit": "ocr_number",
        }.get(kind)
        if step_type is None:
            return None
        name_source = asset["user_name"] or asset["auto_name"] or kind
        step = create_step(step_type, f"{STEP_LABELS.get(step_type, step_type)}_{name_source}")
        step["assets"].append(asset["id"])
        step["input"]["asset_id"] = asset["id"]
        try:
            bbox = json.loads(asset["bbox"] or "null")
        except json.JSONDecodeError:
            bbox = None
        if "bbox" in step["input"]:
            step["input"]["bbox"] = bbox
        if step_type in {"image_check", "click_target", "find_target"}:
            step["input"]["template_path"] = asset["crop_path"]
            step["input"]["threshold"] = 0.75 if step_type == "click_target" else 0.85
            step["input"]["wait_until_found"] = True
            step["input"]["wait_after_found"] = 0.8 if step_type == "click_target" else 0.5
            step["timeout"] = 15.0
        return step

    def selected_rows(self) -> list[dict[str, Any]]:
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        rows: list[dict[str, Any]] = []
        for index in indexes:
            if 0 <= index.row() < len(self.rows):
                rows.append(self.rows[index.row()])
        return rows

    def hide_selected(self) -> None:
        rows = self.selected_rows()
        if not rows:
            QMessageBox.information(self, "历史步骤库", "请先选中要删除的步骤库条目。")
            return
        self.storage.hide_step_library_keys([row["key"] for row in rows])
        self.load()

    def clear_hidden(self) -> None:
        self.storage.clear_hidden_step_library_keys()
        self.load()

    def accept_selected(self) -> None:
        rows = self.selected_rows()
        row = rows[0] if rows else None
        if not row:
            QMessageBox.information(self, "步骤复用", "请先选中一个步骤。")
            return
        if not row["insertable"] or not row.get("step"):
            QMessageBox.information(self, "历史步骤库", "这个条目只有旧截图记录，缺少可执行参数，不能直接插入。")
            return
        self.selected_step = copy.deepcopy(row["step"])
        self.accept()


class DeepSeaChestStatsDialog(QDialog):
    def __init__(self, storage: ProjectStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.records: list[Any] = []
        self.selected_record_id: str | None = None
        self.setWindowTitle("深海6楼宝箱统计")
        self.resize(1320, 820)

        layout = QVBoxLayout(self)
        header = QLabel("深海6楼宝箱统计")
        header.setObjectName("PanelTitle")
        layout.addWidget(header)

        form_box = QFrame()
        form_box.setObjectName("StepCard")
        form = QGridLayout(form_box)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.record_date_edit = QLineEdit(now_iso().split("T", 1)[0])
        self.record_date_edit.setPlaceholderText("YYYY-MM-DD")
        self.record_date_edit.setMaximumWidth(120)
        self.item_combo = QComboBox()
        self.item_combo.addItems(DEEPSEA_6F_CHEST_ITEMS)
        self.item_combo.setMinimumWidth(180)
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, 999999)
        self.quantity_spin.setValue(1)
        self.quantity_spin.setMaximumWidth(90)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("账号 / 备注")

        form.addWidget(QLabel("日期"), 0, 0)
        form.addWidget(self.record_date_edit, 0, 1)
        form.addWidget(QLabel("物品"), 0, 2)
        form.addWidget(self.item_combo, 0, 3)
        form.addWidget(QLabel("数量"), 0, 4)
        form.addWidget(self.quantity_spin, 0, 5)
        form.addWidget(QLabel("备注"), 0, 6)
        form.addWidget(self.note_edit, 0, 7, 1, 2)

        quick_row = QHBoxLayout()
        for value in (1, 2, 3, 4, 5):
            button = QPushButton(str(value))
            button.setMaximumWidth(44)
            button.clicked.connect(lambda checked=False, qty=value: self.quantity_spin.setValue(qty))
            quick_row.addWidget(button)
        self.add_button = QPushButton("录入")
        self.add_button.clicked.connect(self.add_record)
        self.save_button = QPushButton("保存修改")
        self.save_button.clicked.connect(self.save_selected_record)
        self.clear_button = QPushButton("清空选择")
        self.clear_button.clicked.connect(self.clear_selection)
        self.today_button = QPushButton("今天")
        self.today_button.clicked.connect(lambda: self.record_date_edit.setText(now_iso().split("T", 1)[0]))
        self.import_button = QPushButton("导入横向Excel")
        self.import_button.clicked.connect(self.import_matrix_excel)
        quick_row.addSpacing(12)
        quick_row.addWidget(self.add_button)
        quick_row.addWidget(self.save_button)
        quick_row.addWidget(self.clear_button)
        quick_row.addWidget(self.today_button)
        quick_row.addWidget(self.import_button)
        quick_row.addStretch(1)
        form.addLayout(quick_row, 1, 1, 1, 8)

        self.status_label = QLabel("准备录入。")
        self.status_label.setObjectName("Hint")
        form.addWidget(self.status_label, 2, 1, 1, 8)
        layout.addWidget(form_box)

        self.content_tabs = QTabWidget()

        record_page = QWidget()
        left_layout = QVBoxLayout(record_page)
        left_layout.setContentsMargins(0, 0, 0, 0)
        record_title = QLabel("录入记录")
        record_title.setObjectName("SectionLabel")
        left_layout.addWidget(record_title)
        self.record_table = QTableWidget(0, 5)
        self.record_table.setHorizontalHeaderLabels(["时间", "日期", "物品", "数量", "备注"])
        self.record_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.record_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.record_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.record_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.record_table.horizontalHeader().setStretchLastSection(True)
        self.record_table.itemSelectionChanged.connect(self.load_selected_record_into_form)
        left_layout.addWidget(self.record_table, 1)
        record_buttons = QHBoxLayout()
        delete_button = QPushButton("删除选中")
        delete_button.clicked.connect(self.delete_selected_records)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_all)
        record_buttons.addWidget(delete_button)
        record_buttons.addWidget(refresh_button)
        record_buttons.addStretch(1)
        left_layout.addLayout(record_buttons)
        self.content_tabs.addTab(record_page, "录入记录")

        stats_page = QWidget()
        right_layout = QVBoxLayout(stats_page)
        right_layout.setContentsMargins(0, 0, 0, 0)
        summary_row = QHBoxLayout()
        self.total_label = QLabel("累计箱子数：0")
        self.record_count_label = QLabel("记录数：0")
        self.top_item_label = QLabel("最多：-")
        for label in (self.total_label, self.record_count_label, self.top_item_label):
            label.setObjectName("ConnectionPill")
            summary_row.addWidget(label)
        summary_row.addStretch(1)
        right_layout.addLayout(summary_row)

        overview_title = QLabel("统计总览")
        overview_title.setObjectName("SectionLabel")
        right_layout.addWidget(overview_title)
        self.overview_table = QTableWidget(0, 4)
        self.overview_table.setHorizontalHeaderLabels(["物品", "累计数量", "出货率", "Bar"])
        self.overview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.overview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.overview_table.verticalHeader().setVisible(False)
        self.overview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.overview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.overview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.overview_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.overview_table.setAlternatingRowColors(True)
        right_layout.addWidget(self.overview_table, 1)
        self.content_tabs.addTab(stats_page, "统计总览")
        layout.addWidget(self.content_tabs, 1)

        self.refresh_all()

    def import_matrix_excel(self) -> None:
        start_dir = Path.home() / "Documents" / "sqsd"
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "导入横向Excel",
            str(start_dir if start_dir.exists() else Path.home()),
            "Excel 文件 (*.xlsx)",
        )
        if not path:
            return
        try:
            records = read_deepsea_matrix_excel_records(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        if not records:
            QMessageBox.information(self, "导入横向Excel", "没有找到可导入的数量。")
            return
        total_quantity = sum(int(record["quantity"]) for record in records)
        if (
            QMessageBox.question(
                self,
                "导入横向Excel",
                f"将新增 {len(records)} 条记录，合计 {total_quantity} 个箱子。\n继续导入吗？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        for record in records:
            self.storage.add_deepsea_chest_record(chest_key=DEEPSEA_6F_CHEST_KEY, **record)
        self.selected_record_id = None
        self.status_label.setText(f"已导入 {len(records)} 条记录，合计 {total_quantity} 个箱子。")
        self.refresh_all()
        self.content_tabs.setCurrentIndex(1)

    def current_form_payload(self) -> dict[str, Any]:
        item = self.item_combo.currentText().strip()
        if item not in DEEPSEA_6F_CHEST_ITEMS:
            raise ValueError("请选择深海6楼物品。")
        date_text = self.record_date_edit.text().strip()
        if not date_text:
            raise ValueError("日期不能为空。")
        return {
            "record_date": date_text,
            "item_name": item,
            "quantity": int(self.quantity_spin.value()),
            "note": self.note_edit.text().strip(),
        }

    def add_record(self) -> None:
        try:
            payload = self.current_form_payload()
            self.storage.add_deepsea_chest_record(chest_key=DEEPSEA_6F_CHEST_KEY, **payload)
        except Exception as exc:  # noqa: BLE001 - show validation/storage error in dialog
            QMessageBox.warning(self, "录入失败", str(exc))
            return
        self.status_label.setText(f"已录入：{payload['item_name']} x {payload['quantity']}")
        self.selected_record_id = None
        self.note_edit.clear()
        self.quantity_spin.setValue(1)
        self.refresh_all()
        self.content_tabs.setCurrentIndex(1)

    def save_selected_record(self) -> None:
        if not self.selected_record_id:
            QMessageBox.information(self, "保存修改", "请先在录入记录里选中一条。")
            return
        try:
            payload = self.current_form_payload()
            self.storage.update_deepsea_chest_record(
                self.selected_record_id,
                chest_key=DEEPSEA_6F_CHEST_KEY,
                **payload,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.status_label.setText(f"已保存修改：{payload['item_name']} x {payload['quantity']}")
        keep_id = self.selected_record_id
        self.refresh_all(select_id=keep_id)

    def clear_selection(self) -> None:
        self.selected_record_id = None
        self.record_table.clearSelection()
        self.record_date_edit.setText(now_iso().split("T", 1)[0])
        self.quantity_spin.setValue(1)
        self.note_edit.clear()
        self.status_label.setText("准备录入。")

    def selected_record_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.record_table.selectedIndexes()})
        ids: list[str] = []
        for row in rows:
            item = self.record_table.item(row, 0)
            record_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item else ""
            if record_id:
                ids.append(record_id)
        return ids

    def delete_selected_records(self) -> None:
        ids = self.selected_record_ids()
        if not ids:
            QMessageBox.information(self, "删除记录", "请先选择要删除的记录。")
            return
        if QMessageBox.question(self, "删除记录", f"确定删除选中的 {len(ids)} 条记录吗？") != QMessageBox.StandardButton.Yes:
            return
        deleted = self.storage.delete_deepsea_chest_records(ids)
        self.selected_record_id = None
        self.status_label.setText(f"已删除 {deleted} 条记录。")
        self.refresh_all()

    def load_selected_record_into_form(self) -> None:
        rows = sorted({index.row() for index in self.record_table.selectedIndexes()})
        if len(rows) != 1:
            self.selected_record_id = None
            return
        row_index = rows[0]
        if row_index < 0 or row_index >= len(self.records):
            self.selected_record_id = None
            return
        row = self.records[row_index]
        self.selected_record_id = str(row["id"])
        self.record_date_edit.setText(str(row["record_date"] or ""))
        item_index = self.item_combo.findText(str(row["item_name"] or ""))
        if item_index >= 0:
            self.item_combo.setCurrentIndex(item_index)
        self.quantity_spin.setValue(max(1, int(row["quantity"] or 1)))
        self.note_edit.setText(str(row["note"] or ""))
        self.status_label.setText("已载入选中记录，可修改后保存。")

    def refresh_all(self, select_id: str | None = None) -> None:
        self.records = self.storage.list_deepsea_chest_records(DEEPSEA_6F_CHEST_KEY)
        self.refresh_record_table(select_id=select_id)
        totals = self.storage.deepsea_chest_totals(DEEPSEA_6F_CHEST_KEY)
        stats = build_item_stats(totals, DEEPSEA_6F_CHEST_ITEMS)
        self.refresh_stats(stats)
        self.refresh_overview_table(stats)

    def refresh_record_table(self, select_id: str | None = None) -> None:
        self.record_table.blockSignals(True)
        self.record_table.setRowCount(len(self.records))
        selected_row = -1
        for row_index, row in enumerate(self.records):
            values = [
                str(row["created_at"] or "").replace("T", " ")[:19],
                row["record_date"],
                row["item_name"],
                row["quantity"],
                row["note"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["id"])
                if col == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.record_table.setItem(row_index, col, item)
            if select_id and row["id"] == select_id:
                selected_row = row_index
        self.record_table.blockSignals(False)
        if selected_row >= 0:
            self.record_table.selectRow(selected_row)

    def refresh_stats(self, stats: list[Any]) -> None:
        total = sum(item.quantity for item in stats)
        nonzero = [item for item in stats if item.quantity > 0]
        top = max(nonzero, key=lambda item: item.quantity, default=None)
        self.total_label.setText(f"累计箱子数：{total}")
        self.record_count_label.setText(f"记录数：{len(self.records)}")
        self.top_item_label.setText(f"最多：{top.item_name} {top.quantity}" if top else "最多：-")

    def refresh_overview_table(self, stats: list[Any]) -> None:
        rows = sorted(stats, key=lambda item: (-item.quantity, DEEPSEA_6F_CHEST_ITEMS.index(item.item_name)))
        max_qty = max((item.quantity for item in rows), default=0)
        total = sum(item.quantity for item in rows)
        self.overview_table.setRowCount(len(rows))
        for row_index, stat in enumerate(rows):
            name_item = QTableWidgetItem(stat.item_name)
            qty_item = QTableWidgetItem(str(stat.quantity))
            rate_item = QTableWidgetItem(f"{stat.rate:.2%}" if total else "")
            qty_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rate_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.overview_table.setItem(row_index, 0, name_item)
            self.overview_table.setItem(row_index, 1, qty_item)
            self.overview_table.setItem(row_index, 2, rate_item)
            progress = QProgressBar()
            progress.setRange(0, max(1, max_qty))
            progress.setValue(stat.quantity)
            progress.setTextVisible(True)
            progress.setFormat(str(stat.quantity))
            progress.setMinimumHeight(24)
            self.overview_table.setCellWidget(row_index, 3, progress)
            self.overview_table.setRowHeight(row_index, 28)


class MainWindow(QMainWindow):
    log_message = Signal(str)
    log_line_ready = Signal(str)
    result_message = Signal(str)
    ui_call_requested = Signal(object)
    loop_stats_changed = Signal(object)
    runtime_status_changed = Signal(object)
    runtime_error_alert = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.workspace = Path.cwd()
        self.storage = ProjectStorage(self.workspace)
        self.deepsea_action_library = DeepSeaActionLibrary(self.storage.deepsea_action_library_path())
        self.flow = create_flow("副本_001")
        self.current_flow_path: Path | None = None
        self._saved_flow_snapshot = self.flow_snapshot(self.flow)
        self.adb = AdbClient()
        self.worker: ScreenWorker | None = None
        self.runtime_worker: RuntimeWorker | None = None
        self.deepsea_recorder_worker: DeepSeaAutoRecorderWorker | None = None
        self.current_frame: QImage | None = None
        self.current_map = "map_001"
        self.current_game_coord: list[int] | None = None
        self.mumu_endpoint = "127.0.0.1:16384"
        self.expected_resolution = (1920, 1080)
        self.question_capture: dict[str, Any] | None = None
        self.ocr = OCREngine()
        self.coordinate_mapper = CoordinateMapper()
        self.path_planner = PathPlanner()
        self.approach_selector = ApproachPointSelector(self.path_planner)
        self.local_movement_controller = LocalMovementController(self.coordinate_mapper)
        self._stop_requested = False
        self.preview_enabled = True
        self.log_sink: Callable[[str], None] | None = None
        self.mini_runner: MiniRunnerWindow | None = None
        self.movement_library_dialog: MovementLibraryDialog | None = None
        self.deepsea_operation_review_dialog: DeepSeaOperationReviewDialog | None = None
        self.deepsea_chest_dialog: DeepSeaChestStatsDialog | None = None
        self.material_tool_dialog: Any | None = None
        self.material_web_server: Any | None = None
        self.material_web_url: str | None = None
        self._retired_screen_workers: list[ScreenWorker] = []
        self._movement_training_active = False
        self._movement_auto_cleanup_keys: set[str] = set()
        self._pending_log_lines: list[str] = []
        self._recent_log_lines: list[str] = []
        self._log_flush_scheduled = False
        self._bug_report_cooldowns: dict[str, float] = {}
        self._last_flow_failure: dict[str, Any] | None = None
        self._runtime_status: dict[str, Any] = {"state": "idle", "task": "", "detail": ""}
        self._runtime_error_active = False
        self._runtime_alert_reported = False
        self.log_message.connect(self._queue_log_line)
        self.result_message.connect(self._append_result_text)
        self.ui_call_requested.connect(self._run_ui_callable)

        self.setWindowTitle("StoneAge Script Studio")
        self._build_ui()
        self._apply_style()
        self.update_script_title()
        self.refresh_step_list()
        self.populate_properties(None)
        self.log("Studio 已启动。连接 ADB 后即可开始录制。")
        QTimer.singleShot(50, self.auto_load_first_flow_on_startup)
        QTimer.singleShot(500, self.auto_connect_mumu)

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        if not self.confirm_save_if_dirty("关闭软件", "当前副本流程有未保存更改。关闭前要保存吗？"):
            event.ignore()
            return
        if self.mini_runner is not None:
            self.mini_runner.close()
        if self.runtime_worker and self.runtime_worker.isRunning():
            self._stop_requested = True
            self.runtime_worker.wait(3000)
        if self.deepsea_recorder_worker and self.deepsea_recorder_worker.isRunning():
            self.deepsea_recorder_worker.stop()
            self.deepsea_recorder_worker.wait(2000)
        self.stop_screen_worker(wait_ms=1500)
        for worker in list(self._retired_screen_workers):
            worker.stop()
            worker.wait(1500)
        if self.material_web_server is not None:
            self.material_web_server.shutdown()
            self.material_web_server.server_close()
        super().closeEvent(event)

    def auto_load_first_flow_on_startup(self) -> None:
        if self.flow.get("steps"):
            return
        last_path = self.storage.load_last_flow_path()
        if last_path is not None:
            self.load_flow_path(last_path)
            return
        rows = self.storage.list_script_flows()
        if not rows:
            return
        path = rows[0].get("path")
        if not path:
            return
        self.load_flow_path(Path(path))

    def set_workspace_mode(self, mode: str) -> None:
        if not hasattr(self, "workspace_stack"):
            return
        already_runner = self.workspace_stack.currentWidget() is self.runner_panel
        already_editor = self.workspace_stack.currentWidget() is self.editor_workspace
        if mode == "runner":
            self.pause_preview()
            self.runner_mode_button.setChecked(True)
            if not already_runner:
                self.workspace_stack.setCurrentWidget(self.runner_panel)
            self.log_sink = self.runner_panel.append_log
            self.status_label.setText("运行模式：主界面不刷新截图")
            QTimer.singleShot(0, self.refresh_active_runner_panel)
            return

        if not already_editor:
            self.workspace_stack.setCurrentWidget(self.editor_workspace)
        self.edit_mode_button.setChecked(True)
        self.log_sink = None
        if self.runtime_worker and self.runtime_worker.isRunning():
            self.status_label.setText("运行中：制作界面暂停刷新")
        else:
            self.resume_preview()

    def refresh_active_runner_panel(self) -> None:
        if not hasattr(self, "workspace_stack") or self.workspace_stack.currentWidget() is not self.runner_panel:
            return
        self.runner_panel.activate()

    def current_runtime_status(self) -> dict[str, Any]:
        return dict(self._runtime_status)

    def emit_runtime_status(self, state: str, *, task: str = "", detail: str = "") -> None:
        self._runtime_status = {"state": state, "task": task, "detail": detail}
        self.runtime_status_changed.emit(dict(self._runtime_status))

    def open_mini_runner(self) -> None:
        if self.mini_runner is None:
            self.mini_runner = MiniRunnerWindow(self)
        if not hasattr(self, "workspace_stack") or self.workspace_stack.currentWidget() is not self.runner_panel:
            self.set_workspace_mode("runner")
        else:
            self.runner_panel.refresh_loop_stats()
        self.mini_runner.sync_from_controller()
        self.mini_runner.show()
        self.position_mini_runner()
        self.mini_runner.raise_()
        self.mini_runner.activateWindow()
        self.hide()

    def position_mini_runner(self) -> None:
        if self.mini_runner is None:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        size = self.mini_runner.frameGeometry()
        margin = 18
        self.mini_runner.move(
            available.right() - size.width() - margin,
            available.bottom() - size.height() - margin,
        )

    def restore_main_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _build_ui(self) -> None:
        connection_bar = QToolBar("连接")
        connection_bar.setMovable(False)
        self.addToolBar(connection_bar)

        mumu_action = QAction("连接MuMu", self)
        mumu_action.triggered.connect(self.connect_mumu)
        connection_bar.addAction(mumu_action)

        connect_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), "连接ADB", self)
        connect_action.triggered.connect(self.connect_adb)
        connection_bar.addAction(connect_action)

        self.connection_label = QLabel("MuMu 自动检测：未连接")
        self.connection_label.setObjectName("ConnectionPill")
        connection_bar.addWidget(self.connection_label)

        self.script_label = QLabel(f"脚本：{self.flow.get('script_name', '未命名')}")
        self.script_label.setObjectName("ConnectionPill")
        connection_bar.addWidget(self.script_label)

        connection_bar.addSeparator()
        mode_box = QFrame()
        mode_box.setObjectName("ModeSwitch")
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(2, 2, 2, 2)
        mode_layout.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.edit_mode_button = QToolButton()
        self.edit_mode_button.setText("制作模式")
        self.edit_mode_button.setCheckable(True)
        self.edit_mode_button.setChecked(True)
        self.runner_mode_button = QToolButton()
        self.runner_mode_button.setText("运行模式")
        self.runner_mode_button.setCheckable(True)
        for button in (self.edit_mode_button, self.runner_mode_button):
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            mode_layout.addWidget(button)
            self.mode_group.addButton(button)
        self.edit_mode_button.clicked.connect(lambda checked=False: self.set_workspace_mode("editor"))
        self.runner_mode_button.clicked.connect(lambda checked=False: self.set_workspace_mode("runner"))
        connection_bar.addWidget(mode_box)

        mini_action = QAction("迷你运行窗", self)
        mini_action.triggered.connect(self.open_mini_runner)
        connection_bar.addAction(mini_action)

        material_action = QAction("材料库", self)
        material_action.triggered.connect(self.open_material_tool)
        connection_bar.addAction(material_action)

        material_web_action = QAction("网页版材料库", self)
        material_web_action.triggered.connect(self.open_material_web)
        connection_bar.addAction(material_web_action)

        deepsea_chest_action = QAction("深海6楼宝箱统计", self)
        deepsea_chest_action.triggered.connect(self.open_deepsea_chest_stats)
        connection_bar.addAction(deepsea_chest_action)

        self.addToolBarBreak()
        script_bar = QToolBar("制作")
        script_bar.setMovable(False)
        self.addToolBar(script_bar)

        new_flow_action = QAction("新建脚本", self)
        new_flow_action.triggered.connect(self.new_flow_dialog)
        script_bar.addAction(new_flow_action)

        for text, callback in (
            ("保存", self.save_current_flow),
            ("加载", self.load_flow_dialog),
        ):
            action = QAction(text, self)
            action.triggered.connect(callback)
            script_bar.addAction(action)

        self.record_action = QAction("开始录制", self)
        self.record_action.setCheckable(True)
        self.record_action.toggled.connect(self.on_record_toggled)
        script_bar.addAction(self.record_action)

        add_button = QToolButton()
        add_button.setText("添加步骤")
        add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_menu = QMenu(add_button)
        for step_type in VISIBLE_STEP_TYPES:
            label = STEP_LABELS[step_type]
            action = add_menu.addAction(label)
            action.triggered.connect(lambda checked=False, value=step_type: self.add_blank_step(value))
        add_button.setMenu(add_menu)
        script_bar.addWidget(add_button)

        self.editor_workspace = QSplitter(Qt.Orientation.Vertical)
        top = QSplitter(Qt.Orientation.Horizontal)
        self.editor_workspace.addWidget(top)

        left = self._build_left_panel()
        center = self._build_center_panel()
        right = self._build_right_panel()
        top.addWidget(left)
        top.addWidget(center)
        top.addWidget(right)
        top.setSizes([560, 500, 380])

        bottom = self._build_bottom_panel()
        self.editor_workspace.addWidget(bottom)
        self.editor_workspace.setSizes([700, 210])

        self.runner_panel = RunnerPanel(self)
        self.workspace_stack = QStackedWidget()
        self.workspace_stack.addWidget(self.editor_workspace)
        self.workspace_stack.addWidget(self.runner_panel)
        self.setCentralWidget(self.workspace_stack)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(500)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 8, 12)
        layout.setSpacing(8)
        header = QLabel("流程步骤")
        header.setObjectName("PanelTitle")
        layout.addWidget(header)

        self.step_list = StepListWidget()
        self.step_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.step_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.step_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.step_list.currentItemChanged.connect(lambda current, previous: self.populate_properties(self.current_step()))
        self.step_list.model().rowsMoved.connect(lambda *args: QTimer.singleShot(0, self.on_step_rows_moved))
        layout.addWidget(self.step_list, 1)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 8, 12)
        top = QHBoxLayout()
        title = QLabel("脚本工作台")
        title.setObjectName("PanelTitle")
        self.status_label = QLabel("未连接")
        self.status_label.setObjectName("StatusPill")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.status_label)
        layout.addLayout(top)

        screen_box = QFrame()
        screen_box.setObjectName("StepCard")
        screen_layout = QVBoxLayout(screen_box)
        screen_layout.setContentsMargins(10, 10, 10, 10)
        screen_layout.setSpacing(8)
        screen_title = QLabel("当前画面 / 素材 / 预设")
        screen_title.setObjectName("StepName")
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self.immediate_name = QCheckBox("立即命名素材")
        self.immediate_name.setChecked(True)
        title_row.addWidget(screen_title)
        title_row.addStretch(1)
        title_row.addWidget(self.immediate_name)
        screen_layout.addLayout(title_row)

        def add_center_button_group(title_text: str, entries: list[tuple[str, Callable[[], None]]], columns: int = 3) -> None:
            group_label = QLabel(title_text)
            group_label.setObjectName("SectionLabel")
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            for entry_index, (button_text, callback) in enumerate(entries):
                button = QPushButton(button_text)
                button.clicked.connect(callback)
                button.setMinimumHeight(34)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                grid.addWidget(button, entry_index // columns, entry_index % columns)
            screen_layout.addWidget(group_label)
            screen_layout.addLayout(grid)

        add_center_button_group(
            "测试运行",
            [
                ("从头测试", self.run_all_steps),
                ("从选中测试", self.run_from_selected_step),
                ("停止", self.stop_run),
            ],
        )
        add_center_button_group(
            "步骤编辑",
            [
                ("删除", self.delete_selected_steps),
                ("复制", self.copy_selected_steps),
                ("启用/禁用", self.toggle_selected_steps),
                ("上移", lambda: self.move_selected_steps(-1)),
                ("下移", lambda: self.move_selected_steps(1)),
            ],
            columns=5,
        )
        add_center_button_group(
            "结构与判断",
            [
                ("循环选中", self.create_loop_from_selection),
                ("条件分支", lambda: self.add_blank_step("condition", insert_after=True)),
                ("移入循环", self.move_selected_steps_into_loop),
                ("移出循环", self.move_selected_steps_out_of_loop),
                ("合并等待", self.merge_wait_steps),
                ("保存预设", self.save_selected_loop_as_preset),
            ],
        )
        add_center_button_group(
            "快速插入",
            [
                ("当前画面操作", self.open_large_region_dialog),
                ("插入点击", lambda: self.add_blank_step("click", insert_after=True)),
                ("插入等待", lambda: self.add_blank_step("wait", insert_after=True)),
                ("插入验证码", self.add_verify_code_step_from_capture),
                ("插入预设", self.add_battle_speed_preset_step),
            ],
        )
        add_center_button_group(
            "识别与题库",
            [
                ("添加题库", self.start_question_capture),
                ("题库管理", self.open_question_bank),
                ("添加答题步骤", self.add_answer_step_from_bank),
                ("扫描文字数字", self.scan_screen_ocr_library),
                ("录入验证码数字", self.add_verify_digit_samples_from_capture),
                ("Pending Review", self.open_pending_review),
                ("Bug待修复", self.open_bug_reports),
            ],
        )
        add_center_button_group(
            "素材与预设",
            [
                ("开始自动记录", self.start_deepsea_auto_recording),
                ("停止自动记录", self.stop_deepsea_auto_recording),
                ("记录回放标注", self.open_deepsea_operation_review),
                ("深海动作截图", self.open_deepsea_action_capture),
                ("素材管理", self.open_asset_manager),
                ("历史步骤库", self.open_step_reuse),
                ("预设管理", self.open_battle_preset_manager),
            ],
        )
        layout.addWidget(screen_box)

        layout.addStretch(1)
        hint = QLabel("提示：制作模式负责流程编辑和素材准备；切到运行模式后会暂停主界面截图刷新，只保留脚本执行、循环运行和日志。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 12, 12)
        title = QLabel("步骤属性")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)

        self.property_area = QScrollArea()
        self.property_area.setWidgetResizable(True)
        self.property_widget = QWidget()
        self.property_layout = QFormLayout(self.property_widget)
        self.property_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.property_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.property_area.setWidget(self.property_widget)
        layout.addWidget(self.property_area, 1)
        return panel

    def _build_bottom_panel(self) -> QWidget:
        tabs = QTabWidget()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMaximumBlockCount(500)
        tabs.addTab(self.log_view, "日志 / 运行状态")
        tabs.addTab(self.result_view, "识别结果")
        return tabs

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f3f6f8;
                color: #19232d;
                font-size: 13px;
            }
            QToolBar {
                background: #ffffff;
                border-bottom: 1px solid #d8e0e7;
                spacing: 8px;
                padding: 6px;
            }
            QPushButton, QToolButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                padding: 7px 10px;
            }
            QPushButton:hover, QToolButton:hover {
                border-color: #79a7d8;
                background: #f9fbfd;
            }
            QToolButton:checked {
                background: #e1f0ff;
                border-color: #71a7dc;
                color: #0f5f9e;
                font-weight: 650;
            }
            #ModeSwitch {
                background: #edf3f8;
                border: 1px solid #d3dde7;
                border-radius: 7px;
            }
            #ModeSwitch QToolButton {
                border: none;
                border-radius: 5px;
                padding: 6px 12px;
                background: transparent;
            }
            #ModeSwitch QToolButton:checked {
                background: #ffffff;
                color: #0f5f9e;
            }
            QListWidget {
                background: transparent;
                border: none;
                outline: 0;
            }
            QListWidget::item {
                margin: 5px 0;
                border-radius: 8px;
            }
            QListWidget::item:selected {
                background: #dbeafe;
            }
            #StepCard {
                background: #ffffff;
                border: 1px solid #dbe3eb;
                border-radius: 8px;
            }
            #StepIndex {
                background: #eef5fb;
                color: #42627d;
                border-radius: 5px;
                padding: 4px;
                font-weight: 600;
            }
            #StepName {
                font-weight: 650;
                color: #17212b;
            }
            #StepKind {
                color: #637588;
                font-size: 12px;
            }
            #SectionLabel {
                color: #506779;
                font-size: 12px;
                font-weight: 650;
                padding-top: 4px;
            }
            #StepBadgeOn {
                background: #e2f7ed;
                color: #147a4f;
                border-radius: 4px;
                padding: 2px 5px;
                font-size: 11px;
            }
            #StepBadgeOff {
                background: #edf1f5;
                color: #8793a0;
                border-radius: 4px;
                padding: 2px 5px;
                font-size: 11px;
            }
            #PanelTitle {
                font-size: 16px;
                font-weight: 700;
                color: #12202d;
                padding-bottom: 6px;
            }
            #StatusPill {
                background: #edf1f5;
                border-radius: 6px;
                padding: 5px 9px;
                color: #52677a;
            }
            #ConnectionPill {
                background: #edf1f5;
                border-radius: 6px;
                padding: 7px 10px;
                color: #52677a;
                font-weight: 600;
            }
            #Hint {
                color: #5b6c7c;
                padding: 4px;
            }
            QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                padding: 4px;
            }
            QScrollArea {
                border: none;
            }
            """
        )

    def connect_adb(self) -> None:
        try:
            devices = self.adb.devices()
        except AdbError as exc:
            QMessageBox.warning(self, "ADB", str(exc))
            self.log(str(exc))
            return

        ready = [device for device in devices if device.status == "device"]
        if not ready:
            QMessageBox.warning(self, "ADB", "没有找到可用设备。请先确认 adb devices 能看到模拟器。")
            self.log("没有找到可用 ADB 设备。")
            return

        if len(ready) == 1:
            serial = ready[0].serial
        else:
            serial, ok = QInputDialog.getItem(
                self,
                "选择设备",
                "ADB 设备",
                [device.serial for device in ready],
                editable=False,
            )
            if not ok:
                return
        self.adb = AdbClient(serial=serial)
        self.status_label.setText(f"已连接 {serial}")
        self.set_connection_state(serial, "已连接")
        self.log(f"已连接 ADB 设备：{serial}")
        self.capture_current_screen(save=False)

    def set_connection_state(self, endpoint: str, state: str) -> None:
        self.connection_label.setText(f"MuMu {endpoint}：{state}")

    def auto_connect_mumu(self) -> None:
        self.connect_detected_mumu(interactive=False)

    def connect_mumu(self) -> None:
        self.connect_detected_mumu(interactive=True)

    def connect_detected_mumu(self, interactive: bool) -> bool:
        if self.runtime_worker and self.runtime_worker.isRunning():
            self.log("脚本/训练正在运行，先停止后再切换 MuMu 设备。")
            return False
        self.connection_label.setText("MuMu：扫描端口中")
        try:
            endpoints = self.adb.discover_mumu_endpoints(self.mumu_endpoint)
        except AdbError as exc:
            self.set_connection_state(self.mumu_endpoint, "扫描失败")
            self.log(str(exc))
            if interactive:
                QMessageBox.warning(self, "MuMu ADB", str(exc))
            return False

        if not endpoints:
            self.set_connection_state(self.mumu_endpoint, "未发现")
            self.log("未检测到 MuMu ADB 端口。请确认模拟器已启动并打开 ADB 调试。")
            if not interactive:
                return False
            endpoint, ok = QInputDialog.getText(
                self,
                "连接 MuMu",
                "未自动发现端口，请手动输入 ADB 地址",
                text=self.mumu_endpoint,
            )
            if not ok:
                return False
            endpoint = normalize_adb_endpoint(endpoint or self.mumu_endpoint)
            return self.connect_mumu_endpoint(endpoint, interactive=True)

        if len(endpoints) == 1:
            endpoint = endpoints[0]
            self.log(f"检测到 1 个 MuMu ADB 端口：{endpoint}，自动连接。")
            return self.connect_mumu_endpoint(endpoint, interactive=interactive)

        current_index = max(0, endpoints.index(self.mumu_endpoint)) if self.mumu_endpoint in endpoints else 0
        endpoint, ok = QInputDialog.getItem(
            self,
            "选择 MuMu",
            "检测到多个 MuMu 模拟器，请选择脚本要控制的那个",
            endpoints,
            current_index,
            False,
        )
        if not ok:
            self.set_connection_state(self.mumu_endpoint, "未选择")
            self.log(f"检测到多个 MuMu ADB 端口：{', '.join(endpoints)}，已取消选择。")
            return False
        self.log(f"检测到多个 MuMu ADB 端口：{', '.join(endpoints)}，选择 {endpoint}。")
        return self.connect_mumu_endpoint(normalize_adb_endpoint(endpoint), interactive=True)

    def connect_mumu_endpoint(self, endpoint: str, interactive: bool) -> bool:
        self.mumu_endpoint = endpoint
        self.set_connection_state(endpoint, "连接中")
        try:
            output = self.adb.connect(endpoint)
            self.log(output or f"已请求连接 MuMu：{endpoint}")
        except AdbError as exc:
            self.set_connection_state(endpoint, "连接失败")
            if interactive:
                QMessageBox.warning(self, "MuMu ADB", str(exc))
            self.log(str(exc))
            return False
        try:
            devices = {device.serial: device.status for device in self.adb.devices()}
        except AdbError:
            devices = {}
        status = devices.get(endpoint)
        if status and status != "device":
            self.set_connection_state(endpoint, status)
            message = f"MuMu {endpoint} 已连接但状态是 {status}，请确认模拟器授权/启动完成。"
            self.log(message)
            if interactive:
                QMessageBox.warning(self, "MuMu ADB", message)
            return False
        if self.worker:
            self.stop_screen_worker(wait_ms=1500)
        self.adb = AdbClient(serial=endpoint)
        self.status_label.setText(f"MuMu {endpoint}")
        self.set_connection_state(endpoint, "已连接")
        self.log(f"MuMu 已作为默认设备：{endpoint}，期望分辨率 {self.expected_resolution[0]}x{self.expected_resolution[1]}")
        self.capture_current_screen(save=False)
        return True

    def start_screen_worker(self) -> None:
        self.preview_enabled = True
        self.stop_screen_worker(wait_ms=1500)
        self.worker = ScreenWorker(self.adb)
        self.worker.frame_ready.connect(self.on_frame_ready)
        self.worker.status.connect(self.log)
        self.worker.start()

    def stop_screen_worker(self, wait_ms: int = 0) -> None:
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        self._retired_screen_workers.append(worker)

        def forget_worker() -> None:
            try:
                self._retired_screen_workers.remove(worker)
            except ValueError:
                pass
            worker.deleteLater()

        worker.finished.connect(forget_worker)
        worker.stop()
        if wait_ms > 0 and worker.wait(wait_ms):
            forget_worker()

    def pause_preview(self) -> None:
        self.preview_enabled = False
        self.stop_screen_worker(wait_ms=0)
        self.status_label.setText("运行模式：主界面不刷新截图")

    def resume_preview(self) -> None:
        if self.preview_enabled:
            return
        self.preview_enabled = True
        self.status_label.setText(f"MuMu {self.mumu_endpoint}")

    def on_frame_ready(self, image: QImage) -> None:
        self.current_frame = image.copy()
        self.update_frame_status(image)

    def update_frame_status(self, image: QImage) -> None:
        actual = (image.width(), image.height())
        if actual != self.expected_resolution:
            self.status_label.setText(f"画面 {actual[0]}x{actual[1]}")
        else:
            self.status_label.setText(f"MuMu {self.mumu_endpoint} · {actual[0]}x{actual[1]}")

    def on_zoom_changed(self, value: str) -> None:
        if not hasattr(self, "game_scroll_area") or not hasattr(self, "game_view"):
            return
        if value == "适应":
            self.game_scroll_area.setWidgetResizable(True)
            self.game_view.setMinimumSize(420, 240)
            self.game_view.set_zoom(0.0)
            return
        zoom = int(value.rstrip("%")) / 100.0
        self.game_scroll_area.setWidgetResizable(False)
        self.game_view.set_zoom(zoom)
        self.game_view.resize(self.game_view.sizeHint())

    def game_coord_region(self) -> list[int]:
        settings = self.flow.setdefault("settings", {})
        region = settings.get("game_coord_region")
        if not region or [int(value) for value in region] == OLD_DEFAULT_GAME_COORD_REGION:
            region = list(DEFAULT_GAME_COORD_REGION)
            settings["game_coord_region"] = region
        return [int(value) for value in region]

    def update_coord_region_label(self) -> None:
        if hasattr(self, "coord_region_label"):
            self.coord_region_label.setText(f"固定坐标区域：{self.game_coord_region()}")

    def on_record_toggled(self, checked: bool) -> None:
        self.record_action.setText("停止录制" if checked else "开始录制")
        self.log("录制模式已开启。" if checked else "录制模式已关闭。")

    def read_current_coord_to_quick_panel(self) -> None:
        coord = self.read_current_game_coord("quick_move")
        if not coord:
            QMessageBox.information(self, "读取游戏坐标", "当前固定坐标区域没有读到坐标。请用“重新框选坐标区域”校准一次。")
            return
        self.quick_coord_label.setText(f"当前坐标：{coord[0]}, {coord[1]}")
        self.quick_move_x.setValue(int(coord[0]))
        self.quick_move_y.setValue(int(coord[1]))

    def add_quick_move_step(self) -> None:
        self.sync_steps_from_list()
        x = int(self.quick_move_x.value())
        y = int(self.quick_move_y.value())
        step = create_step("move_to_game_coord", f"移动到_{x}_{y}")
        step["input"]["target_coord"] = [x, y]
        step["input"]["tolerance"] = 0
        step["input"]["arrival_mode"] = "exact"
        step["input"]["exact_target"] = True
        step["input"]["use_approach_points"] = False
        step["timeout"] = 45.0
        placement = self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        self.log(f"已插入移动坐标{where}：{step['name']}")

    def add_and_test_quick_move_step(self) -> None:
        self.add_quick_move_step()
        step = self.current_step()
        if step and step.get("type") == "move_to_game_coord":
            self.run_step_for_test(step)

    def add_route_node_preset_step(self) -> None:
        self.sync_steps_from_list()
        script_name = str(self.flow.get("script_name") or "副本_001")
        route_plan = self.storage.load_script_route_plan(script_name)
        nodes = [node for node in route_plan.get("nodes") or [] if isinstance(node.get("coord"), list) and len(node.get("coord")) >= 2]
        if not nodes:
            QMessageBox.information(self, "插入线路点预设", "当前副本还没有 route_plan.json 线路点。")
            return
        choices: list[str] = []
        choice_to_node: dict[str, dict[str, Any]] = {}
        for node in nodes:
            node_id = str(node.get("id") or "")
            label = str(node.get("label") or node_id or "线路点")
            coord = [int(node["coord"][0]), int(node["coord"][1])]
            arrival = str(node.get("arrival") or "near")
            if arrival == "none":
                mode = "无需移动"
            elif arrival == "exact":
                mode = "精准"
            else:
                mode = f"视野/容差{int(node.get('tolerance', 2) or 0)}"
            text = f"{node_id or label} | {label} | {coord[0]},{coord[1]} | {mode}"
            choices.append(text)
            choice_to_node[text] = node
        choice, ok = QInputDialog.getItem(self, "插入线路点预设", "选择要插入的路线点", choices, 0, False)
        if not ok or not choice:
            return
        step = self.create_route_node_step(choice_to_node[choice], prefix="路线点", max_seconds=60)
        if step is None:
            QMessageBox.information(self, "插入线路点预设", "这个线路点配置为无需移动，不能插入移动步骤。")
            return
        placement = self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        self.log(f"已插入路线点预设{where}：{step['name']}")

    def default_battle_end_template_path(self) -> str | None:
        path = self.storage.abs(DEFAULT_BATTLE_END_TEMPLATE)
        return DEFAULT_BATTLE_END_TEMPLATE if path and path.exists() else None

    def default_switch_button_template_path(self) -> str | None:
        path = self.storage.abs(DEFAULT_SWITCH_BUTTON_TEMPLATE)
        return DEFAULT_SWITCH_BUTTON_TEMPLATE if path and path.exists() else None

    def default_switch_close_template_path(self) -> str | None:
        path = self.storage.abs(DEFAULT_SWITCH_CLOSE_TEMPLATE)
        return DEFAULT_SWITCH_CLOSE_TEMPLATE if path and path.exists() else None

    def configure_battle_loop_exit(self, loop_step: dict[str, Any], template_path: str | None = None) -> None:
        data = loop_step.setdefault("input", {})
        data["loop_mode"] = "image_found"
        data["exit_check_timing"] = "after_iteration"
        data["break_on_failure"] = False
        data["fail_when_max_reached"] = False
        condition = data.setdefault("exit_condition", {})
        condition["type"] = "image_found"
        if template_path and not condition.get("template_path"):
            condition["template_path"] = template_path
        condition.setdefault("asset_id", None)
        condition.setdefault("threshold", 0.85)

    def create_battle_speed_loop_step(self, rounds: int, name: str = "战斗预设_双角色切换") -> dict[str, Any]:
        loop_step = create_step("loop", name)
        loop_step["timeout"] = max(60.0, float(rounds) * 3.0)
        loop_step["input"].update(
            {
                "times": int(rounds),
            }
        )
        self.configure_battle_loop_exit(loop_step, self.default_battle_end_template_path())
        children: list[dict[str, Any]] = []
        for child_name in (
            "战斗_点切换_打开2号选择",
            "战斗_选择2号角色",
            "战斗_点切换_打开1号选择",
            "战斗_选择1号角色",
        ):
            child = create_step("click", child_name)
            child["enabled"] = False
            child["input"]["screen_coord"] = [0, 0]
            child["input"]["wait_after"] = 0.4
            children.append(child)
        loop_step["children"] = children
        self.refresh_loop_body_metadata(loop_step)
        return loop_step

    def create_click_target_step(
        self,
        name: str,
        template_path: str | None,
        *,
        threshold: float = 0.85,
        bbox: list[int] | None = None,
        wait_after_found: float = 0.5,
        instant_check: bool = False,
        wait_until_found: bool = True,
        action_on_found: str = "next",
        action_on_missing: str = "fail",
        timeout: float = 10.0,
        search_bbox: list[int] | None = None,
    ) -> dict[str, Any]:
        step = create_step("click_target", name)
        step["timeout"] = float(timeout)
        step["input"].update(
            {
                "template_path": template_path,
                "bbox": bbox,
                "threshold": float(threshold),
                "wait_after_found": float(wait_after_found),
                "instant_check": bool(instant_check),
                "wait_until_found": bool(wait_until_found),
                "action_on_found": action_on_found,
                "action_on_missing": action_on_missing,
                "search_bbox": search_bbox,
            }
        )
        return step

    def create_image_check_step(
        self,
        name: str,
        template_path: str | None,
        *,
        threshold: float = 0.85,
        wait_after_found: float = 0.0,
        instant_check: bool = False,
        wait_until_found: bool = True,
        action_on_found: str = "next",
        action_on_missing: str = "fail",
        timeout: float = 10.0,
        search_bbox: list[int] | None = None,
    ) -> dict[str, Any]:
        step = create_step("image_check", name)
        step["timeout"] = float(timeout)
        step["input"].update(
            {
                "template_path": template_path,
                "threshold": float(threshold),
                "wait_after_found": float(wait_after_found),
                "instant_check": bool(instant_check),
                "wait_until_found": bool(wait_until_found),
                "action_on_found": action_on_found,
                "action_on_missing": action_on_missing,
                "search_bbox": search_bbox,
            }
        )
        return step

    def create_battle_role1_cleanup_steps(self) -> list[dict[str, Any]]:
        switch_button = self.default_switch_button_template_path()
        switch_close = self.default_switch_close_template_path()
        switch_close_search_bbox = [1260, 40, 360, 260]
        open_switch = self.create_click_target_step(
            "战斗收尾_打开切换界面",
            switch_button,
            bbox=[1612, 1000, 79, 18],
            threshold=0.85,
            wait_after_found=1.0,
            instant_check=False,
            wait_until_found=True,
            action_on_missing="skip",
            timeout=3.0,
        )
        confirm_open = self.create_image_check_step(
            "战斗收尾_确认切换界面",
            switch_close,
            threshold=0.50,
            wait_after_found=0.0,
            timeout=4.0,
            search_bbox=switch_close_search_bbox,
        )
        select_role1 = create_step("click", "战斗收尾_切回1号角色")
        select_role1["input"].update(
            {
                "screen_coord": [569, 351],
                "click_count": 1,
                "wait_before": 0.0,
                "wait_after": 2.0,
                "confirm_success": False,
                "verify_game_coord": False,
            }
        )
        close_switch = self.create_click_target_step(
            "战斗收尾_关闭切换界面",
            switch_close,
            threshold=0.50,
            wait_after_found=1.0,
            instant_check=True,
            wait_until_found=False,
            action_on_missing="skip",
            timeout=2.0,
            search_bbox=switch_close_search_bbox,
        )
        close_switch["input"]["click_mode"] = "template_point"
        close_switch["input"]["click_offset"] = [64, 62]
        return [open_switch, confirm_open, select_role1, close_switch]

    def create_battle_role1_cleanup_loop(self) -> dict[str, Any]:
        loop_step = create_step("loop", "战斗收尾_确保1号并关闭界面")
        loop_step["timeout"] = 30.0
        loop_step["input"].update(
            {
                "times": 3,
                "loop_mode": "image_missing",
                "exit_check_timing": "after_iteration",
                "break_on_failure": True,
                "fail_when_max_reached": True,
                "exit_condition": {
                    "type": "image_missing",
                    "template_path": self.default_switch_close_template_path(),
                    "asset_id": None,
                    "threshold": 0.50,
                    "search_bbox": [1260, 40, 360, 260],
                },
            }
        )
        loop_step["children"] = self.create_battle_role1_cleanup_steps()
        self.refresh_loop_body_metadata(loop_step)
        return loop_step

    def is_battle_loop_step(self, step: dict[str, Any]) -> bool:
        if step.get("type") != "loop":
            return False
        name = str(step.get("name") or "")
        if name.startswith("战斗收尾"):
            return False
        condition = step.get("input", {}).get("exit_condition") or {}
        template = str(condition.get("template_path") or "")
        return "战斗" in name or template == DEFAULT_BATTLE_END_TEMPLATE

    def append_battle_cleanup_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return steps

    def refresh_step_identity_preserve_name(self, step: dict[str, Any]) -> None:
        step["id"] = short_id("step")
        now_text = time.strftime("%Y-%m-%dT%H:%M:%S")
        step["created_at"] = now_text
        step["updated_at"] = now_text
        for child in step.get("children") or []:
            self.refresh_step_identity_preserve_name(child)
        if step.get("type") == "loop":
            self.refresh_loop_body_metadata(step)

    def insert_default_battle_loop_for_editing(self) -> bool:
        self.sync_steps_from_list()
        rounds, ok = QInputDialog.getInt(self, "新建战斗切换循环", "战斗动作最多循环几轮？", 30, 1, 9999, 1)
        if not ok:
            return False
        loop_step = self.create_battle_speed_loop_step(int(rounds))
        placement = self.place_step_in_context(loop_step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=loop_step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        self.log(
            f"已新建战斗切换循环{where}：{loop_step['name']}。"
            "请校准并启用 4 个点击子步骤，再在循环属性里框选“战斗结束”判断图片；"
            "运行时会循环切 2 号、切 1 号，直到识别到战斗结束。"
        )
        return True

    def preset_kind_for_steps(self, steps: list[dict[str, Any]]) -> str:
        texts: list[str] = []
        for step in steps:
            texts.append(str(step.get("name") or ""))
            texts.append(str(step.get("type") or ""))
            for child in step.get("children") or []:
                texts.append(str(child.get("name") or ""))
        return "battle" if any("战斗" in text or "battle" in text.lower() for text in texts) else "general"

    def save_steps_as_preset(
        self,
        steps: list[dict[str, Any]],
        *,
        default_name: str | None = None,
        default_repeat_count: int = 1,
    ) -> bool:
        if not steps:
            QMessageBox.information(self, "保存预设", "没有可保存的步骤。")
            return False
        if not default_name:
            if len(steps) == 1:
                default_name = str(steps[0].get("name") or "流程预设")
            else:
                first_name = str(steps[0].get("name") or "步骤")
                default_name = f"{first_name}_等{len(steps)}步"
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称", text=default_name)
        if not ok or not name.strip():
            return False
        repeat_count, ok = QInputDialog.getInt(
            self,
            "保存预设",
            "默认执行几次？如果保存的是循环，通常填 1。",
            max(1, int(default_repeat_count or 1)),
            1,
            9999,
            1,
        )
        if not ok:
            return False

        script_name = str(self.flow.get("script_name") or "副本_001")
        scope_choices = [
            "通用预设（所有副本可用）",
            f"当前副本预设（仅 {script_name}）",
        ]
        scope_choice, ok = QInputDialog.getItem(self, "保存预设", "保存范围", scope_choices, 0, False)
        if not ok or not scope_choice:
            return False
        preset_scope = "global" if scope_choice.startswith("通用") else "script"
        payload = self.storage.load_project_presets() if preset_scope == "global" else self.storage.load_script_presets(script_name)
        existing_id = None
        for preset in payload.get("presets") or []:
            if str(preset.get("name") or "") == name.strip():
                existing_id = str(preset.get("id") or "")
                break
        kind = self.preset_kind_for_steps(steps)
        if kind == "battle" and len(steps) == 1 and steps[0].get("type") == "loop":
            self.configure_battle_loop_exit(steps[0], self.default_battle_end_template_path())
        step_names = [str(step.get("name") or step.get("type") or "步骤") for step in steps]
        preset: dict[str, Any] = {
            "name": name.strip(),
            "kind": kind,
            "repeat_count": int(repeat_count),
            "steps": steps,
            "note": " -> ".join(step_names[:5]),
        }
        if existing_id:
            preset["id"] = existing_id
        if preset_scope == "global":
            saved = self.storage.upsert_project_preset(preset)
            scope_text = "通用"
        else:
            saved = self.storage.upsert_script_preset(script_name, preset)
            scope_text = "当前副本"
        self.log(f"已保存{scope_text}预设：{saved.get('name')}，默认执行 {int(repeat_count)} 次。之后可用“插预设”复用。")
        return True

    def selected_loop_for_preset(self) -> dict[str, Any] | None:
        self.sync_steps_from_list()
        locations = self.selected_locations_or_current(collapse_descendants=True)
        if not locations:
            return None
        loop_candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for step_id, _container, _index, parent in locations:
            step = self.step_by_id(step_id)
            loop_step = step if step and step.get("type") == "loop" else parent
            loop_id = str(loop_step.get("id") if loop_step else "")
            if loop_step and loop_step.get("type") == "loop" and loop_id not in seen:
                loop_candidates.append(loop_step)
                seen.add(loop_id)
        if len(loop_candidates) == 1:
            return loop_candidates[0]
        return None

    def save_selected_loop_as_preset(self) -> bool:
        self.sync_steps_from_list()
        locations = self.selected_locations_or_current(collapse_descendants=True)
        if not locations:
            QMessageBox.information(self, "保存预设", "请先选中一个循环，或选中同一层级里连续的步骤。")
            return False

        if len(locations) == 1:
            step_id, _container, _index, parent = locations[0]
            step = self.step_by_id(step_id)
            if step and step.get("type") == "loop":
                return self.save_steps_as_preset(
                    [copy.deepcopy(step)],
                    default_name=str(step.get("name") or "循环预设"),
                    default_repeat_count=1,
                )

        container = locations[0][1]
        parent = locations[0][3]
        if any(id(item[1]) != id(container) or id(item[3]) != id(parent) for item in locations):
            QMessageBox.information(self, "保存预设", "请只选中同一层级里的连续步骤。循环外和循环内不能混在一起保存。")
            return False
        indices = [item[2] for item in locations]
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.information(self, "保存预设", "请选中连续步骤后再保存预设。")
            return False
        start, end = indices[0], indices[-1]
        steps = copy.deepcopy(container[start : end + 1])
        return self.save_steps_as_preset(steps)

    def save_current_loop_as_battle_preset(self) -> bool:
        self.sync_steps_from_list()
        locations = self.selected_locations_or_current(collapse_descendants=True)
        if not locations:
            QMessageBox.information(self, "保存预设", "请先选中要保存成预设的连续步骤。")
            return False

        container = locations[0][1]
        parent = locations[0][3]
        if any(id(item[1]) != id(container) or id(item[3]) != id(parent) for item in locations):
            QMessageBox.information(self, "保存预设", "请选中同一层级里的步骤。循环外和循环内不能一起保存成一个预设。")
            return False
        indices = [item[2] for item in locations]
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.information(self, "保存预设", "请选中连续步骤后再保存预设。")
            return False

        start, end = indices[0], indices[-1]
        steps = copy.deepcopy(container[start : end + 1])
        return self.save_steps_as_preset(steps)

    def insert_battle_preset(self, preset: dict[str, Any]) -> None:
        self.sync_steps_from_list()
        normalized = self.storage.normalize_script_preset(preset)
        steps = [copy.deepcopy(step) for step in normalized.get("steps") or [] if isinstance(step, dict)]
        if not steps:
            QMessageBox.warning(self, "插入预设", "这个预设缺少可插入的步骤。")
            return
        preset_name = str(normalized.get("name") or "流程预设")
        default_count = max(1, int(normalized.get("repeat_count") or 1))
        repeat_count, ok = QInputDialog.getInt(
            self,
            "插入预设",
            "执行几次？如果预设本身已经是循环，通常填 1。",
            default_count,
            1,
            9999,
            1,
        )
        if not ok:
            return

        for step in steps:
            self.refresh_step_identity_preserve_name(step)
        if int(repeat_count) > 1:
            loop_step = create_step("loop", f"{preset_name}_循环")
            loop_step["input"].update(
                {
                    "times": int(repeat_count),
                    "loop_mode": "fixed_count",
                    "break_on_failure": True,
                    "fail_when_max_reached": False,
                }
            )
            loop_step["children"] = steps
            self.refresh_loop_body_metadata(loop_step)
            placement = self.place_step_in_context(loop_step, bool(self.current_step()))
            selected_id = loop_step["id"]
            inserted_count = self.count_steps([loop_step])
        else:
            placement = self.place_steps_in_context(steps, bool(self.current_step()))
            selected_id = steps[0]["id"]
            inserted_count = sum(self.count_steps([step]) for step in steps)
        self.refresh_step_list(select_step_id=selected_id)
        where = "循环子步骤" if placement == "loop" else "步骤"
        repeat_text = f"，执行 {int(repeat_count)} 次" if int(repeat_count) > 1 else ""
        self.log(f"已插入预设{where}：{preset_name}{repeat_text}，共 {inserted_count} 个步骤。")

    def open_battle_preset_manager(self) -> None:
        script_name = str(self.flow.get("script_name") or "副本_001")
        PresetManagerDialog(
            self.storage,
            script_name,
            self,
            insert_callback=self.insert_battle_preset,
            save_current_callback=self.save_current_loop_as_battle_preset,
            create_default_callback=self.insert_default_battle_loop_for_editing,
        ).exec()

    def add_battle_speed_preset_step(self) -> None:
        self.sync_steps_from_list()
        script_name = str(self.flow.get("script_name") or "副本_001")
        presets = [
            preset
            for preset in self.storage.load_combined_script_presets(script_name).get("presets") or []
            if isinstance(preset, dict) and preset.get("steps")
        ]
        if not presets:
            reply = QMessageBox.question(
                self,
                "插入预设",
                "还没有保存好的通用预设或当前副本预设。\n要打开预设管理，先保存选中步骤或新建战斗切换循环吗？",
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.open_battle_preset_manager()
            return
        if len(presets) == 1:
            self.insert_battle_preset(presets[0])
            return
        choices: list[str] = []
        choice_to_preset: dict[str, dict[str, Any]] = {}
        for preset in presets:
            steps = [step for step in preset.get("steps") or [] if isinstance(step, dict)]
            name = str(preset.get("name") or "流程预设")
            kind = "战斗" if preset.get("kind") == "battle" else "普通"
            scope = "通用" if str(preset.get("_preset_scope") or preset.get("scope") or "") == "global" else "当前副本"
            repeat_count = max(1, int(preset.get("repeat_count") or 1))
            step_count = sum(self.count_steps([step]) for step in steps)
            text = f"[{scope}] {name} | {kind} | 默认{repeat_count}次 | {step_count}步"
            choices.append(text)
            choice_to_preset[text] = preset
        choice, ok = QInputDialog.getItem(self, "插入预设", "选择预设", choices, 0, False)
        if ok and choice:
            self.insert_battle_preset(choice_to_preset[choice])

    def add_quick_coord_to_movement_db(self) -> None:
        x = int(self.quick_move_x.value())
        y = int(self.quick_move_y.value())
        script_name = str(self.flow.get("script_name") or "副本_001")
        added = self.storage.add_script_movement_coord(
            script_name,
            map_id=self.current_map,
            coord=[x, y],
            label=f"{x},{y}",
            tolerance=0,
            exact=True,
        )
        action = "已加入" if added else "已更新"
        count = len(self.storage.load_script_movement_coords(script_name).get("targets") or [])
        self.log(f"{action}脚本移动库：{x}, {y}。当前移动库 {count} 个坐标。")

    def open_movement_library_manager(self) -> None:
        script_name = str(self.flow.get("script_name") or "副本_001")
        if self.movement_library_dialog is None:
            self.movement_library_dialog = MovementLibraryDialog(
                self.storage,
                script_name,
                self.current_map,
                parent=self,
                practice_callback=self.practice_script_movement_db,
                route_practice_callback=self.practice_route_plan,
                map_explore_callback=self.explore_current_map,
                map_report_callback=self.show_walkability_report,
                report_callback=self.show_training_report,
                cleanup_callback=self.cleanup_training_samples,
            )
        else:
            self.movement_library_dialog.script_name = script_name
            self.movement_library_dialog.map_id = self.current_map
            self.movement_library_dialog.practice_callback = self.practice_script_movement_db
            self.movement_library_dialog.route_practice_callback = self.practice_route_plan
            self.movement_library_dialog.map_explore_callback = self.explore_current_map
            self.movement_library_dialog.map_report_callback = self.show_walkability_report
            self.movement_library_dialog.report_callback = self.show_training_report
            self.movement_library_dialog.cleanup_callback = self.cleanup_training_samples
            self.movement_library_dialog.setWindowTitle(f"移动库管理 - {script_name}")
            self.movement_library_dialog.load()
        self.movement_library_dialog.show()
        self.movement_library_dialog.raise_()
        self.movement_library_dialog.activateWindow()

    def create_movement_practice_step(
        self,
        coord: list[int],
        *,
        name: str,
        exact: bool,
        tolerance: int,
        max_seconds: float,
    ) -> dict[str, Any]:
        step = create_step("move_to_game_coord", name)
        step["input"]["target_coord"] = [int(coord[0]), int(coord[1])]
        step["input"]["tolerance"] = int(tolerance)
        step["input"]["exact_target"] = bool(exact)
        step["input"]["arrival_mode"] = "exact" if exact else "near"
        step["input"]["use_approach_points"] = not bool(exact)
        step["input"]["max_seconds"] = float(max_seconds)
        step["input"]["target_backoff_enabled"] = bool(exact)
        return step

    def route_plan_nodes_by_id(self, route_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
        nodes: dict[str, dict[str, Any]] = {}
        for node in route_plan.get("nodes") or []:
            node_id = str(node.get("id") or "")
            coord = node.get("coord")
            if not node_id or not isinstance(coord, list) or len(coord) < 2:
                continue
            nodes[node_id] = node
        return nodes

    def create_route_node_step(self, node: dict[str, Any], *, prefix: str, max_seconds: float) -> dict[str, Any] | None:
        arrival = str(node.get("arrival") or "near")
        if arrival == "none":
            return None
        coord = [int(node["coord"][0]), int(node["coord"][1])]
        exact = arrival == "exact"
        role = str(node.get("role") or "")
        tolerance = int(node.get("tolerance", 0 if exact else 2) or 0)
        step = self.create_movement_practice_step(
            coord,
            name=f"{prefix}_{node.get('label') or node.get('id')}",
            exact=exact,
            tolerance=tolerance,
            max_seconds=max_seconds,
        )
        step_input = step.setdefault("input", {})
        step_input["route_node_id"] = str(node.get("id") or "")
        step_input["route_node_label"] = str(node.get("label") or node.get("id") or "")
        step_input["route_node_role"] = role
        if isinstance(node.get("target_hint"), list) and len(node.get("target_hint") or []) >= 2:
            step_input["target_hint_coord"] = [int(node["target_hint"][0]), int(node["target_hint"][1])]
        if not exact and role in {"view_target", "view_npc"}:
            step_input["require_next_image_visible"] = True
        return step

    def movement_target_id_for_coord(self, script_name: str, coord: list[int]) -> str | None:
        payload = self.storage.load_script_movement_coords(script_name)
        target = [int(coord[0]), int(coord[1])]
        for item in payload.get("targets") or []:
            if item.get("map_id", self.current_map) == self.current_map and item.get("coord") == target:
                return str(item.get("id") or "")
        return None

    def show_training_report(self, log_func: Callable[[str], None] | None = None) -> None:
        emit = log_func or self.log
        script_name = str(self.flow.get("script_name") or "副本_001")
        health = self.storage.movement_sample_health(map_id=self.current_map, script_name=script_name)
        route_plan = self.storage.load_script_route_plan(script_name)
        route_training = self.storage.load_script_route_training(script_name)
        nodes = self.route_plan_nodes_by_id(route_plan)
        edges = route_plan.get("training_edges") or []
        route_stats = route_training.get("edges") or {}
        emit(
            "训练报告："
            f"模型样本 {health['total']}，成功率 {health['success_rate'] * 100:.1f}%，"
            f"当前待清理 {health['cleanup_candidates']}，"
            f"已自动归档坏样本 {health.get('archived_bad', 0)} "
            f"(失败 {health.get('archived_failure', 0)}，卡住 {health.get('archived_stuck', 0)}，"
            f"无进展 {health.get('archived_no_progress', 0)})。"
        )
        if not edges:
            emit("训练报告：当前副本没有线路图训练边。")
            return
        emit("线路边进度：")
        for index, edge in enumerate(edges, 1):
            edge_id = str(edge.get("id") or f"edge_{index}")
            from_id = str(edge.get("from") or "")
            to_id = str(edge.get("to") or "")
            stat = route_stats.get(edge_id) or {}
            success = int(stat.get("success", 0) or 0)
            failure = int(stat.get("failure", 0) or 0)
            total = success + failure
            rate = success / total * 100 if total else 0.0
            from_label = str((nodes.get(from_id) or {}).get("label") or from_id)
            to_label = str((nodes.get(to_id) or {}).get("label") or to_id)
            state = "未练" if total == 0 else f"{success}/{total} {rate:.0f}%"
            emit(f"  {index:02d}. {from_label} -> {to_label}：{state}")

    def cleanup_training_samples(self, log_func: Callable[[str], None] | None = None) -> None:
        emit = log_func or self.log
        script_name = str(self.flow.get("script_name") or "副本_001")
        health = self.storage.movement_sample_health(map_id=self.current_map, script_name=script_name)
        candidates = int(health.get("cleanup_candidates", 0) or 0)
        if candidates <= 0:
            emit("清理垃圾样本：没有需要清理的失败/卡住/无进展样本。")
            return
        reply = QMessageBox.question(
            self,
            "清理垃圾样本",
            f"将删除当前副本当前地图的 {candidates} 条失败/卡住/无进展移动样本。\n"
            "线路成功率记录和地图危险点不会删除。确定继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = self.storage.cleanup_bad_movement_samples(map_id=self.current_map, script_name=script_name)
        self._movement_auto_cleanup_keys.discard(f"{script_name}:{self.current_map}")
        emit(f"清理垃圾样本完成：归档并移出模型样本 {deleted} 条。")

    def auto_cleanup_movement_samples(
        self,
        *,
        log_func: Callable[[str], None] | None = None,
        force: bool = False,
    ) -> int:
        script_name = str(self.flow.get("script_name") or "副本_001")
        key = f"{script_name}:{self.current_map}"
        if not force and key in self._movement_auto_cleanup_keys:
            return 0
        emit = log_func or self.log
        health = self.storage.movement_sample_health(map_id=self.current_map, script_name=script_name)
        candidates = int(health.get("cleanup_candidates", 0) or 0)
        self._movement_auto_cleanup_keys.add(key)
        if candidates <= 0:
            return 0
        deleted = self.storage.cleanup_bad_movement_samples(map_id=self.current_map, script_name=script_name)
        if deleted:
            emit(f"已自动归档并移出垃圾移动样本 {deleted} 条；这些数据保留在坏样本记录里，不参与移动模型。")
        return deleted

    def default_route_training_start_index(self, script_name: str, edges: list[dict[str, Any]]) -> int:
        route_training = self.storage.load_script_route_training(script_name)
        route_stats = route_training.get("edges") or {}
        for index, edge in enumerate(edges, 1):
            edge_id = str(edge.get("id") or f"edge_{index}")
            stat = route_stats.get(edge_id) or {}
            success = int(stat.get("success", 0) or 0)
            failure = int(stat.get("failure", 0) or 0)
            total = success + failure
            goal = float(edge.get("success_goal", 0.9) or 0.9)
            if total == 0 or (success / total) < goal:
                return index
        return max(1, len(edges))

    def auto_route_training_edges(
        self,
        script_name: str,
        edges: list[dict[str, Any]],
    ) -> tuple[list[tuple[int, dict[str, Any], str]], bool]:
        route_training = self.storage.load_script_route_training(script_name)
        route_stats = route_training.get("edges") or {}
        weak_edges: list[tuple[float, int, dict[str, Any], str]] = []
        maintenance_edges: list[tuple[int, dict[str, Any], str]] = []
        for index, edge in enumerate(edges, 1):
            edge_id = str(edge.get("id") or f"edge_{index}")
            stat = route_stats.get(edge_id) or {}
            success = int(stat.get("success", 0) or 0)
            failure = int(stat.get("failure", 0) or 0)
            total = success + failure
            goal = float(edge.get("success_goal", 0.9) or 0.9)
            min_success = int(edge.get("success_samples_goal", edge.get("min_success_samples", 8)) or 8)
            rate = success / total if total else 0.0
            if total == 0:
                weak_edges.append((500.0, index, edge, "未练"))
            elif success < min_success:
                deficit = min_success - success
                weak_edges.append((400.0 + deficit * 10.0 + failure * 2.0, index, edge, f"成功样本不足 {success}/{min_success}"))
            elif rate < goal:
                weak_edges.append((300.0 + (goal - rate) * 100.0 + failure * 3.0, index, edge, f"成功率 {rate * 100:.0f}% < {goal * 100:.0f}%"))
            else:
                maintenance_edges.append((index, edge, f"已达标 {success}/{total} {rate * 100:.0f}%"))
        if weak_edges:
            weak_edges.sort(key=lambda item: (-item[0], item[1]))
            return [(index, edge, reason) for _score, index, edge, reason in weak_edges], False
        return maintenance_edges, True

    def practice_route_plan(self) -> None:
        if self._movement_training_active:
            self.log("已有移动训练正在运行，先停止或等待结束后再开始新的训练。")
            return
        script_name = str(self.flow.get("script_name") or "副本_001")
        route_plan = self.storage.load_script_route_plan(script_name)
        nodes = self.route_plan_nodes_by_id(route_plan)
        edges = [
            edge
            for edge in route_plan.get("training_edges") or []
            if str(edge.get("from") or "") in nodes and str(edge.get("to") or "") in nodes
        ]
        if not edges:
            QMessageBox.information(
                self,
                "按线路图训练",
                "当前副本没有可训练的 route_plan.json。请先生成线路图。",
            )
            return
        cycles, ok = QInputDialog.getInt(self, "按线路图训练", "线路图训练几轮？", 9999, 1, 9999, 1)
        if not ok:
            return
        self.start_runtime_task(
            "线路图训练",
            lambda: self.practice_route_plan_worker(
                script_name,
                nodes,
                edges,
                int(cycles),
            ),
        )

    def practice_route_plan_worker(
        self,
        script_name: str,
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
        cycles: int,
    ) -> None:
        self._stop_requested = False
        self._movement_training_active = True
        self.auto_cleanup_movement_samples(force=True)
        self.log(f"开始按线路图自动训练：{script_name}，{len(edges)} 条边，{cycles} 轮。优先补未达标边，全部达标后巡检。")
        completed_edges = 0
        try:
            for cycle in range(1, int(cycles) + 1):
                selected_edges, maintenance_mode = self.auto_route_training_edges(script_name, edges)
                if not selected_edges:
                    self.log("线路图自动训练：没有可训练的边。")
                    return
                mode_text = "巡检已达标线路" if maintenance_mode else "补训未达标线路"
                self.log(f"线路图自动选边 {cycle}/{cycles}：{mode_text} {len(selected_edges)} 条。")
                for edge_index, edge, reason in selected_edges:
                    self.runtime_yield()
                    if self._stop_requested:
                        self.log(f"线路图训练已停止。已完整记录 {completed_edges} 条边结果，下次可继续。")
                        return
                    from_node = nodes[str(edge.get("from"))]
                    to_node = nodes[str(edge.get("to"))]
                    edge_id = str(edge.get("id") or f"edge_{edge_index}")
                    from_label = str(from_node.get("label") or from_node.get("id"))
                    to_label = str(to_node.get("label") or to_node.get("id"))
                    from_step = self.create_route_node_step(from_node, prefix="线路起点", max_seconds=40)
                    if from_step is not None:
                        self.log(f"线路图训练 {cycle}/{cycles} 边 {edge_index}/{len(edges)}（{reason}）：准备起点 {from_label}")
                        from_ok = self.execute_move_to_game_coord_step(from_step)
                        if self._stop_requested:
                            self.log(f"线路图训练已停止。当前边 {from_label} -> {to_label} 未写入失败记录。")
                            return
                        if not from_ok:
                            self.log(f"线路起点未到位：{from_label}，跳过本条边，避免污染 {from_label} -> {to_label}。")
                            self.storage.mark_script_route_edge_result(
                                script_name,
                                edge_id,
                                from_node=str(edge.get("from") or ""),
                                to_node=str(edge.get("to") or ""),
                                success=False,
                                end_coord=self.current_game_coord,
                            )
                            continue
                    to_step = self.create_route_node_step(to_node, prefix="线路目标", max_seconds=55)
                    if to_step is None:
                        self.log(f"线路图训练跳过无需移动目标：{to_label}")
                        continue
                    self.log(
                        f"线路图训练 {cycle}/{cycles} 边 {edge_index}/{len(edges)}（{reason}）："
                        f"{from_label} -> {to_label}"
                    )
                    ok = self.execute_move_to_game_coord_step(to_step)
                    if self._stop_requested:
                        self.log(f"线路图训练已停止。当前边 {from_label} -> {to_label} 未写入失败记录。")
                        return
                    self.storage.mark_script_route_edge_result(
                        script_name,
                        edge_id,
                        from_node=str(edge.get("from") or ""),
                        to_node=str(edge.get("to") or ""),
                        success=ok,
                        start_coord=[int(from_node["coord"][0]), int(from_node["coord"][1])],
                        end_coord=self.current_game_coord,
                    )
                    completed_edges += 1
                    target_coord = [int(to_node["coord"][0]), int(to_node["coord"][1])]
                    target_id = self.movement_target_id_for_coord(script_name, target_coord)
                    if target_id:
                        origin_coord = [int(from_node["coord"][0]), int(from_node["coord"][1])]
                        self.storage.mark_script_movement_coord_result(
                            script_name,
                            target_id,
                            ok,
                            origin_coord=origin_coord,
                            origin_label=from_label,
                        )
                    if not ok:
                        self.log(f"线路边训练失败：{from_label} -> {to_label}，建议先单独补训这条边。")
            self.log("线路图训练完成。")
        finally:
            self._movement_training_active = False

    def explore_current_map(self) -> None:
        if self._movement_training_active:
            self.log("已有移动训练/地图探索正在运行，先停止或等待结束后再开始。")
            return
        attempts, ok = QInputDialog.getInt(
            self,
            "探索副本地图",
            "最多尝试多少次坐标探测？",
            9999,
            1,
            999999,
            50,
        )
        if not ok:
            return
        self.start_runtime_task("探索副本地图", lambda: self.explore_current_map_worker(int(attempts)))

    def show_walkability_report(self, log_func: Callable[[str], None] | None = None) -> None:
        grid = WalkabilityGrid.load(self.storage.walkability_path(self.current_map), self.current_map)
        removed = grid.prune_outliers()
        script_bounds = self.script_map_bounds(str(self.flow.get("script_name") or ""), padding=8)
        removed += grid.prune_to_bounds(script_bounds)
        if removed:
            grid.save(self.storage.walkability_path(self.current_map))
        summary = grid.summary()
        bounds = summary.get("bounds")
        lines = [
            "坐标地图报告：",
            f"地图：{summary['map_id']}",
            f"可走点：{summary['walkable']}，障碍/边界：{summary['blocked']}，危险点：{summary['danger']}，待扩展边缘：{summary['frontier']}",
            f"范围：{bounds if bounds else '未建立'}",
            f"副本软边界：{list(script_bounds) if script_bounds else '未设置'}",
            f"本次自动清理离群坐标：{removed}",
            f"文件：{self.storage.walkability_path(self.current_map)}",
            "图例：. 可走，# 障碍/边界，! 高风险",
            grid.ascii_map(max_width=96, max_height=56),
        ]
        text = "\n".join(lines)
        if log_func is not None:
            for line in lines:
                log_func(line)
        else:
            self.result_message.emit(text)
            self.log("已生成坐标地图报告。")

    def coord_in_bounds(self, coord: tuple[int, int], bounds: tuple[int, int, int, int] | None) -> bool:
        if bounds is None:
            return True
        min_x, min_y, max_x, max_y = bounds
        return int(min_x) <= int(coord[0]) <= int(max_x) and int(min_y) <= int(coord[1]) <= int(max_y)

    def script_map_bounds(self, script_name: str, *, padding: int = 8) -> tuple[int, int, int, int] | None:
        coords: list[tuple[int, int]] = []
        route_plan = self.storage.load_script_route_plan(script_name) if script_name else {}
        for node in route_plan.get("nodes") or []:
            coord = node.get("coord")
            if isinstance(coord, list) and len(coord) >= 2:
                coords.append((int(coord[0]), int(coord[1])))
        movement_coords = self.storage.load_script_movement_coords(script_name) if script_name else {}
        for target in movement_coords.get("targets") or []:
            coord = target.get("coord")
            if target.get("map_id", self.current_map) == self.current_map and isinstance(coord, list) and len(coord) >= 2:
                coords.append((int(coord[0]), int(coord[1])))
        if not coords:
            return None
        xs = [coord[0] for coord in coords]
        ys = [coord[1] for coord in coords]
        pad = max(0, int(padding))
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad

    def nearest_exploration_frontier(
        self,
        grid: WalkabilityGrid,
        current: list[int],
        bounds: tuple[int, int, int, int] | None,
    ) -> tuple[int, int] | None:
        frontiers = [node for node in grid.frontier_nodes() if self.coord_in_bounds(node, bounds)]
        if not frontiers:
            return None
        current_node = (int(current[0]), int(current[1]))
        frontiers.sort(
            key=lambda node: (
                coord_distance(current_node, node),
                grid.danger_nodes.get(node, 0),
                node[1],
                node[0],
            )
        )
        return frontiers[0]

    def next_map_probe_candidate(
        self,
        grid: WalkabilityGrid,
        current: list[int],
        bounds: tuple[int, int, int, int] | None,
    ) -> tuple[int, int] | None:
        current_node = (int(current[0]), int(current[1]))
        candidates = [node for node in grid.unknown_neighbors(current_node) if self.coord_in_bounds(node, bounds)]
        if not candidates:
            return None
        candidates.sort(
            key=lambda node: (
                grid.danger_nodes.get(node, 0),
                abs(node[0] - current_node[0]) + abs(node[1] - current_node[1]),
                node[1],
                node[0],
            )
        )
        return candidates[0]

    def make_map_probe_step(self, coord: tuple[int, int], *, max_seconds: float = 35.0) -> dict[str, Any]:
        return self.create_movement_practice_step(
            [int(coord[0]), int(coord[1])],
            name=f"地图探索定位_{coord[0]}_{coord[1]}",
            exact=True,
            tolerance=0,
            max_seconds=max_seconds,
        )

    def probe_adjacent_map_coord(
        self,
        current: list[int],
        candidate: tuple[int, int],
        grid: WalkabilityGrid,
        *,
        step_id: str,
    ) -> list[int]:
        target = [int(candidate[0]), int(candidate[1])]
        screen_size = self.current_screen_size()
        character_position = self.coordinate_mapper.estimate_character_screen_position(screen_size)
        plan = self.coordinate_mapper.click_for_game_coord_direct(
            current,
            target,
            character_position=character_position,
            screen_size=screen_size,
            tile_radius=46,
            min_radius=62,
            max_radius=145,
        )
        before = [int(current[0]), int(current[1])]
        self.adb.tap(int(plan.point[0]), int(plan.point[1]))
        self.log(
            f"地图探测：从 {before} 探 {target}，"
            f"相对点击 {list(plan.relative_to_character)} 半径 {plan.radius} 方向 {plan.direction}"
        )
        after, duration = self.wait_for_movement_result(
            step_id,
            before=before,
            max_jump=MAX_NORMAL_MOVEMENT_DELTA,
            target_hint=target,
            save_capture=False,
            poll_interval=0.3,
            settle_seconds=0.7,
        )
        if not after:
            after = before
        delta = [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])]
        plausible = movement_delta_is_plausible(delta) or delta == [0, 0]
        before_distance = coord_distance(before, target)
        after_distance = coord_distance(after, target)
        success = plausible and coord_within_tolerance(after, target, 0)
        stuck = plausible and after == before
        progress = float(before_distance - after_distance) if plausible else 0.0
        self.storage.add_movement_sample(
            map_id=self.current_map,
            before_game_coord=before,
            after_game_coord=after,
            click_relative_to_character=list(plan.relative_to_character),
            click_angle=plan.angle,
            click_radius=plan.radius,
            actual_delta=delta,
            duration=duration,
            success=success,
            stuck=stuck,
            progress_score=progress,
            direction=plan.direction,
            screen_point=list(plan.point),
            start_coord=before,
            end_coord=after,
            strategy=f"map_probe:{plan.direction}:{plan.radius}",
            script_name=str(self.flow.get("script_name") or ""),
        )
        if not plausible:
            grid.mark_danger(candidate, amount=2)
            self.log(f"地图探测结果疑似 OCR 异常：{before} -> {after}，先不写障碍。")
            return before
        grid.record_movement(before, after, success=success, stuck=stuck, waypoint=target)
        if success:
            grid.mark_walkable(after)
            grid.danger_nodes.pop((int(after[0]), int(after[1])), None)
            self.log(f"地图探测成功：{target} 可走。")
            self.current_game_coord = list(after)
            return list(after)
        if stuck:
            previous_failures = int(grid.danger_nodes.get(candidate, 0) or 0)
            if previous_failures >= 2:
                grid.mark_blocked(candidate)
                self.log(f"地图探测边界/障碍：{target} 多次点后未移动，已标记 #。")
            else:
                grid.mark_danger(candidate)
                self.log(f"地图探测未移动：{target} 先标记待复测，避免误把点到人物当障碍。")
            self.current_game_coord = list(before)
            return before
        grid.mark_walkable(after)
        grid.danger_nodes.pop((int(after[0]), int(after[1])), None)
        grid.mark_danger(candidate)
        self.log(f"地图探测偏移：想去 {target}，实际到 {after}；实际点记可走，目标先标风险。")
        self.current_game_coord = list(after)
        return list(after)

    def explore_current_map_worker(self, max_attempts: int) -> None:
        self._stop_requested = False
        self._movement_training_active = True
        grid = WalkabilityGrid.load(self.storage.walkability_path(self.current_map), self.current_map)
        removed = grid.prune_outliers()
        script_bounds = self.script_map_bounds(str(self.flow.get("script_name") or ""), padding=8)
        removed += grid.prune_to_bounds(script_bounds)
        explored = 0
        relocated = 0
        try:
            self.log(f"开始探索副本坐标地图：最多 {max_attempts} 次探测，只写 JSON，不保存截图。")
            if script_bounds:
                self.log(f"使用当前副本软边界探索：{list(script_bounds)}。")
            if removed:
                self.log(f"已清理坐标地图离群点 {removed} 个，避免旧 OCR 垃圾坐标带偏探索。")
                grid.save(self.storage.walkability_path(self.current_map))
            current = self.read_stable_current_game_coord(
                "map_explore",
                max_jump=None,
                save_capture=False,
                sample_count=1,
                min_agreement=1,
                sample_delay=0.05,
            )
            if not current:
                self.log("地图探索无法读取当前坐标，请先校准右上角游戏坐标区域。")
                return
            grid.mark_walkable(current)
            grid.save(self.storage.walkability_path(self.current_map))
            while explored < int(max_attempts):
                self.runtime_yield()
                if self._stop_requested:
                    self.log(f"地图探索已停止：本次探测 {explored} 次，已保存坐标地图。")
                    return
                current = self.read_stable_current_game_coord(
                    "map_explore",
                    max_jump=None,
                    save_capture=False,
                    sample_count=1,
                    min_agreement=1,
                    sample_delay=0.05,
                ) or current
                grid.mark_walkable(current)
                candidate = self.next_map_probe_candidate(grid, current, script_bounds)
                if candidate is None:
                    frontier = self.nearest_exploration_frontier(grid, current, script_bounds)
                    if frontier is None:
                        self.log("地图探索完成：没有剩余待扩展边缘。")
                        return
                    if (int(current[0]), int(current[1])) != frontier:
                        relocated += 1
                        self.log(f"当前区域已探完，移动到最近待扩展点 {list(frontier)}。")
                        step = self.make_map_probe_step(frontier)
                        if not self.execute_move_to_game_coord_step(step):
                            grid.mark_danger(frontier, amount=2)
                            grid.save(self.storage.walkability_path(self.current_map))
                            self.log(f"待扩展点 {list(frontier)} 暂时到不了，标风险后继续。")
                            if relocated > 20 and explored == 0:
                                self.log("地图探索没有取得进展，请先手动移动到副本内部开阔点再启动。")
                                return
                            continue
                        current = [int(frontier[0]), int(frontier[1])]
                        grid.mark_walkable(current)
                        continue
                current = self.probe_adjacent_map_coord(current, candidate, grid, step_id="map_explore")
                explored += 1
                if explored % 10 == 0:
                    summary = grid.summary()
                    self.log(
                        "地图探索进度："
                        f"{explored}/{max_attempts}，可走 {summary['walkable']}，"
                        f"障碍/边界 {summary['blocked']}，待扩展 {summary['frontier']}。"
                    )
                grid.save(self.storage.walkability_path(self.current_map))
            self.log(f"地图探索达到尝试上限：{max_attempts} 次，已保存坐标地图。")
        finally:
            grid.save(self.storage.walkability_path(self.current_map))
            self._movement_training_active = False

    def movement_practice_origins_for_target(
        self,
        target: dict[str, Any],
        targets: list[dict[str, Any]],
        grid: WalkabilityGrid,
        *,
        limit: int,
        cycle_index: int,
    ) -> list[dict[str, Any]]:
        target_coord = [int(target["coord"][0]), int(target["coord"][1])]
        origins: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()

        def add_origin(coord: list[int], label: str, *, exact: bool, tolerance: int, source: str) -> None:
            node = (int(coord[0]), int(coord[1]))
            if len(origins) >= limit or node in seen or node == (target_coord[0], target_coord[1]):
                return
            if node[0] < 0 or node[1] < 0:
                return
            if grid.is_blocked(node) or grid.is_danger(node, threshold=4):
                return
            seen.add(node)
            origins.append(
                {
                    "coord": [node[0], node[1]],
                    "label": label,
                    "exact": bool(exact),
                    "tolerance": int(tolerance),
                    "source": source,
                }
            )

        same_map_targets = [
            item
            for item in targets
            if item is not target
            and item.get("map_id", self.current_map) == self.current_map
            and isinstance(item.get("coord"), list)
        ]
        same_map_targets.sort(
            key=lambda item: coord_distance([int(item["coord"][0]), int(item["coord"][1])], target_coord),
            reverse=True,
        )
        if same_map_targets:
            offset = (max(1, int(cycle_index)) - 1) % len(same_map_targets)
            same_map_targets = same_map_targets[offset:] + same_map_targets[:offset]
        for item in same_map_targets:
            coord = [int(item["coord"][0]), int(item["coord"][1])]
            add_origin(
                coord,
                str(item.get("label") or f"{coord[0]},{coord[1]}"),
                exact=bool(item.get("exact", True)),
                tolerance=int(item.get("tolerance", 0) or 0),
                source="movement_db",
            )

        base_distance = max(3, int(target.get("practice_start_distance", 4) or 4))
        for distance in (base_distance, base_distance + 2):
            for dx, dy in (
                (0, -distance),
                (distance, -distance),
                (distance, 0),
                (distance, distance),
                (0, distance),
                (-distance, distance),
                (-distance, 0),
                (-distance, -distance),
            ):
                coord = [target_coord[0] + dx, target_coord[1] + dy]
                add_origin(
                    coord,
                    f"目标外侧 {coord[0]},{coord[1]}",
                    exact=False,
                    tolerance=1,
                    source="generated",
                )
        return origins

    def practice_script_movement_db(self) -> None:
        if self._movement_training_active:
            self.log("已有移动训练正在运行，先停止或等待结束后再开始新的训练。")
            return
        script_name = str(self.flow.get("script_name") or "副本_001")
        payload = self.storage.load_script_movement_coords(script_name)
        targets = [
            target
            for target in payload.get("targets") or []
            if target.get("map_id", self.current_map) == self.current_map and isinstance(target.get("coord"), list)
        ]
        if not targets:
            QMessageBox.information(self, "练习移动库", "这个脚本还没有移动库坐标。先在上方输入坐标后点“加入移动库”。")
            return
        cycles, ok = QInputDialog.getInt(self, "练习移动库", "循环练习几轮？", 9999, 1, 9999, 1)
        if not ok:
            return
        starts_per_target, ok = QInputDialog.getInt(
            self,
            "练习移动库",
            "每个目标从几个起点练习？",
            min(4, max(1, len(targets))),
            1,
            12,
            1,
        )
        if not ok:
            return
        self.start_runtime_task(
            "移动库练习",
            lambda: self.practice_script_movement_db_worker(
                script_name,
                targets,
                int(cycles),
                int(starts_per_target),
            ),
        )

    def practice_script_movement_db_worker(
        self,
        script_name: str,
        targets: list[dict[str, Any]],
        cycles: int,
        starts_per_target: int,
    ) -> None:
        self._stop_requested = False
        self._movement_training_active = True
        self.auto_cleanup_movement_samples(force=True)
        self.log(
            f"开始练习脚本移动库：{script_name}，{len(targets)} 个目标，"
            f"{cycles} 轮，每个目标 {starts_per_target} 个起点。"
        )
        try:
            for cycle in range(1, int(cycles) + 1):
                for target in targets:
                    self.runtime_yield()
                    if self._stop_requested:
                        self.log("移动库练习已停止。")
                        return
                    coord = [int(target["coord"][0]), int(target["coord"][1])]
                    grid = WalkabilityGrid.load(self.storage.walkability_path(self.current_map), self.current_map)
                    origins = self.movement_practice_origins_for_target(
                        target,
                        targets,
                        grid,
                        limit=int(starts_per_target),
                        cycle_index=cycle,
                    )
                    if not origins:
                        origins = [{"coord": coord, "label": "当前位置", "exact": False, "tolerance": 99, "source": "current"}]
                    for origin in origins:
                        self.runtime_yield()
                        if self._stop_requested:
                            self.log("移动库练习已停止。")
                            return
                        origin_coord = [int(origin["coord"][0]), int(origin["coord"][1])]
                        origin_label = str(origin.get("label") or f"{origin_coord[0]},{origin_coord[1]}")
                        if origin.get("source") != "current":
                            origin_step = self.create_movement_practice_step(
                                origin_coord,
                                name=f"练习起点_{origin_coord[0]}_{origin_coord[1]}",
                                exact=bool(origin.get("exact", False)),
                                tolerance=int(origin.get("tolerance", 1) or 1),
                                max_seconds=35,
                            )
                            self.log(f"练习起点准备：{origin_label} -> {origin_coord}")
                            origin_ok = self.execute_move_to_game_coord_step(origin_step)
                            if self._stop_requested:
                                self.log("移动库练习已停止。当前目标未写入失败记录。")
                                return
                            if not origin_ok:
                                self.log(f"练习起点未到位：{origin_label}，跳过本次回切，避免污染目标 {coord}。")
                                self.storage.mark_script_movement_coord_result(
                                    script_name,
                                    str(target.get("id")),
                                    False,
                                    origin_coord=origin_coord,
                                    origin_label=origin_label,
                                )
                                continue
                        target_exact = bool(target.get("exact", True))
                        target_step = self.create_movement_practice_step(
                            coord,
                            name=f"练习精准目标_{coord[0]}_{coord[1]}",
                            exact=target_exact,
                            tolerance=int(target.get("tolerance", 0) or 0),
                            max_seconds=45,
                        )
                        self.log(
                            f"移动库练习 {cycle}/{cycles}：从 {origin_label} "
                            f"回切到 {target.get('label') or coord}"
                        )
                        ok = self.execute_move_to_game_coord_step(target_step)
                        if self._stop_requested:
                            self.log("移动库练习已停止。当前目标未写入失败记录。")
                            return
                        self.storage.mark_script_movement_coord_result(
                            script_name,
                            str(target.get("id")),
                            ok,
                            origin_coord=origin_coord,
                            origin_label=origin_label,
                        )
                        if not ok:
                            self.log(f"移动库练习未到位：{coord}，继续下一个起点。")
            self.log("移动库练习完成。")
        finally:
            self._movement_training_active = False

    def on_game_clicked(self, point: QPoint) -> None:
        if not self.record_action.isChecked():
            self.log(f"画面坐标：{point.x()}, {point.y()}。开启录制后会生成点击步骤。")
            return
        if self.current_frame is None:
            self.log("没有当前画面，无法录制点击。")
            return

        self.sync_steps_from_list()
        step_index = len(self.flow["steps"]) + 1
        step = create_step("click", f"click_{step_index:03d}")
        step["input"]["screen_coord"] = [point.x(), point.y()]
        step["input"]["wait_after"] = 1.0

        step_dir = self.storage.step_dir(self.flow["script_name"], step["id"], "click")
        before_path = step_dir / "before.png"
        after_path = step_dir / "after.png"
        self.current_frame.save(str(before_path))
        step["screenshots"]["before"] = self.storage.rel(before_path)

        self.log(f"录制点击：{point.x()}, {point.y()} -> {step['name']}")
        try:
            self.adb.tap(point.x(), point.y())
            self.sleep_with_events(float(step["input"].get("wait_after", 1.0)))
            after = QImage.fromData(self.adb.screencap_png(), "PNG")
            if not after.isNull():
                after.save(str(after_path))
                step["screenshots"]["after"] = self.storage.rel(after_path)
                self.on_frame_ready(after)
        except Exception as exc:  # noqa: BLE001
            self.log(f"点击已记录，但 ADB 执行失败：{exc}")

        self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])

    def insert_screen_click_step_from_point(self, point: QPoint) -> None:
        if self.current_frame is None:
            self.log("没有当前画面，无法插入点击位置移动步骤。")
            return
        self.sync_steps_from_list()
        step_index = self.count_steps(self.flow.get("steps") or []) + 1
        step = create_step("click", f"点击位置移动_{step_index:03d}")
        step["input"]["click_type"] = "fixed_screen_coord"
        step["input"]["screen_coord"] = [point.x(), point.y()]
        step["input"]["wait_after"] = 1.0

        step_dir = self.storage.step_dir(self.flow["script_name"], step["id"], "click")
        before_path = step_dir / "before.png"
        self.current_frame.save(str(before_path))
        step["screenshots"]["before"] = self.storage.rel(before_path)

        placement = self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        self.log(f"已插入{where}：点击位置移动 {point.x()}, {point.y()}。运行时会点当前屏幕固定位置。")

    def on_region_selected(self, rect: QRect) -> None:
        if self.current_frame is None:
            return
        if self.question_capture is not None:
            self.handle_question_capture_region(rect)
            return
        menu = QMenu(self)
        large_action = menu.addAction("在大图中重新框选")
        large_action.triggered.connect(self.open_large_region_dialog)
        menu.addSeparator()
        for label, kind in REGION_CHOICES:
            action = menu.addAction(label)
            action.triggered.connect(lambda checked=False, value=kind: self.create_asset_from_region(value, rect))
        menu.exec(self.cursor().pos())

    def open_large_region_dialog(self) -> None:
        if self.current_frame is None:
            if not self.capture_current_screen(save=False):
                QMessageBox.information(self, "当前画面操作", "请先连接 MuMu，并确认 ADB 可以截图。")
                return
        else:
            self.capture_current_screen(save=False)
        if self.question_capture is not None:
            dialog = LargeRegionDialog(
                self.current_frame,
                title="当前画面截图/框选",
                fixed_kind="question",
                hint="当前正在添加题库，请拖拽框选题目或选项区域。",
                parent=self,
            )
        else:
            dialog = LargeRegionDialog(self.current_frame, title="当前画面截图/框选", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.selected_point is not None:
            self.insert_screen_click_step_from_point(dialog.selected_point)
            return
        if dialog.selected_rect is None:
            return
        if self.question_capture is not None:
            self.handle_question_capture_region(dialog.selected_rect)
            return
        self.create_asset_from_region(dialog.selected_kind(), dialog.selected_rect)

    def start_deepsea_auto_recording(self) -> None:
        if self.deepsea_recorder_worker is not None and self.deepsea_recorder_worker.isRunning():
            QMessageBox.information(self, "深海自动记录", "自动记录已经在运行。")
            return
        if not self.adb.serial:
            QMessageBox.information(self, "深海自动记录", "请先连接 MuMu。")
            return
        if self.current_frame is None:
            self.capture_current_screen(save=False)
        worker = DeepSeaAutoRecorderWorker(
            self.adb,
            self.storage.deepsea_operation_records_dir(),
            screen_size=self.current_screen_size(),
        )
        worker.status.connect(self.log)
        worker.operation_recorded.connect(lambda text: self.log(f"深海自动记录：{text}"))
        worker.finished.connect(lambda: self.log("深海自动记录线程已结束。"))
        self.deepsea_recorder_worker = worker
        worker.start()
        self.log("深海自动记录启动中。现在可以直接在 MuMu 里正常打副本。")

    def stop_deepsea_auto_recording(self) -> None:
        worker = self.deepsea_recorder_worker
        if worker is None or not worker.isRunning():
            self.log("深海自动记录没有在运行。")
            return
        worker.stop()
        worker.wait(2000)
        self.log("已请求停止深海自动记录。")

    def open_deepsea_operation_review(self) -> None:
        if self.deepsea_operation_review_dialog is None:
            self.deepsea_operation_review_dialog = DeepSeaOperationReviewDialog(
                self.storage,
                self.deepsea_action_library,
                parent=self,
            )
        else:
            self.deepsea_operation_review_dialog.load_sessions()
        self.deepsea_operation_review_dialog.show()
        self.deepsea_operation_review_dialog.raise_()
        self.deepsea_operation_review_dialog.activateWindow()

    def open_deepsea_action_capture(self) -> None:
        if not self.capture_current_screen(save=False) or self.current_frame is None:
            QMessageBox.information(self, "深海动作截图", "请先连接 MuMu，并确认 ADB 可以截图。")
            return

        setup = DeepSeaActionCaptureSetupDialog(
            self.deepsea_action_library,
            last_actor=str(getattr(self, "_deepsea_last_actor", "1号人物")),
            last_action=str(getattr(self, "_deepsea_last_action", "放风")),
            parent=self,
        )
        if setup.exec() != QDialog.DialogCode.Accepted:
            return
        config = setup.capture_config()
        actor_text = config["actor_text"]
        action_text = config["action_text"]
        step_label = config["step_label"]

        try:
            actor_ref = normalize_actor(actor_text)
            action_key = normalize_action(action_text)
        except ValueError as exc:
            QMessageBox.warning(self, "深海动作截图", str(exc))
            return

        action_name = display_action_name(action_key)
        step_type = str(config.get("step_type") or "template_click")
        if step_type == "swipe":
            swipe_dialog = DeepSeaSwipeCaptureDialog(
                self.current_frame,
                title=f"深海滑动：{actor_ref.label} - {action_name} - {step_label}",
                parent=self,
            )
            if swipe_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if swipe_dialog.selected_start is None or swipe_dialog.selected_end is None:
                return
            start = [int(swipe_dialog.selected_start.x()), int(swipe_dialog.selected_start.y())]
            end = [int(swipe_dialog.selected_end.x()), int(swipe_dialog.selected_end.y())]
            if start == end:
                QMessageBox.warning(self, "深海动作截图", "滑动起点和终点不能相同。")
                return

            margin = 24
            left = max(0, min(start[0], end[0]) - margin)
            top = max(0, min(start[1], end[1]) - margin)
            right = min(self.current_frame.width() - 1, max(start[0], end[0]) + margin)
            bottom = min(self.current_frame.height() - 1, max(start[1], end[1]) + margin)
            bounded = QRect(left, top, max(1, right - left + 1), max(1, bottom - top + 1))
            crop = self.current_frame.copy(bounded)

            user_name = f"深海_{actor_ref.label}_{action_name}_{step_label}"
            paths = self.storage.make_asset_paths(self.current_map, "button", user_name)
            raw_path = Path(paths["raw_path"])
            crop_path = Path(paths["crop_path"])
            annotated_path = Path(paths["annotated_path"])

            annotated = self.current_frame.copy()
            painter = QPainter(annotated)
            painter.setPen(QPen(QColor("#00a5ff"), 5))
            painter.drawLine(QPoint(start[0], start[1]), QPoint(end[0], end[1]))
            painter.setPen(QPen(QColor("#ffcc00"), 4))
            painter.drawEllipse(QPoint(start[0], start[1]), 8, 8)
            painter.setPen(QPen(QColor("#ff375f"), 4))
            painter.drawEllipse(QPoint(end[0], end[1]), 8, 8)
            painter.end()

            self.current_frame.save(str(raw_path))
            crop.save(str(crop_path))
            annotated.save(str(annotated_path))

            bbox = bbox_from_rect(bounded)
            asset_id = str(paths["asset_id"])
            metadata = {
                "created_from": "deepsea_action_capture",
                "step_type": "swipe",
                "actor_type": actor_ref.actor_type,
                "actor_index": actor_ref.index,
                "actor_label": actor_ref.label,
                "action_key": action_key,
                "action_name": action_name,
                "step_label": step_label,
                "swipe_start": start,
                "swipe_end": end,
                "duration_ms": 450,
            }
            self.storage.add_asset(
                asset_id=asset_id,
                user_name=user_name,
                auto_name=str(paths["auto_name"]),
                asset_type="button",
                map_id=self.current_map,
                script_name=self.flow["script_name"],
                step_id=None,
                bbox=bbox,
                raw_path=raw_path,
                crop_path=crop_path,
                annotated_path=annotated_path,
                metadata=metadata,
            )
            self.deepsea_action_library.add_step(
                actor=actor_ref,
                action=action_key,
                step_label=step_label,
                step_type="swipe",
                asset_id=asset_id,
                bbox=bbox,
                swipe_start=start,
                swipe_end=end,
                duration_ms=450,
                wait_after=0.4,
            )
            self.deepsea_action_library.save()
            self._deepsea_last_actor = actor_text.strip()
            self._deepsea_last_action = action_text.strip()

            swiped = False
            if bool(config.get("tap_after_capture")):
                try:
                    self.adb.swipe(start[0], start[1], end[0], end[1], 450)
                    swiped = True
                    self.log(f"深海采集后滑动：{start[0]}, {start[1]} -> {end[0]}, {end[1]}")
                except AdbError as exc:
                    self.log(f"深海滑动已保存，但执行失败：{exc}")
                    QMessageBox.warning(self, "深海动作截图", f"滑动已保存，但执行失败：{exc}")

            missing_count = len(self.deepsea_action_library.missing_required_actions())
            self.log(
                f"已采集深海滑动：{actor_ref.label} / {action_name} / {step_label}，"
                f"{start} -> {end}。"
            )
            self.append_result(
                "深海动作截图\n"
                f"action: {actor_ref.label} / {action_name}\n"
                f"step: {step_label}\n"
                "type: swipe\n"
                f"asset: {asset_id}\n"
                f"swipe: {start} -> {end}\n"
                f"executed_after_capture: {swiped}\n"
                f"missing_required_actions: {missing_count}\n"
            )
            return

        dialog = LargeRegionDialog(
            self.current_frame,
            title=f"深海动作截图：{actor_ref.label} - {action_name}",
            fixed_kind="button",
            hint=f"请框选“{actor_ref.label} / {action_name} / {step_label}”这一步的稳定图片模板。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return

        bounded = dialog.selected_rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        if bounded.width() <= 0 or bounded.height() <= 0:
            QMessageBox.warning(self, "深海动作截图", "框选区域无效，请重新框选。")
            return

        crop = self.current_frame.copy(bounded)
        click_offset = [bounded.width() // 2, bounded.height() // 2]
        if bool(config.get("manual_click_offset")):
            click_dialog = TemplateClickPointDialog(
                crop,
                title=f"点击点：{actor_ref.label} - {action_name} - {step_label}",
                initial_point=click_offset,
                parent=self,
            )
            if click_dialog.exec() != QDialog.DialogCode.Accepted or click_dialog.selected_point is None:
                return
            click_offset = [int(click_dialog.selected_point.x()), int(click_dialog.selected_point.y())]

        user_name = f"深海_{actor_ref.label}_{action_name}_{step_label}"
        paths = self.storage.make_asset_paths(self.current_map, "button", user_name)
        raw_path = Path(paths["raw_path"])
        crop_path = Path(paths["crop_path"])
        annotated_path = Path(paths["annotated_path"])

        annotated = self.current_frame.copy()
        painter = QPainter(annotated)
        painter.setPen(QPen(QColor("#00a5ff"), 3))
        painter.drawRect(bounded)
        painter.end()

        self.current_frame.save(str(raw_path))
        crop.save(str(crop_path))
        annotated.save(str(annotated_path))

        bbox = bbox_from_rect(bounded)
        asset_id = str(paths["asset_id"])
        metadata = {
            "created_from": "deepsea_action_capture",
            "actor_type": actor_ref.actor_type,
            "actor_index": actor_ref.index,
            "actor_label": actor_ref.label,
            "action_key": action_key,
            "action_name": action_name,
            "step_label": step_label,
            "click_offset": click_offset,
        }
        self.storage.add_asset(
            asset_id=asset_id,
            user_name=user_name,
            auto_name=str(paths["auto_name"]),
            asset_type="button",
            map_id=self.current_map,
            script_name=self.flow["script_name"],
            step_id=None,
            bbox=bbox,
            raw_path=raw_path,
            crop_path=crop_path,
            annotated_path=annotated_path,
            metadata=metadata,
        )
        self.deepsea_action_library.add_step(
            actor=actor_ref,
            action=action_key,
            step_label=step_label,
            template_path=self.storage.rel(crop_path),
            asset_id=asset_id,
            bbox=bbox,
            click_offset=click_offset,
            threshold=0.85,
            wait_after=0.4,
        )
        self.deepsea_action_library.save()
        self._deepsea_last_actor = actor_text.strip()
        self._deepsea_last_action = action_text.strip()

        tapped = False
        if bool(config.get("tap_after_capture")):
            tap_x = int(bounded.x() + click_offset[0])
            tap_y = int(bounded.y() + click_offset[1])
            try:
                tapped = self.tap_repeated(tap_x, tap_y, 1, 0.08, label="深海采集后点击")
            except AdbError as exc:
                self.log(f"深海采集已保存，但点击失败：{exc}")
                QMessageBox.warning(self, "深海动作截图", f"素材已保存，但点击失败：{exc}")

        missing_count = len(self.deepsea_action_library.missing_required_actions())
        self.log(
            f"已采集深海动作：{actor_ref.label} / {action_name} / {step_label}，"
            f"模板 {self.storage.rel(crop_path)}，点击偏移 {click_offset}。"
        )
        self.append_result(
            "深海动作截图\n"
            f"action: {actor_ref.label} / {action_name}\n"
            f"step: {step_label}\n"
            f"asset: {asset_id}\n"
            f"bbox: {bbox}\n"
            f"click_offset: {click_offset}\n"
            f"tap_after_capture: {tapped}\n"
            f"missing_required_actions: {missing_count}\n"
        )

    def capture_verify_code_region(self, title: str, hint: str) -> list[int] | None:
        if self.current_frame is None:
            if not self.capture_current_screen(save=False):
                QMessageBox.information(self, title, "请先连接 MuMu，并确认 ADB 可以截图。")
                return None
        dialog = LargeRegionDialog(
            self.current_frame,
            title=title,
            fixed_kind="screenshot",
            hint=hint,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return None
        bounded = dialog.selected_rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        if bounded.width() <= 0 or bounded.height() <= 0:
            return None
        return bbox_from_rect(bounded)

    def add_verify_digit_samples_from_capture(self) -> None:
        if not self.capture_current_screen(save=False) or self.current_frame is None:
            QMessageBox.information(self, "录入验证码数字", "请先连接 MuMu，并确认 ADB 可以截图。")
            return
        digit_bbox = self.capture_verify_code_region(
            "录入验证码数字",
            "框选验证码数字本身，例如 5578。只框数字，不要框输入框、按钮或标题。",
        )
        if digit_bbox is None:
            return
        raw_text, ok = QInputDialog.getText(
            self,
            "录入验证码数字",
            "请输入这张图真实验证码。程序会拆成单个数字，只补齐缺失的 0-9 样本。",
        )
        if not ok:
            return
        code = clean_verification_digits(raw_text)
        if not code:
            QMessageBox.warning(self, "录入验证码数字", "没有读到有效数字，请输入 0-9 组成的验证码。")
            return

        rect = rect_from_bbox(digit_bbox)
        if rect is None:
            return
        bounded = rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        if bounded.width() <= 0 or bounded.height() <= 0:
            QMessageBox.warning(self, "录入验证码数字", "框选区域无效。")
            return

        digit_image = self.current_frame.copy(bounded)
        rects = digit_component_rects(digit_image, len(code))
        if len(rects) != len(code):
            QMessageBox.warning(
                self,
                "录入验证码数字",
                f"拆分数字失败：需要 {len(code)} 位，只拆到 {len(rects)} 位。请重新框紧数字区域。",
            )
            return

        cleanup = self.storage.cleanup_invalid_ocr_digit_library()
        counts = self.storage.ocr_digit_counts()
        seed_dir = self.storage.root / "assets" / "ocr" / "digits" / "seed"
        seed_dir.mkdir(parents=True, exist_ok=True)
        added: list[str] = []
        skipped: list[str] = []
        timestamp = int(time.time() * 1000)
        for index, digit in enumerate(code):
            if counts.get(digit, 0) > 0:
                skipped.append(digit)
                continue
            _, tight_rect = rects[index]
            crop = digit_image.copy(tight_rect)
            crop_path = seed_dir / f"verify_digit_seed_{timestamp}_{index + 1}_{digit}.png"
            crop.save(str(crop_path))
            stored = self.storage.add_public_ocr_sample(
                kind="digit",
                value=digit,
                image_path=crop_path,
                map_id=self.current_map,
                source_ui="verify_digit_seed",
                confidence=1.0,
            )
            if stored:
                added.append(digit)
                counts[digit] = counts.get(digit, 0) + 1

        counts_after = self.storage.ocr_digit_counts()
        missing = [digit for digit in "0123456789" if counts_after.get(digit, 0) <= 0]
        counts_text = " ".join(f"{digit}:{counts_after.get(digit, 0)}" for digit in "0123456789")
        removed_dirs = cleanup.get("removed_dirs") or []
        cleanup_bits: list[str] = []
        if cleanup.get("deleted_rows"):
            cleanup_bits.append(f"删非法记录 {cleanup['deleted_rows']}")
        if cleanup.get("deduped_rows"):
            cleanup_bits.append(f"去重 {cleanup['deduped_rows']}")
        if removed_dirs:
            cleanup_bits.append(f"清理旧目录 {len(removed_dirs)}")

        self.log(
            "验证码数字库："
            f"新增 {''.join(added) or '无'}；"
            f"跳过已有 {''.join(skipped) or '无'}；"
            f"当前 {counts_text}；"
            f"缺失 {''.join(missing) or '无'}。"
        )
        if cleanup_bits:
            self.log(f"验证码数字库清理：{'，'.join(cleanup_bits)}。")

        QMessageBox.information(
            self,
            "录入验证码数字",
            "\n".join(
                [
                    f"新增：{''.join(added) or '无'}",
                    f"跳过已有：{''.join(skipped) or '无'}",
                    f"缺失：{''.join(missing) or '无'}",
                    f"当前计数：{counts_text}",
                ]
            ),
        )

    def add_verify_code_step_from_capture(self) -> None:
        if not self.capture_current_screen(save=False) or self.current_frame is None:
            QMessageBox.information(self, "插入验证码", "请先连接 MuMu，并确认 ADB 可以截图。")
            return
        digit_bbox = self.capture_verify_code_region(
            "验证码数字区域",
            "第一步：只框选验证码数字本身，例如 1714，不要把输入框和按钮框进去。",
        )
        if digit_bbox is None:
            return
        input_bbox = self.capture_verify_code_region(
            "验证码输入框",
            "第二步：框选输入框，脚本会点击这个区域中心后自动输入数字。",
        )
        if input_bbox is None:
            return
        confirm_bbox = self.capture_verify_code_region(
            "验证码确定按钮",
            "第三步：框选“确定”按钮，脚本输入完成后会点击这个区域中心。",
        )
        if confirm_bbox is None:
            return

        self.sync_steps_from_list()
        step = create_step("verify_code", self.default_step_name("verify_code", "captcha"))
        step["input"]["digit_bbox"] = digit_bbox
        step["input"]["input_coord"] = list(bbox_center(input_bbox))
        step["input"]["confirm_coord"] = list(bbox_center(confirm_bbox))
        placement = self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        self.log(
            f"已插入验证码{where}：数字区域 {digit_bbox}，输入点 {step['input']['input_coord']}，确定点 {step['input']['confirm_coord']}。"
        )

    def open_coord_region_dialog(self) -> None:
        if not self.capture_current_screen(save=False) or self.current_frame is None:
            QMessageBox.information(self, "游戏坐标区域", "请先连接 MuMu，并确认 ADB 可以截图。")
            return
        dialog = LargeRegionDialog(
            self.current_frame,
            title="设置游戏坐标区域",
            fixed_kind="coord",
            hint="框选右上角固定的游戏地图坐标，例如 (20,24)。以后所有移动步骤都会自动读取这里。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return
        self.create_asset_from_region("coord", dialog.selected_rect)

    def focus_region(self, rect: QRect) -> None:
        if not hasattr(self, "game_view") or not hasattr(self, "game_scroll_area"):
            return
        self.game_view.set_focus_rect(rect)
        if self.zoom_combo.currentText() == "适应":
            self.game_scroll_area.setWidgetResizable(True)
        else:
            self.game_view.resize(self.game_view.sizeHint())
        self.log(f"已放大区域：{bbox_from_rect(rect)}。现在框选会继续保存原图坐标。")

    def clear_focus_region(self) -> None:
        if not hasattr(self, "game_view") or not hasattr(self, "game_scroll_area"):
            return
        self.game_view.clear_focus_rect()
        if self.zoom_combo.currentText() != "适应":
            self.game_view.resize(self.game_view.sizeHint())
        self.log("已返回全图。")

    def create_asset_from_region(self, kind: str, rect: QRect) -> None:
        if self.current_frame is None:
            return
        self.sync_steps_from_list()

        if kind == "question_progress":
            self.apply_question_step_region("progress_region", rect, "答题进度区域")
            return
        if kind == "question_confirm":
            self.apply_question_step_region("confirm_region", rect, "答题确定按钮区域")
            return

        user_name = ""
        if self.immediate_name.isChecked():
            user_name, ok = QInputDialog.getText(self, "素材命名", "请为这个素材命名")
            if not ok:
                user_name = ""

        step_type = self.step_type_for_asset_kind(kind)
        step = None if step_type is None else create_step(step_type, self.default_step_name(step_type, kind))
        step_id = step["id"] if step else None
        paths = self.storage.make_asset_paths(self.current_map, kind, user_name or None)
        raw_path = Path(paths["raw_path"])
        crop_path = Path(paths["crop_path"])
        annotated_path = Path(paths["annotated_path"])

        bounded = rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        crop = self.current_frame.copy(bounded)
        annotated = self.current_frame.copy()
        painter = QPainter(annotated)
        painter.setPen(QPen(QColor("#ff375f"), 3))
        painter.drawRect(bounded)
        painter.end()

        self.current_frame.save(str(raw_path))
        crop.save(str(crop_path))
        annotated.save(str(annotated_path))

        bbox = [bounded.x(), bounded.y(), bounded.width(), bounded.height()]
        asset_id = str(paths["asset_id"])
        self.storage.add_asset(
            asset_id=asset_id,
            user_name=user_name,
            auto_name=str(paths["auto_name"]),
            asset_type=kind,
            map_id=self.current_map,
            script_name=self.flow["script_name"],
            step_id=step_id,
            bbox=bbox,
            raw_path=raw_path,
            crop_path=crop_path,
            annotated_path=annotated_path,
            metadata={"created_from": "screen_operation"},
        )

        if kind == "coord":
            self.flow.setdefault("settings", {})["game_coord_region"] = bbox
            self.update_coord_region_label()
            self.current_game_coord = self.read_game_coord_from_region(bbox, "global_coord_region")
            self.quick_coord_label.setText(
                f"当前坐标：{self.current_game_coord[0]}, {self.current_game_coord[1]}"
                if self.current_game_coord
                else "当前坐标：未读取"
            )
            self.log(f"已设置固定游戏坐标区域：{bbox}")

        if step:
            step["assets"].append(asset_id)
            step["input"]["asset_id"] = asset_id
            step["input"]["bbox"] = bbox
            if step["type"] in {"image_check", "find_target", "click_target"}:
                step["input"]["template_path"] = self.storage.rel(crop_path)
                step["input"]["wait_until_found"] = True
                step["input"]["wait_after_found"] = 0.5
            if step["type"] in {"ocr_text", "ocr_number"}:
                mode = "digit" if step["type"] == "ocr_number" else "text"
                result = self.ocr.recognize(crop_path, mode=mode)
                step["input"]["last_result"] = result.text
                step["input"]["confidence"] = result.confidence
                step["output"] = {
                    "text": result.text,
                    "confidence": result.confidence,
                    "backend": result.backend,
                    "available": result.available,
                }
                if result.text and result.confidence >= 0.65:
                    if mode == "digit" and is_single_digit_value(result.text):
                        self.storage.insert_ocr_digit(
                            value=result.text,
                            image_path=crop_path,
                            source_asset_id=asset_id,
                            map_id=self.current_map,
                            source_ui=kind,
                        )
                    elif mode == "text":
                        self.storage.insert_ocr_text(
                            value=result.text,
                            image_path=crop_path,
                            source_asset_id=asset_id,
                            map_id=self.current_map,
                            source_ui=kind,
                        )
                else:
                    self.storage.add_pending_review(
                        mode,
                        asset_id,
                        crop_path,
                        {
                            "map_id": self.current_map,
                            "source_ui": kind,
                            "ocr_backend": result.backend,
                            "ocr_error": result.error,
                        },
                    )
            placement = self.place_step_in_context(step, bool(self.current_step()))
            self.refresh_step_list(select_step_id=step["id"])
            where = "循环子步骤" if placement == "loop" else "步骤"
            self.log(f"已从当前画面插入{where}：{step['name']}")

        self.log(f"已保存素材 {paths['auto_name']}：raw/crop/annotated，并写入素材库。")
        self.append_result(f"素材: {asset_id}\n类型: {kind}\nbbox: {bbox}\ncrop: {self.storage.rel(crop_path)}\n")

    def apply_question_step_region(self, input_key: str, rect: QRect, label: str) -> None:
        step = self.current_step()
        if step is None or step.get("type") != "question":
            step = create_step("question", self.default_step_name("question", "auto"))
            self.place_step_in_context(step, bool(self.current_step()))
        step["input"][input_key] = bbox_from_rect(rect)
        if input_key == "progress_region":
            self.storage.update_latest_question_layout(progress_bbox=step["input"][input_key])
        elif input_key == "confirm_region":
            self.storage.update_latest_question_layout(confirm_bbox=step["input"][input_key])
        self.refresh_step_list(select_step_id=step["id"])
        self.log(f"已设置 {step['name']} 的{label}：{step['input'][input_key]}，并同步到最近题库布局。")

    def scan_screen_ocr_library(self) -> None:
        if not self.refresh_current_frame() or self.current_frame is None:
            return
        timestamp = int(time.time() * 1000)
        scan_dir = self.storage.ocr_scan_dir()
        raw_path = scan_dir / f"scan_{timestamp}_raw.png"
        self.current_frame.save(str(raw_path))
        regions = self.ocr.detect_regions(raw_path, mode="text")
        if not regions:
            self.log("扫描文字数字：未检测到文字或数字。")
            return

        confirmed = 0
        pending = 0
        for index, region in enumerate(regions, start=1):
            text = region.text.strip()
            kind = "digit" if is_single_digit_value(text) else "text"
            bbox = list(region.bbox)
            rect = rect_from_bbox(bbox)
            if rect is None or rect.width() < 2 or rect.height() < 2:
                continue
            crop = self.current_frame.copy(rect)
            if text and region.confidence >= 0.70:
                crop_path = scan_dir / f"scan_{timestamp}_{kind}_{index:03d}.png"
                crop.save(str(crop_path))
                self.storage.add_public_ocr_sample(
                    kind=kind,
                    value=text,
                    image_path=crop_path,
                    map_id=self.current_map,
                    source_ui="screen_scan",
                    confidence=region.confidence,
                )
                crop_path.unlink(missing_ok=True)
                confirmed += 1
            else:
                pending_dir = self.storage.public_ocr_dir(kind, "unknown", "pending_review")
                crop_path = pending_dir / f"{self.current_map}_{kind}_{index:03d}_{timestamp}.png"
                crop.save(str(crop_path))
                self.storage.add_pending_review(
                    kind,
                    None,
                    crop_path,
                    {
                        "map_id": self.current_map,
                        "source_ui": "screen_scan",
                        "bbox": bbox,
                        "ocr_text": text,
                        "confidence": region.confidence,
                        "backend": region.backend,
                    },
                )
                pending += 1
        self.log(f"扫描文字数字完成：入公共库 {confirmed} 条，pending_review {pending} 条。")

    def start_question_capture(self) -> None:
        if not self.capture_current_screen(save=False, allow_cached=False) or self.current_frame is None:
            QMessageBox.information(self, "添加题库", "请先连接 MuMu，并确认 ADB 可以截到当前画面。")
            return
        frame = self.current_frame.copy()
        existing = self.find_existing_question_with_latest_layout(frame)
        if existing:
            self.show_existing_question_message(existing)
            self.log(f"题库已存在：{existing['id']}，未进入框选。")
            return
        self.log("开始添加题库：请依次框选题目区域和四个选项。")
        regions = self.capture_question_regions(frame, title="添加题库", stop_on_duplicate=True)
        if not regions:
            return
        self.save_question_from_regions(
            frame,
            regions,
            window_title="添加题库",
            duplicate_mode="stop",
        )

    def find_existing_question_with_latest_layout(self, frame: QImage) -> Any | None:
        row = self.storage.latest_question_layout()
        if not row:
            return None
        try:
            question_bbox = json.loads(row["question_bbox"] or "null")
            option_bboxes = json.loads(row["option_bboxes"] or "[]")
        except json.JSONDecodeError:
            return None
        if not question_bbox:
            return None
        question_dir = self.storage.questions_dir("pending_review")
        timestamp = int(time.time() * 1000)
        question_text = self.ocr_bbox_to_text(frame, question_bbox, question_dir / f"quick_check_q_{timestamp}.png", "question")
        option_texts = []
        for index, bbox in enumerate((option_bboxes or [])[:4]):
            option_texts.append(
                self.ocr_bbox_to_text(frame, bbox, question_dir / f"quick_check_option_{index + 1}_{timestamp}.png", "option")
            )
        self.log(f"题库快速检查：{question_text or '(空)'}")
        return self.find_existing_question_from_capture(frame, question_bbox, question_text, option_texts)

    def open_question_capture_dialog(self) -> None:
        if self.question_capture is None:
            return
        frame = self.question_capture.get("frame")
        if frame is None or frame.isNull():
            return
        stage = self.question_capture["stage"]
        label = self.question_capture_label(stage)
        dialog = LargeRegionDialog(
            frame,
            title=f"添加题库 - {label}",
            fixed_kind="screenshot",
            hint=f"请框选{label}。窗口底部会显示原始截图坐标。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            self.question_capture = None
            self.status_label.setText(f"MuMu {self.mumu_endpoint}")
            self.log("题库录入已取消。")
            return
        self.handle_question_capture_region(dialog.selected_rect)

    def handle_question_capture_region(self, rect: QRect) -> None:
        if not self.question_capture:
            return
        stage = self.question_capture["stage"]
        regions = self.question_capture["regions"]
        regions[stage] = bbox_from_rect(rect)

        next_stage = {
            "question": "option_a",
            "option_a": "option_b",
            "option_b": "option_c",
            "option_c": "option_d",
        }.get(stage)

        if next_stage:
            self.question_capture["stage"] = next_stage
            label = {
                "option_a": "选项 A",
                "option_b": "选项 B",
                "option_c": "选项 C",
                "option_d": "选项 D",
            }[next_stage]
            self.status_label.setText(f"题库录入：{label}")
            self.log(f"已记录 {self.question_capture_label(stage)}，请框选{label}。")
            QTimer.singleShot(80, self.open_question_capture_dialog)
            return

        self.finish_question_capture()

    def question_capture_label(self, stage: str) -> str:
        return {
            "question": "题目区域",
            "option_a": "选项 A",
            "option_b": "选项 B",
            "option_c": "选项 C",
            "option_d": "选项 D",
            "confirm": "确定按钮",
        }.get(stage, stage)

    def finish_question_capture(self) -> None:
        capture = self.question_capture
        self.question_capture = None
        self.status_label.setText(f"MuMu {self.mumu_endpoint}")
        if not capture:
            return

        frame: QImage = capture["frame"]
        regions: dict[str, list[int]] = capture["regions"]
        option_bboxes = [regions[key] for key in ("option_a", "option_b", "option_c", "option_d") if key in regions]
        if "question" not in regions or len(option_bboxes) != 4:
            self.log("题库录入取消：题目或四个选项区域不完整。")
            return
        self.save_question_from_regions(
            frame,
            regions,
            window_title="添加题库",
            duplicate_mode="stop",
        )

    def capture_question_regions(
        self,
        frame: QImage,
        *,
        title: str,
        stop_on_duplicate: bool,
    ) -> dict[str, list[int]] | None:
        regions: dict[str, list[int]] = {}
        stages = [
            ("question", "题目区域"),
            ("option_a", "选项 A"),
            ("option_b", "选项 B"),
            ("option_c", "选项 C"),
            ("option_d", "选项 D"),
        ]
        for stage, label in stages:
            dialog = LargeRegionDialog(
                frame,
                title=f"{title} - {label}",
                fixed_kind="screenshot",
                hint=f"请框选{label}。窗口底部会显示原始截图坐标。",
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
                self.log(f"{title}已取消。")
                return None
            regions[stage] = bbox_from_rect(dialog.selected_rect)

            if stage == "question" and stop_on_duplicate:
                question_dir = self.storage.questions_dir("pending_review")
                check_path = question_dir / f"duplicate_check_{int(time.time() * 1000)}.png"
                question_text = self.ocr_bbox_to_text(frame, regions["question"], check_path, "question")
                self.log(f"题目OCR查重：{question_text or '(空)'}")
                existing = self.find_existing_question_from_capture(frame, regions["question"], question_text)
                if existing:
                    self.show_existing_question_message(existing)
                    self.log(f"题库已存在：{existing['id']}，本次录入停止。")
                    return None

            self.log(f"已记录 {label}。")
        return regions

    def save_question_from_regions(
        self,
        frame: QImage,
        regions: dict[str, list[int]],
        *,
        window_title: str,
        duplicate_mode: str,
        confirm_region: list[int] | None = None,
        progress_region: list[int] | None = None,
    ) -> dict[str, Any] | None:
        option_bboxes = [regions[key] for key in ("option_a", "option_b", "option_c", "option_d") if key in regions]
        if "question" not in regions or len(option_bboxes) != 4:
            self.log("题库录入取消：题目或四个选项区域不完整。")
            return None

        timestamp = int(time.time())
        question_dir = self.storage.questions_dir("confirmed")
        raw_path = question_dir / f"question_{timestamp}_raw.png"
        annotated_path = question_dir / f"question_{timestamp}_annotated.png"
        frame.save(str(raw_path))

        annotated = frame.copy()
        painter = QPainter(annotated)
        colors = ["#ff375f", "#35d0a5", "#4f8cff", "#ffb020", "#9b5cff", "#00a6a6"]
        for index, (label, bbox) in enumerate(regions.items()):
            rect = rect_from_bbox(bbox)
            if not rect:
                continue
            painter.setPen(QPen(QColor(colors[index % len(colors)]), 3))
            painter.drawRect(rect)
            painter.drawText(rect.topLeft() + QPoint(4, 18), self.question_capture_label(label))
        painter.end()
        annotated.save(str(annotated_path))

        question_text = self.ocr_bbox_to_text(frame, regions["question"], question_dir / f"question_{timestamp}_q.png", "question")
        option_texts = []
        for index, bbox in enumerate(option_bboxes):
            option_texts.append(
                self.ocr_bbox_to_text(frame, bbox, question_dir / f"question_{timestamp}_option_{index + 1}.png", "option")
            )

        existing = self.find_existing_question_from_capture(frame, regions["question"], question_text, option_texts)
        if existing:
            self.show_existing_question_message(existing)
            self.log(f"题库已存在：{existing['id']}，不重复保存。")
            if duplicate_mode == "return":
                return {
                    "question_id": existing["id"],
                    "answer": existing["answer"],
                    "option_texts": option_texts,
                    "option_regions": option_bboxes,
                    "existing": True,
                }
            return None

        dialog = QuestionEntryDialog(question_text=question_text, option_texts=option_texts, parent=self)
        dialog.setWindowTitle(window_title)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.log("题库录入已取消。")
            return None

        values = dialog.values()
        if not values["question"]:
            QMessageBox.warning(self, "添加题库", "题目不能为空。")
            return None
        existing = self.storage.find_existing_question(values["question"], values["options"], threshold=0.78)
        if existing:
            self.show_existing_question_message(existing)
            self.log(f"题库已存在：{existing['id']}，不重复保存。")
            if duplicate_mode == "return":
                return {
                    "question_id": existing["id"],
                    "answer": existing["answer"],
                    "option_texts": values["options"],
                    "option_regions": option_bboxes,
                    "existing": True,
                }
            return None

        question_id = self.storage.add_question(
            question=values["question"],
            answer=values["answer"],
            options=values["options"],
            map_id=self.current_map,
            question_bbox=regions["question"],
            option_bboxes=option_bboxes,
            confirm_bbox=confirm_region,
            progress_bbox=progress_region,
            raw_path=raw_path,
            annotated_path=annotated_path,
        )
        self.log(f"题库已保存：{question_id}")
        self.log("题库录入只写入题库，不会自动加入流程。需要流程答题时请点“添加答题步骤”。")
        return {
            "question_id": question_id,
            "answer": values["answer"],
            "option_texts": values["options"],
            "option_regions": option_bboxes,
            "existing": False,
        }

    def show_existing_question_message(self, row: Any) -> None:
        QMessageBox.information(
            self,
            "题库已存在",
            f"这道题已经在题库里了：\n{row['question']}\n\n答案：{row['answer']}",
        )

    def find_existing_question_from_capture(
        self,
        frame: QImage,
        question_bbox: list[int],
        question_text: str,
        option_texts: list[str] | None = None,
    ) -> Any | None:
        normalized_question = normalize_text(question_text)
        if normalized_question:
            threshold = 0.78 if option_texts else 0.985
            row = self.storage.find_existing_question(question_text, option_texts, threshold=threshold)
            if row:
                self.log(f"题库文字查重命中：{row['id']}")
                return row

        current_rect = rect_from_bbox(question_bbox)
        if current_rect is None:
            return None
        current_crop = frame.copy(current_rect)
        best: tuple[float, Any, float] | None = None
        for row in self.storage.list_questions():
            raw_path = self.storage.abs(row["raw_path"])
            if raw_path is None or not raw_path.exists():
                continue
            try:
                stored_bbox = json.loads(row["question_bbox"] or "null")
            except json.JSONDecodeError:
                stored_bbox = None
            stored_rect = rect_from_bbox(stored_bbox)
            if stored_rect is None:
                continue
            stored_image = QImage(str(raw_path))
            if stored_image.isNull():
                continue
            visual_score = self.question_crop_similarity(current_crop, stored_image.copy(stored_rect))
            if visual_score is None:
                continue
            stored_question = normalize_text(row["question"])
            text_score = SequenceMatcher(None, normalized_question, stored_question).ratio() if normalized_question and stored_question else 0.0
            if normalized_question:
                confident = visual_score >= 0.94 and text_score >= 0.90
            else:
                confident = visual_score >= 0.995
            if confident and (best is None or (visual_score, text_score) > (best[0], best[2])):
                best = (visual_score, row, text_score)
        if best:
            self.log(f"题库截图查重命中：{best[1]['id']}，视觉 {best[0]:.3f}，文字 {best[2]:.3f}")
            return best[1]
        return None

    def ocr_bbox_to_text(self, frame: QImage, bbox: list[int], path: Path, mode: str) -> str:
        rect = rect_from_bbox(bbox)
        if rect is None:
            return ""
        crop = frame.copy(rect)
        crop.save(str(path))
        result = self.ocr.recognize(path, mode=mode)
        return result.text

    def step_type_for_asset_kind(self, kind: str) -> str | None:
        return {
            "image": "image_check",
            "digit": "ocr_number",
            "text": "ocr_text",
            "npc": "click_target",
            "target": "click_target",
            "button": "click_target",
            "transition": "image_check",
            "battle": "image_check",
            "question": "ocr_text",
            "question_option": "ocr_text",
            "coord": None,
            "screenshot": None,
        }.get(kind, "image_check")

    def default_step_name(self, step_type: str, kind: str) -> str:
        count = len(self.flow["steps"]) + 1
        readable = STEP_LABELS.get(step_type, step_type)
        return f"{readable}_{kind}_{count:03d}"

    def insert_step_from_asset(self, asset: Any) -> None:
        kind = str(asset["type"])
        step_type = self.step_type_for_asset_kind(kind)
        if step_type is None:
            if kind == "coord":
                try:
                    bbox = json.loads(asset["bbox"] or "null")
                except json.JSONDecodeError:
                    bbox = None
                if bbox:
                    self.flow.setdefault("settings", {})["game_coord_region"] = bbox
                    self.update_coord_region_label()
                    self.log(f"已复用素材设置固定游戏坐标区域：{bbox}")
                return
            QMessageBox.information(self, "复用素材", "普通截图素材不能直接插入为流程步骤。")
            return
        name_source = asset["user_name"] or asset["auto_name"] or kind
        step = create_step(step_type, self.default_step_name(step_type, kind))
        step["name"] = f"{STEP_LABELS.get(step_type, step_type)}_{name_source}"
        step["assets"].append(asset["id"])
        step["input"]["asset_id"] = asset["id"]
        try:
            step["input"]["bbox"] = json.loads(asset["bbox"] or "null")
        except json.JSONDecodeError:
            step["input"]["bbox"] = None
        if step_type in {"image_check", "find_target", "click_target"}:
            step["input"]["template_path"] = asset["crop_path"]
            step["input"]["wait_until_found"] = True
            step["input"]["wait_after_found"] = 0.5
        elif step_type in {"ocr_text", "ocr_number"}:
            step["input"]["bbox"] = step["input"].get("bbox")
        placement = self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        self.log(f"已复用素材插入{where}：{step['name']}")

    def refresh_step_list(self, select_step_id: str | None = None) -> None:
        self.step_list.blockSignals(True)
        self.step_list.clear()
        self.add_steps_to_list(self.flow["steps"], "", depth=0, select_step_id=select_step_id)
        self.step_list.blockSignals(False)
        if select_step_id:
            if self.step_list.currentItem():
                self.step_list.scrollToItem(
                    self.step_list.currentItem(),
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )
            self.populate_properties(self.step_by_id(select_step_id))
        elif self.step_list.currentItem():
            self.populate_properties(self.current_step())
        self.update_dirty_indicator()

    def add_steps_to_list(
        self,
        steps: list[dict[str, Any]],
        prefix: str,
        *,
        depth: int,
        select_step_id: str | None,
    ) -> None:
        for index, step in enumerate(steps, start=1):
            display_index = f"{prefix}.{index}" if prefix else f"{index:02d}"
            self.add_step_list_item(
                step,
                index=index,
                display_index=display_index,
                depth=depth,
                kind="top" if depth == 0 else "child",
                select_step_id=select_step_id,
            )
            children = step.get("children") or []
            if children:
                self.add_steps_to_list(children, display_index, depth=depth + 1, select_step_id=select_step_id)

    def select_step_by_id(self, step_id: str) -> bool:
        for row in range(self.step_list.count()):
            item = self.step_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == step_id:
                self.step_list.setCurrentItem(item)
                return True
        return False

    def add_step_list_item(
        self,
        step: dict[str, Any],
        *,
        index: int,
        display_index: str,
        depth: int,
        kind: str,
        select_step_id: str | None,
    ) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, step["id"])
        item.setData(STEP_LIST_KIND_ROLE, kind)
        item.setData(STEP_LIST_DEPTH_ROLE, depth)
        item.setSizeHint(QSize(300, 62 if depth else 66))
        if depth:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled & ~Qt.ItemFlag.ItemIsDropEnabled)
        self.step_list.addItem(item)
        card = StepCard(index, step, depth=depth, display_index=display_index)
        card.toggled.connect(self.set_step_enabled)
        self.step_list.setItemWidget(item, card)
        if step["id"] == select_step_id:
            self.step_list.setCurrentItem(item)

    def on_step_rows_moved(self) -> None:
        current = self.current_step()
        select_step_id = current["id"] if current else None
        self.sync_steps_from_list()
        self.refresh_step_list(select_step_id=select_step_id)

    def sync_steps_from_list(self) -> None:
        if self.step_list.count() == 0:
            return
        by_id = {step["id"]: step for step in self.flow["steps"]}
        ordered = []
        for row in range(self.step_list.count()):
            item = self.step_list.item(row)
            if item.data(STEP_LIST_KIND_ROLE) != "top":
                continue
            step_id = item.data(Qt.ItemDataRole.UserRole)
            if step_id in by_id:
                ordered.append(by_id[step_id])
        if len(ordered) == len(self.flow["steps"]):
            self.flow["steps"] = ordered

    def current_step(self) -> dict[str, Any] | None:
        item = self.step_list.currentItem()
        if not item:
            return None
        return self.step_by_id(item.data(Qt.ItemDataRole.UserRole))

    def selected_steps(self) -> list[dict[str, Any]]:
        self.sync_steps_from_list()
        ids = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.step_list.selectedItems()
            if item.data(STEP_LIST_KIND_ROLE) == "top"
        }
        return [step for step in self.flow["steps"] if step["id"] in ids]

    def selected_step_ids_all(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for item in self.step_list.selectedItems():
            step_id = str(item.data(Qt.ItemDataRole.UserRole))
            if step_id and step_id not in seen:
                ids.append(step_id)
                seen.add(step_id)
        return ids

    def selected_steps_all(self) -> list[dict[str, Any]]:
        return [step for step_id in self.selected_step_ids_all() if (step := self.step_by_id(step_id))]

    def selected_step_indices(self) -> list[int]:
        self.sync_steps_from_list()
        ids = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.step_list.selectedItems()
            if item.data(STEP_LIST_KIND_ROLE) == "top"
        }
        return [index for index, step in enumerate(self.flow["steps"]) if step["id"] in ids]

    def selected_step_locations(
        self,
    ) -> list[tuple[str, list[dict[str, Any]], int, dict[str, Any] | None]]:
        self.sync_steps_from_list()
        locations: list[tuple[str, list[dict[str, Any]], int, dict[str, Any] | None]] = []
        seen: set[str] = set()
        for item in self.step_list.selectedItems():
            step_id = str(item.data(Qt.ItemDataRole.UserRole))
            if step_id in seen:
                continue
            location = self.step_location(step_id)
            if not location:
                continue
            container, index, parent = location
            locations.append((step_id, container, index, parent))
            seen.add(step_id)
        locations.sort(key=lambda item: item[2])
        return locations

    def selected_step_locations_collapsed(
        self,
    ) -> list[tuple[str, list[dict[str, Any]], int, dict[str, Any] | None]]:
        selected_ids = set(self.selected_step_ids_all())
        locations: list[tuple[str, list[dict[str, Any]], int, dict[str, Any] | None]] = []
        for step_id in selected_ids:
            if self.step_has_selected_ancestor(step_id, selected_ids):
                continue
            location = self.step_location(step_id)
            if location:
                container, index, parent = location
                locations.append((step_id, container, index, parent))
        locations.sort(key=lambda item: item[2])
        return locations

    def step_has_selected_ancestor(self, step_id: str, selected_ids: set[str]) -> bool:
        location = self.step_location(step_id)
        if not location:
            return False
        parent = location[2]
        while parent is not None:
            parent_id = str(parent.get("id") or "")
            if parent_id in selected_ids:
                return True
            parent_location = self.step_location(parent_id)
            parent = parent_location[2] if parent_location else None
        return False

    def selected_locations_or_current(
        self,
        *,
        collapse_descendants: bool = False,
    ) -> list[tuple[str, list[dict[str, Any]], int, dict[str, Any] | None]]:
        locations = self.selected_step_locations_collapsed() if collapse_descendants else self.selected_step_locations()
        if locations:
            return locations
        current = self.current_step()
        if current:
            location = self.step_location(current["id"])
            if location:
                container, index, parent = location
                return [(current["id"], container, index, parent)]
        return []

    def select_step_ids(self, step_ids: list[str], current_id: str | None = None) -> None:
        wanted = set(step_ids)
        for row in range(self.step_list.count()):
            item = self.step_list.item(row)
            step_id = str(item.data(Qt.ItemDataRole.UserRole))
            item.setSelected(step_id in wanted)
            if current_id and step_id == current_id:
                self.step_list.setCurrentItem(item)

    def step_by_id(self, step_id: str) -> dict[str, Any] | None:
        return self.find_step_by_id(self.flow["steps"], step_id)

    def find_step_by_id(self, steps: list[dict[str, Any]], step_id: str) -> dict[str, Any] | None:
        for step in steps:
            if step["id"] == step_id:
                return step
            found = self.find_step_by_id(step.get("children") or [], step_id)
            if found:
                return found
        return None

    def step_location(self, step_id: str) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None] | None:
        return self.find_step_location(self.flow["steps"], step_id, None)

    def find_step_location(
        self,
        steps: list[dict[str, Any]],
        step_id: str,
        parent: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None] | None:
        for index, step in enumerate(steps):
            if step["id"] == step_id:
                return steps, index, parent
            found = self.find_step_location(step.get("children") or [], step_id, step)
            if found:
                return found
        return None

    def top_level_index(self, step_id: str) -> int | None:
        for index, step in enumerate(self.flow["steps"]):
            if step["id"] == step_id:
                return index
        return None

    def refresh_loop_body_metadata(self, loop_step: dict[str, Any]) -> None:
        children = loop_step.get("children") or []
        child_ids = [child["id"] for child in children]
        data = loop_step.setdefault("input", {})
        data["body_step_ids"] = child_ids
        data["body_start_id"] = child_ids[0] if child_ids else None
        data["body_end_id"] = child_ids[-1] if child_ids else None

    def set_step_enabled(self, step_id: str, enabled: bool) -> None:
        step = self.step_by_id(step_id)
        if step:
            step["enabled"] = enabled
            self.update_dirty_indicator()

    def current_step_accepts_direct_children(self, step: dict[str, Any]) -> bool:
        if step.get("type") != "loop":
            return False
        data = step.get("input") or {}
        return bool(data.get("condition_branch"))

    def place_step_in_context(self, step: dict[str, Any], insert_after: bool) -> str:
        current = self.current_step()
        if insert_after and current:
            if self.current_step_accepts_direct_children(current):
                current.setdefault("children", []).append(step)
                self.refresh_loop_body_metadata(current)
                return "loop"
            location = self.step_location(current["id"])
            if location:
                container, index, parent = location
                container.insert(index + 1, step)
                if parent and parent.get("type") == "loop":
                    self.refresh_loop_body_metadata(parent)
                    return "loop"
                return "insert"
        self.flow["steps"].append(step)
        return "append"

    def place_steps_in_context(self, steps: list[dict[str, Any]], insert_after: bool) -> str:
        current = self.current_step()
        if insert_after and current:
            if self.current_step_accepts_direct_children(current):
                current.setdefault("children", []).extend(steps)
                self.refresh_loop_body_metadata(current)
                return "loop"
            location = self.step_location(current["id"])
            if location:
                container, index, parent = location
                container[index + 1 : index + 1] = steps
                if parent and parent.get("type") == "loop":
                    self.refresh_loop_body_metadata(parent)
                    return "loop"
                return "insert"
        self.flow["steps"].extend(steps)
        return "append"

    def add_blank_step(self, step_type: str, insert_after: bool = True) -> None:
        self.sync_steps_from_list()
        count = self.count_steps(self.flow["steps"]) + 1
        step = create_step(step_type, f"{step_type}_{count:03d}")
        if step_type == "question":
            self.apply_latest_question_layout(step, show_message=False)
        placement = self.place_step_in_context(step, insert_after)
        self.refresh_step_list(select_step_id=step["id"])
        if placement == "loop":
            self.log(f"已插入循环子步骤：{step['name']}")
        elif placement == "insert":
            self.log(f"已插入步骤：{step['name']}")
        else:
            self.log(f"已添加步骤：{step['name']}")

    def count_steps(self, steps: list[dict[str, Any]]) -> int:
        total = 0
        for step in steps:
            total += 1
            total += self.count_steps(step.get("children") or [])
        return total

    def add_answer_step_from_bank(self) -> None:
        self.sync_steps_from_list()
        step = create_step("question", self.default_step_name("question", "answer"))
        hydrated = self.apply_latest_question_layout(step, show_message=True)
        placement = self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        if hydrated:
            self.log(f"已添加答题{where}：{step['name']}，并套用最近题库布局。")
        else:
            self.log(f"已添加答题{where}：{step['name']}。还没有题库布局，请先“添加题库”或框选题目/选项区域。")

    def apply_latest_question_layout(self, step: dict[str, Any], show_message: bool = True) -> bool:
        row = self.storage.latest_question_layout()
        if not row:
            if show_message:
                QMessageBox.information(self, "添加答题步骤", "题库里还没有可复用的题目/选项区域。请先点击“添加题库”框选一次布局。")
            return False
        try:
            question_bbox = json.loads(row["question_bbox"] or "null")
            option_bboxes = json.loads(row["option_bboxes"] or "[]")
            confirm_bbox = json.loads(row["confirm_bbox"] or "null") if row["confirm_bbox"] else None
            progress_bbox = json.loads(row["progress_bbox"] or "null") if row["progress_bbox"] else None
        except json.JSONDecodeError:
            if show_message:
                QMessageBox.warning(self, "添加答题步骤", "最近题库布局数据损坏，请重新添加题库。")
            return False
        if not question_bbox or len(option_bboxes) < 4:
            return False
        step["input"]["question_region"] = question_bbox
        step["input"]["option_regions"] = option_bboxes[:4]
        step["input"]["confirm_region"] = confirm_bbox
        step["input"]["progress_region"] = progress_bbox
        return True

    def delete_selected_steps(self) -> None:
        selected_items = self.step_list.selectedItems()
        selected_rows = sorted(self.step_list.row(item) for item in selected_items)
        restore_row = max(0, selected_rows[0] - 1) if selected_rows else 0
        selected = self.selected_steps_all()
        if not selected:
            return
        names = "、".join(step["name"] for step in selected[:3])
        if len(selected) > 3:
            names += "..."
        reply = QMessageBox.question(self, "删除步骤", f"确认删除 {len(selected)} 个步骤？\n{names}")
        if reply != QMessageBox.StandardButton.Yes:
            return
        remove_ids = {step["id"] for step in selected}
        deleted = self.delete_step_ids_from_container(self.flow["steps"], remove_ids)
        self.refresh_step_list()
        if self.step_list.count() > 0:
            restore_row = min(restore_row, self.step_list.count() - 1)
            self.step_list.setCurrentRow(restore_row)
            item = self.step_list.item(restore_row)
            if item:
                self.step_list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.populate_properties(self.current_step())
        else:
            self.populate_properties(None)
        self.log(f"已删除 {deleted} 个步骤。")

    def delete_step_ids_from_container(self, steps: list[dict[str, Any]], remove_ids: set[str]) -> int:
        deleted = 0
        index = 0
        while index < len(steps):
            step = steps[index]
            if step["id"] in remove_ids:
                deleted += self.count_steps([step])
                del steps[index]
                continue
            children = step.get("children")
            if isinstance(children, list) and children:
                deleted += self.delete_step_ids_from_container(children, remove_ids)
                if step.get("type") == "loop":
                    self.refresh_loop_body_metadata(step)
            index += 1
        return deleted

    def copy_selected_steps(self) -> None:
        self.sync_steps_from_list()
        selected = self.selected_steps_all()
        if not selected:
            return
        clones = [clone_step(step) for step in selected]
        placement = self.place_steps_in_context(clones, bool(self.current_step()))
        self.refresh_step_list(select_step_id=clones[-1]["id"])
        where = "循环子步骤" if placement == "loop" else "当前步骤下方"
        self.log(f"已复制 {len(clones)} 个步骤到{where}。")

    def toggle_selected_steps(self) -> None:
        selected = self.selected_steps_all()
        if not selected:
            return
        target = not all(bool(step.get("enabled", True)) for step in selected)
        for step in selected:
            step["enabled"] = target
        self.refresh_step_list(select_step_id=selected[-1]["id"])

    def move_selected_steps(self, direction: int) -> None:
        locations = self.selected_step_locations()
        if not locations:
            QMessageBox.information(self, "移动步骤", "请先选中要移动的步骤。")
            return
        container = locations[0][1]
        parent = locations[0][3]
        if any(id(item[1]) != id(container) for item in locations):
            QMessageBox.information(self, "移动步骤", "请选中同一层级里的步骤。循环外和循环内不能一起移动。")
            return
        indices = [item[2] for item in locations]
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.information(self, "移动步骤", "请选中连续步骤后再移动。")
            return
        current = self.current_step()
        current_id = current["id"] if current else locations[-1][0]
        selected_ids = [item[0] for item in locations]
        start, end = indices[0], indices[-1]
        block = container[start : end + 1]
        action = "上移" if direction < 0 else "下移"

        if direction < 0 and start > 0 and parent is None and container[start - 1].get("type") == "loop":
            target_loop = container[start - 1]
            del container[start : end + 1]
            target_loop.setdefault("children", []).extend(block)
            self.refresh_loop_body_metadata(target_loop)
            self.refresh_step_list(select_step_id=current_id)
            self.select_step_ids(selected_ids, current_id=current_id)
            self.log(f"已把 {len(selected_ids)} 个步骤移入上方循环。")
            return

        if direction > 0 and end < len(container) - 1 and parent is None and container[end + 1].get("type") == "loop":
            target_loop = container[end + 1]
            del container[start : end + 1]
            target_loop.setdefault("children", [])[0:0] = block
            self.refresh_loop_body_metadata(target_loop)
            self.refresh_step_list(select_step_id=current_id)
            self.select_step_ids(selected_ids, current_id=current_id)
            self.log(f"已把 {len(selected_ids)} 个步骤移入下方循环。")
            return

        if direction < 0 and start == 0:
            if parent:
                parent_location = self.step_location(parent["id"])
                if parent_location:
                    parent_container, parent_index, parent_parent = parent_location
                    del container[start : end + 1]
                    parent_container[parent_index:parent_index] = block
                    self.refresh_loop_body_metadata(parent)
                    if parent_parent and parent_parent.get("type") == "loop":
                        self.refresh_loop_body_metadata(parent_parent)
                    self.refresh_step_list(select_step_id=current_id)
                    self.select_step_ids(selected_ids, current_id=current_id)
                    self.log(f"已把 {len(selected_ids)} 个循环子步骤移出到循环前。")
                    return
            self.log("选中步骤已经在最上面。")
            return

        if direction > 0 and end >= len(container) - 1:
            if parent:
                parent_location = self.step_location(parent["id"])
                if parent_location:
                    parent_container, parent_index, parent_parent = parent_location
                    del container[start : end + 1]
                    parent_container[parent_index + 1 : parent_index + 1] = block
                    self.refresh_loop_body_metadata(parent)
                    if parent_parent and parent_parent.get("type") == "loop":
                        self.refresh_loop_body_metadata(parent_parent)
                    self.refresh_step_list(select_step_id=current_id)
                    self.select_step_ids(selected_ids, current_id=current_id)
                    self.log(f"已把 {len(selected_ids)} 个循环子步骤移出到循环后。")
                    return
            self.log("选中步骤已经在最下面。")
            return

        del container[start : end + 1]
        insert_at = start - 1 if direction < 0 else start + 1
        container[insert_at:insert_at] = block
        if parent and parent.get("type") == "loop":
            self.refresh_loop_body_metadata(parent)
        self.refresh_step_list(select_step_id=current_id)
        self.select_step_ids(selected_ids, current_id=current_id)
        self.log(f"已{action} {len(selected_ids)} 个步骤。")

    def move_selected_steps_into_loop(self) -> None:
        locations = self.selected_step_locations()
        if not locations:
            QMessageBox.information(self, "入循环", "请先选中要移入循环的步骤。")
            return
        container = locations[0][1]
        parent = locations[0][3]
        if parent is not None or any(id(item[1]) != id(container) for item in locations):
            QMessageBox.information(self, "入循环", "只能把同一层级的顶层步骤移入循环。")
            return
        indices = [item[2] for item in locations]
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.information(self, "入循环", "请选中连续步骤后再移入循环。")
            return
        start, end = indices[0], indices[-1]
        block = container[start : end + 1]
        target_loop: dict[str, Any] | None = None
        insert_at_start = False
        if start > 0 and container[start - 1].get("type") == "loop":
            target_loop = container[start - 1]
        elif end < len(container) - 1 and container[end + 1].get("type") == "loop":
            target_loop = container[end + 1]
            insert_at_start = True
        if target_loop is None:
            QMessageBox.information(self, "入循环", "请把要移入的步骤放在循环的上方或下方，再点“入循环”。")
            return
        current = self.current_step()
        current_id = current["id"] if current else locations[-1][0]
        selected_ids = [item[0] for item in locations]
        del container[start : end + 1]
        children = target_loop.setdefault("children", [])
        if insert_at_start:
            children[0:0] = block
        else:
            children.extend(block)
        self.refresh_loop_body_metadata(target_loop)
        self.refresh_step_list(select_step_id=current_id)
        self.select_step_ids(selected_ids, current_id=current_id)
        position = "开头" if insert_at_start else "末尾"
        self.log(f"已把 {len(selected_ids)} 个步骤移入循环 {target_loop.get('name')} 的{position}。")

    def move_selected_steps_out_of_loop(self) -> None:
        locations = self.selected_step_locations()
        if not locations:
            QMessageBox.information(self, "出循环", "请先选中要移出循环的子步骤。")
            return
        container = locations[0][1]
        parent = locations[0][3]
        if parent is None or any(id(item[1]) != id(container) for item in locations):
            QMessageBox.information(self, "出循环", "请选中同一个循环里的子步骤。")
            return
        indices = [item[2] for item in locations]
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.information(self, "出循环", "请选中连续子步骤后再移出循环。")
            return
        parent_location = self.step_location(parent["id"])
        if not parent_location:
            QMessageBox.warning(self, "出循环", "找不到外层循环位置。")
            return
        parent_container, parent_index, parent_parent = parent_location
        start, end = indices[0], indices[-1]
        block = container[start : end + 1]
        current = self.current_step()
        current_id = current["id"] if current else locations[-1][0]
        selected_ids = [item[0] for item in locations]
        del container[start : end + 1]
        insert_at = parent_index if start == 0 else parent_index + 1
        parent_container[insert_at:insert_at] = block
        self.refresh_loop_body_metadata(parent)
        if parent_parent and parent_parent.get("type") == "loop":
            self.refresh_loop_body_metadata(parent_parent)
        self.refresh_step_list(select_step_id=current_id)
        self.select_step_ids(selected_ids, current_id=current_id)
        where = "循环前" if insert_at == parent_index else "循环后"
        self.log(f"已把 {len(selected_ids)} 个循环子步骤移出到{where}。")

    def create_loop_from_selection(self) -> None:
        indices = self.selected_step_indices()
        if not indices:
            QMessageBox.information(self, "循环选中", "请先选中要循环的连续步骤。")
            return
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.warning(self, "循环选中", "循环范围必须是连续步骤。")
            return
        selected = [self.flow["steps"][index] for index in indices]
        times, ok = QInputDialog.getInt(self, "最大循环次数", "最多尝试多少次？可在右侧改成“直到图片出现/消失”。", 50, 1, 999, 1)
        if not ok:
            return
        loop_step = create_step("loop", f"loop_{indices[0] + 1:03d}_{indices[-1] + 1:03d}")
        body_ids = [step["id"] for step in selected]
        loop_step["children"] = selected
        loop_step["input"].update(
            {
                "times": int(times),
                "body_step_ids": body_ids,
                "body_start_id": body_ids[0],
                "body_end_id": body_ids[-1],
                "break_on_failure": True,
            }
        )
        start = indices[0]
        end = indices[-1]
        self.flow["steps"][start : end + 1] = [loop_step]
        self.refresh_step_list(select_step_id=loop_step["id"])
        self.log(
            f"已创建循环：{loop_step['name']}，已把第 {start + 1}-{end + 1} 步移入子步骤，最大 {times} 次。"
        )

    def merge_wait_steps(self) -> None:
        self.sync_steps_from_list()
        changed = self.merge_wait_steps_in_container(self.flow["steps"])
        self.refresh_step_list()
        self.log(f"已把 {changed} 个等待步骤合并到上一步的等待属性里。")

    def merge_wait_steps_in_container(self, steps: list[dict[str, Any]]) -> int:
        changed = 0
        index = 0
        while index < len(steps):
            step = steps[index]
            children = step.get("children")
            if isinstance(children, list) and children:
                changed += self.merge_wait_steps_in_container(children)
                self.refresh_loop_body_metadata(step)
            if step.get("type") == "wait" and index > 0:
                previous = steps[index - 1]
                duration = float(step.get("input", {}).get("duration", 1.0))
                if self.absorb_wait_into_step(previous, duration):
                    del steps[index]
                    changed += 1
                    continue
            index += 1
        return changed

    def absorb_wait_into_step(self, step: dict[str, Any], duration: float) -> bool:
        data = step.setdefault("input", {})
        if step.get("type") == "click":
            data["wait_after"] = float(data.get("wait_after", 0.0)) + duration
            return True
        if step.get("type") in {"image_check", "find_target", "click_target"}:
            data["wait_after_found"] = float(data.get("wait_after_found", 0.0)) + duration
            return True
        if step.get("type") == "question":
            data["wait_after_answer"] = float(data.get("wait_after_answer", 0.0)) + duration
            return True
        return False

    def populate_properties(self, step: dict[str, Any] | None) -> None:
        self.clear_layout(self.property_layout)
        if step is None:
            label = QLabel("选择一个步骤后在这里编辑属性。")
            label.setWordWrap(True)
            self.property_layout.addRow(label)
            return

        self.add_readonly("ID", step["id"])
        self.add_readonly("类型", STEP_LABELS.get(step["type"], step["type"]))

        name = QLineEdit(step.get("name", ""))
        name.textChanged.connect(lambda value: self.update_step_value(step, "name", value))
        self.property_layout.addRow("名称", name)

        enabled = QCheckBox("启用")
        enabled.setChecked(bool(step.get("enabled", True)))
        enabled.toggled.connect(lambda value: self.update_step_value(step, "enabled", value, refresh=True))
        self.property_layout.addRow("状态", enabled)

        retry = QSpinBox()
        retry.setRange(0, 999)
        retry.setValue(int(step.get("retry_count", 0)))
        retry.valueChanged.connect(lambda value: self.update_step_value(step, "retry_count", value))
        self.property_layout.addRow("失败重试", retry)

        timeout = QDoubleSpinBox()
        timeout.setRange(0, 9999)
        timeout.setDecimals(1)
        timeout.setValue(float(step.get("timeout", 10.0)))
        timeout.valueChanged.connect(lambda value: self.update_step_value(step, "timeout", value))
        self.property_layout.addRow("超时秒数", timeout)

        failure = QComboBox()
        failure.addItems(["stop", "next", "retry"])
        failure.setCurrentText(str(step.get("on_failure", "stop")))
        failure.currentTextChanged.connect(lambda value: self.update_step_value(step, "on_failure", value, refresh=True))
        self.property_layout.addRow("失败后", failure)

        if step["type"] == "click":
            self.populate_click_properties(step)
        elif step["type"] == "wait":
            self.populate_wait_properties(step)
        elif step["type"] in {"image_check", "find_target", "click_target"}:
            self.populate_image_properties(step)
        elif step["type"] in {"ocr_text", "ocr_number"}:
            self.populate_ocr_properties(step)
        elif step["type"] == "verify_code":
            self.populate_verify_code_properties(step)
        elif step["type"] == "dialog":
            self.populate_dialog_properties(step)
        elif step["type"] in {"read_game_coord", "move_to_game_coord"}:
            self.add_readonly("状态", "游戏坐标相关步骤已停用，请改用固定画面点击或图片识别点击。")
        elif step["type"] == "question":
            self.populate_question_properties(step)
        elif step["type"] == "condition":
            self.populate_condition_properties(step)
        elif step["type"] == "loop":
            if step.get("input", {}).get("condition_branch"):
                self.populate_condition_branch_properties(step)
            else:
                self.populate_loop_properties(step)
        else:
            self.add_readonly("配置", "MVP 已保留此步骤类型，后续会补全专用面板。")

        debug_row = QHBoxLayout()
        run_button = QPushButton("测试此步骤")
        run_button.clicked.connect(lambda: self.run_step_for_test(step))
        run_from_here_button = QPushButton("从此步继续")
        run_from_here_button.clicked.connect(lambda checked=False, step_id=step["id"]: self.run_from_step_id(step_id))
        stop_button = QPushButton("停止")
        stop_button.clicked.connect(self.stop_run)
        debug_row.addWidget(run_button)
        debug_row.addWidget(run_from_here_button)
        debug_row.addWidget(stop_button)
        self.property_layout.addRow("调试", debug_row)

    def populate_click_properties(self, step: dict[str, Any]) -> None:
        data = step["input"]
        click_type = QComboBox()
        click_type.addItems(["fixed_screen_coord"])
        click_type.setCurrentText("fixed_screen_coord")
        click_type.currentTextChanged.connect(lambda value: self.update_input(step, "click_type", value))
        self.property_layout.addRow("点击类型", click_type)

        coord = data.get("screen_coord") or [0, 0]
        x = QSpinBox()
        y = QSpinBox()
        for spin in (x, y):
            spin.setRange(0, 10000)
        x.setValue(int(coord[0]))
        y.setValue(int(coord[1]))
        x.valueChanged.connect(lambda value: self.update_coord(step, 0, value))
        y.valueChanged.connect(lambda value: self.update_coord(step, 1, value))
        row = QHBoxLayout()
        row.addWidget(QLabel("X"))
        row.addWidget(x)
        row.addWidget(QLabel("Y"))
        row.addWidget(y)
        self.property_layout.addRow("屏幕坐标", row)

        self.add_click_repeat_controls(step)

        for label, key in (("点击前等待", "wait_before"), ("点击后等待", "wait_after")):
            wait = QDoubleSpinBox()
            wait.setRange(0, 999)
            wait.setDecimals(2)
            wait.setSingleStep(0.1)
            wait.setValue(float(data.get(key, 0.0)))
            wait.valueChanged.connect(lambda value, k=key: self.update_input(step, k, value))
            self.property_layout.addRow(label, wait)

        confirm = QCheckBox("需要确认点击成功")
        confirm.setChecked(bool(data.get("confirm_success", False)))
        confirm.toggled.connect(lambda value: self.update_input(step, "confirm_success", value))
        self.property_layout.addRow("确认", confirm)

    def add_click_count_control(self, step: dict[str, Any], key: str, label: str, default: int = 1) -> None:
        data = step.setdefault("input", {})
        click_count = QSpinBox()
        click_count.setRange(1, 20)
        click_count.setValue(max(1, int(data.get(key, default) or default)))
        click_count.valueChanged.connect(lambda value, k=key: self.update_input(step, k, value))
        self.property_layout.addRow(label, click_count)

    def add_click_interval_control(self, step: dict[str, Any], key: str = "click_interval", label: str = "连点间隔") -> None:
        data = step.setdefault("input", {})
        click_interval = QDoubleSpinBox()
        click_interval.setRange(0, 5)
        click_interval.setDecimals(2)
        click_interval.setSingleStep(0.05)
        click_interval.setValue(float(data.get(key, 0.08)))
        click_interval.valueChanged.connect(lambda value, k=key: self.update_input(step, k, value))
        self.property_layout.addRow(label, click_interval)

    def add_click_repeat_controls(
        self,
        step: dict[str, Any],
        *,
        count_key: str = "click_count",
        interval_key: str = "click_interval",
        count_label: str = "点击次数",
        interval_label: str = "连点间隔",
    ) -> None:
        self.add_click_count_control(step, count_key, count_label)
        self.add_click_interval_control(step, interval_key, interval_label)

    def populate_wait_properties(self, step: dict[str, Any]) -> None:
        duration = QDoubleSpinBox()
        duration.setRange(0, 999)
        duration.setDecimals(2)
        duration.setValue(float(step["input"].get("duration", 1.0)))
        duration.valueChanged.connect(lambda value: self.update_input(step, "duration", value))
        self.property_layout.addRow("等待秒数", duration)

    def populate_dialog_properties(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        title = QLineEdit(str(data.get("title") or "脚本执行完成"))
        title.textChanged.connect(lambda value: self.update_input(step, "title", value))
        self.property_layout.addRow("弹窗标题", title)

        message = QPlainTextEdit(str(data.get("message") or "脚本执行完成。"))
        message.setFixedHeight(120)
        message.textChanged.connect(lambda edit=message: self.update_input(step, "message", edit.toPlainText()))
        self.property_layout.addRow("弹窗内容", message)

        log_message = QCheckBox("同时写入日志")
        log_message.setChecked(bool(data.get("log_message", True)))
        log_message.toggled.connect(lambda value: self.update_input(step, "log_message", value))
        self.property_layout.addRow("日志", log_message)

    def populate_image_properties(self, step: dict[str, Any]) -> None:
        data = step["input"]
        wait_until = QCheckBox("没找到就继续等，找到后再下一步")
        wait_until.setChecked(bool(data.get("wait_until_found", True)))
        wait_until.toggled.connect(lambda value: self.update_input(step, "wait_until_found", value))
        self.property_layout.addRow("等待识别", wait_until)

        quick_actions = QHBoxLayout()
        instant = QPushButton("设为当前完成判断")
        instant.clicked.connect(lambda: self.set_image_step_instant_check(step))
        optional = QPushButton("设为可选画面")
        optional.clicked.connect(lambda: self.set_image_step_optional_check(step))
        quick_actions.addWidget(instant)
        quick_actions.addWidget(optional)
        self.property_layout.addRow("快捷", quick_actions)

        found_action = QComboBox()
        found_action.addItem("继续下一步", "next")
        found_action.addItem("报错停止", "fail")
        current_found_action = str(data.get("action_on_found") or "next")
        found_index = found_action.findData(current_found_action)
        found_action.setCurrentIndex(found_index if found_index >= 0 else 0)
        found_action.currentIndexChanged.connect(
            lambda _index, combo=found_action: self.update_input(step, "action_on_found", combo.currentData())
        )
        self.property_layout.addRow("找到时", found_action)

        missing_action = QComboBox()
        missing_action.addItem("报错停止", "fail")
        missing_action.addItem("跳过继续", "skip")
        current_missing_action = str(data.get("action_on_missing") or "fail")
        missing_index = missing_action.findData(current_missing_action)
        missing_action.setCurrentIndex(missing_index if missing_index >= 0 else 0)
        missing_action.currentIndexChanged.connect(
            lambda _index, combo=missing_action: self.update_input(step, "action_on_missing", combo.currentData())
        )
        self.property_layout.addRow("没找到时", missing_action)

        threshold = QDoubleSpinBox()
        threshold.setRange(0.1, 1.0)
        threshold.setDecimals(2)
        threshold.setSingleStep(0.01)
        threshold.setValue(float(data.get("threshold", 0.85)))
        threshold.valueChanged.connect(lambda value: self.update_input(step, "threshold", value))
        self.property_layout.addRow("匹配阈值", threshold)

        poll = QDoubleSpinBox()
        poll.setRange(0.1, 10)
        poll.setDecimals(2)
        poll.setSingleStep(0.1)
        poll.setValue(float(data.get("poll_interval", 0.4)))
        poll.valueChanged.connect(lambda value: self.update_input(step, "poll_interval", value))
        self.property_layout.addRow("轮询间隔", poll)

        wait_after = QDoubleSpinBox()
        wait_after.setRange(0, 60)
        wait_after.setDecimals(2)
        wait_after.setSingleStep(0.1)
        wait_after.setValue(float(data.get("wait_after_found", 0.5)))
        wait_after.valueChanged.connect(lambda value: self.update_input(step, "wait_after_found", value))
        self.property_layout.addRow("找到后等待", wait_after)

        if step["type"] == "click_target":
            click_mode = QComboBox()
            click_mode.addItem("匹配中心", "center")
            click_mode.addItem("模板内指定点", "template_point")
            current_mode = str(data.get("click_mode") or ("template_point" if data.get("click_offset") else "center"))
            index = click_mode.findData(current_mode)
            click_mode.setCurrentIndex(index if index >= 0 else 0)
            click_mode.currentIndexChanged.connect(
                lambda _value, combo=click_mode: self.update_input(step, "click_mode", combo.currentData())
            )
            self.property_layout.addRow("点击方式", click_mode)

            offset = data.get("click_offset")
            if isinstance(offset, list) and len(offset) >= 2:
                self.add_readonly("模板内点击点", f"{int(offset[0])}, {int(offset[1])}")
            else:
                self.add_readonly("模板内点击点", "未设置，默认点中心")

            point_buttons = QHBoxLayout()
            set_point = QPushButton("在模板上点落点")
            set_point.clicked.connect(lambda: self.set_click_target_template_point(step))
            clear_point = QPushButton("恢复点击中心")
            clear_point.clicked.connect(lambda: self.clear_click_target_template_point(step))
            point_buttons.addWidget(set_point)
            point_buttons.addWidget(clear_point)
            self.property_layout.addRow("精准落点", point_buttons)
            self.add_click_repeat_controls(step, count_label="目标点击次数")
        else:
            self.add_readonly("找到后", "进入下一步")
        self.add_readonly("bbox", str(data.get("bbox")))
        self.add_readonly("模板", str(data.get("template_path") or "无"))
        template_actions = QHBoxLayout()
        recapture = QPushButton("重新截图替换模板")
        recapture.clicked.connect(lambda: self.recapture_image_step_template(step))
        template_actions.addWidget(recapture)
        self.property_layout.addRow("模板操作", template_actions)
        self.add_asset_preview(data.get("template_path"))

    def set_image_step_instant_check(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        data["wait_until_found"] = False
        data["instant_check"] = True
        step["timeout"] = 1.0
        self.populate_properties(step)
        self.log(f"已把 {step.get('name')} 设为当前完成判断：只检查当下画面，不等待状态变化。")

    def set_image_step_optional_check(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        data["wait_until_found"] = False
        data["instant_check"] = True
        data["action_on_found"] = "next"
        data["action_on_missing"] = "skip"
        step["timeout"] = 1.0
        self.populate_properties(step)
        self.log(f"已把 {step.get('name')} 设为可选画面：出现就执行，没出现就跳过继续。")

    def set_click_target_template_point(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        template = self.storage.abs(data.get("template_path"))
        if template is None or not template.exists():
            QMessageBox.information(self, "模板内点击点", "这个点击目标还没有模板图。")
            return
        image = QImage(str(template))
        if image.isNull():
            QMessageBox.information(self, "模板内点击点", "模板图读取失败。")
            return
        dialog = TemplateClickPointDialog(
            image,
            initial_point=data.get("click_offset"),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_point is None:
            return
        point = dialog.selected_point
        data["click_mode"] = "template_point"
        data["click_offset"] = [int(point.x()), int(point.y())]
        self.populate_properties(step)
        self.log(f"已设置模板内点击点：{point.x()}, {point.y()}。")

    def clear_click_target_template_point(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        data["click_mode"] = "center"
        data["click_offset"] = None
        self.populate_properties(step)
        self.log(f"已恢复点击目标中心：{step.get('name')}")

    def image_step_asset_kind(self, step: dict[str, Any]) -> str:
        data = step.get("input", {})
        template_path = str(data.get("template_path") or "").replace("\\", "/")
        directory_to_kind = {
            "button": "button",
            "npc": "npc",
            "transition": "transition",
            "battle": "battle",
            "question": "question",
            "digit": "digit",
            "text": "text",
            "crops": "image",
        }
        for part in template_path.split("/"):
            if part in directory_to_kind:
                return directory_to_kind[part]
        if step.get("type") == "click_target":
            return "target"
        return "image"

    def expanded_template_search_bbox(self, rect: QRect, frame: QImage) -> list[int]:
        pad_x = max(80, rect.width())
        pad_y = max(60, rect.height())
        x = max(0, rect.x() - pad_x)
        y = max(0, rect.y() - pad_y)
        right = min(frame.width(), rect.right() + pad_x + 1)
        bottom = min(frame.height(), rect.bottom() + pad_y + 1)
        return [x, y, max(1, right - x), max(1, bottom - y)]

    def should_keep_search_bbox(self, search_bbox: Any, template_bbox: list[int]) -> bool:
        if not isinstance(search_bbox, (list, tuple)) or len(search_bbox) < 4:
            return False
        try:
            search_width = max(1, int(search_bbox[2]))
            search_height = max(1, int(search_bbox[3]))
            template_width = max(1, int(template_bbox[2]))
            template_height = max(1, int(template_bbox[3]))
        except (TypeError, ValueError):
            return False
        return (search_width * search_height) >= (template_width * template_height * 4)

    def recapture_image_step_template(self, step: dict[str, Any]) -> None:
        if not self.capture_current_screen(save=False, allow_cached=False) or self.current_frame is None:
            QMessageBox.information(self, "替换模板", "请先连接 MuMu，并确认 ADB 可以截图。")
            return

        frame = self.current_frame.copy()
        step_name = str(step.get("name") or "识别步骤")
        asset_kind = self.image_step_asset_kind(step)
        dialog = LargeRegionDialog(
            frame,
            title=f"替换模板 - {step_name}",
            fixed_kind=asset_kind,
            hint="请框选这个步骤要识别的新图片模板。框选后会替换当前步骤的模板图，原来的搜索区域会尽量保留。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return

        bounded = dialog.selected_rect.intersected(QRect(0, 0, frame.width(), frame.height()))
        if bounded.width() < 2 or bounded.height() < 2:
            QMessageBox.information(self, "替换模板", "框选区域太小，请重新选择。")
            return

        data = step.setdefault("input", {})
        old_search_bbox = data.get("search_bbox")
        paths = self.storage.make_asset_paths(self.current_map, asset_kind, f"{step_name}_替换")
        raw_path = Path(paths["raw_path"])
        crop_path = Path(paths["crop_path"])
        annotated_path = Path(paths["annotated_path"])

        frame.save(str(raw_path))
        frame.copy(bounded).save(str(crop_path))
        annotated = frame.copy()
        painter = QPainter(annotated)
        painter.setPen(QPen(QColor("#ff375f"), 3))
        painter.drawRect(bounded)
        painter.end()
        annotated.save(str(annotated_path))

        asset_id = str(paths["asset_id"])
        bbox = bbox_from_rect(bounded)
        self.storage.add_asset(
            asset_id=asset_id,
            user_name=step_name,
            auto_name=str(paths["auto_name"]),
            asset_type=asset_kind,
            map_id=self.current_map,
            script_name=self.flow["script_name"],
            step_id=step["id"],
            bbox=bbox,
            raw_path=raw_path,
            crop_path=crop_path,
            annotated_path=annotated_path,
            metadata={
                "created_from": "step_template_recapture",
                "step_type": step.get("type"),
                "replaces_template": data.get("template_path"),
            },
        )

        step.setdefault("assets", []).append(asset_id)
        step["updated_at"] = now_iso()
        data["asset_id"] = asset_id
        data["bbox"] = bbox
        data["template_path"] = self.storage.rel(crop_path)
        if not self.should_keep_search_bbox(old_search_bbox, bbox):
            data["search_bbox"] = self.expanded_template_search_bbox(bounded, frame)

        if step.get("type") == "click_target":
            offset = data.get("click_offset")
            try:
                offset_x = int(offset[0]) if isinstance(offset, (list, tuple)) and len(offset) >= 2 else None
                offset_y = int(offset[1]) if isinstance(offset, (list, tuple)) and len(offset) >= 2 else None
            except (TypeError, ValueError):
                offset_x = None
                offset_y = None
            if offset_x is None or offset_y is None or offset_x >= bounded.width() or offset_y >= bounded.height():
                data["click_mode"] = "center"
                data["click_offset"] = None

        self.refresh_step_list(select_step_id=step["id"])
        self.log(f"已替换 {step_name} 的模板：{data['template_path']}，bbox: {bbox}")

    def populate_ocr_properties(self, step: dict[str, Any]) -> None:
        data = step["input"]
        self.add_readonly("OCR区域", str(data.get("bbox")))
        self.add_readonly("识别结果", str(data.get("last_result", "")))
        self.add_readonly("置信度", f"{float(data.get('confidence', 0.0)):.2f}")
        pending = QCheckBox("未知时保存到 pending_review")
        pending.setChecked(bool(data.get("save_unknown_to_pending", True)))
        pending.toggled.connect(lambda value: self.update_input(step, "save_unknown_to_pending", value))
        self.property_layout.addRow("低置信度", pending)
        asset_id = data.get("asset_id")
        if asset_id:
            asset = self.storage.asset(asset_id)
            if asset:
                self.add_asset_preview(asset["crop_path"])

    def populate_verify_code_properties(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        self.add_verify_code_region_row(step, "验证码数字", "digit_bbox")
        self.add_verify_code_coord_row(step, "输入框点击", "input_coord")
        self.add_verify_code_coord_row(step, "确定按钮点击", "confirm_coord")
        self.add_click_count_control(step, "input_click_count", "输入框点击次数")
        self.add_click_count_control(step, "confirm_click_count", "确定点击次数")
        self.add_click_interval_control(step)

        expected_length = QSpinBox()
        expected_length.setRange(0, 12)
        expected_length.setValue(int(data.get("expected_length", 4)))
        expected_length.valueChanged.connect(lambda value: self.update_input(step, "expected_length", value))
        self.property_layout.addRow("数字位数", expected_length)

        confidence = QDoubleSpinBox()
        confidence.setRange(0.0, 1.0)
        confidence.setDecimals(2)
        confidence.setSingleStep(0.01)
        confidence.setValue(float(data.get("min_confidence", DEFAULT_VERIFY_CODE_MIN_CONFIDENCE)))
        confidence.valueChanged.connect(lambda value: self.update_input(step, "min_confidence", value))
        self.property_layout.addRow("最低置信度", confidence)

        wait_before = QDoubleSpinBox()
        wait_before.setRange(0.0, 10.0)
        wait_before.setDecimals(2)
        wait_before.setSingleStep(0.1)
        wait_before.setValue(float(data.get("wait_before_capture", 1.0)))
        wait_before.valueChanged.connect(lambda value: self.update_input(step, "wait_before_capture", value))
        self.property_layout.addRow("识别前等待", wait_before)

        retry_count = QSpinBox()
        retry_count.setRange(1, 5)
        retry_count.setValue(int(data.get("retry_count", 2)))
        retry_count.valueChanged.connect(lambda value: self.update_input(step, "retry_count", value))
        self.property_layout.addRow("识别重试次数", retry_count)

        retry_interval = QDoubleSpinBox()
        retry_interval.setRange(0.0, 5.0)
        retry_interval.setDecimals(2)
        retry_interval.setSingleStep(0.1)
        retry_interval.setValue(float(data.get("retry_interval", 0.7)))
        retry_interval.valueChanged.connect(lambda value: self.update_input(step, "retry_interval", value))
        self.property_layout.addRow("重试间隔", retry_interval)

        for label, key in (
            ("点输入框后等待", "wait_after_focus"),
            ("输入后等待", "wait_after_input"),
            ("确定后等待", "wait_after_confirm"),
        ):
            wait = QDoubleSpinBox()
            wait.setRange(0, 30)
            wait.setDecimals(2)
            wait.setSingleStep(0.1)
            wait.setValue(float(data.get(key, 0.0)))
            wait.valueChanged.connect(lambda value, k=key: self.update_input(step, k, value))
            self.property_layout.addRow(label, wait)

        pending = QCheckBox("识别失败时保存数字截图")
        pending.setChecked(bool(data.get("save_unknown_to_pending", True)))
        pending.toggled.connect(lambda value: self.update_input(step, "save_unknown_to_pending", value))
        self.property_layout.addRow("失败留样", pending)
        self.add_readonly("上次识别", str(data.get("last_result") or ""))
        self.add_readonly("上次置信度", f"{float(data.get('confidence', 0.0)):.2f}")

    def add_verify_code_region_row(self, step: dict[str, Any], label: str, key: str) -> None:
        data = step.setdefault("input", {})
        row = QHBoxLayout()
        row.addWidget(QLabel(str(data.get(key) or "未设置")))
        button = QPushButton("重新框选")
        button.clicked.connect(lambda: self.recapture_verify_code_region(step, key, label))
        row.addWidget(button)
        self.property_layout.addRow(label, row)

    def add_verify_code_coord_row(self, step: dict[str, Any], label: str, key: str) -> None:
        coord = step.setdefault("input", {}).setdefault(key, [0, 0])
        x = QSpinBox()
        y = QSpinBox()
        for spin in (x, y):
            spin.setRange(0, 10000)
        x.setValue(int(coord[0] if len(coord) > 0 else 0))
        y.setValue(int(coord[1] if len(coord) > 1 else 0))
        x.valueChanged.connect(lambda value: self.update_input_coord(step, key, 0, value))
        y.valueChanged.connect(lambda value: self.update_input_coord(step, key, 1, value))
        pick = QPushButton("框选取中心")
        pick.clicked.connect(lambda: self.recapture_verify_code_coord(step, key, label))
        row = QHBoxLayout()
        row.addWidget(QLabel("X"))
        row.addWidget(x)
        row.addWidget(QLabel("Y"))
        row.addWidget(y)
        row.addWidget(pick)
        self.property_layout.addRow(label, row)

    def recapture_verify_code_region(self, step: dict[str, Any], key: str, label: str) -> None:
        if not self.capture_current_screen(save=False) or self.current_frame is None:
            QMessageBox.information(self, label, "请先连接 MuMu，并确认 ADB 可以截图。")
            return
        bbox = self.capture_verify_code_region(label, f"框选{label}区域。")
        if bbox is None:
            return
        self.update_input(step, key, bbox)
        self.populate_properties(step)
        self.log(f"已更新{label}区域：{bbox}")

    def recapture_verify_code_coord(self, step: dict[str, Any], key: str, label: str) -> None:
        if not self.capture_current_screen(save=False) or self.current_frame is None:
            QMessageBox.information(self, label, "请先连接 MuMu，并确认 ADB 可以截图。")
            return
        bbox = self.capture_verify_code_region(label, f"框选{label}所在区域，脚本会取中心点点击。")
        if bbox is None:
            return
        coord = list(bbox_center(bbox))
        self.update_input(step, key, coord)
        self.populate_properties(step)
        self.log(f"已更新{label}：{coord}")

    def populate_coord_properties(self, step: dict[str, Any]) -> None:
        self.add_readonly("坐标区域", str(step["input"].get("coord_region") or step["input"].get("bbox")))
        self.add_readonly("当前游戏坐标", str(self.current_game_coord))
        apply_button = QPushButton("设为全局游戏坐标区域")
        apply_button.clicked.connect(lambda: self.set_global_coord_region(step))
        self.property_layout.addRow(apply_button)

    def set_global_coord_region(self, step: dict[str, Any]) -> None:
        region = step.get("input", {}).get("coord_region") or step.get("input", {}).get("bbox")
        if not region:
            QMessageBox.information(self, "游戏坐标区域", "这个步骤没有坐标区域。")
            return
        self.flow.setdefault("settings", {})["game_coord_region"] = region
        self.update_coord_region_label()
        self.log(f"已设置全局游戏坐标区域：{region}")

    def populate_move_coord_properties(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        target = data.setdefault("target_coord", [0, 0])
        row = QHBoxLayout()
        x = QSpinBox()
        y = QSpinBox()
        for spin in (x, y):
            spin.setRange(0, 999)
        x.setValue(int(target[0]))
        y.setValue(int(target[1]))
        x.valueChanged.connect(lambda value: self.update_target_coord(step, 0, value))
        y.valueChanged.connect(lambda value: self.update_target_coord(step, 1, value))
        row.addWidget(QLabel("X"))
        row.addWidget(x)
        row.addWidget(QLabel("Y"))
        row.addWidget(y)
        self.property_layout.addRow("目标游戏坐标", row)

        tolerance = QSpinBox()
        tolerance.setRange(0, 20)
        tolerance.setValue(int(data.get("tolerance", 1)))
        tolerance.valueChanged.connect(lambda value: self.update_input(step, "tolerance", value))
        self.property_layout.addRow("坐标容差", tolerance)

        exact_target = QCheckBox("必须精准站到目标坐标")
        exact_default = bool(data.get("exact_target", data.get("arrival_mode", "exact") == "exact" or int(data.get("tolerance", 0)) == 0))
        exact_target.setChecked(exact_default)
        exact_target.toggled.connect(lambda value: self.update_exact_target(step, value))
        self.property_layout.addRow("到达方式", exact_target)

        max_seconds = QDoubleSpinBox()
        max_seconds.setRange(1, 999)
        max_seconds.setDecimals(1)
        max_seconds.setValue(float(data.get("max_seconds", 30)))
        max_seconds.valueChanged.connect(lambda value: self.update_input(step, "max_seconds", value))
        self.property_layout.addRow("最长移动秒数", max_seconds)

        poll = QDoubleSpinBox()
        poll.setRange(0.2, 5)
        poll.setDecimals(2)
        poll.setSingleStep(0.1)
        poll.setValue(float(data.get("poll_interval", 0.8)))
        poll.valueChanged.connect(lambda value: self.update_input(step, "poll_interval", value))
        self.property_layout.addRow("每步后等待", poll)

        max_jump = QSpinBox()
        max_jump.setRange(3, 50)
        max_jump.setValue(int(data.get("max_coord_jump", MAX_NORMAL_MOVEMENT_DELTA)))
        max_jump.valueChanged.connect(lambda value: self.update_input(step, "max_coord_jump", value))
        self.property_layout.addRow("坐标跳变过滤", max_jump)

        max_click_radius = QSpinBox()
        max_click_radius.setRange(160, 340)
        max_click_radius.setValue(int(data.get("max_click_radius", 300)))
        max_click_radius.valueChanged.connect(lambda value: self.update_input(step, "max_click_radius", value))
        self.property_layout.addRow("最大点击半径", max_click_radius)

        lookahead = QSpinBox()
        lookahead.setRange(1, 20)
        lookahead.setValue(int(data.get("waypoint_lookahead", 5)))
        lookahead.valueChanged.connect(lambda value: self.update_input(step, "waypoint_lookahead", value))
        self.property_layout.addRow("路线前瞻格数", lookahead)

        approach_radius = QSpinBox()
        approach_radius.setRange(0, 8)
        approach_radius.setValue(int(data.get("approach_radius", 1)))
        approach_radius.valueChanged.connect(lambda value: self.update_input(step, "approach_radius", value))
        self.property_layout.addRow("接近点半径", approach_radius)

        use_approach = QCheckBox("自动选择目标周围可站立点")
        use_approach.setChecked(bool(data.get("use_approach_points", not exact_default)))
        use_approach.toggled.connect(lambda value: self.update_input(step, "use_approach_points", value))
        self.property_layout.addRow("接近点策略", use_approach)

        target_backoff = QCheckBox("贴身点不到时先拉开再回点")
        target_backoff.setChecked(bool(data.get("target_backoff_enabled", True)))
        target_backoff.toggled.connect(lambda value: self.update_input(step, "target_backoff_enabled", value))
        self.property_layout.addRow("精准回切", target_backoff)

        backoff_distance = QSpinBox()
        backoff_distance.setRange(2, 8)
        backoff_distance.setValue(int(data.get("target_backoff_distance", 4)))
        backoff_distance.valueChanged.connect(lambda value: self.update_input(step, "target_backoff_distance", value))
        self.property_layout.addRow("拉开距离", backoff_distance)

        coord_recovery = QCheckBox("坐标读不到时点空位退出对话/选人界面")
        coord_recovery.setChecked(bool(data.get("coord_recovery_enabled", True)))
        coord_recovery.toggled.connect(lambda value: self.update_input(step, "coord_recovery_enabled", value))
        self.property_layout.addRow("界面恢复", coord_recovery)

        coord_recovery_attempts = QSpinBox()
        coord_recovery_attempts.setRange(0, 10)
        coord_recovery_attempts.setValue(int(data.get("coord_recovery_attempts", 3)))
        coord_recovery_attempts.valueChanged.connect(lambda value: self.update_input(step, "coord_recovery_attempts", value))
        self.property_layout.addRow("退界面尝试次数", coord_recovery_attempts)

        add_target = QPushButton("加入脚本移动库")
        add_target.clicked.connect(lambda: self.add_move_step_to_movement_db(step))
        self.property_layout.addRow(add_target)

        stats = self.storage.aggregate_movement_samples(
            self.current_map,
            limit=1200,
            script_name=str(self.flow.get("script_name") or ""),
        )
        if int(stats.get("sample_count", 0) or 0) == 0:
            stats = self.storage.aggregate_movement_samples(self.current_map, limit=1200)
        self.add_readonly("移动方式", "闭环：读当前坐标 -> 规划游戏坐标路径 -> 点人物相对方向 -> 读坐标纠偏。")
        self.add_readonly("地图最佳半径", str(stats.get("map_best_radius") or "待学习"))
        self.add_readonly("移动样本", str(stats.get("sample_count", 0)))

    def update_target_coord(self, step: dict[str, Any], index: int, value: int) -> None:
        coord = step.setdefault("input", {}).setdefault("target_coord", [0, 0])
        coord[index] = int(value)

    def update_exact_target(self, step: dict[str, Any], value: bool) -> None:
        data = step.setdefault("input", {})
        data["exact_target"] = bool(value)
        data["arrival_mode"] = "exact" if value else "near"
        data["use_approach_points"] = not bool(value)
        if value:
            data["tolerance"] = 0
        self.populate_properties(step)

    def add_move_step_to_movement_db(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        coord = data.get("target_coord") or [0, 0]
        script_name = str(self.flow.get("script_name") or "副本_001")
        exact = bool(data.get("exact_target", data.get("arrival_mode", "exact") == "exact"))
        added = self.storage.add_script_movement_coord(
            script_name,
            map_id=self.current_map,
            coord=[int(coord[0]), int(coord[1])],
            label=str(step.get("name") or f"{coord[0]},{coord[1]}"),
            tolerance=int(data.get("tolerance", 0) or 0),
            exact=exact,
        )
        self.log(("已加入" if added else "已更新") + f"脚本移动库：{coord[0]}, {coord[1]}")

    def populate_question_properties(self, step: dict[str, Any]) -> None:
        target = QSpinBox()
        target.setRange(1, 99)
        target.setValue(int(step["input"].get("target_correct_count", 1)))
        target.valueChanged.connect(lambda value: self.update_input(step, "target_correct_count", value))
        self.property_layout.addRow("需要答对", target)

        attempts = QSpinBox()
        attempts.setRange(1, 999)
        attempts.setValue(int(step["input"].get("max_attempts", max(1, target.value() * 2))))
        attempts.valueChanged.connect(lambda value: self.update_input(step, "max_attempts", value))
        self.property_layout.addRow("最多答题次数", attempts)

        wait = QDoubleSpinBox()
        wait.setRange(0, 30)
        wait.setDecimals(2)
        wait.setValue(float(step["input"].get("wait_after_answer", 0.8)))
        wait.valueChanged.connect(lambda value: self.update_input(step, "wait_after_answer", value))
        self.property_layout.addRow("答题后等待", wait)

        self.add_click_count_control(step, "answer_click_count", "答案点击次数")
        self.add_click_count_control(step, "confirm_click_count", "确定点击次数")
        self.add_click_interval_control(step)

        use_visual = QCheckBox("OCR失败时允许题目截图兜底")
        use_visual.setChecked(bool(step["input"].get("use_question_visual_match", False)))
        use_visual.toggled.connect(lambda value: self.update_input(step, "use_question_visual_match", value))
        self.property_layout.addRow("题目视觉兜底", use_visual)

        question_threshold = QDoubleSpinBox()
        question_threshold.setRange(0.50, 1.00)
        question_threshold.setDecimals(3)
        question_threshold.setSingleStep(0.01)
        question_threshold.setValue(float(step["input"].get("question_visual_threshold", 0.90)))
        question_threshold.valueChanged.connect(lambda value: self.update_input(step, "question_visual_threshold", value))
        self.property_layout.addRow("题目视觉阈值", question_threshold)

        option_threshold = QDoubleSpinBox()
        option_threshold.setRange(0.50, 1.00)
        option_threshold.setDecimals(3)
        option_threshold.setSingleStep(0.01)
        option_threshold.setValue(float(step["input"].get("option_visual_threshold", 0.90)))
        option_threshold.valueChanged.connect(lambda value: self.update_input(step, "option_visual_threshold", value))
        self.property_layout.addRow("选项视觉阈值", option_threshold)

        policy = QComboBox()
        policy.addItem("默认选 C", "choose_c")
        policy.addItem("保存并暂停", "pause")
        policy.addItem("跳过", "skip")
        current_policy = str(step["input"].get("unknown_policy", "choose_c"))
        if current_policy == "ask":
            current_policy = "choose_c"
        policy_index = policy.findData(current_policy)
        policy.setCurrentIndex(policy_index if policy_index >= 0 else 0)
        policy.currentIndexChanged.connect(lambda _index, combo=policy: self.update_input(step, "unknown_policy", combo.currentData()))
        self.property_layout.addRow("未知题目", policy)

        self.add_readonly("题目区域", str(step["input"].get("question_region")))
        self.add_readonly("选项区域", str(step["input"].get("option_regions")))
        self.add_readonly("确定按钮", str(step["input"].get("confirm_region")))
        self.add_readonly("进度区域", str(step["input"].get("progress_region")))

    def loop_logic_summary(self, step: dict[str, Any]) -> str:
        data = step.get("input") or {}
        condition = data.get("exit_condition") or {}
        mode = str(data.get("loop_mode") or condition.get("type") or "fixed_count")
        times = int(data.get("times", 1) or 1)
        child_count = len(step.get("children") or data.get("body_step_ids") or [])
        if mode in {"fixed_count", "none"}:
            first_line = f"把 {child_count} 个子步骤按顺序执行，最多 {times} 次。"
        else:
            condition_text = "看到判断图就结束" if mode == "image_found" else "判断图消失就结束"
            timing = str(data.get("exit_check_timing") or "before_each_step")
            timing_text = "每轮完整跑完后检查" if timing == "after_iteration" else "每个子步骤前都会检查"
            first_line = f"最多跑 {times} 次；{timing_text}，{condition_text}。"

        max_text = "到最大次数还没满足条件就报错" if data.get("fail_when_max_reached", True) else "到最大次数也算结束"
        failure_text = "子步骤失败就停止循环" if data.get("break_on_failure", True) else "子步骤失败也继续后面的循环"
        lines = [first_line, f"{max_text}；{failure_text}。"]
        if data.get("first_step_skip_exits_loop"):
            lines.append("如果第 1 个子步骤被跳过，就直接跳过整个循环。")
        return "\n".join(lines)

    def condition_logic_summary(self, step: dict[str, Any]) -> str:
        data = step.get("input") or {}
        on_no_match = str(data.get("on_no_match") or "skip")
        miss_text = "都没命中就跳过这个判断步骤" if on_no_match == "skip" else "都没命中就报错停止"
        lines = [f"按分支顺序判断；命中第一个分支后，只执行那个分支的动作；{miss_text}。"]
        branches = step.get("children") or []
        if not branches:
            lines.append("现在还没有分支。")
            return "\n".join(lines)
        for index, branch in enumerate(branches, start=1):
            branch_input = branch.get("input") or {}
            branch_name = str(branch.get("name") or branch.get("id") or f"分支 {index}")
            action_count = len(branch.get("children") or [])
            if branch_input.get("condition_default_branch"):
                lines.append(f"{index}. {branch_name}：前面都没命中时执行 {action_count} 个动作。")
                continue
            threshold = float(branch_input.get("condition_threshold", data.get("branch_threshold", 0.85)) or 0.85)
            if branch_input.get("condition_template_path"):
                lines.append(f"{index}. {branch_name}：看到判断图（阈值 {threshold:.2f}）就执行 {action_count} 个动作。")
            else:
                lines.append(f"{index}. {branch_name}：还没设置判断图，动作 {action_count} 个。")
        return "\n".join(lines)

    def populate_condition_properties(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        data.setdefault("branch_timeout", 0.4)
        data.setdefault("branch_threshold", 0.85)
        data.setdefault("branch_min_margin", 0.03)
        data.setdefault("on_no_match", "skip")

        self.add_readonly("逻辑摘要", self.condition_logic_summary(step))

        timeout = QDoubleSpinBox()
        timeout.setRange(0.1, 10.0)
        timeout.setDecimals(2)
        timeout.setSingleStep(0.1)
        timeout.setValue(float(data.get("branch_timeout", 0.4)))
        timeout.valueChanged.connect(lambda value: self.update_input(step, "branch_timeout", value))
        self.property_layout.addRow("单个判断最长秒数", timeout)

        threshold = QDoubleSpinBox()
        threshold.setRange(0.1, 1.0)
        threshold.setDecimals(2)
        threshold.setSingleStep(0.01)
        threshold.setValue(float(data.get("branch_threshold", 0.85)))
        threshold.valueChanged.connect(lambda value: self.update_input(step, "branch_threshold", value))
        self.property_layout.addRow("默认图片阈值", threshold)

        margin = QDoubleSpinBox()
        margin.setRange(0.0, 0.5)
        margin.setDecimals(2)
        margin.setSingleStep(0.01)
        margin.setValue(float(data.get("branch_min_margin", 0.03)))
        margin.valueChanged.connect(lambda value: self.update_input(step, "branch_min_margin", value))
        self.property_layout.addRow("分支最小差值", margin)

        on_no_match = QComboBox()
        on_no_match.addItem("都没命中就跳过继续", "skip")
        on_no_match.addItem("都没命中就报错停止", "fail")
        current = str(data.get("on_no_match") or "skip")
        index = on_no_match.findData(current)
        on_no_match.setCurrentIndex(index if index >= 0 else 0)
        on_no_match.currentIndexChanged.connect(
            lambda _index, combo=on_no_match: self.update_input(step, "on_no_match", combo.currentData())
        )
        self.property_layout.addRow("未命中时", on_no_match)

        actions = QHBoxLayout()
        add_image_branch = QPushButton("新建图片分支")
        add_image_branch.clicked.connect(lambda: self.add_condition_image_branch(step))
        add_default_branch = QPushButton("新建默认分支")
        add_default_branch.clicked.connect(lambda: self.add_condition_default_branch(step))
        actions.addWidget(add_image_branch)
        actions.addWidget(add_default_branch)
        self.property_layout.addRow("分支", actions)

        children = step.get("children") or []
        if children:
            lines = []
            for index, branch in enumerate(children, start=1):
                branch_input = branch.get("input") or {}
                branch_children = branch.get("children") or []
                action_count = len(branch_children)
                if branch_input.get("condition_default_branch"):
                    judge_text = "默认：前面都没命中"
                elif branch_input.get("condition_template_path"):
                    judge_text = "图片命中"
                else:
                    judge_text = "未设置判断图"
                lines.append(f"{index}. {branch.get('name', branch.get('id'))}：{judge_text}，动作 {action_count} 个")
            self.add_readonly("已有分支", "\n".join(lines))
        else:
            self.add_readonly("已有分支", "暂无。点“框选图片分支”后，再选中这个分支往里面插动作。")

        self.add_readonly(
            "用法",
            "判断截图都在这个条件分支主界面维护。每个分支会生成一个空动作流程；选中分支后往里面插步骤即可。",
        )

    def add_condition_branch_to_step(
        self,
        condition_step: dict[str, Any],
        branch: dict[str, Any],
        *,
        select_branch: bool = True,
    ) -> None:
        condition_step.setdefault("children", []).append(branch)
        self.refresh_step_list(select_step_id=branch["id"] if select_branch else condition_step["id"])
        self.log(f"已添加条件分支：{branch.get('name')}")

    def create_condition_branch_loop(self, name: str, *, default_branch: bool = False) -> dict[str, Any]:
        branch = create_step("loop", name)
        branch["input"].update(
            {
                "times": 1,
                "loop_mode": "fixed_count",
                "break_on_failure": True,
                "fail_when_max_reached": False,
                "condition_branch": True,
                "condition_default_branch": bool(default_branch),
            }
        )
        branch["children"] = []
        self.refresh_loop_body_metadata(branch)
        return branch

    def add_condition_empty_branch(self, condition_step: dict[str, Any]) -> None:
        children = condition_step.setdefault("children", [])
        branch = self.create_condition_branch_loop(f"分支_{len(children) + 1:02d}")
        self.add_condition_branch_to_step(condition_step, branch)

    def add_condition_default_branch(self, condition_step: dict[str, Any]) -> None:
        children = condition_step.setdefault("children", [])
        branch = self.create_condition_branch_loop(f"默认分支_{len(children) + 1:02d}", default_branch=True)
        self.add_condition_branch_to_step(condition_step, branch)

    def add_condition_image_branch(self, condition_step: dict[str, Any]) -> None:
        if not self.capture_current_screen(save=False, allow_cached=False) or self.current_frame is None:
            QMessageBox.information(self, "条件分支", "请先连接 MuMu，并确认 ADB 可以截图。")
            return
        name, ok = QInputDialog.getText(
            self,
            "条件分支",
            "分支名称，例如：出现验证码 / 领取奖励 / 进入战斗",
            text=f"分支_{len(condition_step.get('children') or []) + 1:02d}",
        )
        if not ok:
            return
        branch_name = name.strip() or f"分支_{len(condition_step.get('children') or []) + 1:02d}"
        dialog = LargeRegionDialog(
            self.current_frame,
            title=f"条件分支 - {branch_name}",
            fixed_kind="screenshot",
            hint="框选这个分支要判断的画面区域。运行时命中它，就执行这个分支后面的动作。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return
        bounded = dialog.selected_rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        paths = self.storage.make_asset_paths(self.current_map, "loop_condition", f"{condition_step.get('name', 'condition')}_{branch_name}")
        raw_path = Path(paths["raw_path"])
        crop_path = Path(paths["crop_path"])
        annotated_path = Path(paths["annotated_path"])
        self.current_frame.save(str(raw_path))
        self.current_frame.copy(bounded).save(str(crop_path))
        annotated = self.current_frame.copy()
        painter = QPainter(annotated)
        painter.setPen(QPen(QColor("#00c853"), 3))
        painter.drawRect(bounded)
        painter.end()
        annotated.save(str(annotated_path))

        asset_id = str(paths["asset_id"])
        self.storage.add_asset(
            asset_id=asset_id,
            user_name=branch_name,
            auto_name=str(paths["auto_name"]),
            asset_type="loop_condition",
            map_id=self.current_map,
            script_name=self.flow["script_name"],
            step_id=condition_step["id"],
            bbox=bbox_from_rect(bounded),
            raw_path=raw_path,
            crop_path=crop_path,
            annotated_path=annotated_path,
            metadata={"created_from": "condition_branch"},
        )

        branch = self.create_condition_branch_loop(branch_name)
        branch["assets"].append(asset_id)
        branch["input"].update(
            {
                "condition_asset_id": asset_id,
                "condition_bbox": bbox_from_rect(bounded),
                "condition_template_path": self.storage.rel(crop_path),
                "condition_threshold": float(condition_step.get("input", {}).get("branch_threshold", 0.85)),
            }
        )
        self.refresh_loop_body_metadata(branch)
        self.add_condition_branch_to_step(condition_step, branch)
        self.log(f"条件分支已保存判断图，分支动作流程为空：{branch_name} -> {self.storage.rel(crop_path)}")

    def populate_condition_branch_properties(self, step: dict[str, Any]) -> None:
        data = step.setdefault("input", {})
        is_default = bool(data.get("condition_default_branch"))
        self.add_readonly("分支说明", "这是条件分支下的动作流程。判断截图在上一级“条件分支”步骤里维护。")
        self.add_readonly("分支类型", "默认分支" if is_default else "图片判断分支")
        if is_default:
            self.add_readonly("执行条件", "前面的图片判断分支都没命中时，执行这个分支。")
            self.add_readonly("判断截图", "默认分支不需要截图，前面的分支都没命中时执行。")
        else:
            template_path = str(data.get("condition_template_path") or "")
            threshold_value = float(data.get("condition_threshold", 0.85))
            self.add_readonly("执行条件", f"看到下面这张判断图就执行这个分支；匹配阈值 {threshold_value:.2f}。")
            self.add_readonly("判断截图", template_path or "未设置")
            if data.get("condition_bbox"):
                self.add_readonly("判断区域", str(data.get("condition_bbox")))
            threshold = QDoubleSpinBox()
            threshold.setRange(0.1, 1.0)
            threshold.setDecimals(2)
            threshold.setSingleStep(0.01)
            threshold.setValue(threshold_value)
            threshold.valueChanged.connect(lambda value: self.update_input(step, "condition_threshold", value))
            self.property_layout.addRow("匹配阈值", threshold)
            recapture = QPushButton("重新框选判断图")
            recapture.clicked.connect(lambda: self.recapture_condition_branch_image(step))
            self.property_layout.addRow("判断截图", recapture)
            self.add_asset_preview(template_path)
        action_count = len(step.get("children") or [])
        self.add_readonly("动作数量", str(action_count))
        self.add_readonly("添加动作", "选中这个分支后，用“插点击 / 插等待 / 当前画面操作”等按钮添加步骤，它们会成为这个分支命中后的动作。")

    def recapture_condition_branch_image(self, branch_step: dict[str, Any]) -> None:
        if not self.capture_current_screen(save=False, allow_cached=False) or self.current_frame is None:
            QMessageBox.information(self, "条件分支", "请先连接 MuMu，并确认 ADB 可以截图。")
            return
        branch_name = str(branch_step.get("name") or "分支")
        dialog = LargeRegionDialog(
            self.current_frame,
            title=f"重新框选判断图 - {branch_name}",
            fixed_kind="screenshot",
            hint="框选这个分支要判断的画面区域。建议把复选框和文字一起框进去，避免只框绿色勾。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return
        bounded = dialog.selected_rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        paths = self.storage.make_asset_paths(self.current_map, "loop_condition", f"{branch_name}_判断")
        raw_path = Path(paths["raw_path"])
        crop_path = Path(paths["crop_path"])
        annotated_path = Path(paths["annotated_path"])
        self.current_frame.save(str(raw_path))
        self.current_frame.copy(bounded).save(str(crop_path))
        annotated = self.current_frame.copy()
        painter = QPainter(annotated)
        painter.setPen(QPen(QColor("#00c853"), 3))
        painter.drawRect(bounded)
        painter.end()
        annotated.save(str(annotated_path))

        asset_id = str(paths["asset_id"])
        self.storage.add_asset(
            asset_id=asset_id,
            user_name=branch_name,
            auto_name=str(paths["auto_name"]),
            asset_type="loop_condition",
            map_id=self.current_map,
            script_name=self.flow["script_name"],
            step_id=branch_step["id"],
            bbox=bbox_from_rect(bounded),
            raw_path=raw_path,
            crop_path=crop_path,
            annotated_path=annotated_path,
            metadata={"created_from": "condition_branch_recapture"},
        )
        branch_step.setdefault("assets", []).append(asset_id)
        data = branch_step.setdefault("input", {})
        data["condition_asset_id"] = asset_id
        data["condition_bbox"] = bbox_from_rect(bounded)
        data["condition_template_path"] = self.storage.rel(crop_path)
        data.setdefault("condition_threshold", 0.85)
        self.refresh_step_list(select_step_id=branch_step["id"])
        self.populate_properties(branch_step)
        self.log(f"已重新框选条件分支判断图：{branch_name} -> {data['condition_template_path']}")

    def populate_loop_properties(self, step: dict[str, Any]) -> None:
        data = step["input"]
        condition = data.setdefault(
            "exit_condition",
            {"type": "none", "template_path": None, "asset_id": None, "threshold": 0.85},
        )

        self.add_readonly("逻辑摘要", self.loop_logic_summary(step))

        mode = QComboBox()
        mode.addItem("固定次数", "fixed_count")
        mode.addItem("直到图片出现", "image_found")
        mode.addItem("直到图片消失", "image_missing")
        current_mode = data.get("loop_mode") or condition.get("type") or "fixed_count"
        if current_mode == "none":
            current_mode = "fixed_count"
        index = mode.findData(current_mode)
        mode.setCurrentIndex(index if index >= 0 else 0)
        mode.currentIndexChanged.connect(lambda _index: self.update_loop_mode(step, str(mode.currentData()), refresh=True))
        self.property_layout.addRow("结束方式", mode)

        times = QSpinBox()
        times.setRange(1, 999)
        times.setValue(int(data.get("times", 1)))
        times.valueChanged.connect(lambda value: self.update_input(step, "times", value))
        self.property_layout.addRow("最大循环次数", times)

        save_preset = QPushButton("保存这个循环为预设")
        save_preset.clicked.connect(self.save_selected_loop_as_preset)
        self.property_layout.addRow("预设", save_preset)

        fail_when_max = QCheckBox("达到最大次数还未满足条件时失败")
        fail_when_max.setChecked(bool(data.get("fail_when_max_reached", True)))
        fail_when_max.toggled.connect(lambda value: self.update_input(step, "fail_when_max_reached", value))
        self.property_layout.addRow("上限处理", fail_when_max)

        break_on_failure = QCheckBox("循环体失败时停止")
        break_on_failure.setChecked(bool(data.get("break_on_failure", True)))
        break_on_failure.toggled.connect(lambda value: self.update_input(step, "break_on_failure", value))
        self.property_layout.addRow("失败处理", break_on_failure)

        first_step_guard = QCheckBox("第 1 个子步骤跳过时，跳过整个循环")
        first_step_guard.setChecked(bool(data.get("first_step_skip_exits_loop", False)))
        first_step_guard.setToolTip("适合把第 1 个图片识别步骤设为可选画面：画面没出现时直接跳过整个循环。")
        first_step_guard.toggled.connect(lambda value: self.update_input(step, "first_step_skip_exits_loop", value))
        self.property_layout.addRow("入口判断", first_step_guard)

        threshold = QDoubleSpinBox()
        threshold.setRange(0.1, 1.0)
        threshold.setDecimals(2)
        threshold.setSingleStep(0.01)
        threshold.setValue(float(condition.get("threshold", 0.85)))
        threshold.valueChanged.connect(lambda value: self.update_loop_condition(step, "threshold", value))
        self.property_layout.addRow("图片阈值", threshold)

        template_path = str(condition.get("template_path") or "")
        self.add_readonly("判断图片", template_path or "未设置")
        self.add_asset_preview(template_path)
        timing = str(data.get("exit_check_timing") or "before_each_step")
        timing_text = (
            "每轮完整执行完循环体后检查；适合战斗切换，保证退出时已回到 1 号角色。"
            if timing == "after_iteration"
            else "每轮开始、每个子步骤前、每轮结束都会检查；图片出现就立即跳出循环。"
        )
        self.add_readonly("判断方式", timing_text)
        capture_button = QPushButton("框选结束判断图片")
        capture_button.clicked.connect(lambda: self.capture_loop_exit_template(step))
        self.property_layout.addRow(capture_button)

        children = step.get("children") or []
        if children:
            child_names = [
                f"{index}. {child.get('name', child.get('id'))} ({STEP_LABELS.get(child.get('type', ''), child.get('type', ''))})"
                for index, child in enumerate(children, start=1)
            ]
            self.add_readonly("子步骤", "\n".join(child_names))
            return

        body_ids = data.get("body_step_ids") or []
        body_names = []
        for body_id in body_ids:
            body_step = self.step_by_id(body_id)
            if body_step:
                body_names.append(body_step.get("name", body_id))
            else:
                body_names.append(f"{body_id} (已丢失)")
        self.add_readonly("循环步骤", " -> ".join(body_names) if body_names else "未设置")

    def update_loop_mode(self, step: dict[str, Any], mode: str, refresh: bool = False) -> None:
        data = step.setdefault("input", {})
        data["loop_mode"] = mode
        condition = data.setdefault("exit_condition", {})
        condition["type"] = "none" if mode == "fixed_count" else mode
        if refresh:
            self.populate_properties(step)

    def update_loop_condition(self, step: dict[str, Any], key: str, value: Any) -> None:
        step.setdefault("input", {}).setdefault("exit_condition", {})[key] = value

    def capture_loop_exit_template(self, step: dict[str, Any]) -> None:
        if not self.capture_current_screen(save=False, allow_cached=False) or self.current_frame is None:
            QMessageBox.information(self, "循环结束判断", "请先连接 MuMu 并显示游戏画面。")
            return
        dialog = LargeRegionDialog(
            self.current_frame,
            title="循环结束判断图片",
            fixed_kind="screenshot",
            hint="框选能代表循环完成的画面区域，例如 5/5、完成提示或任务状态。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return
        bounded = dialog.selected_rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        user_name = f"{step.get('name', 'loop')}_结束判断"
        paths = self.storage.make_asset_paths(self.current_map, "loop_condition", user_name)
        raw_path = Path(paths["raw_path"])
        crop_path = Path(paths["crop_path"])
        annotated_path = Path(paths["annotated_path"])
        self.current_frame.save(str(raw_path))
        self.current_frame.copy(bounded).save(str(crop_path))
        annotated = self.current_frame.copy()
        painter = QPainter(annotated)
        painter.setPen(QPen(QColor("#ffb020"), 3))
        painter.drawRect(bounded)
        painter.end()
        annotated.save(str(annotated_path))
        asset_id = str(paths["asset_id"])
        self.storage.add_asset(
            asset_id=asset_id,
            user_name=user_name,
            auto_name=str(paths["auto_name"]),
            asset_type="loop_condition",
            map_id=self.current_map,
            script_name=self.flow["script_name"],
            step_id=step["id"],
            bbox=bbox_from_rect(bounded),
            raw_path=raw_path,
            crop_path=crop_path,
            annotated_path=annotated_path,
            metadata={"created_from": "loop_exit_condition"},
        )
        condition = step.setdefault("input", {}).setdefault("exit_condition", {})
        condition["template_path"] = self.storage.rel(crop_path)
        condition["asset_id"] = asset_id
        condition.setdefault("threshold", 0.85)
        if step["input"].get("loop_mode") in {None, "fixed_count"}:
            self.update_loop_mode(step, "image_found")
        if "战斗" in str(step.get("name") or ""):
            step.setdefault("input", {})["exit_check_timing"] = "after_iteration"
        self.refresh_step_list(select_step_id=step["id"])
        self.populate_properties(step)
        self.log(f"已设置循环结束判断图片：{condition['template_path']}")

    def add_readonly(self, label: str, value: str) -> None:
        text = QLabel(value)
        text.setWordWrap(True)
        self.property_layout.addRow(label, text)

    def add_asset_preview(self, rel_path: str | None) -> None:
        path = self.storage.abs(rel_path)
        if not path or not path.exists():
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setPixmap(
            pixmap.scaled(240, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
        preview.setFrameShape(QFrame.Shape.StyledPanel)
        self.property_layout.addRow("预览", preview)

    def clear_layout(self, layout: QFormLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                while child_layout.count():
                    child = child_layout.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

    def update_step_value(self, step: dict[str, Any], key: str, value: Any, refresh: bool = False) -> None:
        step[key] = value
        if key == "name":
            self.refresh_step_card(step["id"])
        if refresh:
            self.refresh_step_list(select_step_id=step["id"])
        self.update_dirty_indicator()

    def update_input(self, step: dict[str, Any], key: str, value: Any) -> None:
        step.setdefault("input", {})[key] = value
        self.update_dirty_indicator()

    def update_coord(self, step: dict[str, Any], index: int, value: int) -> None:
        coord = step.setdefault("input", {}).setdefault("screen_coord", [0, 0])
        coord[index] = int(value)
        self.update_dirty_indicator()

    def update_input_coord(self, step: dict[str, Any], key: str, index: int, value: int) -> None:
        coord = step.setdefault("input", {}).setdefault(key, [0, 0])
        while len(coord) <= index:
            coord.append(0)
        coord[index] = int(value)
        self.update_dirty_indicator()

    def refresh_step_card(self, step_id: str) -> None:
        for row in range(self.step_list.count()):
            item = self.step_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) != str(step_id):
                continue
            widget = self.step_list.itemWidget(item)
            if isinstance(widget, StepCard):
                widget.refresh_name()
            return

    def normalize_step_for_dirty_check(self, step: dict[str, Any]) -> None:
        step.pop("output", None)
        data = step.get("input")
        if isinstance(data, dict):
            for key in ("last_result", "confidence", "last_coord"):
                data.pop(key, None)
        for child in step.get("children") or []:
            if isinstance(child, dict):
                self.normalize_step_for_dirty_check(child)

    def flow_for_dirty_check(self, flow: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(flow)
        snapshot.pop("updated_at", None)

        settings = snapshot.setdefault("settings", {})
        if isinstance(settings, dict):
            region = settings.get("game_coord_region")
            try:
                normalized_region = [int(value) for value in region] if region else []
            except (TypeError, ValueError):
                normalized_region = []
            if not normalized_region or normalized_region == OLD_DEFAULT_GAME_COORD_REGION:
                settings["game_coord_region"] = list(DEFAULT_GAME_COORD_REGION)

        for step in snapshot.get("steps") or []:
            if isinstance(step, dict):
                self.normalize_step_for_dirty_check(step)
        return snapshot

    def flow_snapshot(self, flow: dict[str, Any]) -> str:
        stable_flow = self.flow_for_dirty_check(flow)
        return json.dumps(stable_flow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def capture_saved_flow_snapshot(self) -> None:
        self.sync_steps_from_list()
        self._saved_flow_snapshot = self.flow_snapshot(self.flow)
        self.update_dirty_indicator()

    def has_unsaved_changes(self, *, sync_steps: bool = True) -> bool:
        if sync_steps:
            self.sync_steps_from_list()
        return self.flow_snapshot(self.flow) != self._saved_flow_snapshot

    def save_prompt_needs_name(self) -> bool:
        if self.current_flow_path is not None:
            return False
        script_name = str(self.flow.get("script_name") or "")
        return not bool(script_name)

    def confirm_save_if_dirty(self, title: str, message: str) -> bool:
        if not self.has_unsaved_changes():
            return True
        reply = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Yes:
            return self.save_current_flow(prompt_name=self.save_prompt_needs_name())
        return True

    def update_dirty_indicator(self) -> None:
        self.update_script_title()

    def update_script_title(self) -> None:
        script_name = str(self.flow.get("script_name") or "未命名副本")
        marker = " *" if self.has_unsaved_changes(sync_steps=False) else ""
        self.setWindowTitle(f"StoneAge Script Studio - {script_name}{marker}")
        if hasattr(self, "script_label"):
            self.script_label.setText(f"脚本：{script_name}{marker}")

    def suggested_script_name(self) -> str:
        used = {str(row.get("script_name", "")) for row in self.storage.list_script_flows()}
        numbers: list[int] = []
        for name in used:
            match = re.fullmatch(r"副本_(\d+)", name)
            if match:
                numbers.append(int(match.group(1)))
        candidate = f"副本_{(max(numbers) + 1 if numbers else 1):03d}"
        while candidate in used:
            number = int(candidate.rsplit("_", 1)[1]) + 1
            candidate = f"副本_{number:03d}"
        return candidate

    def ask_script_name(self, title: str, label: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, label, text=default)
        if not ok:
            return None
        cleaned = name.strip()
        if not cleaned:
            QMessageBox.information(self, title, "副本脚本名称不能为空。")
            return None
        return cleaned

    def new_flow_dialog(self) -> None:
        if not self.confirm_save_if_dirty("新建副本流程", "当前副本流程有未保存更改。新建前要保存吗？"):
            return

        name = self.ask_script_name("新建副本流程", "副本脚本名称", self.suggested_script_name())
        if not name:
            return
        if self.storage.script_exists(name):
            reply = QMessageBox.question(
                self,
                "新建副本流程",
                f"副本脚本“{name}”已经存在。\n是否直接加载这个已有流程？",
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.load_flow_path(self.storage.script_flow_file(name))
            return

        self.flow = create_flow(name)
        self.current_flow_path = None
        self.capture_saved_flow_snapshot()
        self.refresh_step_list()
        self.populate_properties(None)
        self.update_script_title()
        self.log(f"已新建副本流程：{name}。接下来录制和添加步骤都会保存到这个流程。")

    def save_current_flow(self, checked: bool = False, prompt_name: bool = True) -> bool:
        del checked
        self.sync_steps_from_list()
        current_name = str(self.flow.get("script_name") or self.suggested_script_name())
        if prompt_name:
            name = self.ask_script_name("保存副本流程", "保存为副本脚本", current_name)
            if not name:
                return False
            if name != current_name and self.storage.script_exists(name):
                reply = QMessageBox.question(
                    self,
                    "保存副本流程",
                    f"副本脚本“{name}”已经存在。\n是否覆盖它的 flow.json？",
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return False
            self.flow["script_name"] = name
        path = self.storage.flow_path(self.flow["script_name"])
        backup = self.storage.backup_flow(self.flow["script_name"])
        save_flow(path, self.flow)
        self.current_flow_path = path
        self.capture_saved_flow_snapshot()
        self.update_script_title()
        self.storage.save_last_flow_path(path)
        if backup:
            self.log(f"已备份旧流程：{backup}")
        self.log(f"已保存副本流程：{self.flow['script_name']} -> {path}")
        return True

    def load_flow_dialog(self) -> None:
        dialog = FlowLoadDialog(self.storage, self)
        if dialog.table.rowCount() == 0:
            QMessageBox.information(self, "加载副本流程", "还没有保存过任何副本流程。请先“新建副本”或“保存”。")
            return
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_flow_path:
            return
        if not self.confirm_save_if_dirty("加载副本流程", "当前副本流程有未保存更改。加载其他副本前要保存吗？"):
            return
        self.load_flow_path(Path(dialog.selected_flow_path))

    def load_flow_path(self, path: Path) -> None:
        try:
            path = Path(path)
            self.flow = load_flow(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "加载失败", str(exc))
            return
        self.flow.setdefault("script_name", Path(path).parent.name)
        self.current_flow_path = path
        self.capture_saved_flow_snapshot()
        self.refresh_step_list()
        self.populate_properties(self.current_step())
        self.update_script_title()
        self.storage.save_last_flow_path(path)
        self.log(f"已加载副本流程：{self.flow.get('script_name')} -> {path}")
        self.refresh_runner_panel()

    def refresh_runner_panel(self) -> None:
        if not hasattr(self, "runner_panel"):
            return
        self.runner_panel.load_scripts()
        self.runner_panel.refresh_steps()
        self.runner_panel.refresh_loop_stats()

    def run_selected_step(self) -> None:
        step = self.current_step()
        if not step:
            return
        self.run_step_for_test(step)

    def run_step_for_test(self, step: dict[str, Any]) -> bool:
        if self.is_main_thread():
            return self.start_runtime_task("测试步骤", lambda: self.run_step_for_test(copy.deepcopy(step)))
        self._stop_requested = False
        if step.get("type") == "loop":
            self.log("测试循环步骤：只执行 1 轮；正式运行会按最大循环次数和结束条件执行。")
            step = copy.deepcopy(step)
            step.setdefault("input", {})["times"] = 1
            step.setdefault("input", {})["fail_when_max_reached"] = False
            index = self.top_level_index(step["id"])
            ok, _ = self.execute_loop_step(step, index)
        else:
            step_index = self.top_level_index(step["id"]) if step.get("id") else None
            ok = self.execute_step(step, index=step_index + 1 if step_index is not None else None)
        self.log(f"测试{'完成' if ok else '失败/停止'}：{step.get('name', step.get('id'))}")
        return ok

    def run_all_steps(self) -> None:
        self.run_flow_from_index(0, "制作模式：从头测试脚本。", "制作模式：测试结束。", reset_stop=True)

    def is_main_thread(self) -> bool:
        app = QApplication.instance()
        return app is None or QThread.currentThread() == app.thread()

    def invoke_on_ui_thread(self, callback: Callable[[], Any]) -> Any:
        if self.is_main_thread():
            return callback()
        done = threading.Event()
        payload: dict[str, Any] = {
            "callback": callback,
            "done": done,
            "result": None,
            "error": None,
        }
        self.ui_call_requested.emit(payload)
        while not done.wait(0.05):
            continue
        if payload["error"] is not None:
            raise payload["error"]
        return payload["result"]

    def _run_ui_callable(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        try:
            callback = payload.get("callback")
            if callable(callback):
                payload["result"] = callback()
        except Exception as exc:  # noqa: BLE001 - propagate to runtime worker safely
            payload["error"] = exc
        finally:
            done = payload.get("done")
            if hasattr(done, "set"):
                done.set()

    def runtime_yield(self) -> None:
        if self.is_main_thread():
            QApplication.processEvents()

    def start_runtime_task(self, name: str, callback: Callable[[], Any]) -> bool:
        if self.runtime_worker is not None:
            self.log(f"已有运行任务正在执行：{self.runtime_worker.name}。请先停止或等待结束。")
            return False
        if self.is_main_thread():
            self.pause_preview()
        self._runtime_error_active = False
        self._runtime_alert_reported = False
        self.emit_runtime_status("running", task=name)
        worker = RuntimeWorker(name, callback)
        self.runtime_worker = worker
        worker.failed.connect(self.handle_runtime_failure)
        worker.finished.connect(lambda checked=False, target=worker: self.clear_runtime_worker(target))
        worker.start()
        return True

    def clear_runtime_worker(self, worker: RuntimeWorker) -> None:
        if self.runtime_worker is worker:
            self.runtime_worker = None
            if self._runtime_error_active:
                self.emit_runtime_status("error", detail="运行已停止，请查看 Bug 待修复")
            elif self._stop_requested:
                self.emit_runtime_status("idle", detail="已停止")
            else:
                self.emit_runtime_status("idle", detail="运行结束")

    def run_from_selected_step(self) -> None:
        self.sync_steps_from_list()
        step = self.current_step()
        if not step:
            QMessageBox.information(self, "从选中运行", "请先在左侧选中一个步骤。")
            return
        location = self.step_location(step["id"])
        if not location:
            QMessageBox.warning(self, "从选中运行", "找不到当前选中的步骤。")
            return

        _container, index, parent = location
        if parent:
            parent_index = self.top_level_index(parent["id"])
            next_top_index = (parent_index + 1) if parent_index is not None else len(self.flow["steps"])
            self.start_runtime_task(
                "从选中运行",
                lambda: self.run_loop_child_then_flow(parent, index, next_top_index, step),
            )
            return

        self.run_flow_from_index(index, f"从选中步骤继续运行：第 {index + 1} 步 {step.get('name')}", "从选中运行结束。", reset_stop=True)

    def run_from_step_id(self, step_id: str) -> None:
        if step_id:
            self.select_step_by_id(step_id)
        self.run_from_selected_step()

    def run_loop_child_then_flow(
        self,
        parent: dict[str, Any],
        child_index: int,
        next_top_index: int,
        selected_step: dict[str, Any],
    ) -> None:
        self._stop_requested = False
        self.log(f"从循环子步骤继续运行：{parent.get('name')} -> {selected_step.get('name')}")
        ok = self.execute_loop_children_once(parent, child_index)
        if not ok and self.flow.get("settings", {}).get("stop_on_failure", True):
            self.log(f"从选中运行停止：{selected_step.get('name')}")
            return
        self.run_flow_from_index(next_top_index, "继续运行外层后续步骤。", "从选中运行结束。", reset_stop=False)

    def dungeon_entry_boundary_index(self) -> int | None:
        settings = self.flow.get("settings") or {}
        steps = self.flow.get("steps") or []
        configured_id = str(settings.get("dungeon_entry_step_id") or settings.get("safe_boundary_step_id") or "")
        if configured_id:
            for index, step in enumerate(steps):
                if str(step.get("id") or "") == configured_id:
                    return index

        configured_name = str(settings.get("dungeon_entry_step_name") or settings.get("safe_boundary_step_name") or "")
        if configured_name:
            for index, step in enumerate(steps):
                if str(step.get("name") or "") == configured_name:
                    return index

        for index, step in enumerate(steps):
            name = str(step.get("name") or "")
            if "进入" in name and "副本" in name:
                return index
        return None

    def pre_dungeon_retry_limit(self) -> int:
        settings = self.flow.get("settings") or {}
        value = settings.get("pre_dungeon_retry_limit", DEFAULT_PRE_DUNGEON_RETRY_LIMIT)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return DEFAULT_PRE_DUNGEON_RETRY_LIMIT

    def remember_flow_failure(self, index: int, step: dict[str, Any]) -> None:
        boundary_index = self.dungeon_entry_boundary_index()
        self._last_flow_failure = {
            "index": int(index),
            "step_id": str(step.get("id") or ""),
            "step_name": str(step.get("name") or ""),
            "step_type": str(step.get("type") or ""),
            "boundary_index": boundary_index,
            "before_or_at_dungeon_boundary": boundary_index is not None and int(index) <= int(boundary_index),
        }

    def failure_before_or_at_dungeon_boundary(self, failure: dict[str, Any] | None = None) -> bool:
        failure = failure or self._last_flow_failure
        if not failure:
            return False
        boundary_index = failure.get("boundary_index")
        index = failure.get("index")
        if boundary_index is None or index is None:
            return False
        return int(index) <= int(boundary_index)

    def flow_failure_label(self, failure: dict[str, Any] | None = None) -> str:
        failure = failure or self._last_flow_failure
        if not failure:
            return "未知步骤"
        index = int(failure.get("index") or 0) + 1
        name = str(failure.get("step_name") or failure.get("step_id") or "未知步骤")
        return f"第 {index} 步 {name}"

    def run_flow_from_index(
        self,
        start_index: int,
        start_message: str,
        end_message: str,
        *,
        reset_stop: bool,
    ) -> bool:
        if self.is_main_thread():
            self.sync_steps_from_list()
            return self.start_runtime_task(
                "流程运行",
                lambda: self.run_flow_from_index(
                    start_index,
                    start_message,
                    end_message,
                    reset_stop=reset_stop,
                ),
            )
        if reset_stop:
            self._stop_requested = False
        self.log(start_message)
        self._last_flow_failure = None
        if start_index >= len(self.flow["steps"]):
            self.log("后面没有更多步骤。")
            self.log(end_message)
            return True
        index = max(0, start_index)
        completed = True
        while index < len(self.flow["steps"]):
            self.runtime_yield()
            if self._stop_requested:
                self.log("运行已停止。")
                completed = False
                break
            step = self.flow["steps"][index]
            if not step.get("enabled", True):
                self.log(f"跳过禁用步骤 {index + 1}: {step.get('name')}")
                index += 1
                continue
            if step.get("type") == "loop":
                ok, next_index = self.execute_loop_step(step, index)
                if not ok and not self._stop_requested:
                    self.record_bug_report(
                        f"循环失败：{step.get('name')}",
                        kind="loop_failure",
                        step=step,
                        metadata={"index": index + 1},
                        cooldown_key=f"loop_failure:{step.get('id')}:{int(time.time() // 30)}",
                    )
            else:
                ok = self.execute_step(step, index=index + 1)
                next_index = index + 1
            if not ok and self.flow.get("settings", {}).get("stop_on_failure", True):
                self.remember_flow_failure(index, step)
                self.log(f"流程在步骤 {index + 1} 停止：{step.get('name')}")
                completed = False
                break
            index = next_index
        self.log(end_message)
        return completed

    def run_flow_repeated(self, cycles: int | None, start_index: int = 0) -> bool:
        if self.is_main_thread():
            self.sync_steps_from_list()
            return self.start_runtime_task(
                "循环运行",
                lambda: self.run_flow_repeated(cycles, start_index=start_index),
            )
        self._stop_requested = False
        cycle_text = "一直循环" if cycles is None else f"{cycles} 次"
        self.log(f"开始循环运行脚本：{cycle_text}。")
        if start_index >= len(self.flow["steps"]):
            self.log("流程没有可运行步骤，循环运行未开始。")
            return False
        boundary_index = self.dungeon_entry_boundary_index()
        retry_limit = self.pre_dungeon_retry_limit() if start_index == 0 else 0
        pre_dungeon_retries = 0
        if boundary_index is not None and retry_limit > 0:
            boundary_step = self.flow["steps"][boundary_index]
            self.log(
                f"进本安全边界：第 {boundary_index + 1} 步 {boundary_step.get('name')}。"
                f"边界前失败会从头重试，最多 {retry_limit} 次；边界后失败会停止等待处理。"
            )
        attempted_cycles = 0
        completed_cycles = 0
        self.emit_loop_stats(current_attempt=attempted_cycles, current_completed=completed_cycles, target=cycles)
        while cycles is None or attempted_cycles < cycles:
            self.runtime_yield()
            if self._stop_requested:
                self.log("循环运行已停止。")
                return False
            round_number = attempted_cycles + 1
            total_text = "∞" if cycles is None else str(cycles)
            cycle_started_at = now_iso()
            cycle_started_monotonic = time.monotonic()
            self.emit_loop_stats(
                current_attempt=round_number,
                current_completed=completed_cycles,
                target=cycles,
                active_round=round_number,
            )
            ok = self.run_flow_from_index(
                start_index,
                f"循环运行：第 {round_number}/{total_text} 轮开始。",
                f"循环运行：第 {round_number}/{total_text} 轮结束。",
                reset_stop=False,
            )
            cycle_duration = time.monotonic() - cycle_started_monotonic
            if ok:
                attempted_cycles += 1
                completed_cycles += 1
                pre_dungeon_retries = 0
                self.storage.record_script_loop_cycle(
                    str(self.flow.get("script_name") or ""),
                    success=True,
                    duration_seconds=cycle_duration,
                    cycle_number=round_number,
                    started_at=cycle_started_at,
                )
                self.log(f"本轮副本耗时：{format_duration(cycle_duration)}（第 {round_number}/{total_text} 轮）。")
            elif (
                not self._stop_requested
                and retry_limit > 0
                and pre_dungeon_retries < retry_limit
                and self.failure_before_or_at_dungeon_boundary()
            ):
                pre_dungeon_retries += 1
                self.log(
                    f"进本前失败：{self.flow_failure_label()}。"
                    f"安全从头重试当前轮 {pre_dungeon_retries}/{retry_limit}。"
                )
                self._runtime_error_active = False
                self._runtime_alert_reported = False
                self.emit_runtime_status(
                    "running",
                    task="循环运行",
                    detail=f"进本前失败，正在重试 {pre_dungeon_retries}/{retry_limit}",
                )
                if not self.sleep_with_events(0.5):
                    self.log(f"循环运行已停止，已完成 {completed_cycles} 轮，尝试 {attempted_cycles} 轮。")
                    return False
                continue
            elif not self._stop_requested:
                attempted_cycles += 1
                if boundary_index is not None:
                    if self.failure_before_or_at_dungeon_boundary():
                        if retry_limit > 0:
                            self.log(
                                f"进本前失败已达到自动重试上限 {retry_limit} 次，停止循环，等待处理。"
                            )
                    else:
                        boundary_step = self.flow["steps"][boundary_index]
                        self.log(
                            f"失败发生在进本边界后：{self.flow_failure_label()}；"
                            f"不会从第 1 步重跑，避免副本内步骤错位。"
                            f"边界是第 {boundary_index + 1} 步 {boundary_step.get('name')}。"
                        )
                self.storage.record_script_loop_cycle(
                    str(self.flow.get("script_name") or ""),
                    success=False,
                    duration_seconds=cycle_duration,
                    cycle_number=round_number,
                    started_at=cycle_started_at,
                    notes="流程失败",
                )
                self.log(f"本轮失败耗时：{format_duration(cycle_duration)}（第 {round_number}/{total_text} 轮）。")
            self.emit_loop_stats(current_attempt=attempted_cycles, current_completed=completed_cycles, target=cycles)
            if self._stop_requested:
                self.log(f"循环运行已停止，已完成 {completed_cycles} 轮，尝试 {attempted_cycles} 轮。")
                return False
            if not ok:
                self.log(f"循环运行因流程失败停止，已完成 {completed_cycles} 轮，尝试 {attempted_cycles} 轮。")
                self.record_bug_report(
                    f"副本循环运行失败：第 {attempted_cycles} 轮",
                    kind="cycle_failure",
                    metadata={
                        "attempted_cycles": attempted_cycles,
                        "completed_cycles": completed_cycles,
                        "failure": self._last_flow_failure or {},
                    },
                    cooldown_key=f"cycle:{self.flow.get('script_name')}:{attempted_cycles}:{int(time.time() // 60)}",
                )
                return False
            if cycles is None:
                if not self.sleep_with_events(0.1):
                    self.log(f"循环运行已停止，已完成 {completed_cycles} 轮，尝试 {attempted_cycles} 轮。")
                    return False
        self.log(f"循环运行完成：共完成 {completed_cycles} 轮，尝试 {attempted_cycles} 轮。")
        return True

    def execute_loop_children_once(self, loop_step: dict[str, Any], start_child_index: int = 0) -> bool:
        children = loop_step.get("children") or []
        if not children:
            self.log(f"循环没有子步骤：{loop_step.get('name')}")
            return True
        for child_index in range(max(0, start_child_index), len(children)):
            self.runtime_yield()
            if self._stop_requested:
                return False
            child_step = children[child_index]
            if not child_step.get("enabled", True):
                self.log(f"跳过循环体禁用步骤 {child_index + 1}: {child_step.get('name')}")
                continue
            self.log(f"继续循环体 {child_index + 1}/{len(children)}：{child_step.get('name')}")
            if child_step.get("type") == "loop":
                ok, _ = self.execute_loop_step(child_step, None)
            else:
                ok = self.execute_step(child_step)
            if not ok:
                self.log(f"循环子步骤失败：{child_step.get('name')}")
                return False
        return True

    def stop_run(self) -> None:
        self._stop_requested = True
        self.emit_runtime_status("stopping", detail="已请求停止")
        self.log("已请求停止。")

    def loop_body_indices(self, step: dict[str, Any], loop_index: int | None) -> list[int]:
        if loop_index is None:
            self.log("旧版循环步骤缺少外层位置，无法解析循环体。")
            return []
        body_ids = step.get("input", {}).get("body_step_ids") or []
        if not body_ids:
            start_id = step.get("input", {}).get("body_start_id")
            end_id = step.get("input", {}).get("body_end_id")
            if start_id and end_id:
                body_ids = [start_id, end_id]
        if not body_ids:
            self.log("循环步骤缺少循环体。")
            return []

        positions = {item["id"]: index for index, item in enumerate(self.flow["steps"])}
        missing = [step_id for step_id in body_ids if step_id not in positions]
        if missing:
            self.log(f"循环体步骤已丢失：{missing}")
            return []

        indices = sorted(positions[step_id] for step_id in body_ids)
        if indices[0] <= loop_index <= indices[-1]:
            self.log("循环体不能包含循环步骤本身。")
            return []
        if indices != list(range(indices[0], indices[-1] + 1)):
            self.log("循环体步骤已经不连续，请重新选择步骤创建循环。")
            return []
        return indices

    def loop_exit_condition_met(self, step: dict[str, Any]) -> bool:
        data = step.get("input", {})
        mode = data.get("loop_mode", "fixed_count")
        condition = data.get("exit_condition") or {}
        condition_type = condition.get("type") or ("none" if mode == "fixed_count" else mode)
        if condition_type in {"none", "fixed_count"}:
            return False
        template = self.storage.abs(condition.get("template_path"))
        if template is None or not template.exists():
            self.log("循环结束判断缺少图片模板。")
            return False
        if not self.refresh_current_frame():
            return False
        result = match_template_qimage(
            self.current_frame,
            template,
            float(condition.get("threshold", 0.85)),
            search_bbox=condition.get("search_bbox"),
        )
        if result.error:
            self.log(result.error)
        if condition_type == "image_found":
            self.log(f"循环结束判断：图片出现={result.found}，置信度 {result.confidence:.3f}")
            return bool(result.found)
        if condition_type == "image_missing":
            self.log(f"循环结束判断：图片消失={not result.found}，置信度 {result.confidence:.3f}")
            return not result.found
        return False

    def step_was_skipped(self, step: dict[str, Any]) -> bool:
        output = step.get("output") or {}
        return bool(isinstance(output, dict) and output.get("skipped"))

    def evaluate_condition_guard_step(self, guard_step: dict[str, Any], timeout: float) -> tuple[bool, bool, float]:
        step_type = guard_step.get("type")
        if step_type in {"image_check", "find_target", "click_target"}:
            data = guard_step.setdefault("input", {})
            template = self.storage.abs(data.get("template_path"))
            if not template or not template.exists():
                self.log(f"条件判断缺少图片模板：{guard_step.get('name')}")
                return False, False, 0.0
            threshold = float(data.get("threshold", 0.85))
            poll_interval = max(0.1, float(data.get("poll_interval", 0.2) or 0.2))
            deadline = time.monotonic() + max(0.1, float(timeout))
            best_confidence = 0.0
            attempt = 0
            while True:
                self.runtime_yield()
                if self._stop_requested:
                    return False, False, best_confidence
                if not self.refresh_current_frame(allow_cached=False):
                    return False, False, best_confidence
                result = match_template_qimage(self.current_frame, template, threshold, data.get("bbox"))
                attempt += 1
                best_confidence = max(best_confidence, result.confidence)
                guard_step["output"] = {
                    "found": result.found,
                    "confidence": result.confidence,
                    "bbox": result.bbox,
                    "center": result.center,
                    "error": result.error,
                    "attempt": attempt,
                    "condition_guard": True,
                }
                if result.error:
                    self.log(result.error)
                    return False, False, best_confidence
                if result.found:
                    return True, True, float(result.confidence)
                if time.monotonic() >= deadline:
                    break
                if not self.sleep_with_events(poll_interval):
                    return False, False, best_confidence
            return False, True, best_confidence

        if step_type in {"ocr_text", "ocr_number"}:
            ok = self.execute_ocr_step(guard_step)
            confidence = float(guard_step.get("input", {}).get("confidence", 0.0) or 0.0)
            return bool(ok), True, confidence

        self.log(f"条件分支第一步暂只支持图片识别/找目标/点击目标/OCR：{guard_step.get('name')}")
        return False, True, 0.0

    def evaluate_condition_branch_match(self, branch_step: dict[str, Any], timeout: float) -> tuple[bool, bool, float]:
        data = branch_step.get("input") or {}
        template = self.storage.abs(data.get("condition_template_path"))
        if not template or not template.exists():
            self.log(f"条件分支缺少判断截图：{branch_step.get('name')}")
            return False, False, 0.0
        threshold = float(data.get("condition_threshold", 0.85))
        poll_interval = max(0.1, float(data.get("condition_poll_interval", 0.2) or 0.2))
        deadline = time.monotonic() + max(0.1, float(timeout))
        best_confidence = 0.0
        attempt = 0
        while True:
            self.runtime_yield()
            if self._stop_requested:
                return False, False, best_confidence
            if not self.refresh_current_frame(allow_cached=False):
                return False, False, best_confidence
            result = match_template_qimage(self.current_frame, template, threshold, data.get("condition_bbox"))
            attempt += 1
            best_confidence = max(best_confidence, result.confidence)
            branch_step["output"] = {
                "found": result.found,
                "confidence": result.confidence,
                "bbox": result.bbox,
                "center": result.center,
                "error": result.error,
                "attempt": attempt,
                "condition_branch": True,
            }
            if result.error:
                self.log(result.error)
                return False, False, best_confidence
            if result.found:
                return True, True, float(result.confidence)
            if time.monotonic() >= deadline:
                break
            if not self.sleep_with_events(poll_interval):
                return False, False, best_confidence
        return False, True, best_confidence

    def execute_condition_branch_actions(
        self,
        branch_step: dict[str, Any],
        *,
        start_child_index: int,
    ) -> bool:
        children = branch_step.get("children") or []
        if start_child_index >= len(children):
            self.log(f"条件分支没有后续动作：{branch_step.get('name')}")
            return True
        for child_index in range(start_child_index, len(children)):
            self.runtime_yield()
            if self._stop_requested:
                return False
            child_step = children[child_index]
            if not child_step.get("enabled", True):
                self.log(f"跳过禁用分支动作 {child_index + 1}: {child_step.get('name')}")
                continue
            self.log(f"分支动作 {child_index + 1}/{len(children)}：{child_step.get('name')}")
            if child_step.get("type") == "loop":
                ok, _ = self.execute_loop_step(child_step, None)
            else:
                ok = self.execute_step(child_step)
            if not ok:
                self.log(f"条件分支动作失败：{child_step.get('name')}")
                return False
        return True

    def execute_condition_step(self, step: dict[str, Any]) -> bool:
        branches = step.get("children") or []
        data = step.setdefault("input", {})
        timeout = float(data.get("branch_timeout", 0.4) or 0.4)
        min_margin = max(0.0, float(data.get("branch_min_margin", 0.03) or 0.0))
        on_no_match = str(data.get("on_no_match") or "skip")
        step["output"] = {}
        if not branches:
            self.log(f"条件分支没有配置分支：{step.get('name')}")
            return on_no_match != "fail"

        default_branch: dict[str, Any] | None = None
        candidates: list[dict[str, Any]] = []
        self.log(f"开始条件分支判断：{step.get('name')}，候选 {len(branches)} 个。")
        for branch_index, branch in enumerate(branches, start=1):
            self.runtime_yield()
            if self._stop_requested:
                return False
            if not branch.get("enabled", True):
                self.log(f"跳过禁用分支 {branch_index}: {branch.get('name')}")
                continue

            branch_input = branch.get("input") or {}
            if branch_input.get("condition_default_branch") or str(branch.get("name") or "").startswith("默认"):
                default_branch = branch
                continue

            if branch.get("type") == "loop":
                if branch_input.get("condition_template_path"):
                    matched, valid, confidence = self.evaluate_condition_branch_match(branch, timeout)
                    action_start_index = 0
                else:
                    branch_children = branch.get("children") or []
                    if not branch_children:
                        self.log(f"条件分支缺少判断截图：{branch.get('name')}")
                        continue
                    matched, valid, confidence = self.evaluate_condition_guard_step(branch_children[0], timeout)
                    action_start_index = 1
                if not valid:
                    return False
                candidates.append(
                    {
                        "branch": branch,
                        "matched": matched,
                        "confidence": confidence,
                        "action_start_index": action_start_index,
                    }
                )
                continue

            matched, valid, confidence = self.evaluate_condition_guard_step(branch, timeout)
            if not valid:
                return False
            candidates.append(
                {
                    "branch": branch,
                    "matched": matched,
                    "confidence": confidence,
                    "action_start_index": None,
                }
            )

        if candidates:
            summary = "，".join(
                f"{item['branch'].get('name')}={float(item['confidence']):.3f}"
                for item in candidates
            )
            self.log(f"条件分支评分：{summary}")
            matched_candidates = [item for item in candidates if item["matched"]]
            if matched_candidates:
                matched_candidates.sort(key=lambda item: float(item["confidence"]), reverse=True)
                best = matched_candidates[0]
                second_confidence = float(matched_candidates[1]["confidence"]) if len(matched_candidates) > 1 else None
                if second_confidence is not None and float(best["confidence"]) - second_confidence < min_margin:
                    self.log(
                        f"条件分支结果太接近，已跳过避免误点："
                        f"{best['branch'].get('name')}={float(best['confidence']):.3f}，"
                        f"第二名={second_confidence:.3f}，要求差值 {min_margin:.2f}"
                    )
                    return on_no_match != "fail"
                best_branch = best["branch"]
                self.log(f"条件分支最终命中：{best_branch.get('name')}，置信度 {float(best['confidence']):.3f}")
                action_start_index = best.get("action_start_index")
                if action_start_index is None:
                    self.log(f"条件分支命中：{best_branch.get('name')}，没有分支动作，继续主流程。")
                    return True
                return self.execute_condition_branch_actions(best_branch, start_child_index=int(action_start_index))

        if default_branch is not None:
            self.log(f"条件分支未命中，执行默认分支：{default_branch.get('name')}")
            return self.execute_condition_branch_actions(default_branch, start_child_index=0)

        if on_no_match == "fail":
            self.log(f"条件分支未命中任何分支，按配置停止：{step.get('name')}")
            return False
        step.setdefault("output", {})["skipped"] = True
        self.log(f"条件分支未命中任何分支，已跳过继续：{step.get('name')}")
        return True

    def execute_loop_step(self, step: dict[str, Any], loop_index: int | None) -> tuple[bool, int]:
        children = step.get("children") or []
        times = int(step.get("input", {}).get("times", 1))
        break_on_failure = bool(step.get("input", {}).get("break_on_failure", True))
        loop_mode = step.get("input", {}).get("loop_mode", "fixed_count")
        fail_when_max_reached = bool(step.get("input", {}).get("fail_when_max_reached", True))
        first_step_skip_exits_loop = bool(step.get("input", {}).get("first_step_skip_exits_loop", False))
        exit_check_timing = str(step.get("input", {}).get("exit_check_timing") or "before_each_step")
        check_before_body = exit_check_timing != "after_iteration"

        if children:
            next_index = loop_index + 1 if loop_index is not None else 0
            if check_before_body and loop_mode != "fixed_count" and self.loop_exit_condition_met(step):
                self.log(f"循环条件已满足，跳过循环体：{step.get('name')}")
                return True, next_index
            mode_text = "固定次数" if loop_mode == "fixed_count" else "直到满足图片判断"
            self.log(f"开始循环：{step.get('name')}，子步骤 {len(children)} 个，{mode_text}，最大 {times} 次。")
            for iteration in range(1, times + 1):
                self.log(f"循环 {step.get('name')}：第 {iteration}/{times} 次")
                for child_index, child_step in enumerate(children, start=1):
                    self.runtime_yield()
                    if self._stop_requested:
                        return False, next_index
                    if check_before_body and loop_mode != "fixed_count" and self.loop_exit_condition_met(step):
                        self.log(f"循环结束条件已满足，停止剩余循环体：{step.get('name')}")
                        return True, next_index
                    if not child_step.get("enabled", True):
                        self.log(f"跳过循环体禁用步骤 {child_index}: {child_step.get('name')}")
                        continue
                    self.log(f"循环体 {child_index}/{len(children)}：{child_step.get('name')}")
                    if child_step.get("type") == "loop":
                        ok, _ = self.execute_loop_step(child_step, None)
                    else:
                        ok = self.execute_step(child_step)
                    if ok and first_step_skip_exits_loop and child_index == 1 and self.step_was_skipped(child_step):
                        self.log(f"循环入口判断未命中，已跳过整个循环：{step.get('name')}")
                        return True, next_index
                    if not ok:
                        self.log(
                            f"循环体步骤失败：第 {iteration}/{times} 次，子步骤 {child_index} {child_step.get('name')}"
                        )
                        if break_on_failure:
                            return False, next_index
                if self._stop_requested:
                    return False, next_index
                if loop_mode != "fixed_count" and self.loop_exit_condition_met(step):
                    self.log(f"循环结束条件达成：{step.get('name')}，第 {iteration}/{times} 次后进入下一步。")
                    return True, next_index
            if loop_mode != "fixed_count":
                self.log(f"循环达到最大次数仍未满足结束判断：{step.get('name')}")
                return (not fail_when_max_reached), next_index
            self.log(
                f"循环完成：{step.get('name')}，继续步骤 "
                f"{next_index + 1 if loop_index is not None and next_index < len(self.flow['steps']) else '结束'}"
            )
            return True, next_index

        body_indices = self.loop_body_indices(step, loop_index)
        if not body_indices:
            return False, (loop_index + 1) if loop_index is not None else 0

        next_index = max(body_indices) + 1
        if check_before_body and loop_mode != "fixed_count" and self.loop_exit_condition_met(step):
            self.log(f"循环条件已满足，跳过循环体：{step.get('name')}")
            return True, next_index
        self.log(
            f"开始循环：{step.get('name')}，步骤 {body_indices[0] + 1}-{body_indices[-1] + 1}，最大 {times} 次。"
        )

        for iteration in range(1, times + 1):
            self.log(f"循环 {step.get('name')}：第 {iteration}/{times} 次")
            for body_position, body_index in enumerate(body_indices, start=1):
                self.runtime_yield()
                if self._stop_requested:
                    return False, next_index
                if check_before_body and loop_mode != "fixed_count" and self.loop_exit_condition_met(step):
                    self.log(f"循环结束条件已满足，停止剩余循环体：{step.get('name')}")
                    return True, next_index
                body_step = self.flow["steps"][body_index]
                if not body_step.get("enabled", True):
                    self.log(f"跳过循环体禁用步骤 {body_index + 1}: {body_step.get('name')}")
                    continue
                if body_step.get("type") == "loop":
                    self.log("暂时不支持嵌套循环。")
                    return False, next_index
                ok = self.execute_step(body_step, index=body_index + 1)
                if ok and first_step_skip_exits_loop and body_position == 1 and self.step_was_skipped(body_step):
                    self.log(f"循环入口判断未命中，已跳过整个循环：{step.get('name')}")
                    return True, next_index
                if not ok:
                    self.log(
                        f"循环体步骤失败：第 {iteration}/{times} 次，步骤 {body_index + 1} {body_step.get('name')}"
                    )
                    if break_on_failure:
                        return False, next_index
            if self._stop_requested:
                return False, next_index
            if loop_mode != "fixed_count" and self.loop_exit_condition_met(step):
                self.log(f"循环结束条件达成：{step.get('name')}，第 {iteration}/{times} 次后进入下一步。")
                return True, next_index
        if loop_mode != "fixed_count":
            self.log(f"循环达到最大次数仍未满足结束判断：{step.get('name')}")
            return (not fail_when_max_reached), next_index
        self.log(f"循环完成：{step.get('name')}，继续步骤 {next_index + 1 if next_index < len(self.flow['steps']) else '结束'}")
        return True, next_index

    def execute_dialog_step(self, step: dict[str, Any]) -> bool:
        data = step.setdefault("input", {})
        title = str(data.get("title") or step.get("name") or "脚本执行完成")
        message = str(data.get("message") or "脚本执行完成。")
        if bool(data.get("log_message", True)):
            self.log(f"{title}：{message}")
        self.invoke_on_ui_thread(lambda: QMessageBox.information(self, title, message))
        return True

    def execute_step(self, step: dict[str, Any], index: int | None = None) -> bool:
        prefix = f"[{index:02d}] " if index is not None else ""
        name = step.get("name", step.get("id"))
        self.log(f"{prefix}运行步骤：{name}")
        try:
            step_type = step["type"]
            if step_type == "click":
                ok = self.execute_click(step)
            elif step_type == "wait":
                duration = float(step["input"].get("duration", 1.0))
                if not self.sleep_with_events(duration):
                    return False
                self.log(f"等待 {duration:.2f}s 完成。")
                ok = True
            elif step_type in {"image_check", "find_target", "click_target"}:
                ok = self.execute_image_step(step)
            elif step_type in {"ocr_text", "ocr_number"}:
                ok = self.execute_ocr_step(step)
            elif step_type == "verify_code":
                ok = self.execute_verify_code_step(step)
            elif step_type == "dialog":
                ok = self.execute_dialog_step(step)
            elif step_type in {"read_game_coord", "move_to_game_coord"}:
                self.log("游戏坐标相关步骤已停用。请改用“当前画面操作”里的点击位置移动或图片识别点击。")
                ok = False
            elif step_type == "question":
                ok = self.execute_question_step(step)
            elif step_type == "condition":
                ok = self.execute_condition_step(step)
            elif step_type == "loop":
                ok, _ = self.execute_loop_step(step, self.top_level_index(step["id"]))
            else:
                self.log(f"{STEP_LABELS.get(step_type, step_type)} 已保留，MVP 暂未实现执行逻辑。")
                ok = True
            if not ok and not self._stop_requested:
                self.record_bug_report(
                    f"步骤失败：{name}",
                    kind="step_failure",
                    step=step,
                    metadata={"index": index},
                    cooldown_key=f"step_failure:{step.get('id')}:{int(time.time() // 30)}",
                )
            return ok
        except Exception as exc:  # noqa: BLE001
            self.log(f"步骤失败：{exc}")
            self.record_bug_report(
                f"步骤异常：{name}",
                kind="step_exception",
                step=step,
                metadata={"error": str(exc), "index": index},
                cooldown_key=f"step_exception:{step.get('id')}:{str(exc)}",
            )
            return False

    def read_game_coord_from_region(
        self,
        bbox: list[int],
        step_id: str = "runtime",
        *,
        max_jump: int | None = None,
        target_hint: list[int] | None = None,
        save_capture: bool = True,
    ) -> list[int] | None:
        if not self.refresh_current_frame() or self.current_frame is None:
            return None
        rect = rect_from_bbox(bbox)
        if rect is None:
            return None
        expanded = QRect(rect.x() - 10, rect.y() - 6, rect.width() + 24, rect.height() + 12)
        expanded = expanded.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
        crop = self.current_frame.copy(expanded)
        temp_path: Path | None = None
        if save_capture:
            step_dir = self.storage.step_dir(self.flow["script_name"], step_id, "game_coord")
            path = step_dir / f"coord_{int(time.time() * 1000)}.png"
        else:
            handle = tempfile.NamedTemporaryFile(prefix="stoneage_coord_", suffix=".png", delete=False)
            path = Path(handle.name)
            temp_path = path
            handle.close()
        crop.save(str(path))
        previous = list(self.current_game_coord) if self.current_game_coord else None
        try:
            result = self.ocr.recognize_game_coord(path, previous=previous)
            coord = parse_game_coord_text(result.text, previous=previous)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        if coord:
            target_repaired = repair_coord_against_target_hint(
                coord,
                target_hint,
                max_jump=max_jump or MAX_NORMAL_MOVEMENT_DELTA,
            )
            if target_repaired is not None:
                self.log(f"游戏坐标 OCR 按目标纠正：{coord} -> {target_repaired}，目标 {target_hint}")
                coord = target_repaired
            if max_jump is not None and previous and not coord_jump_is_plausible(previous, coord, max_jump):
                repaired = repair_implausible_coord(
                    coord,
                    previous,
                    target_hint=target_hint,
                    max_jump=max_jump,
                )
                if repaired is not None:
                    self.log(
                        f"游戏坐标 OCR 已纠正：{coord} -> {repaired}，"
                        f"前值 {previous}，目标 {target_hint or '(无)'}"
                    )
                    coord = repaired
                else:
                    self.log(
                        f"游戏坐标跳变异常，忽略本次 OCR：{previous} -> {coord}，"
                        f"原始结果 {result.text or '(空)'}"
                    )
                    return None
            if max_jump is not None and previous and not coord_jump_is_plausible(previous, coord, max_jump):
                self.log(
                    f"游戏坐标跳变异常，忽略本次 OCR：{previous} -> {coord}，"
                    f"原始结果 {result.text or '(空)'}"
                )
                return None
            self.current_game_coord = coord
            self.log(f"读取游戏坐标：{coord[0]}, {coord[1]} ({result.backend})")
        else:
            self.log(f"游戏坐标 OCR 未识别：{result.text or '(空)'}，请把坐标区域框完整一点。")
        return coord

    def read_current_game_coord(
        self,
        step_id: str = "runtime",
        *,
        max_jump: int | None = None,
        target_hint: list[int] | None = None,
        save_capture: bool = True,
    ) -> list[int] | None:
        region = self.game_coord_region()
        return self.read_game_coord_from_region(
            region,
            step_id,
            max_jump=max_jump,
            target_hint=target_hint,
            save_capture=save_capture,
        )

    def read_stable_current_game_coord(
        self,
        step_id: str = "runtime",
        *,
        max_jump: int | None = None,
        target_hint: list[int] | None = None,
        save_capture: bool = True,
        sample_count: int = 2,
        min_agreement: int = 1,
        sample_delay: float = 0.12,
    ) -> list[int] | None:
        reader = GameCoordReader(
            lambda: self.read_current_game_coord(
                step_id,
                max_jump=max_jump,
                target_hint=target_hint,
                save_capture=save_capture,
            ),
            sample_count=sample_count,
            min_agreement=min_agreement,
            sample_delay=sample_delay,
        )
        reading = reader.read(previous=self.current_game_coord, max_jump=max_jump)
        if reading.coord is None:
            return None
        coord = [int(reading.coord[0]), int(reading.coord[1])]
        self.current_game_coord = coord
        if reading.accepted_samples > 1:
            self.log(f"稳定坐标确认：{coord}，置信 {reading.confidence:.2f}")
        return coord

    def current_screen_size(self) -> tuple[int, int]:
        if self.current_frame is not None and not self.current_frame.isNull():
            return int(self.current_frame.width()), int(self.current_frame.height())
        return int(self.expected_resolution[0]), int(self.expected_resolution[1])

    def sleep_with_events(self, seconds: float, *, chunk: float = 0.05) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            self.runtime_yield()
            if self._stop_requested:
                return False
            time.sleep(min(max(0.01, chunk), max(0.0, deadline - time.monotonic())))
        return not self._stop_requested

    def dismiss_possible_coord_blocking_ui(self, *, attempt: int, points: Any = None) -> None:
        screen_w, screen_h = self.current_screen_size()
        default_points = [
            [0.52, 0.28],
            [0.42, 0.34],
            [0.60, 0.34],
            [0.48, 0.22],
        ]
        normalized: list[list[float]] = []
        source_points = points if isinstance(points, list) and points else default_points
        for item in source_points:
            if not isinstance(item, list) or len(item) < 2:
                continue
            try:
                raw_x = float(item[0])
                raw_y = float(item[1])
            except (TypeError, ValueError):
                continue
            if 0.0 <= raw_x <= 1.0 and 0.0 <= raw_y <= 1.0:
                normalized.append([raw_x * screen_w, raw_y * screen_h])
            else:
                normalized.append([raw_x, raw_y])
        if not normalized:
            normalized = [[screen_w * 0.52, screen_h * 0.28]]
        point = normalized[(max(1, int(attempt)) - 1) % len(normalized)]
        x = max(8, min(screen_w - 8, int(point[0])))
        y = max(8, min(screen_h - 8, int(point[1])))
        self.adb.tap(x, y)
        self.log(f"坐标连续未识别，已点空白处尝试退出对话/选人界面：{x}, {y}")

    def select_target_backoff_point(
        self,
        current: list[int],
        target: list[int],
        grid: WalkabilityGrid,
        *,
        distance: int,
        require_walkable: bool = False,
        avoid: set[tuple[int, int]] | None = None,
    ) -> list[int] | None:
        current_node = (int(current[0]), int(current[1]))
        target_node = (int(target[0]), int(target[1]))
        avoid = avoid or set()
        away_x = coord_sign(current_node[0] - target_node[0])
        away_y = coord_sign(current_node[1] - target_node[1])
        if away_x == 0 and away_y == 0:
            return None

        directions: list[tuple[int, int]] = []

        def add_direction(dx: int, dy: int) -> None:
            if (dx, dy) != (0, 0) and (dx, dy) not in directions:
                directions.append((dx, dy))

        add_direction(away_x, away_y)
        if away_x and away_y:
            add_direction(away_x, 0)
            add_direction(0, away_y)
        elif away_x:
            add_direction(away_x, 1)
            add_direction(away_x, -1)
            add_direction(away_x, 0)
        elif away_y:
            add_direction(1, away_y)
            add_direction(-1, away_y)
            add_direction(0, away_y)

        candidates: list[tuple[float, tuple[int, int]]] = []
        start_distance = max(2, int(distance))
        for step_distance in range(start_distance, start_distance + 3):
            for dx, dy in directions:
                candidate = (target_node[0] + dx * step_distance, target_node[1] + dy * step_distance)
                if candidate == current_node or candidate == target_node:
                    continue
                if candidate in avoid:
                    continue
                if require_walkable and not grid.is_walkable(candidate):
                    continue
                if grid.is_blocked(candidate) or grid.is_danger(candidate, threshold=4):
                    continue
                plan = self.path_planner.plan(
                    current_node,
                    candidate,
                    grid,
                    allow_unknown=not require_walkable,
                    lookahead=min(3, max(1, coord_distance(current_node, candidate))),
                )
                if not plan.path:
                    continue
                known_penalty = 0.0 if grid.is_walkable(candidate) else 0.5
                danger_penalty = float(grid.danger_nodes.get(candidate, 0) * 4)
                candidates.append((float(len(plan.path) - 1) + known_penalty + danger_penalty, candidate))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        best = candidates[0][1]
        return [best[0], best[1]]

    def select_view_clear_point(
        self,
        current: list[int],
        target: list[int],
        grid: WalkabilityGrid,
        *,
        target_hint: list[int] | None = None,
        avoid: set[tuple[int, int]] | None = None,
    ) -> list[int] | None:
        current_node = (int(current[0]), int(current[1]))
        target_node = (int(target[0]), int(target[1]))
        avoid = avoid or set()
        directions = [
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (1, -1),
            (-1, 1),
            (-1, -1),
        ]
        if target_hint:
            hint_dx = coord_sign(int(target_hint[0]) - target_node[0])
            hint_dy = coord_sign(int(target_hint[1]) - target_node[1])
            preferred = [
                (-hint_dy, hint_dx),
                (hint_dy, -hint_dx),
                (-hint_dx, -hint_dy),
                (hint_dx, hint_dy),
            ]
            directions = [item for item in preferred if item != (0, 0)] + [
                item for item in directions if item not in preferred
            ]
        candidates: list[tuple[float, tuple[int, int]]] = []
        for distance in range(1, 4):
            for dx, dy in directions:
                candidate = (target_node[0] + dx * distance, target_node[1] + dy * distance)
                if candidate == current_node or candidate == target_node or candidate in avoid:
                    continue
                if grid.is_blocked(candidate) or grid.is_danger(candidate, threshold=4):
                    continue
                plan = self.path_planner.plan(
                    current_node,
                    candidate,
                    grid,
                    allow_unknown=True,
                    lookahead=min(3, max(1, coord_distance(current_node, candidate))),
                )
                if not plan.path:
                    continue
                known_bonus = -0.5 if grid.is_walkable(candidate) else 0.0
                distance_penalty = abs(coord_distance(candidate, target_node) - 2) * 0.4
                candidates.append((float(len(plan.path) - 1) + distance_penalty + known_bonus, candidate))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        best = candidates[0][1]
        return [best[0], best[1]]

    def wait_for_movement_result(
        self,
        step_id: str,
        *,
        before: list[int],
        max_jump: int,
        target_hint: list[int] | None = None,
        poll_interval: float,
        settle_seconds: float,
        save_capture: bool = False,
    ) -> tuple[list[int] | None, float]:
        started = time.monotonic()
        last_coord = list(before)
        for attempt in range(2):
            self.runtime_yield()
            if self._stop_requested:
                return last_coord, time.monotonic() - started
            if not self.sleep_with_events(settle_seconds if attempt == 0 else max(0.15, poll_interval)):
                return last_coord, time.monotonic() - started
            coord = self.read_stable_current_game_coord(
                step_id,
                max_jump=max_jump,
                target_hint=target_hint,
                save_capture=save_capture,
                sample_count=1,
                min_agreement=1,
                sample_delay=0.0,
            )
            if coord is None:
                continue
            last_coord = list(coord)
            break
        return last_coord, time.monotonic() - started

    def wait_for_recorded_game_coord(self, step: dict[str, Any]) -> bool:
        step.setdefault("input", {})["verify_game_coord"] = False
        return True

    def tap_repeated(
        self,
        x: int,
        y: int,
        count: int = 1,
        interval: float = 0.08,
        *,
        label: str = "ADB 点击",
    ) -> bool:
        click_count = max(1, int(count or 1))
        click_interval = max(0.0, float(interval or 0.0))
        for click_index in range(click_count):
            if self._stop_requested:
                return False
            self.adb.tap(int(x), int(y))
            suffix = f" ({click_index + 1}/{click_count})" if click_count > 1 else ""
            self.log(f"{label}{suffix}：{int(x)}, {int(y)}")
            if click_index < click_count - 1 and click_interval:
                if not self.sleep_with_events(click_interval):
                    return False
        return not self._stop_requested

    def execute_click(self, step: dict[str, Any]) -> bool:
        data = step["input"]
        wait_before = float(data.get("wait_before", 0.0))
        wait_after = float(data.get("wait_after", 1.0))
        if wait_before:
            if not self.sleep_with_events(wait_before):
                return False
        if not self.wait_for_recorded_game_coord(step):
            return False
        coord = data.get("screen_coord") or [0, 0]
        if not self.tap_repeated(
            int(coord[0]),
            int(coord[1]),
            data.get("click_count", 1),
            data.get("click_interval", 0.08),
        ):
            return False
        if wait_after:
            if not self.sleep_with_events(wait_after):
                return False
            self.log(f"点击后等待 {wait_after:.2f}s 完成。")
        return True

    def execute_read_game_coord_step(self, step: dict[str, Any]) -> bool:
        region = step.get("input", {}).get("coord_region") or step.get("input", {}).get("bbox")
        if not region:
            self.log("读取游戏坐标步骤缺少坐标区域。请先框选右上角地图坐标文字。")
            return False
        coord = self.read_game_coord_from_region(region, step["id"])
        step.setdefault("input", {})["last_coord"] = coord
        return coord is not None

    def next_step_image_match_status(self, index: int | None) -> tuple[str, float, float] | None:
        if index is None:
            return None
        steps = self.flow.get("steps") or []
        next_pos = int(index)
        if next_pos < 0 or next_pos >= len(steps):
            return None
        next_step = steps[next_pos]
        if next_step.get("type") not in {"image_check", "find_target", "click_target"}:
            return None
        data = next_step.get("input") or {}
        template_path = data.get("template_path")
        if not template_path:
            return None
        template = self.storage.abs(template_path)
        if template is None or not template.exists():
            return None
        if not self.refresh_current_frame() or self.current_frame is None:
            return None
        threshold = float(data.get("threshold", 0.85))
        result = match_template_qimage(self.current_frame, template, threshold)
        if result.error:
            return None
        return str(next_step.get("name") or next_step.get("id") or "下一目标"), float(result.confidence), threshold

    def next_image_missing_for_route_arrival(
        self,
        data: dict[str, Any],
        *,
        exact_target: bool,
        index: int | None,
    ) -> tuple[bool, str, float, float]:
        if exact_target:
            return False, "", 0.0, 0.0
        explicit = data.get("require_next_image_visible")
        if explicit is False:
            return False, "", 0.0, 0.0
        status = self.next_step_image_match_status(index)
        if status is None:
            return False, "", 0.0, 0.0
        next_name, confidence, threshold = status
        visible_confidence = max(0.55, threshold * 0.80)
        if confidence >= visible_confidence:
            return False, next_name, confidence, visible_confidence
        return True, next_name, confidence, visible_confidence

    def leading_one_coord_alternatives(self, coord: list[int]) -> list[tuple[int, list[int]]]:
        alternatives: list[tuple[int, list[int]]] = []
        for axis in (0, 1):
            value = int(coord[axis])
            if 10 <= value <= 19:
                candidate = [int(coord[0]), int(coord[1])]
                candidate[axis] = value % 10
                if candidate != coord:
                    alternatives.append((axis, candidate))
        return alternatives

    def previous_route_coord_hint(self, index: int | None) -> list[int] | None:
        if index is None:
            return None
        steps = self.flow.get("steps") or []
        for pos in range(min(int(index) - 2, len(steps) - 1), -1, -1):
            step = steps[pos]
            if step.get("type") != "move_to_game_coord":
                continue
            coord = (step.get("input") or {}).get("target_coord")
            if coord and len(coord) >= 2:
                return [int(coord[0]), int(coord[1])]
        return None

    def correct_suspicious_near_arrival(
        self,
        current: list[int],
        *,
        nav_goal: list[int],
        target: list[int],
        arrival_tolerance: int,
        exact_target: bool,
        index: int | None,
    ) -> list[int] | None:
        if exact_target or not coord_within_tolerance(current, nav_goal, arrival_tolerance):
            return None
        status = self.next_step_image_match_status(index)
        if status is None:
            return None
        next_name, confidence, threshold = status
        visible_confidence = max(0.55, threshold * 0.80)
        if confidence >= visible_confidence:
            return None
        candidates = [
            (axis, candidate)
            for axis, candidate in self.leading_one_coord_alternatives(current)
            if int(current[axis]) % 10 >= 7
            and not coord_within_tolerance(candidate, nav_goal, arrival_tolerance)
        ]
        if not candidates:
            return None
        anchor = self.previous_route_coord_hint(index)

        def candidate_score(item: tuple[int, list[int]]) -> tuple[int, int, int, int]:
            axis, candidate = item
            original_value = int(current[axis])
            unit = original_value % 10
            anchor_distance = coord_distance(candidate, anchor) if anchor else 0
            return (
                1 if unit >= 7 else 0,
                coord_distance(candidate, nav_goal),
                -anchor_distance,
                -coord_distance(candidate, target),
            )

        _, corrected = max(candidates, key=candidate_score)
        self.log(
            f"路线点疑似坐标 OCR 多读 1：当前 {current} 已接近 {nav_goal}，"
            f"但下一目标 {next_name} 置信度 {confidence:.3f} < {visible_confidence:.3f}，"
            f"改用 {corrected} 继续移动。"
        )
        return corrected

    def execute_move_to_game_coord_step(self, step: dict[str, Any], index: int | None = None) -> bool:
        data = step.setdefault("input", {})
        target = [int(value) for value in (data.get("target_coord") or [0, 0])[:2]]
        tolerance = int(data.get("tolerance", 1))
        if "exact_target" in data:
            exact_target = bool(data.get("exact_target"))
        elif "arrival_mode" in data:
            exact_target = str(data.get("arrival_mode") or "exact") == "exact"
        else:
            exact_target = tolerance == 0
        max_jump = int(data.get("max_coord_jump", MAX_NORMAL_MOVEMENT_DELTA))
        waypoint_lookahead = int(data.get("waypoint_lookahead", 5))
        poll_interval = max(0.15, float(data.get("poll_interval", 0.35)))
        settle_seconds = max(poll_interval, float(data.get("movement_settle_seconds", 0.8)))
        route_node_role = str(data.get("route_node_role") or "")
        route_step_name = str(step.get("name") or "")
        if route_step_name.startswith("路线点_") and (not route_node_role or "target_hint_coord" not in data):
            route_label = route_step_name.removeprefix("路线点_")
            route_plan = self.storage.load_script_route_plan(str(self.flow.get("script_name") or ""))
            for node in route_plan.get("nodes") or []:
                node_label = str(node.get("label") or node.get("id") or "")
                if route_label != node_label and route_label != str(node.get("id") or ""):
                    continue
                route_node_role = str(node.get("role") or route_node_role)
                if "route_node_role" not in data and route_node_role:
                    data["route_node_role"] = route_node_role
                target_hint_value = node.get("target_hint")
                if (
                    "target_hint_coord" not in data
                    and isinstance(target_hint_value, list)
                    and len(target_hint_value) >= 2
                ):
                    data["target_hint_coord"] = [int(target_hint_value[0]), int(target_hint_value[1])]
                break
        next_image_step = next_step_has_image_target(self.flow, index)
        route_requires_next_image = (
            not exact_target
            and next_image_step
            and (
                bool(data.get("require_next_image_visible"))
                or route_node_role in {"view_target", "view_npc"}
                or route_step_name.startswith("路线点_")
            )
        )
        if route_requires_next_image:
            data["require_next_image_visible"] = True
            data["tolerance"] = tolerance
        use_approach_points = bool(data.get("use_approach_points", not exact_target)) and not exact_target
        arrival_tolerance = 0 if exact_target else max(0, tolerance)
        approach_radius = 0 if exact_target else int(data.get("approach_radius", 1 if use_approach_points else 0))
        max_approach_radius = 0 if exact_target else int(data.get("max_approach_radius", max(approach_radius, 3)))
        view_visibility_radius = int(data.get("view_visibility_radius", max(4, arrival_tolerance + 2)))
        max_click_radius = int(data.get("max_click_radius", 300))
        click_radii = normalize_click_radii(
            data.get("click_radii"),
            [180, 220, 260, 300],
            max_radius=max_click_radius,
        )
        fine_click_radii = normalize_click_radii(
            data.get("fine_click_radii"),
            [80, 110, 140],
            max_radius=min(max_click_radius, 160),
        )
        fine_tune_distance = int(data.get("fine_tune_distance", 3))
        target_backoff_enabled = bool(data.get("target_backoff_enabled", True))
        target_backoff_distance = int(data.get("target_backoff_distance", 3))
        target_backoff_trigger_distance = int(data.get("target_backoff_trigger_distance", 2))
        target_backoff_max_attempts = int(data.get("target_backoff_max_attempts", 5))
        exact_direct_click_enabled = bool(data.get("exact_direct_click_enabled", True))
        exact_direct_click_distance = int(data.get("exact_direct_click_distance", 3))
        exact_direct_click_attempt_limit = int(data.get("exact_direct_click_attempts", 8))
        exact_direct_click_min_radius = int(data.get("exact_direct_click_min_radius", 70))
        exact_direct_click_tile_radius = int(data.get("exact_direct_click_tile_radius", 54))
        exact_direct_click_max_radius = int(data.get("exact_direct_click_max_radius", min(max_click_radius, 260)))
        coord_recovery_enabled = bool(data.get("coord_recovery_enabled", True))
        coord_recovery_missing_reads = max(1, int(data.get("coord_recovery_missing_reads", 2)))
        coord_recovery_attempt_limit = max(0, int(data.get("coord_recovery_attempts", 3)))
        coord_recovery_wait_seconds = max(0.1, float(data.get("coord_recovery_wait_seconds", 0.45)))
        stable_frames = 1
        stable_agreement = 1
        grid = WalkabilityGrid.load(self.storage.walkability_path(self.current_map), self.current_map)
        if exact_target:
            grid.blocked_nodes.discard((target[0], target[1]))
        stuck_detector = StuckDetector()
        self.auto_cleanup_movement_samples()
        movement_profile: dict[str, Any] | None = None
        reusable_current: list[int] | None = None
        target_backoff_goal: list[int] | None = None
        target_backoff_attempts = 0
        bad_backoff_goals: set[tuple[int, int]] = set()
        bad_view_clear_goals: set[tuple[int, int]] = set()
        target_direct_click_ready = False
        target_direct_click_attempts = 0
        # The previous coordinate may be stale after a battle/dialog/click sequence.
        # Trust the first read of each move step, then use jump filtering inside
        # this step once we have a fresh anchor.
        movement_coord_known = False
        no_coord_count = 0
        coord_recovery_attempts = 0
        step_budget = float(data.get("max_seconds", step.get("timeout", 30)))
        if exact_target:
            step_budget = max(step_budget, 90.0)
        elif route_requires_next_image:
            step_budget = max(step_budget, 75.0)
        deadline = time.monotonic() + step_budget
        while True:
            self.runtime_yield()
            if self._stop_requested:
                grid.save(self.storage.walkability_path(self.current_map))
                return False
            if reusable_current is not None:
                current = reusable_current
                reusable_current = None
            else:
                current = self.read_stable_current_game_coord(
                    step["id"],
                    max_jump=max_jump if movement_coord_known else None,
                    target_hint=target,
                    save_capture=False,
                    sample_count=stable_frames,
                    min_agreement=stable_agreement,
                    sample_delay=min(0.12, poll_interval / 2),
                )
            if current and route_requires_next_image and coord_chebyshev_distance(current, target) <= view_visibility_radius:
                status = self.next_step_image_match_status(index)
                if status is not None:
                    next_name, confidence, threshold = status
                    visible_confidence = max(0.55, threshold * 0.80)
                    if confidence >= visible_confidence:
                        grid.mark_walkable(current)
                        grid.save(self.storage.walkability_path(self.current_map))
                        self.log(
                            f"下一目标 {next_name} 已可见，视野站位到位：当前 {current}，"
                            f"目标 {target}，置信度 {confidence:.3f}。"
                        )
                        return True
            force_target_until_visible = False
            if current and coord_within_tolerance(current, target, arrival_tolerance):
                missing_next, next_name, confidence, visible_confidence = self.next_image_missing_for_route_arrival(
                    data,
                    exact_target=exact_target,
                    index=index,
                )
                if missing_next:
                    force_target_until_visible = True
                    self.log(
                        f"已接近路线点 {target}，但下一目标 {next_name} 置信度 "
                        f"{confidence:.3f} < {visible_confidence:.3f}，继续推进到目标坐标。"
                    )
                else:
                    corrected_current = self.correct_suspicious_near_arrival(
                        current,
                        nav_goal=target,
                        target=target,
                        arrival_tolerance=arrival_tolerance,
                        exact_target=exact_target,
                        index=index,
                    ) if not movement_coord_known else None
                    if corrected_current is not None:
                        self.current_game_coord = corrected_current
                        reusable_current = corrected_current
                        movement_coord_known = True
                        continue
                    grid.mark_walkable(current)
                    grid.save(self.storage.walkability_path(self.current_map))
                    self.log(f"已到达游戏坐标：{current}")
                    return True
            if time.monotonic() >= deadline:
                grid.save(self.storage.walkability_path(self.current_map))
                self.log(f"移动到游戏坐标超时：当前 {current}，目标 {target}。")
                return False
            if not current:
                no_coord_count += 1
                if (
                    coord_recovery_enabled
                    and no_coord_count >= coord_recovery_missing_reads
                    and coord_recovery_attempts < coord_recovery_attempt_limit
                ):
                    coord_recovery_attempts += 1
                    self.dismiss_possible_coord_blocking_ui(
                        attempt=coord_recovery_attempts,
                        points=data.get("coord_recovery_tap_points")
                        or self.flow.get("settings", {}).get("coord_recovery_tap_points"),
                    )
                    no_coord_count = 0
                    if not self.sleep_with_events(coord_recovery_wait_seconds):
                        return False
                    continue
                if no_coord_count >= 2 and movement_coord_known:
                    movement_coord_known = False
                    self.log("连续坐标跳变异常，下一次将重新锁定当前坐标，不再使用旧坐标过滤。")
                self.log(f"没有读到当前坐标，等待后重试。目标 {target}")
                if not self.sleep_with_events(poll_interval):
                    return False
                continue
            no_coord_count = 0
            had_reliable_coord = movement_coord_known
            movement_coord_known = True
            grid.mark_walkable(current)
            if target_backoff_goal is not None and coord_within_tolerance(current, target_backoff_goal, 1):
                self.log(f"已拉开距离：当前 {current}，重新回切精准目标 {target}")
                target_backoff_goal = None
                target_direct_click_ready = True
                stuck_detector.clear_strategy_failures()
            near_exact_distance = coord_chebyshev_distance(current, target)
            if (
                exact_target
                and exact_direct_click_enabled
                and target_backoff_goal is None
                and not coord_within_tolerance(current, target, 0)
                and near_exact_distance <= max(1, exact_direct_click_distance)
                and target_direct_click_attempts < max(1, exact_direct_click_attempt_limit)
            ):
                if (
                    target_backoff_enabled
                    and not target_direct_click_ready
                    and target_direct_click_attempts > 0
                    and near_exact_distance <= 1
                    and target_backoff_attempts < target_backoff_max_attempts
                ):
                    backoff_goal = self.select_target_backoff_point(
                        current,
                        target,
                        grid,
                        distance=max(target_backoff_distance, exact_direct_click_distance + 1),
                        require_walkable=True,
                        avoid=bad_backoff_goals,
                    )
                    if backoff_goal is not None:
                        target_backoff_goal = backoff_goal
                        target_backoff_attempts += 1
                        stuck_detector.clear_strategy_failures()
                        self.log(
                            f"精准目标贴身，先离开人物遮挡区：当前 {current}，"
                            f"拉开到 {target_backoff_goal}，再直点目标 {target}。"
                        )
                        grid.save(self.storage.walkability_path(self.current_map))
                        continue
                screen_size = self.current_screen_size()
                character_position = self.coordinate_mapper.estimate_character_screen_position(
                    screen_size,
                    data.get("character_screen_position"),
                )
                direct_plan = self.coordinate_mapper.click_for_game_coord_direct(
                    current,
                    target,
                    character_position=character_position,
                    screen_size=screen_size,
                    tile_radius=exact_direct_click_tile_radius,
                    min_radius=exact_direct_click_min_radius,
                    max_radius=exact_direct_click_max_radius,
                    safe_rect=data.get("movement_click_safe_rect"),
                )
                before = list(current)
                before_distance = coord_distance(before, target)
                self.adb.tap(int(direct_plan.point[0]), int(direct_plan.point[1]))
                self.log(
                    f"精准目标直点：当前 {current} -> 目标 {target}，"
                    f"相对点击 {list(direct_plan.relative_to_character)} 半径 {direct_plan.radius} "
                    f"方向 {direct_plan.direction}"
                )
                after, duration = self.wait_for_movement_result(
                    step["id"],
                    before=before,
                    max_jump=max_jump,
                    target_hint=target,
                    save_capture=False,
                    poll_interval=poll_interval,
                    settle_seconds=settle_seconds,
                )
                if not after:
                    after = before
                delta = [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])]
                plausible = movement_delta_is_plausible(delta) or delta == [0, 0]
                if not plausible:
                    grid.mark_danger(target)
                    grid.save(self.storage.walkability_path(self.current_map))
                    self.log(f"精准直点 delta 过大 {delta}，疑似 OCR 误读，已重规划。")
                    reusable_current = None
                    target_direct_click_attempts += 1
                    continue
                outcome = stuck_detector.evaluate(
                    before,
                    after,
                    waypoint=target,
                    target=target,
                    tolerance=0,
                )
                self.storage.add_movement_sample(
                    map_id=self.current_map,
                    before_game_coord=before,
                    after_game_coord=after,
                    click_relative_to_character=list(direct_plan.relative_to_character),
                    click_angle=direct_plan.angle,
                    click_radius=direct_plan.radius,
                    actual_delta=delta,
                    duration=duration,
                    success=outcome.success,
                    stuck=outcome.stuck,
                    progress_score=outcome.progress_score,
                    direction=direct_plan.direction,
                    screen_point=list(direct_plan.point),
                    start_coord=before,
                    end_coord=after,
                    strategy=f"direct_target:{direct_plan.direction}:{direct_plan.radius}",
                    script_name=str(self.flow.get("script_name") or ""),
                )
                reusable_current = list(after)
                target_direct_click_ready = False
                target_direct_click_attempts += 1
                grid.record_movement(before, after, success=outcome.success, stuck=outcome.stuck, waypoint=target)
                if coord_within_tolerance(after, target, 0):
                    grid.mark_walkable(after)
                    grid.save(self.storage.walkability_path(self.current_map))
                    self.log(f"精准直点到达游戏坐标：{after}")
                    return True
                after_distance = coord_distance(after, target)
                if (
                    target_backoff_enabled
                    and target_backoff_attempts < target_backoff_max_attempts
                    and (outcome.stuck or after_distance >= before_distance or coord_chebyshev_distance(after, target) <= 1)
                ):
                    backoff_goal = self.select_target_backoff_point(
                        after,
                        target,
                        grid,
                        distance=max(target_backoff_distance, exact_direct_click_distance + target_backoff_attempts),
                        require_walkable=True,
                        avoid=bad_backoff_goals,
                    )
                    if backoff_goal is not None:
                        target_backoff_goal = backoff_goal
                        target_backoff_attempts += 1
                        stuck_detector.clear_strategy_failures()
                        self.log(
                            f"精准直点未到位，疑似仍点到人物或边缘：{before} -> {after}，"
                            f"换拉开点 {target_backoff_goal} 后再直点目标 {target}。"
                        )
                        grid.save(self.storage.walkability_path(self.current_map))
                        continue
                if outcome.stuck:
                    grid.mark_danger(target)
                    self.log(f"精准直点无进展：{before} -> {after}，下次换拉开点或路线。")
                else:
                    grid.mark_walkable(after)
                    self.log(f"精准直点样本：{before} -> {after}，进展 {outcome.progress_score:.1f}")
                grid.save(self.storage.walkability_path(self.current_map))
                continue
            if movement_profile is None:
                movement_profile = self.storage.aggregate_movement_samples(
                    self.current_map,
                    limit=2000,
                    script_name=str(self.flow.get("script_name") or ""),
                )
                if int(movement_profile.get("sample_count", 0) or 0) < 8:
                    movement_profile = self.storage.aggregate_movement_samples(self.current_map, limit=2000)
            profile = movement_profile
            backoff_mode = target_backoff_goal is not None
            nav_goal_tolerance = arrival_tolerance
            if backoff_mode:
                nav_goal = [int(target_backoff_goal[0]), int(target_backoff_goal[1])]
                plan = self.path_planner.plan(
                    current,
                    nav_goal,
                    grid,
                    allow_unknown=True,
                    lookahead=min(waypoint_lookahead, max(1, coord_distance(current, nav_goal))),
                )
            elif force_target_until_visible or route_requires_next_image:
                nav_goal = [int(target[0]), int(target[1])]
                nav_goal_tolerance = 0 if force_target_until_visible and not exact_target else arrival_tolerance
                plan = self.path_planner.plan(
                    current,
                    nav_goal,
                    grid,
                    allow_unknown=True,
                    lookahead=min(waypoint_lookahead, max(1, coord_distance(current, nav_goal))),
                )
            else:
                approach = self.approach_selector.select(
                    current,
                    target,
                    grid,
                    tolerance=arrival_tolerance,
                    approach_radius=approach_radius if use_approach_points else 0,
                    max_radius=max_approach_radius,
                    lookahead=min(waypoint_lookahead, max(1, coord_distance(current, target))),
                    allow_unknown=True,
                )
                nav_goal = [approach.coord[0], approach.coord[1]]
                plan = approach.plan
                nav_goal_tolerance = arrival_tolerance if exact_target else min(1, arrival_tolerance)
            if coord_within_tolerance(current, nav_goal, nav_goal_tolerance):
                missing_next, next_name, confidence, visible_confidence = self.next_image_missing_for_route_arrival(
                    data,
                    exact_target=exact_target,
                    index=index,
                )
                if missing_next and not backoff_mode:
                    if nav_goal != target:
                        self.log(
                            f"已到达接近点 {nav_goal}，但下一目标 {next_name} 置信度 "
                            f"{confidence:.3f} < {visible_confidence:.3f}，继续向目标坐标 {target} 推进。"
                        )
                        nav_goal = [int(target[0]), int(target[1])]
                        nav_goal_tolerance = 0
                        plan = self.path_planner.plan(
                            current,
                            nav_goal,
                            grid,
                            allow_unknown=True,
                            lookahead=min(waypoint_lookahead, max(1, coord_distance(current, nav_goal))),
                        )
                    else:
                        clear_goal = self.select_view_clear_point(
                            current,
                            target,
                            grid,
                            target_hint=data.get("target_hint_coord"),
                            avoid=bad_view_clear_goals,
                        )
                        if clear_goal is not None:
                            bad_view_clear_goals.add((int(clear_goal[0]), int(clear_goal[1])))
                            nav_goal = clear_goal
                            nav_goal_tolerance = 1
                            plan = self.path_planner.plan(
                                current,
                                nav_goal,
                                grid,
                                allow_unknown=True,
                                lookahead=min(waypoint_lookahead, max(1, coord_distance(current, nav_goal))),
                            )
                            self.log(
                                f"已到达路线点 {target}，但下一目标 {next_name} 仍不可见 "
                                f"({confidence:.3f} < {visible_confidence:.3f})，疑似队伍遮挡，换站位 {nav_goal}。"
                            )
                        else:
                            self.log(
                                f"已到达路线点 {target}，但下一目标 {next_name} 仍不可见 "
                                f"({confidence:.3f} < {visible_confidence:.3f})，继续重试移动。"
                            )
                else:
                    corrected_current = self.correct_suspicious_near_arrival(
                        current,
                        nav_goal=nav_goal,
                        target=target,
                        arrival_tolerance=nav_goal_tolerance,
                        exact_target=exact_target,
                        index=index,
                    ) if not had_reliable_coord else None
                    if corrected_current is not None:
                        self.current_game_coord = corrected_current
                        reusable_current = corrected_current
                        movement_coord_known = True
                        continue
                    grid.mark_walkable(current)
                    grid.save(self.storage.walkability_path(self.current_map))
                    if nav_goal != target:
                        self.log(f"已到达目标接近点：当前 {current}，目标 {target}，接近点 {nav_goal}")
                    else:
                        self.log(f"已到达游戏坐标：{current}")
                    return True
            waypoint = plan.waypoint
            if waypoint is None:
                self.log(f"路线规划失败：当前 {current}，目标 {target}。")
                grid.save(self.storage.walkability_path(self.current_map))
                return False
            screen_size = self.current_screen_size()
            character_position = data.get("character_screen_position")
            fine_tuning = (not exact_target) and (not backoff_mode) and coord_distance(current, nav_goal) <= fine_tune_distance
            effective_radii = click_radii if backoff_mode else fine_click_radii if fine_tuning else click_radii
            click_profile = dict(profile)
            if fine_tuning and effective_radii:
                click_profile["map_best_radius"] = int(effective_radii[min(1, len(effective_radii) - 1)])
            move_click = self.local_movement_controller.choose_click(
                current,
                waypoint,
                screen_size=screen_size,
                profile=click_profile,
                character_position=character_position,
                configured_radii=effective_radii,
                blocked_strategies=stuck_detector.blocked_strategies,
                safe_rect=data.get("movement_click_safe_rect"),
            )
            before = list(current)
            before_distance = coord_distance(before, nav_goal)
            self.adb.tap(int(move_click.point[0]), int(move_click.point[1]))
            self.log(
                f"移动一步：当前 {current} -> waypoint {list(waypoint)} -> 接近点 {nav_goal}，"
                f"相对点击 {list(move_click.relative_to_character)} 半径 {move_click.radius} "
                f"方向 {move_click.direction}"
            )
            after, duration = self.wait_for_movement_result(
                step["id"],
                before=before,
                max_jump=max_jump,
                target_hint=nav_goal,
                save_capture=False,
                poll_interval=poll_interval,
                settle_seconds=settle_seconds,
            )
            if not after:
                after = before
            delta = [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])]
            plausible = movement_delta_is_plausible(delta) or delta == [0, 0]
            if not plausible:
                stuck_detector.mark_strategy_failed(move_click.strategy_key)
                grid.mark_danger(waypoint)
                grid.save(self.storage.walkability_path(self.current_map))
                self.log(f"移动 delta 过大 {delta}，疑似 OCR 误读或传送，已重读当前位置并重规划。")
                continue
            outcome = stuck_detector.evaluate(
                before,
                after,
                waypoint=waypoint,
                target=nav_goal,
                tolerance=nav_goal_tolerance,
            )
            backoff_recovery_goal: list[int] | None = None
            if (
                exact_target
                and target_backoff_enabled
                and not backoff_mode
                and outcome.stuck
                and target_backoff_attempts < target_backoff_max_attempts
                and not coord_within_tolerance(after, target, 0)
                and coord_chebyshev_distance(after, target) <= max(1, target_backoff_trigger_distance)
            ):
                backoff_recovery_goal = self.select_target_backoff_point(
                    after,
                    target,
                    grid,
                    distance=target_backoff_distance,
                    require_walkable=True,
                    avoid=bad_backoff_goals,
                )
            self.storage.add_movement_sample(
                map_id=self.current_map,
                before_game_coord=before,
                after_game_coord=after,
                click_relative_to_character=list(move_click.relative_to_character),
                click_angle=move_click.angle,
                click_radius=move_click.radius,
                actual_delta=delta,
                duration=duration,
                success=outcome.success,
                stuck=outcome.stuck,
                progress_score=outcome.progress_score,
                direction=move_click.direction,
                screen_point=list(move_click.point),
                start_coord=before,
                end_coord=after,
                strategy=move_click.strategy_key,
                script_name=str(self.flow.get("script_name") or ""),
            )
            reusable_current = list(after)
            grid.record_movement(before, after, success=outcome.success, stuck=outcome.stuck, waypoint=waypoint)
            after_distance = coord_distance(after, nav_goal)
            if backoff_mode and (outcome.stuck or after_distance >= before_distance):
                bad_backoff_goals.add((int(nav_goal[0]), int(nav_goal[1])))
                target_backoff_goal = None
                target_direct_click_ready = True
                stuck_detector.clear_strategy_failures()
                grid.mark_danger(nav_goal, amount=2)
                self.log(
                    f"拉开点疑似不可达或在地图边缘外：{nav_goal}，"
                    f"放弃这个拉开点，改为直接回切目标 {target}。"
                )
                grid.save(self.storage.walkability_path(self.current_map))
                continue
            if backoff_recovery_goal is not None:
                target_backoff_goal = backoff_recovery_goal
                target_backoff_attempts += 1
                stuck_detector.clear_strategy_failures()
                grid.mark_danger(after)
                self.log(
                    f"目标坐标贴身点击疑似被人物挡住：当前 {after}，"
                    f"先拉开到 {target_backoff_goal}，再回切目标 {target}。"
                )
                grid.save(self.storage.walkability_path(self.current_map))
                continue
            if outcome.stuck:
                stuck_detector.mark_strategy_failed(move_click.strategy_key)
                grid.mark_danger(waypoint)
                if outcome.consecutive_stuck >= 2 and tuple(waypoint) != (target[0], target[1]):
                    grid.mark_blocked(waypoint)
                self.log(
                    f"检测到卡点：{before} -> {after}，原因 {outcome.reason}，"
                    f"下次换半径/角度并重规划。"
                )
                self.record_bug_report(
                    f"移动卡点：{before} -> {after}",
                    kind="stuck",
                    step=step,
                    metadata={
                        "before": before,
                        "after": after,
                        "reason": outcome.reason,
                        "waypoint": waypoint,
                        "target": target,
                    },
                    cooldown_key=f"stuck:{step.get('id')}:{before}:{after}:{int(time.time() // 60)}",
                    cooldown_seconds=60.0,
                )
            elif after_distance > before_distance:
                stuck_detector.mark_strategy_failed(move_click.strategy_key)
                grid.mark_danger(waypoint)
                self.log("这一步离接近点更远，已标记为风险方向并重规划。")
            else:
                stuck_detector.clear_strategy_failures()
                grid.mark_walkable(after)
                self.log(f"移动样本：{before} -> {after}，进展 {outcome.progress_score:.1f}")
            grid.save(self.storage.walkability_path(self.current_map))

    def click_point_for_match(self, result: Any, data: dict[str, Any]) -> tuple[tuple[int, int] | None, str]:
        click_mode = str(data.get("click_mode") or "center")
        offset = data.get("click_offset")
        bbox = result.bbox
        if (
            click_mode == "template_point"
            and isinstance(offset, list)
            and len(offset) >= 2
            and bbox
            and len(bbox) >= 4
        ):
            ox = max(0, min(int(bbox[2]) - 1, int(offset[0])))
            oy = max(0, min(int(bbox[3]) - 1, int(offset[1])))
            return (int(bbox[0]) + ox, int(bbox[1]) + oy), f"模板内点击点 {ox}, {oy}"
        if result.center:
            return (int(result.center[0]), int(result.center[1])), "匹配中心"
        return None, "无点击点"

    def execute_image_step(self, step: dict[str, Any]) -> bool:
        template = self.storage.abs(step["input"].get("template_path"))
        if not template:
            self.log("图片识别步骤缺少模板。")
            return False
        data = step.setdefault("input", {})
        threshold = float(data.get("threshold", 0.85))
        wait_until = bool(data.get("wait_until_found", True)) and not bool(data.get("instant_check", False))
        timeout = float(step.get("timeout", 10.0))
        poll_interval = max(0.1, float(data.get("poll_interval", 0.4)))
        wait_after_found = max(0.0, float(data.get("wait_after_found", 0.5)))
        action_on_found = str(data.get("action_on_found") or "next")
        action_on_missing = str(data.get("action_on_missing") or "fail")
        deadline = time.monotonic() + max(0.1, timeout)
        best_confidence = 0.0
        attempt = 0
        last_wait_log = 0.0
        last_logged_confidence = 0.0

        while True:
            self.runtime_yield()
            if self._stop_requested:
                return False
            if not self.refresh_current_frame():
                return False
            result = match_template_qimage(self.current_frame, template, threshold, search_bbox=data.get("search_bbox"))
            attempt += 1
            best_confidence = max(best_confidence, result.confidence)
            step["output"] = {
                "found": result.found,
                "confidence": result.confidence,
                "bbox": result.bbox,
                "center": result.center,
                "error": result.error,
                "attempt": attempt,
            }
            if result.found or result.error or attempt == 1 or attempt % 8 == 0:
                self.append_result(str(step["output"]))
            if result.error:
                self.log(result.error)
                return False
            if result.found:
                self.log(f"识别成功，置信度 {result.confidence:.3f}，位置 {result.center}")
                if action_on_found == "fail":
                    self.log("已识别到目标画面，按配置判定为失败停止。")
                    return False
                if action_on_found == "skip":
                    step.setdefault("output", {})["skipped"] = True
                    self.log("已识别到目标画面，按配置跳过此步骤。")
                    return True
                if step["type"] == "click_target":
                    click_point, click_label = self.click_point_for_match(result, data)
                    step["output"]["click_point"] = click_point
                    step["output"]["click_label"] = click_label
                    if click_point:
                        if not self.tap_repeated(
                            click_point[0],
                            click_point[1],
                            data.get("click_count", 1),
                            data.get("click_interval", 0.08),
                            label=f"已点击{click_label}",
                        ):
                            return False
                if wait_after_found:
                    if not self.sleep_with_events(wait_after_found):
                        return False
                    self.log(f"识别后等待 {wait_after_found:.2f}s 完成。")
                return True
            if not wait_until or time.monotonic() >= deadline:
                break
            now = time.monotonic()
            if now - last_wait_log >= 1.5 or best_confidence - last_logged_confidence >= 0.05:
                self.log(f"等待图片出现：最高置信度 {best_confidence:.3f}，继续等待...")
                last_wait_log = now
                last_logged_confidence = best_confidence
            if not self.sleep_with_events(poll_interval):
                return False

        if data.get("instant_check"):
            self.log(f"当前完成判断未满足，最高置信度 {best_confidence:.3f}")
        else:
            self.log(f"等待图片超时，未找到模板，最高置信度 {best_confidence:.3f}")
        if action_on_missing == "skip":
            step.setdefault("output", {})["skipped"] = True
            self.log("未找到目标画面，按配置跳过此步骤，继续后续流程。")
            return True
        return False

    def execute_ocr_step(self, step: dict[str, Any]) -> bool:
        if not self.refresh_current_frame():
            return False
        bbox = step["input"].get("bbox")
        if not bbox:
            self.log("OCR 步骤缺少区域。")
            return False
        rect = QRect(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
        crop = self.current_frame.copy(rect)
        step_dir = self.storage.step_dir(self.flow["script_name"], step["id"], step["type"])
        crop_path = step_dir / f"ocr_{int(time.time())}.png"
        crop.save(str(crop_path))
        mode = "digit" if step["type"] == "ocr_number" else "text"
        result = self.ocr.recognize(crop_path, mode=mode)
        step["input"]["last_result"] = result.text
        step["input"]["confidence"] = result.confidence
        step["output"] = {
            "text": result.text,
            "confidence": result.confidence,
            "backend": result.backend,
            "available": result.available,
            "error": result.error,
        }
        self.append_result(str(step["output"]))
        if result.text and result.confidence >= 0.65:
            self.log(f"OCR 成功：{result.text} ({result.confidence:.2f}, {result.backend})")
            return True
        if step["input"].get("save_unknown_to_pending", True):
            self.storage.add_pending_review(
                mode,
                step["input"].get("asset_id"),
                crop_path,
                {
                    "map_id": self.current_map,
                    "source_ui": "runtime_ocr",
                    "ocr_backend": result.backend,
                    "ocr_error": result.error,
                },
            )
            self.log("OCR 低置信度，已保存到 pending_review。")
        return False

    def execute_verify_code_step(self, step: dict[str, Any]) -> bool:
        data = step.setdefault("input", {})
        bbox = data.get("digit_bbox") or data.get("bbox")
        rect = rect_from_bbox(bbox)
        if rect is None:
            self.log("验证码步骤缺少数字区域。请先用“插入验证码”或属性里的“重新框选”设置。")
            return False

        step_dir = self.storage.step_dir(self.flow["script_name"], step["id"], "verify_code")
        expected_length = int(data.get("expected_length", 4))
        min_confidence = float(data.get("min_confidence", DEFAULT_VERIFY_CODE_MIN_CONFIDENCE))
        retry_count = max(1, int(data.get("retry_count", 2) or 1))
        retry_interval = max(0.0, float(data.get("retry_interval", 0.7) or 0.0))
        wait_before_capture = max(0.0, float(data.get("wait_before_capture", 1.0) or 0.0))

        code = ""
        digit_parts: list[dict[str, Any]] = []
        chosen_backend = ""
        chosen_confidence = 0.0
        crop_path: Path | None = None
        failure_reason = "single digit recognition empty"

        if wait_before_capture:
            if not self.sleep_with_events(wait_before_capture):
                return False

        for attempt in range(1, retry_count + 1):
            if attempt > 1 and retry_interval:
                self.log(f"验证码识别未完成，等待 {retry_interval:.2f}s 后重试 {attempt}/{retry_count}。")
                if not self.sleep_with_events(retry_interval):
                    return False
            if not self.refresh_current_frame(allow_cached=False) or self.current_frame is None:
                return False
            bounded = rect.intersected(QRect(0, 0, self.current_frame.width(), self.current_frame.height()))
            if bounded.width() <= 0 or bounded.height() <= 0:
                self.log(f"验证码数字区域无效：{bbox}")
                return False

            crop_path = step_dir / f"verify_code_{int(time.time() * 1000)}.png"
            digit_image = self.current_frame.copy(bounded)
            digit_image.save(str(crop_path))

            per_digit = self.recognize_verify_code_digits(digit_image, step_dir, expected_length)
            digit_parts = per_digit["parts"]
            code = str(per_digit["code"])
            chosen_backend = str(per_digit["backend"])
            chosen_confidence = float(per_digit["confidence"])

            data["last_result"] = code
            data["confidence"] = chosen_confidence
            step["output"] = {
                "code": code,
                "confidence": chosen_confidence,
                "backend": chosen_backend,
                "crop": self.storage.rel(crop_path),
                "parts": digit_parts,
                "attempt": attempt,
            }
            self.append_result(str(step["output"]))

            if not code:
                failure_reason = "single digit recognition empty"
                self.log(f"验证码 OCR 未识别到数字：{self.storage.rel(crop_path)}")
                continue
            if expected_length and len(code) != expected_length:
                failure_reason = f"expected {expected_length}, got {len(code)}"
                self.log(f"验证码单字未完整识别：{code or '(空)'}，期望 {expected_length} 位。")
                continue
            if chosen_confidence < min_confidence:
                failure_reason = "low confidence"
                self.log(f"验证码置信度过低：{code} ({chosen_confidence:.2f} < {min_confidence:.2f})")
                continue
            break
        else:
            if crop_path is not None:
                self.save_verify_code_pending(step, crop_path, failure_reason)
            return False

        if not code or (expected_length and len(code) != expected_length) or chosen_confidence < min_confidence:
            if crop_path is not None:
                self.save_verify_code_pending(step, crop_path, failure_reason)
            return False

        input_coord = data.get("input_coord") or [0, 0]
        confirm_coord = data.get("confirm_coord") or [0, 0]
        if len(input_coord) < 2 or len(confirm_coord) < 2:
            self.log("验证码步骤缺少输入框或确定按钮坐标。")
            return False

        self.log(f"验证码识别成功：{code} ({chosen_confidence:.2f}, {chosen_backend})")
        if not self.tap_repeated(
            int(input_coord[0]),
            int(input_coord[1]),
            data.get("input_click_count", 1),
            data.get("click_interval", 0.08),
            label="点击验证码输入框",
        ):
            return False
        if not self.sleep_with_events(float(data.get("wait_after_focus", 0.20))):
            return False
        if self._stop_requested:
            return False
        self.adb.text(code)
        if not self.sleep_with_events(float(data.get("wait_after_input", 0.20))):
            return False
        if not self.tap_repeated(
            int(confirm_coord[0]),
            int(confirm_coord[1]),
            data.get("confirm_click_count", 1),
            data.get("click_interval", 0.08),
            label="点击验证码确定",
        ):
            return False
        wait_after = float(data.get("wait_after_confirm", 0.80))
        if wait_after:
            if not self.sleep_with_events(wait_after):
                return False
        self.log(f"已输入验证码并点击确定：{code}")
        if digit_parts and data.get("auto_archive_verify_digits", False):
            self.archive_verify_code_digit_samples(code, digit_parts, data.get("asset_id"))
        return True

    def ocr_digit_library_candidates(self, path: Path) -> list[dict[str, Any]]:
        target_mask = verification_digit_mask_from_path(path)
        if target_mask is None:
            return []
        scores: dict[str, list[float]] = {}
        best_paths: dict[str, str] = {}
        for sample in self.storage.list_ocr_digit_samples(limit_per_digit=12):
            digit = str(sample["value"])
            if not is_single_digit_value(digit):
                continue
            sample_path = self.storage.abs(sample["image_path"])
            if sample_path is None or not sample_path.exists():
                continue
            sample_mask = verification_digit_mask_from_path(sample_path)
            similarity = verification_digit_mask_similarity(target_mask, sample_mask)
            if similarity < 0.48:
                continue
            scores.setdefault(digit, []).append(similarity)
            if digit not in best_paths or similarity > max(scores[digit][:-1] or [0.0]):
                best_paths[digit] = self.storage.rel(sample_path) or str(sample_path)
        candidates: list[dict[str, Any]] = []
        for digit, digit_scores in scores.items():
            digit_scores.sort(reverse=True)
            best = digit_scores[0]
            top_scores = digit_scores[:3]
            average = sum(top_scores) / len(top_scores)
            confidence = min(0.99, 0.58 + max(best, average) * 0.42)
            candidates.append(
                {
                    "digit": digit,
                    "confidence": confidence,
                    "backend": f"digit-library:{len(digit_scores)}",
                    "library_score": best,
                    "library_path": best_paths.get(digit, ""),
                }
            )
        candidates.sort(key=lambda item: float(item["confidence"]), reverse=True)
        return candidates

    def recognize_verify_code_digits(self, image: QImage, step_dir: Path, expected_length: int) -> dict[str, Any]:
        if expected_length <= 0:
            return {"code": "", "confidence": 0.0, "backend": "single-digit-disabled", "parts": []}
        rect_groups = digit_component_rects(image, expected_length)
        digits: list[str] = []
        confidences: list[float] = []
        backends: list[str] = []
        parts: list[dict[str, Any]] = []
        timestamp = int(time.time() * 1000)
        for index, (full_rect, tight_rect) in enumerate(rect_groups, start=1):
            candidates: list[dict[str, Any]] = []
            for variant, rect in (("full", full_rect), ("tight", tight_rect)):
                path = step_dir / f"verify_digit_{timestamp}_{index}_{variant}.png"
                image.copy(rect).save(str(path))
                result = self.ocr.recognize(path, mode="digit")
                text = clean_verification_digits(result.text)
                if len(text) == 1:
                    candidates.append(
                        {
                            "digit": text,
                            "confidence": float(result.confidence),
                            "backend": result.backend,
                            "path": self.storage.rel(path),
                            "abs_path": path,
                            "variant": variant,
                            "bbox": bbox_from_rect(rect),
                            "source": "ocr",
                        }
                    )
                if variant == "tight":
                    for extra in self.ocr_digit_library_candidates(path):
                        candidates.append(
                            {
                                "digit": extra["digit"],
                                "confidence": float(extra["confidence"]),
                                "backend": extra["backend"],
                                "path": self.storage.rel(path),
                                "abs_path": path,
                                "variant": variant,
                                "bbox": bbox_from_rect(rect),
                                "source": "library",
                                "library_score": float(extra.get("library_score", 0.0)),
                                "library_path": extra.get("library_path", ""),
                            }
                        )
                    for extra in tesseract_single_digit_candidates(path):
                        candidates.append(
                            {
                                "digit": extra["digit"],
                                "confidence": float(extra["confidence"]),
                                "backend": extra["backend"],
                                "path": self.storage.rel(path),
                                "abs_path": path,
                                "variant": variant,
                                "bbox": bbox_from_rect(rect),
                                "source": "tesseract",
                            }
                        )
            if not candidates:
                parts.append({"index": index, "digit": "", "confidence": 0.0, "backend": "none"})
                continue
            library_candidates = [
                candidate for candidate in candidates if candidate.get("source") == "library"
            ]
            library_candidates.sort(key=lambda item: float(item["confidence"]), reverse=True)
            best: dict[str, Any] | None = None
            if library_candidates:
                top = library_candidates[0]
                runner_up = library_candidates[1] if len(library_candidates) > 1 else None
                margin = float(top["confidence"]) - float(runner_up["confidence"]) if runner_up else 1.0
                if float(top["confidence"]) >= 0.78 and margin >= 0.035:
                    best = dict(top)
            scores: dict[str, dict[str, Any]] = {}
            for candidate in candidates:
                digit = str(candidate.get("digit", ""))
                if not is_single_digit_value(digit):
                    continue
                current = scores.get(digit)
                if current is None or float(candidate.get("confidence", 0.0)) > float(current.get("confidence", 0.0)):
                    scores[digit] = candidate
            if best is None and not scores:
                parts.append({"index": index, "digit": "", "confidence": 0.0, "backend": "none"})
                continue
            if best is None:
                best = dict(max(scores.values(), key=lambda item: float(item.get("confidence", 0.0))))
                has_matching_library = any(
                    candidate.get("source") == "library"
                    and candidate.get("digit") == best.get("digit")
                    and float(candidate.get("confidence", 0.0)) >= 0.72
                    for candidate in candidates
                )
                if best.get("digit") in VERIFY_AMBIGUOUS_DIGITS and not has_matching_library:
                    best["confidence"] = min(float(best.get("confidence", 0.0)), 0.54)
            digits.append(str(best["digit"]))
            confidences.append(float(best["confidence"]))
            backends.append(str(best["backend"]))
            parts.append(
                {
                    "index": index,
                    "digit": best["digit"],
                    "confidence": best["confidence"],
                    "backend": best["backend"],
                    "path": best["path"],
                    "variant": best["variant"],
                    "bbox": best["bbox"],
                    "abs_path": str(best["abs_path"]),
                }
            )
        code = "".join(digits)
        confidence = min(confidences) if confidences else 0.0
        backend = "single-digit:" + ",".join(backends) if backends else "single-digit:none"
        if parts:
            readable = " ".join(
                f"{part.get('index')}={part.get('digit') or '?'}({float(part.get('confidence', 0.0)):.2f})"
                for part in parts
            )
            self.log(f"验证码单字识别：{readable} -> {code or '(空)'}")
        return {"code": code, "confidence": confidence, "backend": backend, "parts": parts}

    def archive_verify_code_digit_samples(
        self,
        code: str,
        parts: list[dict[str, Any]],
        source_asset_id: str | None,
    ) -> None:
        archived: list[str] = []
        skipped: list[str] = []
        counts = self.storage.ocr_digit_counts()
        for digit, part in zip(code, parts):
            digit = str(digit)
            if counts.get(digit, 0) > 0:
                skipped.append(digit)
                continue
            path_value = part.get("abs_path")
            if not path_value:
                continue
            path = Path(str(path_value))
            if not path.exists():
                continue
            self.storage.add_public_ocr_sample(
                kind="digit",
                value=str(digit),
                image_path=path,
                map_id=self.current_map,
                source_ui="verify_code_digit",
                confidence=float(part.get("confidence", 0.0)),
                source_asset_id=source_asset_id,
            )
            archived.append(digit)
            counts[digit] = counts.get(digit, 0) + 1
        if archived:
            self.log(f"已补齐缺失验证码数字样本：{' '.join(archived)}")
        if skipped:
            self.log(f"验证码数字样本已存在，跳过归档：{' '.join(skipped)}")

    def save_verify_code_pending(self, step: dict[str, Any], crop_path: Path, error: str | None) -> None:
        data = step.setdefault("input", {})
        if not data.get("save_unknown_to_pending", True):
            return
        self.storage.add_pending_review(
            "digit",
            data.get("asset_id"),
            crop_path,
            {
                "map_id": self.current_map,
                "source_ui": "verify_code",
                "step_id": step.get("id"),
                "ocr_error": error,
            },
        )
        self.log("验证码识别失败截图已保存到 pending_review。")

    def execute_question_step(self, step: dict[str, Any]) -> bool:
        data = step["input"]
        question_region = data.get("question_region")
        option_regions = data.get("option_regions") or []
        if (not question_region or len(option_regions) < 4) and self.apply_latest_question_layout(step, show_message=False):
            data = step["input"]
            question_region = data.get("question_region")
            option_regions = data.get("option_regions") or []
            self.log("答题步骤缺少区域，已自动套用最近题库布局。")
        if not question_region or len(option_regions) < 4:
            self.log("答题步骤缺少题目区域或四个选项区域。请先点“添加题库”框选一次布局。")
            return False

        target_count = int(data.get("target_correct_count", 1))
        max_attempts = int(data.get("max_attempts", target_count))
        wait_after = float(data.get("wait_after_answer", 0.8))
        progress_region = data.get("progress_region")
        confirm_region = data.get("confirm_region")
        use_question_visual_match = bool(data.get("use_question_visual_match", False))
        question_visual_threshold = float(data.get("question_visual_threshold", 0.90))
        option_visual_threshold = float(data.get("option_visual_threshold", 0.90))

        last_progress: tuple[int, int] | None = None
        for attempt in range(1, max_attempts + 1):
            self.runtime_yield()
            if self._stop_requested:
                return False
            if not self.refresh_current_frame():
                return False

            if progress_region:
                last_progress = self.read_question_progress(progress_region, target_count)
                if last_progress:
                    current, total = last_progress
                    self.log(f"答题进度：{current}/{total}")
                    if current >= target_count:
                        self.log("答题目标已完成。")
                        return True

            question_text = self.read_region_text(question_region, step["id"], "question", "question")
            option_texts = [
                self.read_region_text(bbox, step["id"], f"option_{index + 1}", "option")
                for index, bbox in enumerate(option_regions[:4])
            ]
            self.append_result(
                f"题目OCR: {question_text}\n选项OCR: {option_texts}\n"
            )

            row = self.storage.find_question(question_text, option_texts)
            if row is None:
                row = self.find_existing_question_from_capture(
                    self.current_frame,
                    question_region,
                    question_text,
                    option_texts,
                )
                if row:
                    self.log(f"题库快速查重命中：{row['id']}")
            if row is None and use_question_visual_match:
                row = self.find_question_by_visual(question_region, question_visual_threshold)
            elif row is None:
                self.log("题目文字未命中题库，已跳过题目截图相似度兜底，避免背景误判。")
            answer = ""
            question_id = ""
            if row:
                answer = row["answer"] or ""
                question_id = row["id"]
                self.log(f"题库命中：{question_id} -> {answer}")
            else:
                resolved = self.resolve_unknown_question(step, question_text, option_texts, option_regions, confirm_region)
                if not resolved:
                    return False
                answer = resolved["answer"]
                option_texts = resolved.get("option_texts") or option_texts
                option_regions = resolved.get("option_regions") or option_regions

            option_index = answer_to_index(answer, option_texts)
            if option_index is not None:
                self.log(f"通过当前选项 OCR 定位答案：{chr(ord('A') + option_index)}")
            if option_index is None and row:
                option_index = self.match_answer_option_by_visual(
                    row,
                    option_regions,
                    answer,
                    option_visual_threshold,
                )
            manual_answer_index: int | None = None
            if option_index is None and row:
                option_index = self.ask_answer_option(answer, option_texts)
                manual_answer_index = option_index

            if option_index is None or option_index >= len(option_regions):
                if option_regions:
                    option_index = 2 if len(option_regions) > 2 else 0
                    self.log(f"无法安全定位答案 {answer!r}，默认选择 {chr(ord('A') + option_index)}。")
                else:
                    self.log(f"无法安全定位答案 {answer!r}，且没有选项区域。")
                    return False

            if question_id and manual_answer_index is not None:
                remembered_answer = (option_texts[option_index] or "").strip() or chr(ord("A") + option_index)
                self.storage.update_question_answer(
                    question_id,
                    answer=remembered_answer,
                    options=option_texts,
                )
                answer = remembered_answer
                self.log(f"已记住人工定位答案：{question_id} -> {answer}")

            x, y = bbox_center(option_regions[option_index])
            if not self.tap_repeated(
                x,
                y,
                data.get("answer_click_count", 1),
                data.get("click_interval", 0.08),
                label=f"点击答案 {chr(ord('A') + option_index)}",
            ):
                return False
            if confirm_region:
                if not self.sleep_with_events(0.15):
                    return False
                cx, cy = bbox_center(confirm_region)
                if not self.tap_repeated(
                    cx,
                    cy,
                    data.get("confirm_click_count", 1),
                    data.get("click_interval", 0.08),
                    label="点击确定",
                ):
                    return False
            if question_id:
                self.storage.mark_question_answered(question_id)
            if not self.sleep_with_events(wait_after):
                return False

            if not progress_region and attempt >= target_count:
                self.log(f"已按目标次数答题：{attempt}/{target_count}")
                return True

        if last_progress:
            self.log(f"达到最多答题次数，最后进度：{last_progress[0]}/{last_progress[1]}")
        else:
            self.log("达到最多答题次数，答题步骤未确认完成。")
        return False

    def ask_answer_option(self, answer: str, option_texts: list[str]) -> int | None:
        fallback = 2 if len(option_texts) >= 3 else 0
        self.log(f"题库答案 {answer!r} 当前无法自动定位，默认选择 {chr(ord('A') + fallback)}。")
        return fallback

    def match_answer_option_by_visual(
        self,
        row: Any,
        current_option_regions: list[list[int]],
        answer: str,
        threshold: float,
    ) -> int | None:
        if self.current_frame is None:
            return None
        raw_path = self.storage.abs(row["raw_path"])
        if raw_path is None or not raw_path.exists():
            return None
        try:
            stored_options = json.loads(row["options"] or "[]")
            stored_option_bboxes = json.loads(row["option_bboxes"] or "[]")
        except json.JSONDecodeError:
            return None
        stored_index = answer_to_index(answer, stored_options)
        if stored_index is None or stored_index >= len(stored_option_bboxes):
            return None
        stored_image = QImage(str(raw_path))
        stored_rect = rect_from_bbox(stored_option_bboxes[stored_index])
        if stored_image.isNull() or stored_rect is None:
            return None
        stored_crop = stored_image.copy(stored_rect)

        best: tuple[float, int] | None = None
        for index, bbox in enumerate(current_option_regions[:4]):
            current_rect = rect_from_bbox(bbox)
            if current_rect is None:
                continue
            score = self.image_similarity(self.current_frame.copy(current_rect), stored_crop)
            if score is not None and (best is None or score > best[0]):
                best = (score, index)
        if best and best[0] >= threshold:
            self.log(f"答案选项视觉匹配：{chr(ord('A') + best[1])}，相似度 {best[0]:.3f}")
            return best[1]
        if best:
            self.log(f"答案选项视觉匹配未达阈值：最高 {best[0]:.3f}，阈值 {threshold:.3f}")
        return None

    def find_question_by_visual(self, question_region: list[int], threshold: float) -> Any | None:
        if self.current_frame is None:
            return None
        current_rect = rect_from_bbox(question_region)
        if current_rect is None:
            return None
        current_crop = self.current_frame.copy(current_rect)
        best: tuple[float, Any] | None = None
        for row in self.storage.list_questions():
            try:
                stored_bbox = json.loads(row["question_bbox"] or "null")
            except json.JSONDecodeError:
                stored_bbox = None
            stored_rect = rect_from_bbox(stored_bbox)
            raw_path = self.storage.abs(row["raw_path"])
            if stored_rect is None or raw_path is None or not raw_path.exists():
                continue
            stored_image = QImage(str(raw_path))
            if stored_image.isNull():
                continue
            stored_crop = stored_image.copy(stored_rect)
            score = self.image_similarity(current_crop, stored_crop)
            if score is not None and (best is None or score > best[0]):
                best = (score, row)
        if best and best[0] >= threshold:
            self.log(f"题库视觉命中：{best[1]['id']}，相似度 {best[0]:.3f}")
            return best[1]
        if best:
            self.log(f"题库视觉未达阈值：最高 {best[0]:.3f}，阈值 {threshold:.3f}")
        return None

    def image_similarity(self, first: QImage, second: QImage) -> float | None:
        try:
            import numpy as np  # type: ignore
        except Exception:
            return None
        if first.isNull() or second.isNull():
            return None
        width = min(240, max(16, min(first.width(), second.width())))
        height = min(80, max(16, min(first.height(), second.height())))
        a_img = first.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        b_img = second.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        a_img = a_img.convertToFormat(QImage.Format.Format_RGB888)
        b_img = b_img.convertToFormat(QImage.Format.Format_RGB888)
        a = np.frombuffer(a_img.bits(), dtype=np.uint8).reshape((height, a_img.bytesPerLine()))[:, : width * 3]
        b = np.frombuffer(b_img.bits(), dtype=np.uint8).reshape((height, b_img.bytesPerLine()))[:, : width * 3]
        diff = np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))) / 255.0
        return max(0.0, min(1.0, 1.0 - float(diff)))

    def question_crop_similarity(self, first: QImage, second: QImage) -> float | None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return self.image_similarity(first, second)
        if first.isNull() or second.isNull():
            return None
        width = min(420, max(80, min(first.width(), second.width())))
        height = min(120, max(36, min(first.height(), second.height())))
        first_gray = first.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        second_gray = second.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        first_gray = first_gray.convertToFormat(QImage.Format.Format_Grayscale8)
        second_gray = second_gray.convertToFormat(QImage.Format.Format_Grayscale8)
        a = np.frombuffer(first_gray.bits(), dtype=np.uint8).reshape((height, first_gray.bytesPerLine()))[:, :width]
        b = np.frombuffer(second_gray.bits(), dtype=np.uint8).reshape((height, second_gray.bytesPerLine()))[:, :width]
        a_edges = cv2.Canny(a, 50, 150)
        b_edges = cv2.Canny(b, 50, 150)
        edge_score = 1.0 - float(np.mean(np.abs(a_edges.astype(np.int16) - b_edges.astype(np.int16))) / 255.0)
        tone_score = 1.0 - float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))) / 255.0)
        return max(0.0, min(1.0, edge_score * 0.72 + tone_score * 0.28))

    def refresh_current_frame(self, display: bool = False, allow_cached: bool = True) -> bool:
        try:
            image = QImage.fromData(self.adb.screencap_png(), "PNG")
        except Exception as exc:  # noqa: BLE001
            if self.current_frame is None or not allow_cached:
                self.log(f"无法截图：{exc}")
                return False
            self.log(f"截图失败，使用当前缓存画面：{exc}")
            return True
        if image.isNull():
            self.log("ADB 截图为空。")
            return False
        self.current_frame = image.copy()
        if self.is_main_thread():
            self.update_frame_status(image)
        if display and self.preview_enabled and hasattr(self, "game_view") and self.is_main_thread():
            self.game_view.set_frame(image)
        return True

    def capture_current_screen(self, save: bool = True, allow_cached: bool = True) -> bool:
        if not self.refresh_current_frame(display=True, allow_cached=allow_cached) or self.current_frame is None:
            return False
        if save:
            path = self.storage.screenshots_dir() / f"{self.current_map}_screen_{int(time.time() * 1000)}.png"
            self.current_frame.save(str(path))
            self.log(f"已截图当前画面：{self.storage.rel(path)}")
        else:
            self.log("已截图当前画面。")
        return True

    def read_region_text(self, bbox: list[int], step_id: str, name: str, mode: str) -> str:
        if self.current_frame is None:
            return ""
        step_dir = self.storage.step_dir(self.flow["script_name"], step_id, "question")
        path = step_dir / f"{name}_{int(time.time() * 1000)}.png"
        rect = rect_from_bbox(bbox)
        if rect is None:
            return ""
        self.current_frame.copy(rect).save(str(path))
        result = self.ocr.recognize(path, mode=mode)
        self.log(f"OCR {name}: {result.text or '(空)'} [{result.backend}]")
        return result.text

    def read_question_progress(self, bbox: list[int], fallback_total: int) -> tuple[int, int] | None:
        text = self.read_region_text(bbox, "runtime_progress", "progress", "text")
        progress = parse_progress_text(text, fallback_total)
        if not progress:
            self.log(f"未能解析答题进度 OCR：{text!r}")
        return progress

    def resolve_unknown_question(
        self,
        step: dict[str, Any],
        question_text: str,
        option_texts: list[str],
        option_regions: list[list[int]],
        confirm_region: list[int] | None,
    ) -> dict[str, Any] | None:
        policy = step["input"].get("unknown_policy", "choose_c")
        if policy in {"ask", "choose_c", "default_c"}:
            self.log("未知题目，默认选择 C。")
            return {"answer": "C", "option_texts": option_texts, "option_regions": option_regions}
        if policy == "skip":
            self.log("未知题目，按配置跳过。")
            return None
        if policy == "pause":
            self.storage.add_pending_review(
                "question",
                None,
                None,
                {
                    "question": question_text,
                    "options": option_texts,
                    "map_id": self.current_map,
                    "source_ui": "answer_step",
                },
            )
            self.log("未知题目，已保存到 pending_review 并暂停。")
            return None
        self.log("未知题目策略未识别，默认选择 C。")
        return {"answer": "C", "option_texts": option_texts, "option_regions": option_regions}

    def open_asset_manager(self) -> None:
        AssetManagerDialog(self.storage, self, use_callback=self.insert_step_from_asset).exec()

    def open_step_reuse(self) -> None:
        dialog = StepReuseDialog(self.storage, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_step:
            return
        step = dialog.selected_step
        refresh_step_identity(step)
        placement = self.place_step_in_context(step, bool(self.current_step()))
        self.refresh_step_list(select_step_id=step["id"])
        where = "循环子步骤" if placement == "loop" else "步骤"
        self.log(f"已复用历史{where}：{step.get('name')}")

    def capture_question_result_condition(self, question_id: str, kind: str) -> bool:
        if self.current_frame is None and not self.capture_current_screen(save=False):
            QMessageBox.information(self, "题库判定截图", "请先连接 MuMu，并确认 ADB 可以截图。")
            return False
        if self.current_frame is None:
            return False
        label = "成功" if kind == "success" else "失败"
        frame = self.current_frame.copy()
        dialog = LargeRegionDialog(
            frame,
            title=f"题库{label}判定截图",
            fixed_kind="screenshot",
            hint=f"请框选之后用来判断答题{label}的画面区域。当前版本只保存占位，不参与成功失败判定。",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return False
        bbox = bbox_from_rect(dialog.selected_rect)
        rect = rect_from_bbox(bbox)
        if rect is None:
            return False
        bounded = rect.intersected(QRect(0, 0, frame.width(), frame.height()))
        if bounded.width() < 2 or bounded.height() < 2:
            QMessageBox.information(self, "题库判定截图", "框选区域太小，请重新选择。")
            return False
        out_dir = self.storage.question_result_conditions_dir()
        timestamp = int(time.time() * 1000)
        crop_path = out_dir / f"{question_id}_{kind}_{timestamp}.png"
        frame.copy(bounded).save(str(crop_path))
        self.storage.update_question_result_condition(
            question_id,
            kind=kind,
            image_path=crop_path,
            bbox=bbox_from_rect(bounded),
        )
        self.log(f"已保存题库{label}判定截图：{self.storage.rel(crop_path)}")
        return True

    def open_pending_review(self) -> None:
        PendingReviewDialog(self.storage, self).exec()

    def open_question_bank(self) -> None:
        QuestionBankDialog(
            self.storage,
            self,
            result_capture_callback=self.capture_question_result_condition,
            add_question_callback=self.start_question_capture,
        ).exec()

    def open_material_tool(self) -> None:
        from .material_tool import MaterialToolDialog

        if self.material_tool_dialog is None:
            self.material_tool_dialog = MaterialToolDialog(
                self.workspace,
                self,
                capture_callback=self.capture_material_tool_region,
            )
        self.material_tool_dialog.show()
        self.material_tool_dialog.raise_()
        self.material_tool_dialog.activateWindow()

    def open_deepsea_chest_stats(self) -> None:
        if self.deepsea_chest_dialog is None:
            self.deepsea_chest_dialog = DeepSeaChestStatsDialog(self.storage, self)
        else:
            self.deepsea_chest_dialog.refresh_all()
        self.deepsea_chest_dialog.show()
        self.deepsea_chest_dialog.raise_()
        self.deepsea_chest_dialog.activateWindow()

    def open_material_web(self) -> None:
        from .material_web import start_server_in_thread

        if self.material_web_server is None:
            try:
                self.material_web_server, self.material_web_url = start_server_in_thread(self.workspace, port=8765)
            except OSError:
                self.material_web_server, self.material_web_url = start_server_in_thread(self.workspace, port=0)
            self.log(f"网页版材料库已启动：{self.material_web_url}")
        if self.material_web_url:
            webbrowser.open(self.material_web_url)

    def capture_material_tool_region(self, parent: QWidget, hint: str) -> dict[str, str] | None:
        if self.current_frame is None and not self.capture_current_screen(save=False):
            QMessageBox.information(parent, "材料库截图 OCR", "请先连接 MuMu，并确认 ADB 可以截图。")
            return None
        if self.current_frame is None:
            return None
        frame = self.current_frame.copy()
        dialog = LargeRegionDialog(
            frame,
            title="材料库截图 OCR",
            fixed_kind="text",
            hint=f"{hint}。框选后会 OCR 识别文字，并保存裁剪图；识别结果仍可手动修改。",
            parent=parent,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_rect is None:
            return None
        bounded = dialog.selected_rect.intersected(QRect(0, 0, frame.width(), frame.height()))
        if bounded.width() < 2 or bounded.height() < 2:
            QMessageBox.information(parent, "材料库截图 OCR", "框选区域太小，请重新选择。")
            return None
        out_dir = self.storage.root / "assets" / "material_tool"
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        raw_path = out_dir / f"material_tool_{timestamp}_raw.png"
        crop_path = out_dir / f"material_tool_{timestamp}_crop.png"
        frame.save(str(raw_path))
        frame.copy(bounded).save(str(crop_path))
        result = self.ocr.recognize(crop_path, mode="text")
        text = (result.text or "").strip()
        if not text:
            QMessageBox.information(parent, "材料库截图 OCR", f"OCR 未识别到文字，已保存裁剪图：{self.storage.rel(crop_path)}")
        else:
            self.log(f"材料库 OCR：{text} ({result.backend}, {result.confidence:.2f})")
        return {
            "text": text,
            "raw_path": self.storage.rel(raw_path) or str(raw_path),
            "crop_path": self.storage.rel(crop_path) or str(crop_path),
            "bbox": json.dumps(bbox_from_rect(bounded), ensure_ascii=False),
        }

    def open_bug_reports(self) -> None:
        BugReportDialog(self.storage, self).exec()

    def open_runner(self) -> None:
        self.set_workspace_mode("runner")

    def recent_log_excerpt(self, limit: int = 80) -> str:
        return "\n".join(self._recent_log_lines[-limit:])

    def capture_bug_screenshot(self, title: str) -> Path | None:
        out_dir = self.storage.bug_reports_dir()
        safe_title = re.sub(r"[\\/:*?\"<>|\s]+", "_", title.strip()).strip("._") or "runtime"
        path = out_dir / f"{int(time.time() * 1000)}_{safe_title[:48]}.png"
        try:
            image = QImage.fromData(self.adb.screencap_png(), "PNG")
            if not image.isNull():
                image.save(str(path))
                return path
        except Exception:
            pass
        try:
            if self.current_frame is not None and not self.current_frame.isNull():
                self.current_frame.save(str(path))
                return path
        except Exception:
            pass
        return None

    def record_bug_report(
        self,
        title: str,
        *,
        kind: str = "runtime",
        step: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        cooldown_key: str | None = None,
        cooldown_seconds: float = 25.0,
    ) -> str | None:
        key = cooldown_key or f"{kind}:{step.get('id') if step else ''}:{title}"
        now = time.monotonic()
        previous = self._bug_report_cooldowns.get(key)
        if previous is not None and now - previous < cooldown_seconds:
            return None
        self._bug_report_cooldowns[key] = now
        screenshot = self.capture_bug_screenshot(title)
        report_id = self.storage.add_bug_report(
            title=title,
            kind=kind,
            script_name=str(self.flow.get("script_name") or ""),
            step_id=str(step.get("id") or "") if step else "",
            step_name=str(step.get("name") or "") if step else "",
            step_type=str(step.get("type") or "") if step else "",
            log_excerpt=self.recent_log_excerpt(),
            screenshot_path=screenshot,
            metadata=metadata or {},
        )
        self.log(f"已加入 Bug 待修复：{report_id} - {title}")
        self._runtime_error_active = True
        self.emit_runtime_status("error", detail=title)
        if not self._runtime_alert_reported:
            self._runtime_alert_reported = True
            self.runtime_error_alert.emit({"title": title, "report_id": report_id, "kind": kind})
        return report_id

    def handle_runtime_failure(self, message: str) -> None:
        self.log(message)
        self._runtime_error_active = True
        self.emit_runtime_status("error", detail=message)
        self.record_bug_report(message, kind="runtime_exception", cooldown_key=f"runtime:{message}")

    def emit_loop_stats(
        self,
        *,
        current_attempt: int,
        current_completed: int,
        target: int | None,
        active_round: int | None = None,
    ) -> None:
        script_name = str(self.flow.get("script_name") or "")
        history = self.storage.script_loop_stats(script_name) if script_name else {}
        self.loop_stats_changed.emit(
            {
                "script_name": script_name,
                "current_attempt": current_attempt,
                "current_completed": current_completed,
                "target": target,
                "active_round": active_round,
                "history_completed": int(history.get("loop_completed_count") or 0),
                "history_failed": int(history.get("loop_failed_count") or 0),
                "last_duration_seconds": float(history.get("last_duration_seconds") or 0.0),
                "last_duration_success": bool(history.get("last_duration_success")),
                "avg_success_duration_seconds": float(history.get("avg_success_duration_seconds") or 0.0),
                "best_success_duration_seconds": float(history.get("best_success_duration_seconds") or 0.0),
                "worst_success_duration_seconds": float(history.get("worst_success_duration_seconds") or 0.0),
                "timed_success_count": int(history.get("timed_success_count") or 0),
            }
        )

    def log(self, message: str) -> None:
        text = str(message)
        if not self.is_main_thread():
            self._remember_log_line(self.format_log_line(text))
            self.log_message.emit(text)
            return
        self._queue_log_line(text)

    def format_log_line(self, message: str) -> str:
        return f"[{time.strftime('%H:%M:%S')}] {message}"

    def _remember_log_line(self, line: str) -> None:
        self._recent_log_lines.append(line)
        if len(self._recent_log_lines) > 220:
            del self._recent_log_lines[:-220]

    def _queue_log_line(self, message: str) -> None:
        line = self.format_log_line(message)
        self._remember_log_line(line)
        self._pending_log_lines.append(line)
        if not self._log_flush_scheduled:
            self._log_flush_scheduled = True
            QTimer.singleShot(80, self.flush_logs)

    def append_result(self, message: str) -> None:
        if not self.is_main_thread():
            self.result_message.emit(str(message))
            return
        self._append_result_text(str(message))

    def _append_result_text(self, message: str) -> None:
        self.result_view.appendPlainText(message)

    def flush_logs(self) -> None:
        if not self._pending_log_lines:
            self._log_flush_scheduled = False
            return
        lines = self._pending_log_lines
        self._pending_log_lines = []
        self._log_flush_scheduled = False
        text = "\n".join(lines)
        self.log_view.appendPlainText(text)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        if self.log_sink:
            for line in lines:
                self.log_sink(line)
        for line in lines:
            self.log_line_ready.emit(line)
