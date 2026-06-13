from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TouchDeviceInfo:
    path: str
    name: str = ""
    max_x: int = 0
    max_y: int = 0
    orientation: int = 0


def parse_touch_device(getevent_lp: str, dumpsys_input: str = "") -> TouchDeviceInfo | None:
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in str(getevent_lp or "").splitlines():
        device_match = re.match(r"add device \d+: (?P<path>/dev/input/event\d+)", line.strip())
        if device_match:
            if current:
                devices.append(current)
            current = {"path": device_match.group("path"), "name": "", "max_x": 0, "max_y": 0, "direct": False}
            continue
        if current is None:
            continue
        name_match = re.search(r'name:\s+"([^"]+)"', line)
        if name_match:
            current["name"] = name_match.group(1)
        x_match = re.search(r"ABS_MT_POSITION_X\s*:.*max\s+(-?\d+)", line)
        if x_match:
            current["max_x"] = int(x_match.group(1))
        y_match = re.search(r"ABS_MT_POSITION_Y\s*:.*max\s+(-?\d+)", line)
        if y_match:
            current["max_y"] = int(y_match.group(1))
        if "INPUT_PROP_DIRECT" in line:
            current["direct"] = True
    if current:
        devices.append(current)

    orientation = parse_surface_orientation(dumpsys_input)
    for item in devices:
        if item.get("direct") and item.get("max_x") and item.get("max_y"):
            return TouchDeviceInfo(
                path=str(item["path"]),
                name=str(item.get("name") or ""),
                max_x=int(item["max_x"]),
                max_y=int(item["max_y"]),
                orientation=orientation,
            )
    for item in devices:
        if item.get("max_x") and item.get("max_y"):
            return TouchDeviceInfo(
                path=str(item["path"]),
                name=str(item.get("name") or ""),
                max_x=int(item["max_x"]),
                max_y=int(item["max_y"]),
                orientation=orientation,
            )
    return None


def parse_surface_orientation(dumpsys_input: str) -> int:
    match = re.search(r"SurfaceOrientation:\s*(\d+)", str(dumpsys_input or ""))
    if match:
        return int(match.group(1))
    match = re.search(r"orientation:\s*(\d+)", str(dumpsys_input or ""))
    return int(match.group(1)) if match else 0


def hex_event_value(value: str) -> int:
    text = str(value or "").strip()
    if text.upper() == "DOWN":
        return 1
    if text.upper() == "UP":
        return 0
    if re.fullmatch(r"f{8,}", text.lower()):
        return -1
    try:
        return int(text, 16)
    except ValueError:
        return int(text)


def parse_getevent_line(line: str) -> tuple[str, int] | None:
    text = str(line or "")
    if "ABS_MT_POSITION_X" in text:
        return "x", hex_event_value(text.split()[-1])
    if "ABS_MT_POSITION_Y" in text:
        return "y", hex_event_value(text.split()[-1])
    if "ABS_MT_TRACKING_ID" in text:
        return "tracking_id", hex_event_value(text.split()[-1])
    if "BTN_TOUCH" in text:
        return "touch", hex_event_value(text.split()[-1])
    if "SYN_REPORT" in text:
        return "syn", 0
    return None


def map_raw_point(
    raw_x: int,
    raw_y: int,
    *,
    device: TouchDeviceInfo,
    screen_size: tuple[int, int],
) -> list[int]:
    screen_w, screen_h = int(screen_size[0]), int(screen_size[1])
    max_x = max(1, int(device.max_x))
    max_y = max(1, int(device.max_y))
    orientation = int(device.orientation) % 4

    if orientation == 1:
        x = raw_y / max_y * (screen_w - 1)
        y = (max_x - raw_x) / max_x * (screen_h - 1)
    elif orientation == 3:
        x = (max_y - raw_y) / max_y * (screen_w - 1)
        y = raw_x / max_x * (screen_h - 1)
    elif orientation == 2:
        x = (max_x - raw_x) / max_x * (screen_w - 1)
        y = (max_y - raw_y) / max_y * (screen_h - 1)
    else:
        # MuMu sometimes reports portrait raw axes while the logical frame is
        # landscape. If the axes look swapped, map them without rotation first;
        # the raw coordinates are still kept in the recording for correction.
        if max_x <= screen_h + 8 and max_y >= screen_w - 8:
            x = raw_y / max_y * (screen_w - 1)
            y = raw_x / max_x * (screen_h - 1)
        else:
            x = raw_x / max_x * (screen_w - 1)
            y = raw_y / max_y * (screen_h - 1)
    return [
        max(0, min(screen_w - 1, int(round(x)))),
        max(0, min(screen_h - 1, int(round(y)))),
    ]


def classify_operation(points: list[list[int]], duration_seconds: float) -> str:
    if len(points) < 2:
        return "tap"
    start = points[0]
    end = points[-1]
    distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    if distance >= 28 or duration_seconds >= 0.35:
        return "swipe"
    return "tap"
