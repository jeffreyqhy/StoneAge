from __future__ import annotations

from pathlib import Path
from typing import Any


def cv2_imread(path: str | Path, flags: int) -> Any | None:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        raise

    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data:
        return None

    buffer = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(buffer, flags)
