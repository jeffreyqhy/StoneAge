from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .database import MaterialDatabase
from .models import ImportSummary
from .normalizer import normalize_item_name, parse_item_quantity


_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_RE = re.compile(r"([A-Z]+)(\d+)")


def import_excel_sources(
    db: MaterialDatabase,
    path: str | Path,
    *,
    mode: str = "merge",
    notes: str = "",
) -> ImportSummary:
    """Import source columns from an .xlsx file using only the standard library."""
    file_path = Path(path)
    if file_path.suffix.lower() != ".xlsx":
        raise ValueError("只支持 .xlsx 文件")
    if mode not in {"merge", "replace", "append"}:
        raise ValueError("导入模式必须是 merge / replace / append")
    if mode == "replace":
        db.clear_excel_imports()

    summary = ImportSummary(file_path=str(file_path), file_name=file_path.name)
    batch_id = db.create_import_batch(file_path, notes=notes or f"Excel导入：{mode}")
    summary.import_batch_id = batch_id

    with zipfile.ZipFile(file_path) as archive:
        shared_strings = _load_shared_strings(archive)
        sheets = _load_workbook_sheets(archive)
        summary.sheet_count = len(sheets)
        if not sheets:
            summary.warnings.append("Excel 中没有找到工作表。")
            return summary

        for sheet_name, sheet_path in sheets:
            try:
                rows = _load_sheet_rows(archive, sheet_path, shared_strings)
            except KeyError:
                summary.warnings.append(f"工作表文件缺失：{sheet_name} ({sheet_path})")
                continue
            if not rows:
                summary.warnings.append(f"工作表为空：{sheet_name}")
                continue
            headers = {
                col_index: normalize_item_name(value)
                for col_index, value in rows.get(1, {}).items()
                if normalize_item_name(value)
            }
            if not headers:
                summary.warnings.append(f"第一行没有出处名称：{sheet_name}")
                continue
            summary.source_count += len(headers)
            for row_index in sorted(key for key in rows if key > 1):
                row = rows[row_index]
                for col_index, source_name in headers.items():
                    raw = normalize_item_name(row.get(col_index, ""))
                    if not raw:
                        continue
                    parsed = parse_item_quantity(raw)
                    if not parsed.item_name:
                        summary.skipped_count += 1
                        continue
                    inserted = db.add_source_item(
                        item_name=parsed.item_name,
                        raw_text=parsed.raw_text,
                        source_name=source_name,
                        parsed_quantity=parsed.parsed_quantity,
                        sheet_name=sheet_name,
                        row_index=row_index,
                        col_index=col_index,
                        import_batch_id=batch_id,
                        source_type="excel",
                        skip_duplicate=mode != "append",
                    )
                    if inserted is None:
                        summary.skipped_count += 1
                    else:
                        summary.record_count += 1
    return summary


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(xml)
    strings: list[str] = []
    for item in root.findall(f"{_NS_MAIN}si"):
        parts = [node.text or "" for node in item.findall(f".//{_NS_MAIN}t")]
        strings.append("".join(parts))
    return strings


def _load_workbook_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets: dict[str, str] = {}
    for rel in rels.findall(f"{_NS_PACKAGE_REL}Relationship"):
        rel_id = str(rel.attrib.get("Id") or "")
        target = str(rel.attrib.get("Target") or "")
        if not rel_id or not target:
            continue
        if target.startswith("/"):
            target_path = target.lstrip("/")
        else:
            target_path = f"xl/{target}"
        targets[rel_id] = _normalize_zip_path(target_path)

    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{_NS_MAIN}sheet"):
        name = str(sheet.attrib.get("name") or "Sheet")
        rel_id = str(sheet.attrib.get(f"{_NS_REL}id") or "")
        target = targets.get(rel_id)
        if target:
            sheets.append((name, target))
    return sheets


def _load_sheet_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> dict[int, dict[int, str]]:
    root = ElementTree.fromstring(archive.read(sheet_path))
    rows: dict[int, dict[int, str]] = {}
    for cell in root.findall(f".//{_NS_MAIN}c"):
        ref = str(cell.attrib.get("r") or "")
        match = _CELL_RE.match(ref)
        if not match:
            continue
        col_index = _column_index(match.group(1))
        row_index = int(match.group(2))
        value = _cell_value(cell, shared_strings)
        if value == "":
            continue
        rows.setdefault(row_index, {})[col_index] = value
    return rows


def _cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = str(cell.attrib.get("t") or "")
    if cell_type == "inlineStr":
        parts = [node.text or "" for node in cell.findall(f".//{_NS_MAIN}t")]
        return normalize_item_name("".join(parts))
    value_node = cell.find(f"{_NS_MAIN}v")
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text
    if cell_type == "s":
        try:
            return normalize_item_name(shared_strings[int(value)])
        except (IndexError, ValueError):
            return ""
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return normalize_item_name(value)


def _column_index(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _normalize_zip_path(value: str) -> str:
    parts: list[str] = []
    for part in value.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)
