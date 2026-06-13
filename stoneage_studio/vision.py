from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MatchResult:
    found: bool
    confidence: float
    bbox: tuple[int, int, int, int] | None = None
    center: tuple[int, int] | None = None
    error: str | None = None


def match_template_qimage(
    frame: Any,
    template_path: str | Path,
    threshold: float = 0.85,
    search_bbox: Any = None,
) -> MatchResult:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QRect
    except Exception as exc:  # noqa: BLE001 - optional dependency probing
        return MatchResult(False, 0.0, error=f"缺少图像识别依赖：{short_dependency_error(exc)}")

    if frame is None or frame.isNull():
        return MatchResult(False, 0.0, error="当前没有游戏画面")

    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        return MatchResult(False, 0.0, error=f"模板读取失败：{template_path}")

    origin_x = 0
    origin_y = 0
    source_image = frame
    if search_bbox is not None:
        try:
            x, y, width, height = [int(value) for value in list(search_bbox)[:4]]
        except (TypeError, ValueError):
            return MatchResult(False, 0.0, error=f"搜索区域无效：{search_bbox}")
        full_rect = QRect(0, 0, int(frame.width()), int(frame.height()))
        rect = QRect(x, y, width, height).intersected(full_rect)
        if rect.width() <= 0 or rect.height() <= 0:
            return MatchResult(False, 0.0, error=f"搜索区域超出画面：{search_bbox}")
        origin_x = int(rect.x())
        origin_y = int(rect.y())
        source_image = frame.copy(rect)

    image = source_image.convertToFormat(QImage.Format.Format_RGB888)
    width = image.width()
    height = image.height()
    bytes_per_line = image.bytesPerLine()
    buffer = image.bits()
    arr = np.frombuffer(buffer, dtype=np.uint8).reshape((height, bytes_per_line))
    arr = arr[:, : width * 3].reshape((height, width, 3))
    screen = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    if screen.shape[0] < template.shape[0] or screen.shape[1] < template.shape[1]:
        return MatchResult(False, 0.0, error="模板尺寸大于当前画面")

    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_value, _, max_location = cv2.minMaxLoc(result)
    confidence = float(max_value)
    found = confidence >= float(threshold)
    x, y = max_location
    h, w = template.shape[:2]
    bbox = (int(origin_x + x), int(origin_y + y), int(w), int(h))
    center = (int(origin_x + x + w / 2), int(origin_y + y + h / 2))
    return MatchResult(found, confidence, bbox=bbox, center=center)


def short_dependency_error(exc: Exception) -> str:
    message = str(exc)
    if "incompatible architecture" in message or "need 'x86_64'" in message or "need 'arm64'" in message:
        return "Python/NumPy 架构不一致。请完全退出 Studio，再用新版启动器重新打开。"
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "")
    return first_line[:240] or exc.__class__.__name__
