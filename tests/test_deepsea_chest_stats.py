from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stoneage_studio.deepsea_chest import (
    DEEPSEA_6F_CHEST_ITEMS,
    DEEPSEA_TICKET_DIAMOND_PRICE,
    build_item_stats,
    build_profit_summary,
)
from stoneage_studio.storage import ProjectStorage


class DeepSeaChestStatsTests(unittest.TestCase):
    def test_records_can_be_added_updated_deleted_and_summarized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(Path(tmp))
            skill = "技能碎片"
            shell = "深海贝壳"

            first_id = storage.add_deepsea_chest_record(item_name=skill, quantity=3, record_date="2026-06-11", note="1号")
            storage.add_deepsea_chest_record(item_name=skill, quantity=1, record_date="2026-06-11", note="2号")
            storage.add_deepsea_chest_record(item_name=shell, quantity=1, record_date="2026-06-11", note="3号")

            totals = storage.deepsea_chest_totals()
            self.assertEqual(totals[skill], 4)
            self.assertEqual(totals[shell], 1)
            self.assertEqual(len(storage.list_deepsea_chest_records()), 3)

            storage.update_deepsea_chest_record(first_id, item_name=skill, quantity=5, record_date="2026-06-11", note="改")
            totals = storage.deepsea_chest_totals()
            self.assertEqual(totals[skill], 6)

            self.assertEqual(storage.delete_deepsea_chest_records([first_id]), 1)
            totals = storage.deepsea_chest_totals()
            self.assertEqual(totals[skill], 1)
            self.assertEqual(totals[shell], 1)

    def test_chest_items_can_be_managed_without_code_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(Path(tmp))

            seeded_items = storage.deepsea_chest_item_names()
            self.assertEqual(seeded_items[:2], DEEPSEA_6F_CHEST_ITEMS[:2])

            item_id = storage.add_deepsea_chest_item(item_name=" 新道具  ", diamond_price=1234, note="第一次出现")
            self.assertIn("新道具", storage.deepsea_chest_item_names())
            self.assertEqual(storage.deepsea_chest_item_prices()["新道具"], 1234)
            with self.assertRaises(ValueError):
                storage.add_deepsea_chest_item(item_name="新道具")

            storage.add_deepsea_chest_record(item_name="新道具", quantity=2, record_date="2026-06-11")
            storage.update_deepsea_chest_item(item_id, item_name="新道具改名", diamond_price=1500, note="已更新")
            totals = storage.deepsea_chest_totals()
            self.assertNotIn("新道具", totals)
            self.assertEqual(totals["新道具改名"], 2)
            self.assertEqual(storage.deepsea_chest_item_prices()["新道具改名"], 1500)
            storage.update_deepsea_chest_item(item_id, item_name="新道具改名", note="只改备注")
            self.assertEqual(storage.deepsea_chest_item_prices()["新道具改名"], 1500)

            rows = storage.list_deepsea_chest_items()
            ids = [str(row["id"]) for row in rows]
            storage.reorder_deepsea_chest_items([ids[-1], *ids[:-1]])
            self.assertEqual(storage.list_deepsea_chest_items()[0]["id"], ids[-1])

            self.assertEqual(storage.delete_deepsea_chest_items([item_id]), 1)
            self.assertNotIn("新道具改名", storage.deepsea_chest_item_names())
            self.assertEqual(storage.deepsea_chest_totals()["新道具改名"], 2)

    def test_item_stats_follow_fixed_item_order_and_rates(self) -> None:
        stats = build_item_stats({"技能碎片": 3, "魔神石": 1}, DEEPSEA_6F_CHEST_ITEMS)
        by_name = {row.item_name: row for row in stats}
        self.assertEqual(by_name["技能碎片"].quantity, 3)
        self.assertAlmostEqual(by_name["技能碎片"].rate, 0.75)
        self.assertEqual(stats[0].item_name, "焰狱魔兽自选")

    def test_item_stats_keep_recorded_items_missing_from_current_catalog(self) -> None:
        stats = build_item_stats({"旧道具": 2, "技能碎片": 3}, ["技能碎片"])
        self.assertEqual([row.item_name for row in stats], ["技能碎片", "旧道具"])
        self.assertEqual(stats[1].quantity, 2)

    def test_profit_summary_uses_item_prices_ticket_cost_and_rmb_rate(self) -> None:
        summary = build_profit_summary(
            {"技能碎片": 2, "深海贝壳": 1},
            {"技能碎片": 5000, "深海贝壳": 1000},
            ["技能碎片", "深海贝壳"],
        )
        self.assertEqual(summary.total_quantity, 3)
        self.assertEqual(summary.ticket_diamond_price, DEEPSEA_TICKET_DIAMOND_PRICE)
        self.assertEqual(summary.total_revenue_diamonds, 11000)
        self.assertEqual(summary.total_cost_diamonds, 3 * DEEPSEA_TICKET_DIAMOND_PRICE)
        self.assertAlmostEqual(summary.revenue_cost_ratio or 0, 11000 / (3 * DEEPSEA_TICKET_DIAMOND_PRICE))

    def test_deepsea_chest_economy_settings_default_to_550_diamonds_per_rmb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(Path(tmp))
            settings = storage.deepsea_chest_settings()
            self.assertEqual(settings["ticket_diamond_price"], DEEPSEA_TICKET_DIAMOND_PRICE)
            self.assertEqual(settings["diamond_per_rmb"], 550)

            storage.update_deepsea_chest_settings(diamond_per_rmb=600)
            self.assertEqual(storage.deepsea_chest_settings()["diamond_per_rmb"], 600)


if __name__ == "__main__":
    unittest.main()
