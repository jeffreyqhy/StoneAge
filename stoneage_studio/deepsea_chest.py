from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET


DEEPSEA_6F_CHEST_KEY = "deepsea_6f"

DEEPSEA_6F_CHEST_ITEMS = [
    "焰狱魔兽自选",
    "战神斧碎片",
    "暴躁蛮牛自选",
    "技能碎片",
    "魔神石",
    "魔法技能碎片",
    "深海鱼鳞",
    "深海贝壳",
    "深海之泪",
    "深海泥土",
    "战神首饰碎片",
    "暴龙玩具3",
    "暴龙玩具4",
    "10点金币",
    "剧毒宝石碎片",
    "沉默勋章3",
    "粉红暴自选",
    "粉红暴骑证",
    "3D人龙自选",
]


@dataclass(frozen=True)
class ChestItemStat:
    item_name: str
    quantity: int
    rate: float


def normalize_chest_item_name(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def build_item_stats(totals: dict[str, int], items: list[str] | None = None) -> list[ChestItemStat]:
    ordered_items = list(items or DEEPSEA_6F_CHEST_ITEMS)
    total_quantity = sum(max(0, int(value or 0)) for value in totals.values())
    stats: list[ChestItemStat] = []
    for item in ordered_items:
        quantity = max(0, int(totals.get(item, 0) or 0))
        rate = quantity / total_quantity if total_quantity > 0 else 0.0
        stats.append(ChestItemStat(item_name=item, quantity=quantity, rate=rate))
    return stats


def _column_index(column_letters: str) -> int:
    value = 0
    for char in column_letters.upper():
        if "A" <= char <= "Z":
            value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _cell_position(ref: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([A-Z]+)(\d+)", ref.upper())
    if not match:
        return None
    return int(match.group(2)), _column_index(match.group(1))


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: list[str] = []
    for item in root.findall("x:si", ns):
        parts = [node.text or "" for node in item.findall(".//x:t", ns)]
        values.append("".join(parts))
    return values


def _sheet_path_for_name(archive: zipfile.ZipFile, sheet_name: str) -> str:
    ns = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("rel:Relationship", ns)}
    for sheet in workbook.findall(".//x:sheet", ns):
        if sheet.attrib.get("name") != sheet_name:
            continue
        rel_id = sheet.attrib.get(f"{{{ns['r']}}}id")
        target = rel_targets.get(str(rel_id or ""))
        if not target:
            break
        return "xl/" + target.lstrip("/")
    raise ValueError(f"工作簿里找不到工作表：{sheet_name}")


def _cell_text(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        value_node = cell.find("x:v", ns)
        if value_node is None or value_node.text is None:
            return ""
        try:
            return shared_strings[int(value_node.text)]
        except (IndexError, ValueError):
            return ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", ns))
    value_node = cell.find("x:v", ns)
    return "" if value_node is None or value_node.text is None else str(value_node.text)


def _excel_date_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%Y-%m-%d")
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        return text.replace("/", "-")
    try:
        serial = float(text)
    except ValueError:
        return text
    if 1 <= serial <= 60000:
        return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime("%Y-%m-%d")
    return text


def read_deepsea_matrix_excel_records(path: str | Path) -> list[dict[str, str | int]]:
    """Read records from the horizontal Excel template without requiring openpyxl."""
    workbook_path = Path(path)
    if not workbook_path.exists():
        raise FileNotFoundError(str(workbook_path))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(workbook_path) as archive:
        shared = _shared_strings(archive)
        sheet_path = _sheet_path_for_name(archive, "深海6楼_快速录入")
        root = ET.fromstring(archive.read(sheet_path))
        values: dict[tuple[int, int], str] = {}
        max_col = 0
        for cell in root.findall(".//x:c", ns):
            ref = cell.attrib.get("r", "")
            position = _cell_position(ref)
            if position is None:
                continue
            row, col = position
            values[(row, col)] = _cell_text(cell, shared, ns)
            max_col = max(max_col, col)

    records: list[dict[str, str | int]] = []
    for row in range(14, 33):
        item = normalize_chest_item_name(values.get((row, 1), ""))
        if item not in DEEPSEA_6F_CHEST_ITEMS:
            continue
        for col in range(3, max_col + 1):
            raw_quantity = str(values.get((row, col), "")).strip()
            if not raw_quantity:
                continue
            try:
                quantity = int(float(raw_quantity))
            except ValueError:
                continue
            if quantity <= 0:
                continue
            record_number = str(values.get((8, col), "")).strip() or str(col - 2)
            note = str(values.get((10, col), "")).strip() or f"横向Excel列{record_number}"
            records.append(
                {
                    "record_date": _excel_date_text(values.get((9, col), "")),
                    "item_name": item,
                    "quantity": quantity,
                    "note": note,
                }
            )
    return records
