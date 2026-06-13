from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stoneage_studio.deepsea_chest import DEEPSEA_6F_CHEST_ITEMS, build_item_stats
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

    def test_item_stats_follow_fixed_item_order_and_rates(self) -> None:
        stats = build_item_stats({"技能碎片": 3, "魔神石": 1}, DEEPSEA_6F_CHEST_ITEMS)
        by_name = {row.item_name: row for row in stats}
        self.assertEqual(by_name["技能碎片"].quantity, 3)
        self.assertAlmostEqual(by_name["技能碎片"].rate, 0.75)
        self.assertEqual(stats[0].item_name, "焰狱魔兽自选")


if __name__ == "__main__":
    unittest.main()
