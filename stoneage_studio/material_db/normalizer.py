from __future__ import annotations

import re
import unicodedata

from .models import ParsedItemText


_SPACE_RE = re.compile(r"\s+")
_TRAILING_QUANTITY_RE = re.compile(
    r"^(?P<name>.+?)(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>个|颗|件|枚|张|块|份|瓶|次|片|套)?$"
)
_LEADING_QUANTITY_RE = re.compile(
    r"^(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>个|颗|件|枚|张|块|份|瓶|次|片|套)?\s*(?P<name>.+)$"
)
_MULTIPLY_QUANTITY_RE = re.compile(r"^(?P<name>.+?)\s*[xX*×]\s*(?P<qty>\d+(?:\.\d+)?)$")


def normalize_item_name(value: str | None) -> str:
    """Normalize user-entered StoneAge item text without inventing a new name."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("，", ",").replace("（", "(").replace("）", ")")
    text = text.replace("\u3000", " ")
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def _quantity_value(value: str) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else number


def parse_item_quantity(value: str | None) -> ParsedItemText:
    raw = str(value or "").strip()
    normalized = normalize_item_name(raw)
    if not normalized:
        return ParsedItemText(raw_text=raw, item_name="", parsed_quantity=None, confidence=0.0)

    match = _LEADING_QUANTITY_RE.match(normalized)
    if match:
        name = normalize_item_name(match.group("name"))
        if name:
            return ParsedItemText(
                raw_text=raw,
                item_name=name,
                parsed_quantity=_quantity_value(match.group("qty")),
                confidence=0.92,
            )

    match = _MULTIPLY_QUANTITY_RE.match(normalized)
    if match:
        name = normalize_item_name(match.group("name"))
        if name:
            return ParsedItemText(
                raw_text=raw,
                item_name=name,
                parsed_quantity=_quantity_value(match.group("qty")),
                confidence=0.9,
            )

    match = _TRAILING_QUANTITY_RE.match(normalized)
    if match:
        name = normalize_item_name(match.group("name"))
        # Keep mixed names such as "满石79玛蕾菲亚" intact; this branch only
        # fires when the whole cell ends in a numeric quantity.
        if name and not name[-1:].isdigit():
            return ParsedItemText(
                raw_text=raw,
                item_name=name,
                parsed_quantity=_quantity_value(match.group("qty")),
                confidence=0.88,
            )

    return ParsedItemText(raw_text=raw, item_name=normalized, parsed_quantity=None, confidence=0.72)
