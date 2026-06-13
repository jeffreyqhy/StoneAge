import tempfile
import unittest

from stoneage_studio.flow import create_flow, create_step, save_flow
from stoneage_studio.storage import ProjectStorage


class PresetScopeTests(unittest.TestCase):
    def test_global_and_script_presets_are_combined_without_bleeding_between_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            global_preset = storage.upsert_project_preset(
                {
                    "name": "打开设置",
                    "steps": [create_step("click", "点击设置")],
                    "repeat_count": 1,
                }
            )
            script_preset = storage.upsert_script_preset(
                "财神副本",
                {
                    "name": "财神专用",
                    "steps": [create_step("wait", "等财神")],
                    "repeat_count": 1,
                },
            )

            new_script_presets = storage.load_combined_script_presets("新副本")["presets"]
            self.assertEqual([preset["name"] for preset in new_script_presets], ["打开设置"])
            self.assertEqual(new_script_presets[0]["_preset_scope"], "global")

            current_script_presets = storage.load_combined_script_presets("财神副本")["presets"]
            self.assertEqual({preset["name"] for preset in current_script_presets}, {"打开设置", "财神专用"})

            storage.delete_script_preset("财神副本", script_preset["id"])
            self.assertTrue(storage.load_combined_script_presets("财神副本")["presets"])
            storage.delete_project_preset(global_preset["id"])
            self.assertFalse(storage.load_combined_script_presets("财神副本")["presets"])


class ScriptOrderTests(unittest.TestCase):
    def test_saved_script_order_controls_flow_listing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            for name in ["A副本", "B副本", "C副本"]:
                save_flow(storage.script_flow_file(name), create_flow(name))

            initial_rows = storage.list_script_flows()
            keys = {row["script_name"]: row["order_key"] for row in initial_rows}
            storage.save_script_order([keys["B副本"], keys["A副本"]])

            ordered_rows = storage.list_script_flows()
            self.assertEqual([row["script_name"] for row in ordered_rows[:2]], ["B副本", "A副本"])
            self.assertIn("C副本", [row["script_name"] for row in ordered_rows])

    def test_delete_script_flow_removes_it_from_saved_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            save_flow(storage.script_flow_file("A副本"), create_flow("A副本"))
            save_flow(storage.script_flow_file("B副本"), create_flow("B副本"))
            rows = storage.list_script_flows()
            keys = {row["script_name"]: row["order_key"] for row in rows}
            storage.save_script_order([keys["A副本"], keys["B副本"]])

            storage.delete_script_flow_path(storage.script_flow_file("A副本"))

            self.assertEqual(storage.load_script_order(), [keys["B副本"]])


class StudioStateTests(unittest.TestCase):
    def test_last_flow_path_is_persisted_independently_from_script_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            save_flow(storage.script_flow_file("财神副本"), create_flow("财神副本"))
            save_flow(storage.script_flow_file("深海1-4自动"), create_flow("深海1-4自动"))
            rows = storage.list_script_flows()
            keys = {row["script_name"]: row["order_key"] for row in rows}
            storage.save_script_order([keys["财神副本"], keys["深海1-4自动"]])

            deepsea_path = storage.script_flow_file("深海1-4自动")
            storage.save_last_flow_path(deepsea_path)

            self.assertEqual(storage.load_last_flow_path().resolve(), deepsea_path.resolve())
            self.assertEqual(storage.list_script_flows()[0]["script_name"], "财神副本")


if __name__ == "__main__":
    unittest.main()
