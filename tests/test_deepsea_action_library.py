from __future__ import annotations

import tempfile
import unittest

from stoneage_studio.deepsea_action_library import (
    ActorRef,
    DeepSeaActionLibrary,
    action_id,
    normalize_action,
    normalize_actor,
    suggest_step_label,
    suggested_step_labels,
)
from stoneage_studio.storage import ProjectStorage


class DeepSeaActionLibraryTests(unittest.TestCase):
    def test_normalizes_actor_and_action_aliases(self) -> None:
        self.assertEqual(normalize_actor("1号人物"), ActorRef("role", 1))
        self.assertEqual(normalize_actor("角色2"), ActorRef("role", 2))
        self.assertEqual(normalize_actor("4号宠物"), ActorRef("pet", 4))
        self.assertEqual(normalize_actor("道具"), ActorRef("item", None))

        self.assertEqual(normalize_action("放风"), "wind_spirit_l5")
        self.assertEqual(normalize_action("风的精灵LV.5"), "wind_spirit_l5")
        self.assertEqual(normalize_action("恩惠6"), "grace_l6")
        self.assertEqual(normalize_action("神之撕裂"), "divine_rip")
        self.assertEqual(normalize_action("PK用豪华船生鱼片"), "luxury_sashimi")

    def test_add_step_replaces_same_label_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            library = DeepSeaActionLibrary(storage.deepsea_action_library_path())
            first = library.add_step(
                actor="1号人物",
                action="放风",
                step_label="打开咒术",
                template_path="assets/maps/map_001/button/wind_step_1.png",
                bbox=[100, 200, 80, 40],
                click_offset=[40, 20],
            )
            second = library.add_step(
                actor="角色1",
                action="风的精灵LV5",
                step_label="打开咒术",
                template_path="assets/maps/map_001/button/wind_step_1_retry.png",
                bbox=[101, 201, 80, 40],
                click_offset=[41, 21],
            )
            library.save()

            self.assertEqual(first["id"], second["id"])
            action = library.get_action("1号人物", "放风")
            self.assertIsNotNone(action)
            self.assertEqual(len(action["steps"]), 1)
            self.assertEqual(action["steps"][0]["template_path"], "assets/maps/map_001/button/wind_step_1_retry.png")

            reloaded = DeepSeaActionLibrary(storage.deepsea_action_library_path())
            sequence = reloaded.build_click_sequence("1号人物", "放风")
            self.assertEqual(len(sequence), 1)
            self.assertEqual(sequence[0]["click_offset"], [41, 21])

    def test_missing_required_actions_counts_only_actions_with_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            library = DeepSeaActionLibrary(storage.deepsea_action_library_path())
            required = (
                ("role", 1, "wind_spirit_l5"),
                ("role", 2, "wind_spirit_l5"),
                ("pet", 1, "divine_rip"),
            )

            self.assertEqual(len(library.missing_required_actions(required)), 3)
            library.add_step(actor="1号人物", action="放风", step_label="打开咒术", template_path="wind.png")

            missing = library.missing_required_actions(required)
            self.assertEqual(
                [action_id(ActorRef(row["actor_type"], row["actor_index"]), row["action_key"]) for row in missing],
                ["role:2:wind_spirit_l5", "pet:1:divine_rip"],
            )

    def test_suggests_next_action_step_label(self) -> None:
        self.assertEqual(suggested_step_labels("放风"), ("打开咒术", "滚动技能列表", "选择风的精灵", "选择目标/确认"))
        self.assertEqual(suggest_step_label("放风", []), "打开咒术")
        self.assertEqual(suggest_step_label("放风", ["打开咒术"]), "滚动技能列表")
        self.assertEqual(suggest_step_label("放风", ["打开咒术", "滚动技能列表"]), "选择风的精灵")
        self.assertEqual(suggest_step_label("放风", ["打开咒术", "滚动技能列表", "选择风的精灵", "选择目标/确认"]), "步骤 5")

    def test_swipe_step_is_included_in_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            library = DeepSeaActionLibrary(storage.deepsea_action_library_path())
            library.add_step(
                actor="1号人物",
                action="放风",
                step_label="滚动技能列表",
                step_type="swipe",
                swipe_start=[1500, 780],
                swipe_end=[1500, 430],
                duration_ms=450,
            )

            sequence = library.build_click_sequence("1号人物", "放风")
            self.assertEqual(sequence[0]["type"], "swipe")
            self.assertEqual(sequence[0]["swipe_start"], [1500, 780])
            self.assertEqual(sequence[0]["swipe_end"], [1500, 430])

    def test_recorded_tap_step_is_included_in_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = ProjectStorage(tmp)
            library = DeepSeaActionLibrary(storage.deepsea_action_library_path())
            library.add_step(
                actor="3号人物",
                action="恩惠6",
                step_label="选择恩惠",
                step_type="recorded_tap",
                click_point=[1400, 320],
            )

            sequence = library.build_click_sequence("3号人物", "恩惠6")
            self.assertEqual(sequence[0]["type"], "tap")
            self.assertEqual(sequence[0]["click_point"], [1400, 320])


if __name__ == "__main__":
    unittest.main()
