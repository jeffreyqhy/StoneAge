from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class OCRResult:
    text: str
    confidence: float
    backend: str
    available: bool = True
    raw: Any = None
    error: str | None = None


@dataclass
class OCRRegion:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]
    backend: str
    raw: Any = None


class OCREngine:
    def __init__(self) -> None:
        self._paddle: Any = None
        self._easyocr: Any = None
        self._rapidocr: Any = None

    def recognize(self, image_path: str | Path, mode: str = "text") -> OCRResult:
        path = Path(image_path)
        strict_text = mode in {"question", "option"}
        recognizers = [
            self._recognize_rapidocr,
            self._recognize_paddle,
            self._recognize_easyocr,
        ]
        if not strict_text:
            recognizers.extend([self._recognize_tesseract_cli, self._recognize_tesseract])
        errors: list[str] = []
        for recognizer in recognizers:
            result = recognizer(path, mode)
            if result.available and result.text:
                return result
            if result.available and strict_text:
                return result
            if result.error:
                errors.append(f"{result.backend}: {result.error}")
        return OCRResult(
            text="",
            confidence=0.0,
            backend="none",
            available=False,
            error="; ".join(errors) or "未安装 OCR 后端。请安装 rapidocr_onnxruntime。",
        )

    def recognize_game_coord(self, image_path: str | Path, previous: tuple[int, int] | list[int] | None = None) -> OCRResult:
        path = Path(image_path)
        candidates: list[str] = []
        executable = shutil.which("tesseract")
        if executable:
            candidates.extend(self._recognize_game_coord_tesseract_variants(path, executable))

        fallback = self.recognize(path, mode="text")
        if fallback.text:
            candidates.append(fallback.text)

        parsed: list[tuple[tuple[int, int], float]] = []
        for text in candidates:
            coord = parse_coord_candidate(text, previous=previous)
            if coord:
                parsed.append((coord, coord_candidate_score(text)))
        if parsed:
            scores: dict[tuple[int, int], float] = {}
            for coord, score in parsed:
                scores[coord] = scores.get(coord, 0.0) + score
            coord, score = max(scores.items(), key=lambda item: item[1])
            return OCRResult(
                text=f"{coord[0]},{coord[1]}",
                confidence=min(0.99, 0.70 + score * 0.08),
                backend="coord-ocr",
                raw=candidates,
            )
        return OCRResult(
            text=fallback.text,
            confidence=fallback.confidence,
            backend=fallback.backend,
            available=fallback.available,
            raw={"fallback": fallback.raw, "candidates": candidates},
            error=fallback.error,
        )

    def _recognize_game_coord_tesseract_variants(self, path: Path, executable: str) -> list[str]:
        try:
            from PIL import Image, ImageOps  # type: ignore
        except Exception:
            return []

        try:
            image = Image.open(path).convert("RGB")
        except Exception:
            return []

        outputs: list[str] = []
        temp_paths: list[Path] = []
        try:
            for scale in (4, 6, 8):
                resized = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
                gray = ImageOps.grayscale(resized)
                for threshold in (140, 160, 180, 200, 220):
                    variant = gray.point(lambda value, t=threshold: 255 if value > t else 0)
                    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    handle.close()
                    temp = Path(handle.name)
                    variant.save(temp)
                    temp_paths.append(temp)
                    for psm in ("7", "8", "13"):
                        try:
                            result = subprocess.run(
                                [
                                    executable,
                                    str(temp),
                                    "stdout",
                                    "-l",
                                    "eng",
                                    "--psm",
                                    psm,
                                    "-c",
                                    "tessedit_char_whitelist=0123456789,()",
                                ],
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                        except Exception:
                            continue
                        text = result.stdout.strip()
                        if text:
                            outputs.append(text)
        finally:
            for temp in temp_paths:
                temp.unlink(missing_ok=True)
        return outputs

    def detect_regions(self, image_path: str | Path, mode: str = "text") -> list[OCRRegion]:
        path = Path(image_path)
        regions = self._detect_regions_rapidocr(path, mode)
        if regions:
            return regions
        result = self.recognize(path, mode=mode)
        if not result.text:
            return []
        try:
            from PIL import Image  # type: ignore

            with Image.open(path) as image:
                width, height = image.size
        except Exception:
            width, height = 0, 0
        return [
            OCRRegion(
                text=result.text,
                confidence=result.confidence,
                bbox=(0, 0, int(width), int(height)),
                backend=result.backend,
                raw=result.raw,
            )
        ]

    def _detect_regions_rapidocr(self, path: Path, mode: str) -> list[OCRRegion]:
        try:
            if self._rapidocr is None:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                self._rapidocr = RapidOCR()
            raw, _ = self._rapidocr(str(path))
        except Exception:
            return []

        regions: list[OCRRegion] = []
        for item in raw or []:
            if len(item) < 3:
                continue
            box, text, confidence = item[0], str(item[1]).strip(), float(item[2])
            if not text:
                continue
            if mode == "digit":
                text = "".join(ch for ch in text if ch.isdigit() or ch == "/")
                if not text:
                    continue
            xs = [int(point[0]) for point in box or [] if len(point) >= 2]
            ys = [int(point[1]) for point in box or [] if len(point) >= 2]
            if not xs or not ys:
                continue
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            regions.append(
                OCRRegion(
                    text=text,
                    confidence=confidence,
                    bbox=(x1, y1, max(1, x2 - x1), max(1, y2 - y1)),
                    backend="rapidocr",
                    raw=item,
                )
            )
        return regions

    def _recognize_paddle(self, path: Path, mode: str) -> OCRResult:
        try:
            if self._paddle is None:
                from paddleocr import PaddleOCR  # type: ignore

                self._paddle = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            raw = self._paddle.ocr(str(path), cls=True)
        except Exception as exc:  # noqa: BLE001 - optional backend probing
            return OCRResult("", 0.0, "paddleocr", available=False, error=str(exc))

        lines = []
        confidences: list[float] = []
        for page in raw or []:
            for item in page or []:
                if len(item) >= 2:
                    text, confidence = item[1][0], float(item[1][1])
                    lines.append(str(text))
                    confidences.append(confidence)
        text = " ".join(lines).strip()
        if mode == "digit":
            text = "".join(ch for ch in text if ch.isdigit())
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OCRResult(text, confidence, "paddleocr", raw=raw)

    def _recognize_easyocr(self, path: Path, mode: str) -> OCRResult:
        try:
            if self._easyocr is None:
                import easyocr  # type: ignore

                self._easyocr = easyocr.Reader(["ch_sim", "en"], gpu=False)
            raw = self._easyocr.readtext(str(path), detail=1)
        except Exception as exc:  # noqa: BLE001 - optional backend probing
            return OCRResult("", 0.0, "easyocr", available=False, error=str(exc))

        lines = []
        confidences: list[float] = []
        for item in raw or []:
            if len(item) >= 3:
                lines.append(str(item[1]))
                confidences.append(float(item[2]))
        text = " ".join(lines).strip()
        if mode == "digit":
            text = "".join(ch for ch in text if ch.isdigit())
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OCRResult(text, confidence, "easyocr", raw=raw)

    def _recognize_rapidocr(self, path: Path, mode: str) -> OCRResult:
        try:
            if self._rapidocr is None:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                self._rapidocr = RapidOCR()
            raw, _ = self._rapidocr(str(path))
        except Exception as exc:  # noqa: BLE001 - optional backend probing
            return OCRResult("", 0.0, "rapidocr", available=False, error=str(exc))

        lines: list[str] = []
        confidences: list[float] = []
        for item in raw or []:
            if len(item) >= 3:
                lines.append(str(item[1]))
                confidences.append(float(item[2]))
        text = " ".join(lines).strip()
        if mode == "digit":
            text = "".join(ch for ch in text if ch.isdigit() or ch == "/")
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OCRResult(text, confidence, "rapidocr", raw=raw)

    def _recognize_tesseract(self, path: Path, mode: str) -> OCRResult:
        try:
            from PIL import Image  # type: ignore
            import pytesseract  # type: ignore

            image = Image.open(path)
            config = "--psm 7 -c tessedit_char_whitelist=0123456789" if mode == "digit" else "--psm 6"
            text = pytesseract.image_to_string(image, lang="chi_sim+eng", config=config)
        except Exception as exc:  # noqa: BLE001 - optional backend probing
            return OCRResult("", 0.0, "pytesseract", available=False, error=str(exc))

        text = text.strip()
        if mode == "digit":
            text = "".join(ch for ch in text if ch.isdigit())
        confidence = 0.75 if text else 0.0
        return OCRResult(text, confidence, "pytesseract")

    def _recognize_tesseract_cli(self, path: Path, mode: str) -> OCRResult:
        executable = shutil.which("tesseract")
        if not executable:
            return OCRResult("", 0.0, "tesseract-cli", available=False, error="tesseract 命令不存在")

        source_path = path
        temp_path: Path | None = None
        try:
            temp_path = self._preprocess_for_tesseract(path, mode)
            if temp_path is not None:
                source_path = temp_path

            config = ["--psm", "7"] if mode == "digit" else ["--psm", "6"]
            if mode == "digit":
                config.extend(["-c", "tessedit_char_whitelist=0123456789/"])
            result = subprocess.run(
                [executable, str(source_path), "stdout", "-l", "chi_sim+eng", *config],
                check=True,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except Exception as exc:  # noqa: BLE001 - optional backend probing
            return OCRResult("", 0.0, "tesseract-cli", available=False, error=str(exc))
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        text = result.stdout.strip()
        if mode == "digit":
            text = "".join(ch for ch in text if ch.isdigit() or ch == "/")
        confidence = 0.72 if text else 0.0
        return OCRResult(text, confidence, "tesseract-cli", raw=result.stdout)

    def _preprocess_for_tesseract(self, path: Path, mode: str) -> Path | None:
        try:
            import cv2  # type: ignore
        except Exception:
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            return None
        scale = 4 if mode == "digit" else 3
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        if mode == "digit":
            processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        else:
            processed = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                7,
            )
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.close()
        temp_path = Path(handle.name)
        cv2.imwrite(str(temp_path), processed)
        return temp_path


def _coord_component_candidates(value: int, previous_value: int | None) -> list[int]:
    values = [int(value)]
    if previous_value is None:
        return values
    previous_value = int(previous_value)
    if 10 <= int(value) <= 19 and 0 <= previous_value <= 9:
        ones = int(value) % 10
        if abs(ones - previous_value) <= 6:
            values.append(ones)
    if not 0 <= int(value) <= 9:
        return list(dict.fromkeys(values))
    bases = {(previous_value // 10) * 10}
    if previous_value < 10:
        bases.add(10)
    for base in bases:
        candidate = int(base) + int(value)
        if candidate == value or candidate > 99:
            continue
        # Common OCR miss: (10,10) becomes 0,10, or 18 becomes 8.
        # Only trust the repair when it stays close to the last reliable coord.
        if abs(candidate - previous_value) <= 6:
            values.append(candidate)
    return list(dict.fromkeys(values))


def _choose_coord_candidate(
    candidates: list[tuple[int, int]],
    previous: tuple[int, int] | list[int] | None = None,
) -> tuple[int, int] | None:
    if not candidates:
        return None
    deduped = list(dict.fromkeys(candidates))
    if previous:
        px, py = int(previous[0]), int(previous[1])
        return min(deduped, key=lambda coord: abs(coord[0] - px) + abs(coord[1] - py))
    return deduped[0]


def parse_coord_candidate(text: str, previous: tuple[int, int] | list[int] | None = None) -> tuple[int, int] | None:
    cleaned = (
        text.replace("，", ",")
        .replace("（", "(")
        .replace("）", ")")
        .replace("O", "0")
        .replace("o", "0")
        .replace("G", "6")
        .replace("g", "6")
    )
    match = re.search(r"\(?(\d{1,2}),(\d{1,2})\)?", cleaned)
    if match:
        x = int(match.group(1))
        y = int(match.group(2))
        candidates = [(x, y)]
        if previous:
            x_candidates = _coord_component_candidates(x, int(previous[0]))
            y_candidates = _coord_component_candidates(y, int(previous[1]))
            candidates = [(cx, cy) for cx in x_candidates for cy in y_candidates]
        return _choose_coord_candidate(candidates, previous)
    if re.search(r"[A-NP-Za-np-z\u4e00-\u9fff]", cleaned):
        return None
    candidates: list[tuple[int, int]] = []
    groups = re.findall(r"\d{1,2}", cleaned)
    if len(groups) == 2:
        candidates.append((int(groups[0]), int(groups[1])))
    elif len(groups) == 3:
        left_join = groups[0] + groups[1]
        right_join = groups[1] + groups[2]
        if len(left_join) <= 2:
            candidates.append((int(left_join), int(groups[2])))
        if len(right_join) <= 2:
            candidates.append((int(groups[0]), int(right_join)))

    digits = re.sub(r"\D", "", cleaned)
    if len(digits) == 2:
        candidates.append((int(digits[0]), int(digits[1])))
    elif len(digits) == 3:
        candidates.append((int(digits[0]), int(digits[1:])))
        candidates.append((int(digits[:2]), int(digits[2])))
    elif len(digits) == 4:
        candidates.append((int(digits[:2]), int(digits[2:])))
    if not candidates:
        return None

    return _choose_coord_candidate(candidates, previous)


def coord_candidate_score(text: str) -> float:
    cleaned = text.replace("，", ",").replace("（", "(").replace("）", ")")
    if re.search(r"\(?\d{1,2},\d{1,2}\)?", cleaned):
        return 4.0
    if re.search(r"[A-NP-Za-np-z\u4e00-\u9fff]", cleaned):
        return 0.1
    digits = re.sub(r"\D", "", cleaned)
    if 2 <= len(digits) <= 4:
        return 1.0
    return 0.2
