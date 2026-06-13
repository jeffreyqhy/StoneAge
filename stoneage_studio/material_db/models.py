from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedItemText:
    raw_text: str
    item_name: str
    parsed_quantity: float | None = None
    confidence: float = 1.0


@dataclass
class ImportSummary:
    file_path: str
    file_name: str
    sheet_count: int = 0
    source_count: int = 0
    record_count: int = 0
    skipped_count: int = 0
    warnings: list[str] = field(default_factory=list)
    import_batch_id: int | None = None

    def as_text(self) -> str:
        lines = [
            f"文件：{self.file_name}",
            f"工作表：{self.sheet_count}",
            f"出处列：{self.source_count}",
            f"导入记录：{self.record_count}",
            f"跳过记录：{self.skipped_count}",
        ]
        if self.import_batch_id is not None:
            lines.append(f"批次：{self.import_batch_id}")
        if self.warnings:
            lines.append("提示：")
            lines.extend(f"- {item}" for item in self.warnings)
        return "\n".join(lines)
