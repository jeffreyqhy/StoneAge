from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from stoneage_studio.image_io import cv2_imread


class ImageIOTests(unittest.TestCase):
    def test_cv2_imread_supports_unicode_paths(self) -> None:
        try:
            import cv2  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"OpenCV unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            image_dir = Path(tmp) / "中文素材"
            image_dir.mkdir()
            image_path = image_dir / "模板_定位1.png"
            Image.new("RGB", (4, 3), (10, 20, 30)).save(image_path)

            image = cv2_imread(image_path, cv2.IMREAD_COLOR)

        self.assertIsNotNone(image)
        self.assertEqual(tuple(image.shape[:2]), (3, 4))

    def test_cv2_imread_missing_file_returns_none(self) -> None:
        try:
            import cv2  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"OpenCV unavailable: {exc}")

        image = cv2_imread("不存在的素材.png", cv2.IMREAD_COLOR)

        self.assertIsNone(image)


if __name__ == "__main__":
    unittest.main()
