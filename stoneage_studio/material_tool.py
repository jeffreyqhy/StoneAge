from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .material_db import MaterialCalculator, MaterialDatabase, import_excel_sources
from .material_db.calculator import ceil_quantity
from .material_db.normalizer import normalize_item_name
from .material_db.price_calculator import net_after_trade_tax, required_trade_gross_for_net, trade_tax_amount


CaptureCallback = Callable[[QWidget, str], dict[str, str] | None]


class MaterialNameDelegate(QStyledItemDelegate):
    def __init__(self, names_callback: Callable[[], list[str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.names_callback = names_callback

    def createEditor(self, parent: QWidget, option: Any, index: Any) -> QWidget:  # noqa: N802
        editor = QLineEdit(parent)
        completer = QCompleter(self.names_callback(), editor)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        editor.setCompleter(completer)
        return editor


class MaterialToolDialog(QDialog):
    def __init__(
        self,
        workspace: str | Path,
        parent: QWidget | None = None,
        *,
        capture_callback: CaptureCallback | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("材料库 / 合成打造计算器 / 市场价值计算器")
        self.resize(1280, 820)
        self.db = MaterialDatabase(workspace)
        self.calculator = MaterialCalculator(self.db)
        self.capture_callback = capture_callback
        self.selected_material_id: int | None = None
        self.selected_recipe_id: int | None = None
        self.selected_upgrade_id: int | None = None
        self.selected_price_id: int | None = None
        self.last_upgrade_result: dict[str, Any] | None = None

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_query_tab(), "查询")
        self.tabs.addTab(self._build_recipe_tab(), "配方管理")
        self.tabs.addTab(self._build_upgrade_tab(), "升级管理")
        self.tabs.addTab(self._build_material_tab(), "材料管理")
        self.tabs.addTab(self._build_settings_tab(), "设置")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh_everything()

    def refresh_everything(self) -> None:
        self.refresh_query_ratio()
        self.refresh_material_table()
        self.refresh_material_source_table()
        self.refresh_recipe_table()
        self.refresh_upgrade_table()
        self.refresh_alias_table()
        self.settings_ratio.setValue(float(self.db.diamond_per_rmb()))
        if hasattr(self, "material_ratio"):
            self.material_ratio.setValue(float(self.db.diamond_per_rmb()))

    def item_names(self) -> list[str]:
        return self.db.all_item_names(limit=5000)

    def source_names(self) -> list[str]:
        return self.db.all_source_names(limit=5000)

    # Query tab
    def _build_query_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        controls = QGridLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("输入材料名")
        self.query_type = QComboBox()
        self.query_type.addItems(["查材料", "查副本/出处掉落", "查成品配方", "查装备升级"])
        self.query_target_qty = QSpinBox()
        self.query_target_qty.setRange(1, 99999)
        self.query_target_qty.setValue(1)
        self.query_from_level = QSpinBox()
        self.query_from_level.setRange(0, 999)
        self.query_from_level.setValue(12)
        self.query_to_level = QSpinBox()
        self.query_to_level.setRange(1, 1000)
        self.query_to_level.setValue(20)
        self.query_confidence = QComboBox()
        for label, value in (("90% 稳妥", 0.90), ("95% 稳妥", 0.95), ("99% 稳妥", 0.99)):
            self.query_confidence.addItem(label, value)
        self.query_confidence.setCurrentText("95% 稳妥")
        run = QPushButton("查询 / 计算")
        copy = QPushButton("复制结果")
        self.query_ratio_label = QLabel()
        controls.addWidget(QLabel("搜索"), 0, 0)
        controls.addWidget(self.query_edit, 0, 1, 1, 5)
        controls.addWidget(QLabel("类型"), 0, 6)
        controls.addWidget(self.query_type, 0, 7)
        controls.addWidget(QLabel("目标数量"), 1, 0)
        controls.addWidget(self.query_target_qty, 1, 1)
        controls.addWidget(QLabel("起始等级"), 1, 2)
        controls.addWidget(self.query_from_level, 1, 3)
        controls.addWidget(QLabel("目标等级"), 1, 4)
        controls.addWidget(self.query_to_level, 1, 5)
        controls.addWidget(QLabel("稳妥模式"), 1, 6)
        controls.addWidget(self.query_confidence, 1, 7)
        controls.addWidget(self.query_ratio_label, 2, 0, 1, 4)
        controls.addWidget(run, 2, 6)
        controls.addWidget(copy, 2, 7)
        layout.addLayout(controls)

        trade_box = QGroupBox("交易税计算")
        trade_layout = QGridLayout(trade_box)
        self.trade_target_net = QSpinBox()
        self.trade_target_net.setRange(0, 999999999)
        self.trade_target_net.setValue(10000)
        self.trade_gross = QSpinBox()
        self.trade_gross.setRange(0, 999999999)
        self.trade_gross.setValue(10000)
        self.trade_tax_rate = QDoubleSpinBox()
        self.trade_tax_rate.setRange(0, 99.999)
        self.trade_tax_rate.setDecimals(3)
        self.trade_tax_rate.setSuffix("%")
        self.trade_tax_rate.setValue(5)
        self.trade_required_label = QLabel()
        self.trade_required_detail = QLabel()
        self.trade_net_label = QLabel()
        self.trade_net_detail = QLabel()
        for label in (self.trade_required_label, self.trade_required_detail, self.trade_net_label, self.trade_net_detail):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        trade_layout.addWidget(QLabel("对方到账"), 0, 0)
        trade_layout.addWidget(self.trade_target_net, 0, 1)
        trade_layout.addWidget(QLabel("税率"), 0, 2)
        trade_layout.addWidget(self.trade_tax_rate, 0, 3)
        trade_layout.addWidget(QLabel("需要交易"), 0, 4)
        trade_layout.addWidget(self.trade_required_label, 0, 5)
        trade_layout.addWidget(self.trade_required_detail, 0, 6, 1, 2)
        trade_layout.addWidget(QLabel("实际交易"), 1, 0)
        trade_layout.addWidget(self.trade_gross, 1, 1)
        trade_layout.addWidget(QLabel("对方收到"), 1, 4)
        trade_layout.addWidget(self.trade_net_label, 1, 5)
        trade_layout.addWidget(self.trade_net_detail, 1, 6, 1, 2)
        layout.addWidget(trade_box)

        self.query_table = QTableWidget(0, 8)
        self.query_table.setHorizontalHeaderLabels(["材料/物品", "标准", "期望", "稳妥", "单价钻", "合计钻", "出处/来源", "备注"])
        self.query_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.query_table.horizontalHeader().setStretchLastSection(True)
        self.query_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.query_table, 1)
        self.query_result = QPlainTextEdit()
        self.query_result.setReadOnly(True)
        self.query_result.setPlaceholderText("查询结果会显示在这里，可一键复制为纯文本。")
        layout.addWidget(self.query_result, 1)
        run.clicked.connect(self.run_query)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.query_result.toPlainText()))
        self.query_edit.returnPressed.connect(self.run_query)
        self.query_type.currentTextChanged.connect(self.refresh_query_completer)
        self.trade_target_net.valueChanged.connect(lambda _value: self.update_trade_tax_calculator())
        self.trade_gross.valueChanged.connect(lambda _value: self.update_trade_tax_calculator())
        self.trade_tax_rate.valueChanged.connect(lambda _value: self.update_trade_tax_calculator())
        self.refresh_query_completer()
        self.update_trade_tax_calculator()
        return panel

    def refresh_query_ratio(self) -> None:
        if hasattr(self, "query_ratio_label"):
            self.query_ratio_label.setText(f"当前钻石比例：{self.db.diamond_per_rmb():g} 钻 = 1 RMB")

    def refresh_query_completer(self) -> None:
        if not hasattr(self, "query_edit"):
            return
        kind = self.query_type.currentText()
        names = self.source_names() if kind == "查副本/出处掉落" else self.item_names()
        placeholders = {
            "查材料": "输入材料名",
            "查副本/出处掉落": "输入副本/出处名",
            "查成品配方": "输入成品名",
            "查装备升级": "输入装备名",
        }
        self.query_edit.setPlaceholderText(placeholders.get(kind, "输入名称"))
        completer = QCompleter(names, self.query_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.query_edit.setCompleter(completer)

    def update_trade_tax_calculator(self) -> None:
        if not hasattr(self, "trade_tax_rate"):
            return
        tax_rate = float(self.trade_tax_rate.value()) / 100.0
        try:
            target_net = int(self.trade_target_net.value())
            required_gross = required_trade_gross_for_net(target_net, tax_rate)
            guaranteed_net = net_after_trade_tax(required_gross, tax_rate)
            required_tax = trade_tax_amount(required_gross, tax_rate)
            gross = int(self.trade_gross.value())
            net = net_after_trade_tax(gross, tax_rate)
            tax = trade_tax_amount(gross, tax_rate)
        except ValueError as exc:
            self.trade_required_label.setText("不可用")
            self.trade_required_detail.setText(str(exc))
            self.trade_net_label.setText("不可用")
            self.trade_net_detail.clear()
            return
        self.trade_required_label.setText(f"{_fmt_qty(required_gross)} 钻")
        self.trade_required_detail.setText(f"扣税 {_fmt_qty(required_tax)} 钻，预计到账 {_fmt_qty(guaranteed_net)} 钻")
        self.trade_net_label.setText(f"{_fmt_qty(net)} 钻")
        self.trade_net_detail.setText(f"扣税 {_fmt_qty(tax)} 钻")

    def run_query(self) -> None:
        query = self.query_edit.text().strip()
        kind = self.query_type.currentText()
        self.query_table.setRowCount(0)
        if not query:
            self.query_result.setPlainText("请输入要查询的名称。")
            return
        try:
            if kind == "查材料":
                rows = self.db.list_materials(query)
                self.show_material_query(rows)
            elif kind == "查副本/出处掉落":
                rows = self.db.list_source_drops(query)
                self.show_source_drop_query(rows, query)
            elif kind == "查成品配方":
                result = self.calculator.recipe_cost(
                    query,
                    target_quantity=int(self.query_target_qty.value()),
                    confidence=float(self.query_confidence.currentData()),
                )
                self.show_calculation_materials(result)
                self.query_result.setPlainText(result["text"])
            else:
                result = self.calculator.upgrade_cost(
                    query,
                    int(self.query_from_level.value()),
                    int(self.query_to_level.value()),
                    target_quantity=int(self.query_target_qty.value()),
                    confidence=float(self.query_confidence.currentData()),
                )
                self.last_upgrade_result = result
                self.show_calculation_materials(result)
                self.query_result.setPlainText(result["text"])
        except Exception as exc:  # noqa: BLE001 - UI should show validation errors
            self.query_result.setPlainText(str(exc))

    def show_material_query(self, rows: list[dict[str, Any]]) -> None:
        self.query_table.setColumnCount(7)
        self.query_table.setHorizontalHeaderLabels(["材料名", "出处", "单价钻", "单价RMB", "价格来源", "截图", "备注"])
        self.query_table.setRowCount(len(rows))
        lines = []
        if not rows:
            lines.append("暂无数据")
        for row_index, row in enumerate(rows):
            price = row.get("price_diamonds")
            price_value = float(price) if price not in {None, ""} else None
            price_text = _fmt_qty(price_value) if price_value is not None else "暂无价格"
            rmb_text = f"{self.db.diamonds_to_rmb(price_value):.2f}" if price_value is not None else ""
            sources = _source_text(row.get("source_names"))
            values = [
                row.get("name", ""),
                sources,
                price_text,
                rmb_text,
                row.get("price_source", ""),
                row.get("icon_path", ""),
                row.get("notes", ""),
            ]
            for col, value in enumerate(values):
                self.query_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
            source_text = sources or "暂无出处"
            price_line = _money(price_value, self.db) if price_value is not None else "暂无价格"
            lines.append(
                f"- {row.get('name')}：出处：{source_text}；价格：{price_line}"
                f"{'；备注：' + str(row.get('notes')) if row.get('notes') else ''}"
            )
        self.query_result.setPlainText("\n".join(lines))

    def show_source_drop_query(self, rows: list[dict[str, Any]], query: str) -> None:
        self.query_table.setColumnCount(8)
        self.query_table.setHorizontalHeaderLabels(["出处", "材料", "数量", "单价钻", "单价RMB", "价格来源", "截图", "备注"])
        self.query_table.setRowCount(len(rows))
        lines = []
        if not rows:
            lines.append(f"暂无掉落数据：{query}")
        current_source = ""
        for row_index, row in enumerate(rows):
            price = row.get("price_diamonds")
            price_value = float(price) if price not in {None, ""} else None
            qty_text = _quantity_list_text(row.get("quantities"))
            values = [
                row.get("source_name", ""),
                row.get("item_name", ""),
                qty_text,
                _fmt_qty(price_value) if price_value is not None else "暂无价格",
                f"{self.db.diamonds_to_rmb(price_value):.2f}" if price_value is not None else "",
                row.get("price_source", ""),
                row.get("icon_path", ""),
                row.get("item_notes", ""),
            ]
            for col, value in enumerate(values):
                self.query_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
            source_name = str(row.get("source_name") or "")
            if source_name != current_source:
                lines.append(f"{source_name}：")
                current_source = source_name
            price_line = _money(price_value, self.db) if price_value is not None else "暂无价格"
            qty_line = f"，数量：{qty_text}" if qty_text else ""
            lines.append(f"- {row.get('item_name')}{qty_line}，价格：{price_line}")
        self.query_result.setPlainText("\n".join(lines))

    def show_source_query(self, rows: list[dict[str, Any]]) -> None:
        self.query_table.setColumnCount(7)
        self.query_table.setHorizontalHeaderLabels(["材料名", "出处", "数量", "单价钻", "单价RMB", "价格来源", "备注"])
        self.query_table.setRowCount(len(rows))
        lines = []
        if not rows:
            lines.append("暂无数据")
        for row_index, row in enumerate(rows):
            price = self.db.get_price(str(row.get("item_name") or ""))
            price_value = float(price["price_diamonds"]) if price else None
            values = [
                row.get("item_name", ""),
                row.get("source_name", ""),
                _fmt_qty(row.get("parsed_quantity")),
                _fmt_qty(price_value) if price_value is not None else "暂无价格",
                f"{self.db.diamonds_to_rmb(price_value):.2f}" if price_value is not None else "",
                price.get("price_source", "") if price else "",
                row.get("notes", ""),
            ]
            for col, value in enumerate(values):
                self.query_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
            price_line = _money(price_value, self.db) if price_value is not None else "暂无价格"
            qty = _fmt_qty(row.get("parsed_quantity")) or "未填"
            lines.append(f"- {row.get('item_name')}：{row.get('source_name')}，数量：{qty}，价格：{price_line}")
        self.query_result.setPlainText("\n".join(lines))

    def show_price_query(self, rows: list[dict[str, Any]]) -> None:
        self.query_table.setColumnCount(8)
        self.query_table.setHorizontalHeaderLabels(["物品", "价格钻", "价格RMB", "启用", "来源", "更新时间", "备注", ""])
        self.query_table.setRowCount(len(rows))
        lines = []
        if not rows:
            lines.append("暂无市场价格")
        for row_index, row in enumerate(rows):
            price = float(row.get("price_diamonds") or 0)
            values = [
                row.get("item_name", ""),
                _fmt_qty(price),
                f"{self.db.diamonds_to_rmb(price):.2f}",
                "是" if row.get("is_active") else "否",
                row.get("price_source", ""),
                row.get("updated_at", ""),
                row.get("notes", ""),
                "",
            ]
            for col, value in enumerate(values):
                self.query_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
            lines.append(f"- {row.get('item_name')}：{_money(price, self.db)}，来源：{row.get('price_source') or '未填写'}，备注：{row.get('notes') or ''}")
        self.query_result.setPlainText("\n".join(lines))

    def show_calculation_materials(self, result: dict[str, Any]) -> None:
        materials = result.get("materials") or []
        self.query_table.setColumnCount(8)
        first_column = "底层材料" if result.get("kind") == "upgrade" else "材料/物品"
        self.query_table.setHorizontalHeaderLabels([first_column, "标准", "期望", "稳妥", "单价", "稳妥合计", "出处", "提示"])
        self.query_table.setRowCount(len(materials))
        for row_index, row in enumerate(materials):
            hints = []
            if not row.get("has_sources"):
                hints.append("暂无出处资料")
            if not row.get("has_price"):
                hints.append("暂无市场价格")
            values = [
                row.get("material_name", ""),
                _fmt_qty(row.get("standard_quantity")),
                _fmt_qty(row.get("expected_quantity")),
                _fmt_qty(row.get("safe_quantity")),
                _money(float(row.get("unit_price_diamonds") or 0), self.db) if row.get("has_price") else "暂无",
                _money(float(row.get("safe_total_diamonds") or 0), self.db) if row.get("has_price") else "暂无",
                " / ".join(row.get("sources") or []),
                "；".join(hints),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if "暂无出处" in str(value):
                    item.setBackground(Qt.GlobalColor.red)
                elif "暂无市场价格" in str(value):
                    item.setBackground(Qt.GlobalColor.yellow)
                self.query_table.setItem(row_index, col, item)

    # Material tab
    def _build_material_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        top = QHBoxLayout()
        self.material_search = QLineEdit()
        self.material_search.setPlaceholderText("搜索材料 / 备注")
        refresh = QPushButton("刷新")
        self.material_ratio = QDoubleSpinBox()
        self.material_ratio.setRange(0.0001, 999999999)
        self.material_ratio.setDecimals(4)
        self.material_ratio.setValue(500)
        save_ratio = QPushButton("保存比例")
        top.addWidget(QLabel("搜索"))
        top.addWidget(self.material_search, 1)
        top.addWidget(refresh)
        top.addWidget(QLabel("钻/RMB"))
        top.addWidget(self.material_ratio)
        top.addWidget(save_ratio)
        layout.addLayout(top)

        import_row = QHBoxLayout()
        self.excel_path = QLineEdit()
        self.excel_path.setPlaceholderText("首次导入 Excel 出处表，可留空不用")
        browse = QPushButton("选择 .xlsx")
        self.excel_mode = QComboBox()
        self.excel_mode.addItem("合并导入", "merge")
        self.excel_mode.addItem("覆盖已导入 Excel 数据", "replace")
        self.excel_mode.addItem("追加导入", "append")
        do_import = QPushButton("导入 Excel")
        export_public_site = QPushButton("导出官方网站")
        self.import_summary = QLabel("Excel 只用于首次批量导入出处，后续直接在材料库维护。")
        self.import_summary.setWordWrap(True)
        import_row.addWidget(QLabel("首次导入"))
        import_row.addWidget(self.excel_path, 1)
        import_row.addWidget(browse)
        import_row.addWidget(self.excel_mode)
        import_row.addWidget(do_import)
        import_row.addWidget(export_public_site)
        layout.addLayout(import_row)
        layout.addWidget(self.import_summary)

        publish_row = QHBoxLayout()
        self.public_site_path = QLineEdit(
            self.db.get_setting(
                "public_site_output_dir",
                str(self.db.workspace / "data" / "public_material_site"),
            )
        )
        self.public_site_path.setPlaceholderText("官方网站同步目录，例如 GitHub Pages 仓库文件夹")
        browse_public_site = QPushButton("选择目录")
        sync_public_site = QPushButton("同步更新官网")
        publish_row.addWidget(QLabel("官方网站"))
        publish_row.addWidget(self.public_site_path, 1)
        publish_row.addWidget(browse_public_site)
        publish_row.addWidget(sync_public_site)
        layout.addLayout(publish_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QSplitter(Qt.Orientation.Vertical)
        self.material_table = QTableWidget(0, 10)
        self.material_table.setHorizontalHeaderLabels(["ID", "价格ID", "材料", "出处", "钻石", "RMB", "启用", "价格来源", "截图", "备注"])
        self.material_table.setColumnHidden(0, True)
        self.material_table.setColumnHidden(1, True)
        self.material_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.material_table.horizontalHeader().setStretchLastSection(True)
        self.material_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.material_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        left.addWidget(self.material_table)

        source_box = QGroupBox("选中材料的出处")
        source_layout = QVBoxLayout(source_box)
        self.material_source_table = QTableWidget(0, 5)
        self.material_source_table.setHorizontalHeaderLabels(["ID", "材料", "出处", "数量", "备注"])
        self.material_source_table.setColumnHidden(0, True)
        self.material_source_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.material_source_table.horizontalHeader().setStretchLastSection(True)
        self.material_source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.material_source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        source_layout.addWidget(self.material_source_table)
        left.addWidget(source_box)
        left.setSizes([520, 220])
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        form_box = QGroupBox("材料资料")
        form = QFormLayout(form_box)
        self.material_name = QLineEdit()
        self.material_name.setCompleter(self._completer())
        self.material_source_name = QLineEdit()
        self.material_source_name.setPlaceholderText("商店 / 副本 / 宝箱 / 活动等")
        self.material_source_qty = QDoubleSpinBox()
        self.material_source_qty.setRange(0, 999999999)
        self.material_source_qty.setDecimals(2)
        self.material_icon = QLineEdit()
        capture_icon = QPushButton("保存当前截图/图标")
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.material_icon, 1)
        icon_row.addWidget(capture_icon)
        self.material_price = QDoubleSpinBox()
        self.material_price.setRange(0, 999999999)
        self.material_price.setDecimals(2)
        self.material_price_source = QLineEdit("手动录入")
        self.material_price_active = QCheckBox("启用该价格")
        self.material_price_active.setChecked(True)
        self.material_notes = QPlainTextEdit()
        self.material_notes.setMaximumHeight(110)
        form.addRow("材料名", self.material_name)
        form.addRow("新增出处", self.material_source_name)
        form.addRow("出处数量", self.material_source_qty)
        form.addRow("截图/图标", icon_row)
        form.addRow("当前钻石单价", self.material_price)
        form.addRow("价格来源", self.material_price_source)
        form.addRow("", self.material_price_active)
        form.addRow("备注", self.material_notes)
        right_layout.addWidget(form_box)

        actions = QGridLayout()
        save = QPushButton("保存材料")
        add_source = QPushButton("新增出处")
        delete_source = QPushButton("删除选中出处")
        history = QPushButton("查看价格历史")
        delete_price = QPushButton("删除价格")
        clear = QPushButton("新增材料")
        actions.addWidget(save, 0, 0)
        actions.addWidget(add_source, 0, 1)
        actions.addWidget(delete_source, 0, 2)
        actions.addWidget(history, 1, 0)
        actions.addWidget(delete_price, 1, 1)
        actions.addWidget(clear, 1, 2)
        right_layout.addLayout(actions)

        alias_box = QGroupBox("别名管理")
        alias_layout = QVBoxLayout(alias_box)
        alias_top = QGridLayout()
        self.alias_search = QLineEdit()
        self.alias_item = QLineEdit()
        self.alias_item.setCompleter(self._completer())
        self.alias_value = QLineEdit()
        add_alias = QPushButton("新增别名")
        delete_alias = QPushButton("删除选中别名")
        alias_top.addWidget(QLabel("搜索"), 0, 0)
        alias_top.addWidget(self.alias_search, 0, 1, 1, 3)
        alias_top.addWidget(QLabel("正式名"), 1, 0)
        alias_top.addWidget(self.alias_item, 1, 1)
        alias_top.addWidget(QLabel("别名"), 1, 2)
        alias_top.addWidget(self.alias_value, 1, 3)
        alias_top.addWidget(add_alias, 2, 2)
        alias_top.addWidget(delete_alias, 2, 3)
        alias_layout.addLayout(alias_top)
        self.alias_table = QTableWidget(0, 4)
        self.alias_table.setHorizontalHeaderLabels(["ID", "正式名", "别名", "创建时间"])
        self.alias_table.setColumnHidden(0, True)
        self.alias_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.alias_table.horizontalHeader().setStretchLastSection(True)
        self.alias_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alias_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        alias_layout.addWidget(self.alias_table)
        right_layout.addWidget(alias_box, 1)

        splitter.addWidget(right)
        splitter.setSizes([820, 420])
        layout.addWidget(splitter, 1)

        refresh.clicked.connect(self.refresh_material_table)
        self.material_search.returnPressed.connect(self.refresh_material_table)
        self.material_table.itemSelectionChanged.connect(self.populate_material_form)
        self.material_table.currentCellChanged.connect(lambda row, _col, _old_row, _old_col: self.populate_material_form(row))
        self.material_table.itemClicked.connect(lambda _item: self.populate_material_form())
        save.clicked.connect(self.save_material)
        add_source.clicked.connect(self.add_material_source_item)
        delete_source.clicked.connect(self.delete_selected_material_source_items)
        history.clicked.connect(self.show_material_price_history)
        delete_price.clicked.connect(self.delete_material_price)
        clear.clicked.connect(lambda: self.clear_material_form())
        save_ratio.clicked.connect(self.save_ratio_from_material)
        browse.clicked.connect(self.choose_excel_path)
        do_import.clicked.connect(self.import_excel)
        export_public_site.clicked.connect(self.export_public_material_site)
        browse_public_site.clicked.connect(self.choose_public_site_path)
        sync_public_site.clicked.connect(self.sync_public_material_site)
        capture_icon.clicked.connect(self.capture_material_icon)
        self.alias_search.returnPressed.connect(self.refresh_alias_table)
        add_alias.clicked.connect(self.add_alias)
        delete_alias.clicked.connect(self.delete_alias)
        return panel

    def refresh_material_table(self) -> None:
        if not hasattr(self, "material_table"):
            return
        current_name = self.material_name.text().strip() if hasattr(self, "material_name") else ""
        rows = self.db.list_materials(self.material_search.text().strip() if hasattr(self, "material_search") else "")
        self.material_table.blockSignals(True)
        self.material_table.setRowCount(0)
        self.material_table.setRowCount(len(rows))
        target_row = -1
        for row_index, row in enumerate(rows):
            price = row.get("price_diamonds")
            price_value = float(price) if price not in {None, ""} else None
            name = str(row.get("name") or "")
            if current_name and name == current_name:
                target_row = row_index
            values = [
                row.get("id", ""),
                row.get("price_id", ""),
                name,
                _source_text(row.get("source_names")),
                _fmt_qty(price_value) if price_value is not None else "",
                f"{self.db.diamonds_to_rmb(price_value):.2f}" if price_value is not None else "",
                "是" if row.get("is_active") else ("否" if row.get("price_id") else ""),
                row.get("price_source", ""),
                row.get("icon_path", ""),
                row.get("notes", ""),
            ]
            for col, value in enumerate(values):
                self.material_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
        self.material_table.blockSignals(False)
        if rows:
            selected_row = target_row if target_row >= 0 else 0
            self.material_table.setCurrentCell(selected_row, 2)
            self.material_table.selectRow(selected_row)
            self.populate_material_form(selected_row)
        else:
            self.clear_material_form(clear_selection=False)

    def populate_material_form(self, row: int | None = None) -> None:
        row = self.material_table.currentRow() if row is None or row < 0 else row
        if row < 0:
            return
        self.selected_material_id = _item_int(self.material_table, row, 0)
        self.selected_price_id = _item_int(self.material_table, row, 1)
        name = _item_text(self.material_table, row, 2)
        self.material_name.setText(name)
        self.alias_item.setText(name)
        self.material_source_name.clear()
        self.material_source_qty.setValue(0)
        self.material_price.setValue(float(_item_text(self.material_table, row, 4) or 0))
        self.material_price_source.setText(_item_text(self.material_table, row, 7) or "手动录入")
        self.material_icon.setText(_item_text(self.material_table, row, 8))
        self.material_notes.setPlainText(_item_text(self.material_table, row, 9))
        active_text = _item_text(self.material_table, row, 6)
        self.material_price_active.setChecked(active_text != "否")
        self.refresh_material_source_table()

    def refresh_material_source_table(self) -> None:
        if not hasattr(self, "material_source_table"):
            return
        name = self.material_name.text().strip() if hasattr(self, "material_name") else ""
        rows = self.db.item_sources(name) if name else []
        self.material_source_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("id", ""),
                row.get("item_name", ""),
                row.get("source_name", ""),
                _fmt_qty(row.get("parsed_quantity")),
                row.get("notes", ""),
            ]
            for col, value in enumerate(values):
                self.material_source_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))

    def clear_material_form(self, *, clear_selection: bool = True) -> None:
        if clear_selection and hasattr(self, "material_table"):
            self.material_table.blockSignals(True)
            self.material_table.clearSelection()
            self.material_table.blockSignals(False)
        self.selected_material_id = None
        self.selected_price_id = None
        self.material_name.clear()
        self.material_source_name.clear()
        self.material_source_qty.setValue(0)
        self.material_icon.clear()
        self.material_price.setValue(0)
        self.material_price_source.setText("手动录入")
        self.material_price_active.setChecked(True)
        self.material_notes.clear()
        self.material_source_table.setRowCount(0)
        self.alias_item.clear()
        self.material_name.setFocus()

    def save_material(self) -> None:
        name = self.material_name.text().strip()
        if not name:
            QMessageBox.information(self, "保存材料", "请先输入材料名。")
            return
        try:
            self.db.update_item_details(name, category="材料", notes=self.material_notes.toPlainText().strip(), icon_path=self.material_icon.text().strip())
            price_value = float(self.material_price.value())
            if price_value > 0 or self.selected_price_id:
                self.selected_price_id = self.db.set_price(
                    name,
                    price_value,
                    price_source=self.material_price_source.text().strip() or "手动录入",
                    notes="",
                    is_active=self.material_price_active.isChecked(),
                )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存材料", str(exc))
            return
        self.refresh_material_table()
        self.refresh_material_source_table()
        self.refresh_query_ratio()
        QMessageBox.information(self, "保存材料", "已保存材料资料。")

    def add_material_source_item(self) -> None:
        name = self.material_name.text().strip()
        source = self.material_source_name.text().strip()
        if not name:
            QMessageBox.information(self, "新增出处", "请先输入或选中材料。")
            return
        if not source:
            QMessageBox.information(self, "新增出处", "请填写出处名称。")
            return
        qty = float(self.material_source_qty.value())
        try:
            self.db.update_item_details(name, category="材料", notes=self.material_notes.toPlainText().strip(), icon_path=self.material_icon.text().strip())
            self.db.add_source_item(
                item_name=name,
                raw_text=f"{name}{_fmt_qty(qty) if qty else ''}",
                source_name=source,
                parsed_quantity=qty if qty > 0 else None,
                source_type="manual",
                notes="手动新增",
                skip_duplicate=False,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "新增出处", str(exc))
            return
        self.material_source_name.clear()
        self.material_source_qty.setValue(0)
        self.refresh_material_table()
        self.refresh_material_source_table()

    def delete_selected_material_source_items(self) -> None:
        rows = sorted({index.row() for index in self.material_source_table.selectionModel().selectedRows()})
        ids = [_item_int(self.material_source_table, row, 0) for row in rows]
        ids = [item for item in ids if item]
        if not ids:
            QMessageBox.information(self, "删除出处", "请先选中要删除的出处。")
            return
        if QMessageBox.question(self, "删除出处", f"确定删除 {len(ids)} 条出处？不会删除材料本身。") != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_source_items(ids)
        self.refresh_material_table()
        self.refresh_material_source_table()

    def delete_material_price(self) -> None:
        if not self.selected_price_id:
            QMessageBox.information(self, "删除价格", "当前材料还没有价格记录。")
            return
        if QMessageBox.question(self, "删除价格", "确定删除当前材料的价格？") != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_price(self.selected_price_id)
        self.selected_price_id = None
        self.material_price.setValue(0)
        self.refresh_material_table()

    def show_material_price_history(self) -> None:
        name = self.material_name.text().strip()
        if not name:
            QMessageBox.information(self, "价格历史", "请先输入或选中材料。")
            return
        self._show_price_history_dialog(name)

    def capture_material_icon(self) -> None:
        if not self.capture_callback:
            QMessageBox.information(self, "截图/图标", "当前主窗口没有提供截图回调。")
            return
        result = self.capture_callback(self, "框选要保存为材料截图/图标的区域")
        if result and result.get("crop_path"):
            self.material_icon.setText(result["crop_path"])

    def save_ratio_from_material(self) -> None:
        try:
            self.db.set_diamond_per_rmb(float(self.material_ratio.value()))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存比例", str(exc))
            return
        if hasattr(self, "settings_ratio"):
            self.settings_ratio.setValue(float(self.db.diamond_per_rmb()))
        self.refresh_query_ratio()
        self.refresh_material_table()
        self.refresh_price_table()
        QMessageBox.information(self, "保存比例", "钻石兑换人民币比例已更新。")

    # Price tab
    def _build_price_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        top = QHBoxLayout()
        self.price_search = QLineEdit()
        self.price_search.setPlaceholderText("搜索物品价格")
        refresh = QPushButton("刷新")
        export_csv = QPushButton("导出价格CSV")
        import_csv = QPushButton("导入价格CSV")
        batch = QPushButton("批量乘以系数")
        top.addWidget(QLabel("搜索"))
        top.addWidget(self.price_search, 1)
        top.addWidget(refresh)
        top.addWidget(import_csv)
        top.addWidget(export_csv)
        top.addWidget(batch)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.price_table = QTableWidget(0, 8)
        self.price_table.setHorizontalHeaderLabels(["ID", "物品", "钻石", "RMB", "启用", "来源", "更新时间", "备注"])
        self.price_table.setColumnHidden(0, True)
        self.price_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.price_table.horizontalHeader().setStretchLastSection(True)
        self.price_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.price_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        splitter.addWidget(self.price_table)

        form_box = QGroupBox("新增 / 修改价格")
        form = QFormLayout(form_box)
        self.price_item = QLineEdit()
        self.price_item.setCompleter(self._completer())
        self.price_value = QDoubleSpinBox()
        self.price_value.setRange(0, 999999999)
        self.price_value.setDecimals(2)
        self.price_source = QLineEdit("手动录入")
        self.price_active = QCheckBox("启用该价格")
        self.price_active.setChecked(True)
        self.price_notes = QPlainTextEdit()
        self.price_notes.setMaximumHeight(120)
        save = QPushButton("保存价格")
        delete = QPushButton("删除价格")
        history = QPushButton("查看历史")
        form.addRow("物品名", self.price_item)
        form.addRow("当前钻石单价", self.price_value)
        form.addRow("价格来源", self.price_source)
        form.addRow("", self.price_active)
        form.addRow("备注", self.price_notes)
        form.addRow(save)
        form.addRow(history)
        form.addRow(delete)
        splitter.addWidget(form_box)
        splitter.setSizes([820, 360])
        layout.addWidget(splitter, 1)

        refresh.clicked.connect(self.refresh_price_table)
        self.price_search.returnPressed.connect(self.refresh_price_table)
        self.price_table.itemSelectionChanged.connect(self.populate_price_form)
        save.clicked.connect(self.save_price)
        delete.clicked.connect(self.delete_price)
        history.clicked.connect(self.show_price_history)
        export_csv.clicked.connect(self.export_prices_csv)
        import_csv.clicked.connect(self.import_prices_csv)
        batch.clicked.connect(self.batch_adjust_prices)
        return panel

    def refresh_price_table(self) -> None:
        if not hasattr(self, "price_table"):
            return
        rows = self.db.list_prices(self.price_search.text().strip() if hasattr(self, "price_search") else "")
        self.price_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            price = float(row.get("price_diamonds") or 0)
            values = [
                row.get("id", ""),
                row.get("item_name", ""),
                _fmt_qty(price),
                f"{self.db.diamonds_to_rmb(price):.2f}",
                "是" if row.get("is_active") else "否",
                row.get("price_source", ""),
                row.get("updated_at", ""),
                row.get("notes", ""),
            ]
            for col, value in enumerate(values):
                self.price_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))

    def populate_price_form(self) -> None:
        row = self.price_table.currentRow()
        if row < 0:
            return
        self.selected_price_id = _item_int(self.price_table, row, 0)
        self.price_item.setText(_item_text(self.price_table, row, 1))
        self.price_value.setValue(float(_item_text(self.price_table, row, 2) or 0))
        self.price_source.setText(_item_text(self.price_table, row, 5) or "手动录入")
        self.price_notes.setPlainText(_item_text(self.price_table, row, 7))
        self.price_active.setChecked(_item_text(self.price_table, row, 4) != "否")

    def save_price(self) -> None:
        try:
            self.db.set_price(
                self.price_item.text(),
                float(self.price_value.value()),
                price_source=self.price_source.text().strip() or "手动录入",
                notes=self.price_notes.toPlainText().strip(),
                is_active=self.price_active.isChecked(),
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存价格", str(exc))
            return
        self.refresh_price_table()
        self.refresh_query_ratio()

    def delete_price(self) -> None:
        if not self.selected_price_id:
            QMessageBox.information(self, "删除价格", "请先选中价格记录。")
            return
        if QMessageBox.question(self, "删除价格", "确定删除选中的价格记录？") != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_price(self.selected_price_id)
        self.selected_price_id = None
        self.refresh_price_table()

    def show_price_history(self) -> None:
        name = self.price_item.text().strip()
        if not name:
            QMessageBox.information(self, "价格历史", "请先输入或选中物品。")
            return
        self._show_price_history_dialog(name)

    def _show_price_history_dialog(self, name: str) -> None:
        rows = self.db.price_history(name)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"价格历史 - {name}")
        dialog.resize(760, 420)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(["时间", "旧价格", "新价格", "来源", "备注"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for row_index, row in enumerate(rows):
            values = [row.get("changed_at"), row.get("old_price_diamonds"), row.get("new_price_diamonds"), row.get("price_source"), row.get("notes")]
            for col, value in enumerate(values):
                table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
        layout.addWidget(table)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dialog.reject)
        layout.addWidget(box)
        dialog.exec()

    def export_prices_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出价格CSV", str(Path.cwd() / "stoneage_prices.csv"), "CSV (*.csv)")
        if path:
            self.db.export_prices_csv(path)
            QMessageBox.information(self, "导出价格CSV", f"已导出：{path}")

    def import_prices_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入价格CSV", str(Path.cwd()), "CSV (*.csv)")
        if not path:
            return
        try:
            count = self.db.import_prices_csv(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入价格CSV", str(exc))
            return
        self.refresh_price_table()
        QMessageBox.information(self, "导入价格CSV", f"已导入/更新 {count} 条价格。")

    def batch_adjust_prices(self) -> None:
        rows = sorted({index.row() for index in self.price_table.selectionModel().selectedRows()})
        if not rows:
            QMessageBox.information(self, "批量修改价格", "请先选中要修改的价格行。")
            return
        factor, ok = QInputDialog.getDouble(self, "批量修改价格", "价格乘以系数，例如 1.1 或 0.9", 1.0, 0.0001, 1000, 4)
        if not ok:
            return
        for row in rows:
            name = _item_text(self.price_table, row, 1)
            price = float(_item_text(self.price_table, row, 2) or 0) * float(factor)
            self.db.set_price(name, price, price_source="批量修改", notes=f"按系数 {factor:g} 调整")
        self.refresh_price_table()

    # Recipe tab
    def _build_recipe_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        recipe_filter = QHBoxLayout()
        self.recipe_search = QLineEdit()
        self.recipe_search.setPlaceholderText("搜索成品")
        self.recipe_category_filter = QComboBox()
        self.recipe_category_filter.addItems(["全部", "装备", "宠物", "材料", "道具", "称号", "碎片", "其他"])
        self.recipe_type_filter = QComboBox()
        self.recipe_type_filter.addItems(["全部", "打造", "合成", "升级", "兑换", "任务", "其他"])
        refresh = QPushButton("刷新")
        recipe_filter.addWidget(self.recipe_search, 1)
        recipe_filter.addWidget(self.recipe_category_filter)
        recipe_filter.addWidget(self.recipe_type_filter)
        recipe_filter.addWidget(refresh)
        left_layout.addLayout(recipe_filter)
        self.recipe_table = QTableWidget(0, 7)
        self.recipe_table.setHorizontalHeaderLabels(["ID", "成品", "分类", "类型", "成功率", "材料数", "更新时间"])
        self.recipe_table.setColumnHidden(0, True)
        self.recipe_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.recipe_table.horizontalHeader().setStretchLastSection(True)
        self.recipe_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.recipe_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        left_layout.addWidget(self.recipe_table, 1)
        splitter.addWidget(left)

        right = QWidget()
        form = QVBoxLayout(right)
        top_form = QGridLayout()
        self.recipe_product = QLineEdit()
        self.recipe_product.setCompleter(self._completer())
        ocr = QPushButton("从当前截图 OCR 名称")
        icon = QPushButton("保存当前截图/图标")
        self.recipe_category = QComboBox()
        self.recipe_category.addItems(["装备", "宠物", "材料", "道具", "称号", "碎片", "其他"])
        self.recipe_type = QComboBox()
        self.recipe_type.addItems(["打造", "合成", "升级", "兑换", "任务", "其他"])
        self.recipe_success = QDoubleSpinBox()
        self.recipe_success.setRange(0.01, 100.0)
        self.recipe_success.setSuffix("%")
        self.recipe_success.setValue(100.0)
        self.recipe_output = QDoubleSpinBox()
        self.recipe_output.setRange(0.0001, 999999)
        self.recipe_output.setValue(1)
        self.recipe_diamond = QDoubleSpinBox()
        self.recipe_diamond.setRange(0, 999999999)
        self.recipe_coin = QDoubleSpinBox()
        self.recipe_coin.setRange(0, 999999999)
        self.recipe_fail_materials = QCheckBox("失败消耗材料")
        self.recipe_fail_materials.setChecked(True)
        self.recipe_fail_diamonds = QCheckBox("失败消耗钻石")
        self.recipe_fail_diamonds.setChecked(True)
        self.recipe_fail_coin = QCheckBox("失败消耗石币/金币")
        self.recipe_fail_coin.setChecked(True)
        self.recipe_screenshot = QLineEdit()
        self.recipe_notes = QPlainTextEdit()
        self.recipe_notes.setMaximumHeight(80)
        top_form.addWidget(QLabel("成品名称"), 0, 0)
        top_form.addWidget(self.recipe_product, 0, 1, 1, 3)
        top_form.addWidget(ocr, 0, 4)
        top_form.addWidget(icon, 0, 5)
        top_form.addWidget(QLabel("分类"), 1, 0)
        top_form.addWidget(self.recipe_category, 1, 1)
        top_form.addWidget(QLabel("配方类型"), 1, 2)
        top_form.addWidget(self.recipe_type, 1, 3)
        top_form.addWidget(QLabel("成功率"), 1, 4)
        top_form.addWidget(self.recipe_success, 1, 5)
        top_form.addWidget(QLabel("产出数量"), 2, 0)
        top_form.addWidget(self.recipe_output, 2, 1)
        top_form.addWidget(QLabel("每次钻石"), 2, 2)
        top_form.addWidget(self.recipe_diamond, 2, 3)
        top_form.addWidget(QLabel("每次石币/金币"), 2, 4)
        top_form.addWidget(self.recipe_coin, 2, 5)
        top_form.addWidget(self.recipe_fail_materials, 3, 0, 1, 2)
        top_form.addWidget(self.recipe_fail_diamonds, 3, 2, 1, 2)
        top_form.addWidget(self.recipe_fail_coin, 3, 4, 1, 2)
        top_form.addWidget(QLabel("截图/图标"), 4, 0)
        top_form.addWidget(self.recipe_screenshot, 4, 1, 1, 5)
        top_form.addWidget(QLabel("备注"), 5, 0)
        top_form.addWidget(self.recipe_notes, 5, 1, 1, 5)
        form.addLayout(top_form)

        material_buttons = QHBoxLayout()
        add_material = QPushButton("添加材料")
        delete_material = QPushButton("删除材料")
        material_buttons.addWidget(QLabel("材料明细"))
        material_buttons.addStretch(1)
        material_buttons.addWidget(add_material)
        material_buttons.addWidget(delete_material)
        form.addLayout(material_buttons)
        self.recipe_material_table = self._material_edit_table()
        form.addWidget(self.recipe_material_table, 1)
        actions = QHBoxLayout()
        new = QPushButton("新增")
        save = QPushButton("保存")
        delete = QPushButton("删除")
        duplicate = QPushButton("复制配方")
        calc = QPushButton("查询/计算此成品")
        actions.addWidget(new)
        actions.addWidget(save)
        actions.addWidget(delete)
        actions.addWidget(duplicate)
        actions.addStretch(1)
        actions.addWidget(calc)
        form.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([420, 820])
        layout.addWidget(splitter)

        refresh.clicked.connect(self.refresh_recipe_table)
        self.recipe_search.returnPressed.connect(self.refresh_recipe_table)
        self.recipe_category_filter.currentTextChanged.connect(lambda _v: self.refresh_recipe_table())
        self.recipe_type_filter.currentTextChanged.connect(lambda _v: self.refresh_recipe_table())
        self.recipe_table.itemSelectionChanged.connect(self.populate_recipe_form)
        add_material.clicked.connect(lambda: self.add_material_row(self.recipe_material_table))
        delete_material.clicked.connect(lambda: self.delete_material_rows(self.recipe_material_table))
        new.clicked.connect(self.clear_recipe_form)
        save.clicked.connect(self.save_recipe)
        delete.clicked.connect(self.delete_recipe)
        duplicate.clicked.connect(self.duplicate_recipe)
        calc.clicked.connect(self.query_current_recipe)
        ocr.clicked.connect(self.capture_recipe_name)
        icon.clicked.connect(self.capture_recipe_icon)
        return panel

    def refresh_recipe_table(self) -> None:
        if not hasattr(self, "recipe_table"):
            return
        rows = self.db.list_recipes(
            self.recipe_search.text().strip(),
            self.recipe_category_filter.currentText(),
            self.recipe_type_filter.currentText(),
        )
        self.recipe_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("id"),
                row.get("product_name"),
                row.get("category"),
                row.get("recipe_type"),
                f"{float(row.get('success_rate') or 0) * 100:.2f}%",
                row.get("material_count"),
                row.get("updated_at"),
            ]
            for col, value in enumerate(values):
                self.recipe_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))

    def populate_recipe_form(self) -> None:
        row = self.recipe_table.currentRow()
        if row < 0:
            return
        recipe_id = _item_int(self.recipe_table, row, 0)
        recipe = self.db.get_recipe(recipe_id) if recipe_id else None
        if not recipe:
            return
        self.selected_recipe_id = int(recipe["id"])
        self.recipe_product.setText(str(recipe.get("product_name") or ""))
        self.recipe_category.setCurrentText(str(recipe.get("category") or "其他"))
        self.recipe_type.setCurrentText(str(recipe.get("recipe_type") or "打造"))
        self.recipe_success.setValue(float(recipe.get("success_rate") or 1) * 100)
        self.recipe_output.setValue(float(recipe.get("output_quantity") or 1))
        self.recipe_diamond.setValue(float(recipe.get("diamond_cost") or 0))
        self.recipe_coin.setValue(float(recipe.get("coin_cost") or 0))
        self.recipe_fail_materials.setChecked(bool(recipe.get("failure_consumes_materials")))
        self.recipe_fail_diamonds.setChecked(bool(recipe.get("failure_consumes_diamonds")))
        self.recipe_fail_coin.setChecked(bool(recipe.get("failure_consumes_coin")))
        self.recipe_screenshot.setText(str(recipe.get("screenshot_path") or ""))
        self.recipe_notes.setPlainText(str(recipe.get("notes") or ""))
        self.load_material_rows(self.recipe_material_table, recipe.get("materials") or [])

    def clear_recipe_form(self) -> None:
        self.selected_recipe_id = None
        self.recipe_product.clear()
        self.recipe_category.setCurrentText("其他")
        self.recipe_type.setCurrentText("打造")
        self.recipe_success.setValue(100)
        self.recipe_output.setValue(1)
        self.recipe_diamond.setValue(0)
        self.recipe_coin.setValue(0)
        self.recipe_fail_materials.setChecked(True)
        self.recipe_fail_diamonds.setChecked(True)
        self.recipe_fail_coin.setChecked(True)
        self.recipe_screenshot.clear()
        self.recipe_notes.clear()
        self.recipe_material_table.setRowCount(0)

    def save_recipe(self) -> None:
        self.commit_material_table_editor(self.recipe_material_table)
        data = {
            "product_name": self.recipe_product.text(),
            "category": self.recipe_category.currentText(),
            "recipe_type": self.recipe_type.currentText(),
            "success_rate": float(self.recipe_success.value()) / 100.0,
            "output_quantity": float(self.recipe_output.value()),
            "diamond_cost": float(self.recipe_diamond.value()),
            "coin_cost": float(self.recipe_coin.value()),
            "failure_consumes_materials": self.recipe_fail_materials.isChecked(),
            "failure_consumes_diamonds": self.recipe_fail_diamonds.isChecked(),
            "failure_consumes_coin": self.recipe_fail_coin.isChecked(),
            "screenshot_path": self.recipe_screenshot.text().strip(),
            "notes": self.recipe_notes.toPlainText().strip(),
        }
        try:
            self.selected_recipe_id = self.db.save_recipe(data, self.material_rows(self.recipe_material_table), self.selected_recipe_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存配方", str(exc))
            return
        self.refresh_recipe_table()
        self.refresh_material_table()
        self.refresh_price_table()
        QMessageBox.information(self, "保存配方", "已保存配方。")

    def delete_recipe(self) -> None:
        if not self.selected_recipe_id:
            QMessageBox.information(self, "删除配方", "请先选中配方。")
            return
        if QMessageBox.question(self, "删除配方", "确定删除这个配方？不会删除材料出处库。") != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_recipe(self.selected_recipe_id)
        self.clear_recipe_form()
        self.refresh_recipe_table()

    def duplicate_recipe(self) -> None:
        if not self.selected_recipe_id:
            QMessageBox.information(self, "复制配方", "请先选中配方。")
            return
        self.selected_recipe_id = None
        self.recipe_product.setText(self.recipe_product.text().strip() + "_复制")

    def query_current_recipe(self) -> None:
        name = self.recipe_product.text().strip()
        if not name:
            return
        self.tabs.setCurrentIndex(0)
        self.query_type.setCurrentText("查成品配方")
        self.query_edit.setText(name)
        self.run_query()

    def capture_recipe_name(self) -> None:
        if not self.capture_callback:
            QMessageBox.information(self, "截图 OCR", "当前主窗口没有提供截图 OCR 回调。")
            return
        result = self.capture_callback(self, "框选成品名称区域")
        if not result:
            return
        text = (result.get("text") or "").strip()
        if text:
            self.recipe_product.setText(text)
        if result.get("crop_path"):
            self.recipe_screenshot.setText(result["crop_path"])

    def capture_recipe_icon(self) -> None:
        if not self.capture_callback:
            QMessageBox.information(self, "截图/图标", "当前主窗口没有提供截图回调。")
            return
        result = self.capture_callback(self, "框选要保存为成品截图/图标的区域")
        if result and result.get("crop_path"):
            self.recipe_screenshot.setText(result["crop_path"])

    # Upgrade tab
    def _build_upgrade_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        search_row = QHBoxLayout()
        self.upgrade_search = QLineEdit()
        self.upgrade_search.setPlaceholderText("搜索装备名称")
        refresh = QPushButton("刷新")
        search_row.addWidget(self.upgrade_search, 1)
        search_row.addWidget(refresh)
        left_layout.addLayout(search_row)
        self.upgrade_table = QTableWidget(0, 8)
        self.upgrade_table.setHorizontalHeaderLabels(["ID", "装备", "从", "到", "成功率", "钻石", "材料数", "更新时间"])
        self.upgrade_table.setColumnHidden(0, True)
        self.upgrade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.upgrade_table.horizontalHeader().setStretchLastSection(True)
        self.upgrade_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.upgrade_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        left_layout.addWidget(self.upgrade_table, 1)
        splitter.addWidget(left)

        right = QWidget()
        form = QVBoxLayout(right)
        grid = QGridLayout()
        self.upgrade_equipment = QLineEdit()
        self.upgrade_equipment.setCompleter(self._completer())
        self.upgrade_from = QSpinBox()
        self.upgrade_from.setRange(0, 999)
        self.upgrade_to = QSpinBox()
        self.upgrade_to.setRange(1, 1000)
        self.upgrade_to.setValue(1)
        self.upgrade_success = QDoubleSpinBox()
        self.upgrade_success.setRange(0.01, 100.0)
        self.upgrade_success.setSuffix("%")
        self.upgrade_success.setValue(100)
        self.upgrade_diamond = QDoubleSpinBox()
        self.upgrade_diamond.setRange(0, 999999999)
        self.upgrade_coin = QDoubleSpinBox()
        self.upgrade_coin.setRange(0, 999999999)
        self.upgrade_fail_materials = QCheckBox("失败消耗材料")
        self.upgrade_fail_materials.setChecked(True)
        self.upgrade_fail_diamonds = QCheckBox("失败消耗钻石")
        self.upgrade_fail_diamonds.setChecked(True)
        self.upgrade_downgrade = QCheckBox("失败会降级（字段保留，第一版暂不计算）")
        self.upgrade_notes = QPlainTextEdit()
        self.upgrade_notes.setMaximumHeight(80)
        grid.addWidget(QLabel("装备名称"), 0, 0)
        grid.addWidget(self.upgrade_equipment, 0, 1, 1, 5)
        grid.addWidget(QLabel("当前等级"), 1, 0)
        grid.addWidget(self.upgrade_from, 1, 1)
        grid.addWidget(QLabel("目标等级"), 1, 2)
        grid.addWidget(self.upgrade_to, 1, 3)
        grid.addWidget(QLabel("成功率"), 1, 4)
        grid.addWidget(self.upgrade_success, 1, 5)
        grid.addWidget(QLabel("每次钻石"), 2, 0)
        grid.addWidget(self.upgrade_diamond, 2, 1)
        grid.addWidget(QLabel("每次石币/金币"), 2, 2)
        grid.addWidget(self.upgrade_coin, 2, 3)
        grid.addWidget(self.upgrade_fail_materials, 3, 0, 1, 2)
        grid.addWidget(self.upgrade_fail_diamonds, 3, 2, 1, 2)
        grid.addWidget(self.upgrade_downgrade, 3, 4, 1, 2)
        grid.addWidget(QLabel("备注"), 4, 0)
        grid.addWidget(self.upgrade_notes, 4, 1, 1, 5)
        form.addLayout(grid)

        material_buttons = QHBoxLayout()
        add_material = QPushButton("添加材料")
        delete_material = QPushButton("删除材料")
        material_buttons.addWidget(QLabel("升级材料"))
        material_buttons.addStretch(1)
        material_buttons.addWidget(add_material)
        material_buttons.addWidget(delete_material)
        form.addLayout(material_buttons)
        self.upgrade_material_table = self._material_edit_table()
        form.addWidget(self.upgrade_material_table, 1)
        actions = QHBoxLayout()
        new = QPushButton("新增")
        save = QPushButton("保存")
        delete = QPushButton("删除")
        batch = QPushButton("批量新增连续等级")
        calc = QPushButton("计算升级区间")
        export_json = QPushButton("导出计划JSON")
        export_csv = QPushButton("导出计划CSV")
        actions.addWidget(new)
        actions.addWidget(save)
        actions.addWidget(delete)
        actions.addWidget(batch)
        actions.addStretch(1)
        actions.addWidget(calc)
        actions.addWidget(export_json)
        actions.addWidget(export_csv)
        form.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([420, 820])
        layout.addWidget(splitter)

        refresh.clicked.connect(self.refresh_upgrade_table)
        self.upgrade_search.returnPressed.connect(self.refresh_upgrade_table)
        self.upgrade_table.itemSelectionChanged.connect(self.populate_upgrade_form)
        add_material.clicked.connect(lambda: self.add_material_row(self.upgrade_material_table))
        delete_material.clicked.connect(lambda: self.delete_material_rows(self.upgrade_material_table))
        new.clicked.connect(self.clear_upgrade_form)
        save.clicked.connect(self.save_upgrade)
        delete.clicked.connect(self.delete_upgrade)
        batch.clicked.connect(self.batch_add_upgrade_steps)
        calc.clicked.connect(self.query_current_upgrade)
        export_json.clicked.connect(self.export_upgrade_plan_json)
        export_csv.clicked.connect(self.export_upgrade_plan_csv)
        return panel

    def refresh_upgrade_table(self) -> None:
        if not hasattr(self, "upgrade_table"):
            return
        rows = self.db.list_upgrade_steps(self.upgrade_search.text().strip())
        selected_id = self.selected_upgrade_id
        target_row = -1
        self.upgrade_table.blockSignals(True)
        self.upgrade_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            if selected_id is not None and int(row.get("id") or 0) == int(selected_id):
                target_row = row_index
            values = [
                row.get("id"),
                row.get("equipment_name"),
                row.get("from_level"),
                row.get("to_level"),
                f"{float(row.get('success_rate') or 0) * 100:.2f}%",
                _fmt_qty(row.get("diamond_cost")),
                row.get("material_count"),
                row.get("updated_at"),
            ]
            for col, value in enumerate(values):
                self.upgrade_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))
        self.upgrade_table.blockSignals(False)
        if target_row >= 0:
            self.upgrade_table.setCurrentCell(target_row, 1)
            self.upgrade_table.selectRow(target_row)

    def populate_upgrade_form(self) -> None:
        row = self.upgrade_table.currentRow()
        if row < 0:
            return
        step_id = _item_int(self.upgrade_table, row, 0)
        step = self.db.get_upgrade_step(step_id) if step_id else None
        if not step:
            return
        self.selected_upgrade_id = int(step["id"])
        self.upgrade_equipment.setText(str(step.get("equipment_name") or ""))
        self.upgrade_from.setValue(int(step.get("from_level") or 0))
        self.upgrade_to.setValue(int(step.get("to_level") or 1))
        self.upgrade_success.setValue(float(step.get("success_rate") or 1) * 100)
        self.upgrade_diamond.setValue(float(step.get("diamond_cost") or 0))
        self.upgrade_coin.setValue(float(step.get("coin_cost") or 0))
        self.upgrade_fail_materials.setChecked(bool(step.get("failure_consumes_materials")))
        self.upgrade_fail_diamonds.setChecked(bool(step.get("failure_consumes_diamonds")))
        self.upgrade_downgrade.setChecked(bool(step.get("failure_downgrades_level")))
        self.upgrade_notes.setPlainText(str(step.get("notes") or ""))
        self.load_material_rows(self.upgrade_material_table, step.get("materials") or [])

    def clear_upgrade_form(self) -> None:
        self.selected_upgrade_id = None
        self.upgrade_equipment.clear()
        self.upgrade_from.setValue(0)
        self.upgrade_to.setValue(1)
        self.upgrade_success.setValue(100)
        self.upgrade_diamond.setValue(0)
        self.upgrade_coin.setValue(0)
        self.upgrade_fail_materials.setChecked(True)
        self.upgrade_fail_diamonds.setChecked(True)
        self.upgrade_downgrade.setChecked(False)
        self.upgrade_notes.clear()
        self.upgrade_material_table.setRowCount(0)

    def save_upgrade(self) -> None:
        self.commit_material_table_editor(self.upgrade_material_table)
        data = {
            "equipment_name": self.upgrade_equipment.text(),
            "from_level": int(self.upgrade_from.value()),
            "to_level": int(self.upgrade_to.value()),
            "success_rate": float(self.upgrade_success.value()) / 100.0,
            "diamond_cost": float(self.upgrade_diamond.value()),
            "coin_cost": float(self.upgrade_coin.value()),
            "failure_consumes_materials": self.upgrade_fail_materials.isChecked(),
            "failure_consumes_diamonds": self.upgrade_fail_diamonds.isChecked(),
            "failure_downgrades_level": self.upgrade_downgrade.isChecked(),
            "notes": self.upgrade_notes.toPlainText().strip(),
        }
        try:
            self.selected_upgrade_id = self.db.save_upgrade_step(data, self.material_rows(self.upgrade_material_table), self.selected_upgrade_id)
            self.refresh_upgrade_table()
            self.refresh_material_table()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存升级步骤", str(exc))
            return
        QMessageBox.information(self, "保存升级步骤", "已保存升级步骤。")

    def delete_upgrade(self) -> None:
        if not self.selected_upgrade_id:
            QMessageBox.information(self, "删除升级步骤", "请先选中升级步骤。")
            return
        if QMessageBox.question(self, "删除升级步骤", "确定删除这个升级步骤？") != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_upgrade_step(self.selected_upgrade_id)
        self.clear_upgrade_form()
        self.refresh_upgrade_table()

    def batch_add_upgrade_steps(self) -> None:
        name, ok = QInputDialog.getText(self, "批量新增升级步骤", "装备名称", text=self.upgrade_equipment.text())
        if not ok or not name.strip():
            return
        start, ok = QInputDialog.getInt(self, "批量新增升级步骤", "起始等级", int(self.upgrade_from.value()), 0, 999, 1)
        if not ok:
            return
        end, ok = QInputDialog.getInt(self, "批量新增升级步骤", "目标等级", max(start + 1, int(self.upgrade_to.value())), start + 1, 1000, 1)
        if not ok:
            return
        success, ok = QInputDialog.getDouble(self, "批量新增升级步骤", "每级成功率（%）", float(self.upgrade_success.value()), 0.01, 100, 2)
        if not ok:
            return
        diamond, ok = QInputDialog.getDouble(self, "批量新增升级步骤", "每次尝试钻石消耗", float(self.upgrade_diamond.value()), 0, 999999999, 2)
        if not ok:
            return
        material_name, ok = QInputDialog.getItem(self, "批量新增升级步骤", "材料名（可留空后再编辑）", self.item_names(), editable=True)
        if not ok:
            return
        quantity = 0.0
        if material_name.strip():
            quantity, ok = QInputDialog.getDouble(self, "批量新增升级步骤", "每级材料数量", 1, 0.0001, 999999, 2)
            if not ok:
                return
        created = 0
        for level in range(int(start), int(end)):
            materials = [{"material_name": material_name, "quantity": quantity}] if material_name.strip() and quantity > 0 else []
            self.db.save_upgrade_step(
                {
                    "equipment_name": name,
                    "from_level": level,
                    "to_level": level + 1,
                    "success_rate": success / 100.0,
                    "diamond_cost": diamond,
                    "failure_consumes_materials": True,
                    "failure_consumes_diamonds": True,
                    "failure_downgrades_level": False,
                    "notes": "批量新增，可继续编辑",
                },
                materials,
            )
            created += 1
        self.refresh_upgrade_table()
        self.refresh_material_table()
        QMessageBox.information(self, "批量新增升级步骤", f"已新增 {created} 条升级步骤。")

    def query_current_upgrade(self) -> None:
        name = self.upgrade_equipment.text().strip()
        if not name:
            return
        self.tabs.setCurrentIndex(0)
        self.query_type.setCurrentText("查装备升级")
        self.query_edit.setText(name)
        self.query_from_level.setValue(int(self.upgrade_from.value()))
        self.query_to_level.setValue(max(int(self.upgrade_to.value()), int(self.upgrade_from.value()) + 1))
        self.run_query()

    def export_upgrade_plan_json(self) -> None:
        result = self.last_upgrade_result
        if not result or result.get("kind") != "upgrade":
            QMessageBox.information(self, "导出计划JSON", "请先在查询页或升级页计算一次升级计划。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出升级计划JSON", str(Path.cwd() / "upgrade_plan.json"), "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(_jsonable(result), ensure_ascii=False, indent=2), encoding="utf-8")
            QMessageBox.information(self, "导出计划JSON", f"已导出：{path}")

    def export_upgrade_plan_csv(self) -> None:
        result = self.last_upgrade_result
        if not result or result.get("kind") != "upgrade":
            QMessageBox.information(self, "导出计划CSV", "请先在查询页或升级页计算一次升级计划。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出升级计划CSV", str(Path.cwd() / "upgrade_plan.csv"), "CSV (*.csv)")
        if not path:
            return
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["材料", "标准数量", "期望数量", "稳妥数量", "单价钻", "出处"])
            for row in result.get("materials") or []:
                writer.writerow([
                    row.get("material_name"),
                    row.get("standard_quantity"),
                    row.get("expected_quantity"),
                    row.get("safe_quantity"),
                    row.get("unit_price_diamonds") or "",
                    " / ".join(row.get("sources") or []),
                ])
            writer.writerow([])
            writer.writerow(["模式", "材料成本钻", "直接钻石", "总成本钻", "总成本RMB"])
            for key in ("standard", "expected", "safe"):
                cost = result["costs"][key]
                writer.writerow([key, cost["material_diamonds"], cost["direct_diamonds"], cost["total_diamonds"], cost["total_rmb"]])
        QMessageBox.information(self, "导出计划CSV", f"已导出：{path}")

    # Data tab
    def _build_data_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        import_box = QGroupBox("导入 Excel 出处表")
        import_layout = QGridLayout(import_box)
        self.excel_path = QLineEdit()
        browse = QPushButton("选择 .xlsx")
        self.excel_mode = QComboBox()
        self.excel_mode.addItem("合并导入", "merge")
        self.excel_mode.addItem("覆盖已导入 Excel 数据", "replace")
        self.excel_mode.addItem("追加导入", "append")
        do_import = QPushButton("导入")
        self.import_summary = QLabel("尚未导入")
        self.import_summary.setWordWrap(True)
        import_layout.addWidget(QLabel("文件"), 0, 0)
        import_layout.addWidget(self.excel_path, 0, 1)
        import_layout.addWidget(browse, 0, 2)
        import_layout.addWidget(QLabel("模式"), 0, 3)
        import_layout.addWidget(self.excel_mode, 0, 4)
        import_layout.addWidget(do_import, 0, 5)
        import_layout.addWidget(self.import_summary, 1, 0, 1, 6)
        layout.addWidget(import_box)

        source_box = QGroupBox("材料出处库")
        source_layout = QVBoxLayout(source_box)
        source_top = QHBoxLayout()
        self.source_search = QLineEdit()
        self.source_search.setPlaceholderText("搜索出处")
        source_refresh = QPushButton("刷新")
        add_source = QPushButton("手动新增出处关系")
        delete_source = QPushButton("删除选中出处关系")
        source_top.addWidget(self.source_search, 1)
        source_top.addWidget(source_refresh)
        source_top.addWidget(add_source)
        source_top.addWidget(delete_source)
        source_layout.addLayout(source_top)
        self.source_table = QTableWidget(0, 8)
        self.source_table.setHorizontalHeaderLabels(["ID", "材料", "出处", "原文", "数量", "Sheet", "行", "列"])
        self.source_table.setColumnHidden(0, True)
        self.source_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.source_table.horizontalHeader().setStretchLastSection(True)
        self.source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        source_layout.addWidget(self.source_table, 1)
        layout.addWidget(source_box, 1)

        alias_box = QGroupBox("别名管理")
        alias_layout = QVBoxLayout(alias_box)
        alias_top = QHBoxLayout()
        self.alias_search = QLineEdit()
        self.alias_item = QLineEdit()
        self.alias_item.setCompleter(self._completer())
        self.alias_value = QLineEdit()
        add_alias = QPushButton("新增别名")
        delete_alias = QPushButton("删除选中别名")
        alias_top.addWidget(QLabel("搜索"))
        alias_top.addWidget(self.alias_search, 1)
        alias_top.addWidget(QLabel("正式名"))
        alias_top.addWidget(self.alias_item)
        alias_top.addWidget(QLabel("别名"))
        alias_top.addWidget(self.alias_value)
        alias_top.addWidget(add_alias)
        alias_top.addWidget(delete_alias)
        alias_layout.addLayout(alias_top)
        self.alias_table = QTableWidget(0, 4)
        self.alias_table.setHorizontalHeaderLabels(["ID", "正式名", "别名", "创建时间"])
        self.alias_table.setColumnHidden(0, True)
        self.alias_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.alias_table.horizontalHeader().setStretchLastSection(True)
        self.alias_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alias_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        alias_layout.addWidget(self.alias_table)
        layout.addWidget(alias_box)

        exports = QHBoxLayout()
        export_json = QPushButton("导出数据库JSON")
        import_json_btn = QPushButton("从JSON恢复")
        export_sources = QPushButton("导出材料出处CSV")
        export_recipes = QPushButton("导出配方CSV")
        export_upgrades = QPushButton("导出升级CSV")
        export_prices = QPushButton("导出价格CSV")
        for button in (export_json, import_json_btn, export_sources, export_recipes, export_upgrades, export_prices):
            exports.addWidget(button)
        exports.addStretch(1)
        layout.addLayout(exports)

        browse.clicked.connect(self.choose_excel_path)
        do_import.clicked.connect(self.import_excel)
        source_refresh.clicked.connect(self.refresh_source_table)
        self.source_search.returnPressed.connect(self.refresh_source_table)
        add_source.clicked.connect(self.add_manual_source_item)
        delete_source.clicked.connect(self.delete_selected_source_items)
        self.alias_search.returnPressed.connect(self.refresh_alias_table)
        add_alias.clicked.connect(self.add_alias)
        delete_alias.clicked.connect(self.delete_alias)
        export_json.clicked.connect(self.export_database_json)
        import_json_btn.clicked.connect(self.import_database_json)
        export_sources.clicked.connect(lambda: self.export_csv_file("材料出处CSV", "stoneage_sources.csv", self.db.export_sources_csv))
        export_recipes.clicked.connect(lambda: self.export_csv_file("配方CSV", "stoneage_recipes.csv", self.db.export_recipes_csv))
        export_upgrades.clicked.connect(lambda: self.export_csv_file("升级CSV", "stoneage_upgrades.csv", self.db.export_upgrades_csv))
        export_prices.clicked.connect(lambda: self.export_csv_file("价格CSV", "stoneage_prices.csv", self.db.export_prices_csv))
        return panel

    def choose_excel_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 Excel 出处表", str(Path.home()), "Excel (*.xlsx)")
        if path:
            self.excel_path.setText(path)

    def import_excel(self) -> None:
        path = self.excel_path.text().strip()
        if not path:
            QMessageBox.information(self, "导入 Excel", "请先选择 .xlsx 文件。")
            return
        try:
            summary = import_excel_sources(self.db, path, mode=str(self.excel_mode.currentData()))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入 Excel", str(exc))
            return
        self.import_summary.setText(summary.as_text().replace("\n", "  "))
        self.refresh_material_table()
        self.refresh_material_source_table()
        self.refresh_source_table()
        self.refresh_recipe_table()
        QMessageBox.information(self, "导入 Excel", summary.as_text())

    def export_public_material_site(self) -> None:
        from .material_site_export import export_material_site

        path = self.public_site_output_dir()
        try:
            output = export_material_site(self.db.workspace, path)
            self.db.set_setting("public_site_output_dir", str(output))
            self.public_site_path.setText(str(output))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导出官方网站", str(exc))
            return
        QMessageBox.information(
            self,
            "导出官方网站",
            f"已导出官方网站文件：\n{output}\n\n上传整个文件夹即可给别人查看。",
        )

    def choose_public_site_path(self) -> None:
        current = self.public_site_output_dir()
        current.mkdir(parents=True, exist_ok=True)
        path = QFileDialog.getExistingDirectory(self, "选择官方网站同步目录", str(current))
        if path:
            self.public_site_path.setText(path)
            self.db.set_setting("public_site_output_dir", path)

    def sync_public_material_site(self) -> None:
        from .material_site_export import sync_material_site

        output_dir = self.public_site_output_dir()
        try:
            result = sync_material_site(self.db.workspace, output_dir, push=True)
            self.db.set_setting("public_site_output_dir", str(result.output_dir))
            self.public_site_path.setText(str(result.output_dir))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "同步更新官网", str(exc))
            return
        QMessageBox.information(
            self,
            "同步更新官网",
            f"{result.message}\n\n目录：{result.output_dir}",
        )

    def public_site_output_dir(self) -> Path:
        text = self.public_site_path.text().strip() if hasattr(self, "public_site_path") else ""
        return Path(text).expanduser() if text else self.db.workspace / "data" / "public_material_site"

    def refresh_source_table(self) -> None:
        if not hasattr(self, "source_table"):
            return
        rows = self.db.search_source_items_by_source(self.source_search.text().strip() if hasattr(self, "source_search") else "", limit=1000)
        self.source_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("id"),
                row.get("item_name"),
                row.get("source_name"),
                row.get("raw_text"),
                _fmt_qty(row.get("parsed_quantity")),
                row.get("sheet_name"),
                row.get("row_index"),
                row.get("col_index"),
            ]
            for col, value in enumerate(values):
                self.source_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))

    def add_manual_source_item(self) -> None:
        item_name, ok = QInputDialog.getItem(self, "手动新增出处关系", "材料名", self.item_names(), editable=True)
        if not ok or not item_name.strip():
            return
        source_name, ok = QInputDialog.getText(self, "手动新增出处关系", "出处名称")
        if not ok or not source_name.strip():
            return
        qty, ok = QInputDialog.getDouble(self, "手动新增出处关系", "可能数量（不确定可填 0）", 0, 0, 999999, 2)
        if not ok:
            return
        self.db.add_source_item(
            item_name=item_name,
            raw_text=f"{item_name}{_fmt_qty(qty) if qty else ''}",
            source_name=source_name,
            parsed_quantity=qty if qty > 0 else None,
            source_type="manual",
            notes="手动新增",
            skip_duplicate=False,
        )
        self.refresh_source_table()

    def delete_selected_source_items(self) -> None:
        ids = [_item_int(self.source_table, row, 0) for row in sorted({index.row() for index in self.source_table.selectionModel().selectedRows()})]
        ids = [item for item in ids if item]
        if not ids:
            QMessageBox.information(self, "删除出处关系", "请先选中要删除的行。")
            return
        if QMessageBox.question(self, "删除出处关系", f"确定删除 {len(ids)} 条出处关系？不会删除物品本身。") != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_source_items(ids)
        self.refresh_source_table()

    def refresh_alias_table(self) -> None:
        if not hasattr(self, "alias_table"):
            return
        rows = self.db.list_aliases(self.alias_search.text().strip() if hasattr(self, "alias_search") else "")
        self.alias_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col, value in enumerate([row.get("id"), row.get("item_name"), row.get("alias"), row.get("created_at")]):
                self.alias_table.setItem(row_index, col, QTableWidgetItem(str(value or "")))

    def add_alias(self) -> None:
        try:
            self.db.add_alias(self.alias_item.text(), self.alias_value.text())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "新增别名", str(exc))
            return
        self.alias_value.clear()
        self.refresh_alias_table()

    def delete_alias(self) -> None:
        row = self.alias_table.currentRow()
        alias_id = _item_int(self.alias_table, row, 0) if row >= 0 else None
        if not alias_id:
            QMessageBox.information(self, "删除别名", "请先选中别名。")
            return
        self.db.delete_alias(alias_id)
        self.refresh_alias_table()

    def export_database_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出数据库JSON", str(Path.cwd() / "stoneage_materials_backup.json"), "JSON (*.json)")
        if path:
            self.db.export_json(path)
            QMessageBox.information(self, "导出数据库JSON", f"已导出：{path}")

    def import_database_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "从JSON恢复", str(Path.cwd()), "JSON (*.json)")
        if not path:
            return
        if QMessageBox.question(self, "从JSON恢复", "恢复会覆盖当前材料库数据库。确定继续？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.db.import_json(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "从JSON恢复", str(exc))
            return
        self.refresh_everything()
        QMessageBox.information(self, "从JSON恢复", "已恢复数据库。")

    def export_csv_file(self, title: str, default_name: str, callback: Callable[[str], None]) -> None:
        path, _ = QFileDialog.getSaveFileName(self, f"导出{title}", str(Path.cwd() / default_name), "CSV (*.csv)")
        if path:
            callback(path)
            QMessageBox.information(self, f"导出{title}", f"已导出：{path}")

    # Settings tab
    def _build_settings_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        box = QGroupBox("全局设置")
        form = QFormLayout(box)
        self.settings_ratio = QDoubleSpinBox()
        self.settings_ratio.setRange(0.0001, 999999999)
        self.settings_ratio.setDecimals(4)
        self.settings_ratio.setValue(500)
        save = QPushButton("保存设置")
        form.addRow("每 1 RMB 对应钻石数", self.settings_ratio)
        form.addRow("默认值", QLabel("500 钻 = 1 RMB"))
        form.addRow(save)
        layout.addWidget(box)
        layout.addStretch(1)
        save.clicked.connect(self.save_settings)
        return panel

    def save_settings(self) -> None:
        try:
            self.db.set_diamond_per_rmb(float(self.settings_ratio.value()))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存设置", str(exc))
            return
        if hasattr(self, "material_ratio"):
            self.material_ratio.setValue(float(self.db.diamond_per_rmb()))
        self.refresh_query_ratio()
        self.refresh_material_table()
        self.refresh_price_table()
        QMessageBox.information(self, "保存设置", "钻石兑换人民币比例已更新。")

    # Shared material table helpers
    def _material_edit_table(self) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["材料名称", "数量", "当前单价钻", "备注"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setItemDelegateForColumn(0, MaterialNameDelegate(self.item_names, table))
        table.itemChanged.connect(
            lambda item, source=table: self.update_material_price_cell(source, item.row())
            if item.column() == 0
            else None
        )
        return table

    def add_material_row(self, table: QTableWidget, name: str = "", quantity: float = 1, notes: str = "") -> None:
        if not name:
            name, ok = QInputDialog.getItem(self, "添加材料", "材料名称", self.item_names(), editable=True)
            if not ok:
                return
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(name))
        table.setItem(row, 1, QTableWidgetItem(_fmt_qty(quantity)))
        table.setItem(row, 2, QTableWidgetItem(""))
        table.setItem(row, 3, QTableWidgetItem(notes))
        self.update_material_price_cell(table, row)

    def delete_material_rows(self, table: QTableWidget) -> None:
        self.commit_material_table_editor(table)
        selected = table.selectionModel()
        rows = {index.row() for index in selected.selectedRows()} if selected else set()
        if not rows and selected:
            rows = {index.row() for index in selected.selectedIndexes()}
        if not rows and table.currentRow() >= 0:
            rows = {table.currentRow()}
        rows = sorted(rows, reverse=True)
        for row in rows:
            table.removeRow(row)

    def commit_material_table_editor(self, table: QTableWidget) -> None:
        widget = QApplication.focusWidget()
        if widget is not None and table.isAncestorOf(widget):
            table.setFocus()

    def load_material_rows(self, table: QTableWidget, rows: list[dict[str, Any]]) -> None:
        table.setRowCount(0)
        for row in rows:
            self.add_material_row(
                table,
                name=str(row.get("material_name") or ""),
                quantity=float(row.get("quantity") or 1),
                notes=str(row.get("notes") or ""),
            )

    def material_rows(self, table: QTableWidget) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in range(table.rowCount()):
            name = _item_text(table, row, 0).strip()
            if not name:
                continue
            try:
                quantity = float(_item_text(table, row, 1) or 0)
            except ValueError:
                quantity = 0
            if quantity <= 0:
                continue
            rows.append({"material_name": name, "quantity": quantity, "notes": _item_text(table, row, 3)})
        return rows

    def update_material_price_cell(self, table: QTableWidget, row: int) -> None:
        if row < 0 or row >= table.rowCount():
            return
        name = _item_text(table, row, 0).strip()
        price = self.db.get_price(name) if name else None
        price_text = _fmt_qty(price.get("price_diamonds")) if price else "暂无价格"
        if table.item(row, 2) is None:
            table.setItem(row, 2, QTableWidgetItem(price_text))
        else:
            table.item(row, 2).setText(price_text)

    def _completer(self) -> QCompleter:
        completer = QCompleter(self.item_names(), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        return completer


def _item_text(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text() if item else ""


def _item_int(table: QTableWidget, row: int, col: int) -> int | None:
    try:
        return int(_item_text(table, row, col))
    except (TypeError, ValueError):
        return None


def _fmt_qty(value: Any) -> str:
    if value in {None, ""}:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _money(value: float, db: MaterialDatabase) -> str:
    return f"{_fmt_qty(value)}钻 / {db.diamonds_to_rmb(value):.2f} RMB"


def _source_text(value: Any) -> str:
    return str(value or "").replace(",", " / ")


def _quantity_list_text(value: Any) -> str:
    parts = []
    for item in str(value or "").split(","):
        text = _fmt_qty(item.strip())
        if text and text not in parts:
            parts.append(text)
    return " / ".join(parts)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items() if key != "text"}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
