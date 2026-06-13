from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from stoneage_studio.material_db import MaterialCalculator, MaterialDatabase, import_excel_sources
from stoneage_studio.material_db.calculator import attempts_for_confidence
from stoneage_studio.material_db.normalizer import normalize_item_name, parse_item_quantity
from stoneage_studio.material_db.price_calculator import net_after_trade_tax, required_trade_gross_for_net, trade_tax_amount


class MaterialDbTests(unittest.TestCase):
    def make_db(self, root: Path) -> MaterialDatabase:
        return MaterialDatabase(root, root / "data" / "stoneage_materials.db")

    def test_normalize_and_parse_quantities(self) -> None:
        self.assertEqual(normalize_item_name("  玛蕾菲雅　（绑定） "), "玛蕾菲雅 (绑定)")
        cases = {
            "技能碎片3个": ("技能碎片", 3),
            "洗练神石10个": ("洗练神石", 10),
            "2个满石79玛蕾菲亚": ("满石79玛蕾菲亚", 2),
            "绑定钻石800": ("绑定钻石", 800),
        }
        for raw, expected in cases.items():
            parsed = parse_item_quantity(raw)
            self.assertEqual(parsed.item_name, expected[0])
            self.assertEqual(parsed.parsed_quantity, expected[1])

    def test_excel_import_and_source_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xlsx = root / "sources.xlsx"
            make_test_xlsx(
                xlsx,
                {
                    "宝箱": [
                        ["副本A", "世界树大箱"],
                        ["技能碎片3个", "洗练神石10个"],
                        ["2个满石79玛蕾菲亚", "绑定钻石800"],
                    ]
                },
            )
            db = self.make_db(root)
            summary = import_excel_sources(db, xlsx, mode="merge")
            self.assertEqual(summary.record_count, 4)
            rows = db.search_sources("技能碎片")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_name"], "副本A")
            self.assertEqual(rows[0]["parsed_quantity"], 3)
            rows = db.search_sources("满石79玛蕾菲亚")
            self.assertEqual(rows[0]["item_name"], "满石79玛蕾菲亚")

    def test_price_ratio_csv_and_json_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.make_db(root)
            self.assertEqual(db.diamond_per_rmb(), 500)
            db.set_diamond_per_rmb(400)
            self.assertAlmostEqual(db.diamonds_to_rmb(800), 2.0)
            db.set_diamond_per_rmb(600)
            self.assertAlmostEqual(db.diamonds_to_rmb(1200), 2.0)
            db.set_price("材料A", 100, price_source="测试")
            db.set_price("成品A", 5000, price_source="测试")

            csv_path = root / "prices.csv"
            db.export_prices_csv(csv_path)
            db2 = self.make_db(root / "restore_csv")
            imported = db2.import_prices_csv(csv_path)
            self.assertEqual(imported, 2)
            self.assertEqual(db2.get_price("材料A")["price_diamonds"], 100)

            json_path = root / "backup.json"
            db.export_json(json_path)
            db3 = self.make_db(root / "restore_json")
            db3.import_json(json_path)
            self.assertEqual(db3.get_price("成品A")["price_diamonds"], 5000)
            self.assertAlmostEqual(db3.diamond_per_rmb(), 600)

    def test_trade_tax_calculator_rounds_to_guarantee_net(self) -> None:
        self.assertEqual(required_trade_gross_for_net(10000), 10526)
        self.assertEqual(net_after_trade_tax(10526), 10000)
        self.assertEqual(trade_tax_amount(10526), 526)
        self.assertEqual(required_trade_gross_for_net(9968), 10492)
        self.assertEqual(net_after_trade_tax(10492), 9968)
        self.assertEqual(trade_tax_amount(10492), 524)
        self.assertEqual(net_after_trade_tax(10000), 9500)
        self.assertEqual(trade_tax_amount(10000), 500)
        self.assertEqual(required_trade_gross_for_net(0), 0)
        with self.assertRaises(ValueError):
            required_trade_gross_for_net(10000, 1)

    def test_material_list_links_sources_prices_and_rmb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.make_db(root)
            db.add_source_item(item_name="中级强化宝石", raw_text="中级强化宝石", source_name="世界树大箱", source_type="manual")
            db.add_source_item(item_name="中级强化宝石", raw_text="中级强化宝石", source_name="机械副本困难", source_type="manual")
            db.add_source_item(item_name="没有价格的材料", raw_text="没有价格的材料", source_name="商店", source_type="manual")
            db.set_diamond_per_rmb(400)
            db.set_price("中级强化宝石", 800, price_source="手动录入")

            rows = db.list_materials("中级")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "中级强化宝石")
            self.assertEqual(rows[0]["price_diamonds"], 800)
            self.assertIn("世界树大箱", rows[0]["source_names"])
            self.assertIn("机械副本困难", rows[0]["source_names"])
            self.assertAlmostEqual(db.diamonds_to_rmb(rows[0]["price_diamonds"]), 2.0)

            no_price = db.list_materials("没有价格的材料")
            self.assertEqual(len(no_price), 1)
            self.assertIsNone(no_price[0]["price_diamonds"])
            self.assertEqual(no_price[0]["source_names"], "商店")

    def test_source_drop_query_lists_materials_from_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.make_db(root)
            db.add_source_item(item_name="中级强化宝石", raw_text="中级强化宝石", source_name="机械副本困难", parsed_quantity=1, source_type="manual")
            db.add_source_item(item_name="高级强化宝石", raw_text="高级强化宝石", source_name="机械副本困难", parsed_quantity=2, source_type="manual")
            db.add_source_item(item_name="世界树叶", raw_text="世界树叶", source_name="世界树大箱", parsed_quantity=3, source_type="manual")
            db.set_price("中级强化宝石", 500)
            db.set_diamond_per_rmb(250)

            source_names = db.all_source_names("机械")
            self.assertEqual(source_names, ["机械副本困难"])

            rows = db.list_source_drops("机械副本")
            self.assertEqual([row["item_name"] for row in rows], ["中级强化宝石", "高级强化宝石"])
            self.assertEqual(rows[0]["source_name"], "机械副本困难")
            self.assertEqual(rows[0]["quantities"], "1.0")
            self.assertEqual(rows[0]["price_diamonds"], 500)
            self.assertAlmostEqual(db.diamonds_to_rmb(rows[0]["price_diamonds"]), 2.0)

    def test_material_and_source_search_stay_separated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.make_db(root)
            db.add_source_item(item_name="中级强化宝石", raw_text="中级强化宝石", source_name="机械副本困难", parsed_quantity=1, source_type="manual")
            db.add_source_item(item_name="世界树叶", raw_text="世界树叶", source_name="世界树大箱", parsed_quantity=2, source_type="manual")
            db.set_price("中级强化宝石", 800, price_source="机械副本困难")

            self.assertEqual(db.list_materials("机械副本困难"), [])
            self.assertEqual([row["name"] for row in db.list_materials("中级")], ["中级强化宝石"])
            self.assertEqual(db.list_source_drops("中级强化宝石"), [])
            self.assertEqual(
                [row["item_name"] for row in db.search_source_items_by_source("机械副本")],
                ["中级强化宝石"],
            )
            self.assertEqual(db.search_source_items_by_source("中级强化宝石"), [])
            self.assertEqual(db.search_sources("中级强化宝石")[0]["source_name"], "机械副本困难")

    def test_recipe_calculation_success_rates_and_missing_prices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.make_db(root)
            db.add_source_item(item_name="材料A", raw_text="材料A", source_name="副本普通", source_type="manual")
            db.add_source_item(item_name="材料B", raw_text="材料B", source_name="机械副本困难", source_type="manual")
            db.set_price("材料A", 100)
            db.set_price("材料B", 250)
            db.set_price("魔神枪", 5000)
            recipe_id = db.save_recipe(
                {
                    "product_name": "魔神枪",
                    "category": "装备",
                    "recipe_type": "打造",
                    "success_rate": 1.0,
                    "output_quantity": 1,
                    "diamond_cost": 0,
                },
                [
                    {"material_name": "材料A", "quantity": 1},
                    {"material_name": "材料B", "quantity": 1},
                    {"material_name": "材料C", "quantity": 1},
                ],
            )
            calc = MaterialCalculator(db)
            result = calc.recipe_cost("魔神枪", confidence=0.95)
            self.assertEqual(result["materials"][0]["standard_quantity"], 1)
            self.assertEqual(result["costs"]["standard"]["material_diamonds"], 350)
            self.assertIn("暂无价格", result["text"])
            self.assertEqual(result["profit_diamonds"]["standard"], 4650)

            db.save_recipe(
                {
                    "product_name": "半成品",
                    "success_rate": 0.5,
                    "output_quantity": 1,
                    "diamond_cost": 10,
                    "failure_consumes_materials": True,
                    "failure_consumes_diamonds": True,
                },
                [{"material_name": "材料A", "quantity": 1}],
            )
            half = calc.recipe_cost("半成品")
            self.assertEqual(half["materials"][0]["expected_quantity"], 2)
            self.assertEqual(half["costs"]["expected"]["direct_diamonds"], 20)

            db.save_recipe(
                {
                    "product_name": "四分之一成品",
                    "success_rate": 0.25,
                    "output_quantity": 1,
                },
                [{"material_name": "材料A", "quantity": 1}],
            )
            quarter = calc.recipe_cost("四分之一成品", confidence=0.95)
            self.assertEqual(quarter["materials"][0]["expected_quantity"], 4)
            self.assertEqual(quarter["attempts"]["safe"], attempts_for_confidence(1, 0.25, 0.95))
            self.assertGreaterEqual(quarter["materials"][0]["safe_quantity"], 4)

            db.save_recipe(
                {"product_name": "无价成品", "success_rate": 1.0},
                [{"material_name": "材料A", "quantity": 1}],
            )
            no_product_price = calc.recipe_cost("无价成品")
            self.assertIsNone(no_product_price["product_price"])
            self.assertIn("暂无成品价格", no_product_price["text"])
            self.assertIsNotNone(db.get_recipe(recipe_id))
            self.assertEqual(db.list_recipes("魔神枪")[0]["product_name"], "魔神枪")

    def test_upgrade_calculation_and_missing_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.make_db(root)
            db.set_price("升级石", 100)
            for level in range(12, 20):
                db.save_upgrade_step(
                    {
                        "equipment_name": "某某装备",
                        "from_level": level,
                        "to_level": level + 1,
                        "success_rate": 0.5,
                        "diamond_cost": 100,
                        "failure_consumes_materials": True,
                        "failure_consumes_diamonds": True,
                    },
                    [{"material_name": "升级石", "quantity": 1}],
                )
            calc = MaterialCalculator(db)
            result = calc.upgrade_cost("某某装备", 12, 20, confidence=0.95)
            self.assertEqual(result["materials"][0]["standard_quantity"], 8)
            self.assertEqual(result["materials"][0]["expected_quantity"], 16)
            self.assertEqual(result["costs"]["standard"]["direct_diamonds"], 800)
            self.assertEqual(result["costs"]["expected"]["direct_diamonds"], 1600)
            self.assertGreater(result["costs"]["safe"]["total_rmb"], 0)
            self.assertEqual(len(db.list_upgrade_steps("某某装备")), 8)

            db.delete_upgrade_step(db.find_upgrade_step("某某装备", 15, 16)["id"])
            missing = calc.upgrade_cost("某某装备", 12, 20)
            self.assertIn("15 -> 16", missing["missing_steps"])
            self.assertIn("缺少升级资料", missing["text"])

    def test_upgrade_expands_intermediate_equipment_and_compares_market_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.make_db(root)
            db.set_price("合成手环1", 700)
            db.set_price("合成手环4", 1000)
            db.set_price("初级强化宝石", 150)
            db.set_price("中级强化宝石", 550)
            db.set_price("高级强化宝石", 6000)
            for level, stone, success in (
                (1, "初级强化宝石", 0.8),
                (2, "中级强化宝石", 0.7),
                (3, "中级强化宝石", 0.6),
                (4, "高级强化宝石", 0.5),
            ):
                db.save_upgrade_step(
                    {
                        "equipment_name": "合成手环",
                        "from_level": level,
                        "to_level": level + 1,
                        "success_rate": success,
                        "diamond_cost": 10,
                        "failure_consumes_materials": True,
                        "failure_consumes_diamonds": True,
                    },
                    [
                        {"material_name": f"合成手环{level}", "quantity": 2},
                        {"material_name": stone, "quantity": 1},
                    ],
                )

            result = MaterialCalculator(db).upgrade_cost("合成手环", 1, 5, confidence=0.95)
            quantities = {row["material_name"]: row["standard_quantity"] for row in result["materials"]}
            self.assertEqual(quantities["合成手环1"], 16)
            self.assertEqual(quantities["初级强化宝石"], 8)
            self.assertEqual(quantities["中级强化宝石"], 6)
            self.assertEqual(quantities["高级强化宝石"], 1)
            self.assertNotIn("合成手环2", quantities)
            self.assertNotIn("合成手环3", quantities)
            self.assertNotIn("合成手环4", quantities)
            self.assertEqual(result["costs"]["standard"]["direct_diamonds"], 150)
            level4 = next(row for row in result["market_comparisons"] if row["level"] == 4)
            self.assertEqual(level4["market_total_diamonds"], 1000)
            self.assertLess(level4["difference_diamonds"]["safe"], 0)
            self.assertEqual(result["recommended_route"]["entry_level"], 4)
            self.assertIn("买 合成手环4 后升到 5 级", result["recommended_route"]["label"])
            self.assertIn("底层材料清单", result["text"])
            self.assertIn("最终路线对比", result["text"])


def make_test_xlsx(path: Path, sheets: dict[str, list[list[str]]]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
""" + "".join(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for index in range(1, len(sheets) + 1)
            ) + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        sheet_entries = []
        rel_entries = []
        for index, sheet_name in enumerate(sheets, 1):
            sheet_entries.append(f'<sheet name="{sheet_name}" sheetId="{index}" r:id="rId{index}"/>')
            rel_entries.append(
                f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
            )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>""" + "".join(sheet_entries) + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">"""
            + "".join(rel_entries)
            + "</Relationships>",
        )
        for index, rows in enumerate(sheets.values(), 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(rows))


def sheet_xml(rows: list[list[str]]) -> str:
    xml_rows = []
    for r_index, row in enumerate(rows, 1):
        cells = []
        for c_index, value in enumerate(row, 1):
            col = column_name(c_index)
            escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            cells.append(f'<c r="{col}{r_index}" t="inlineStr"><is><t>{escaped}</t></is></c>')
        xml_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )


def column_name(index: int) -> str:
    chars = []
    value = int(index)
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


if __name__ == "__main__":
    unittest.main()
