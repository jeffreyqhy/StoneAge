from __future__ import annotations

import unittest

from stoneage_studio.flow import create_step


class FlowStepTests(unittest.TestCase):
    def test_click_step_has_hold_seconds(self) -> None:
        step = create_step("click", "长按点击")

        self.assertEqual(step["input"]["hold_seconds"], 0.0)

    def test_swipe_step_defaults_to_up_swipe(self) -> None:
        step = create_step("swipe", "上滑列表")

        self.assertEqual(step["label"], "滑动")
        self.assertEqual(step["input"]["direction"], "up")
        self.assertEqual(step["input"]["start_coord"], [960, 780])
        self.assertEqual(step["input"]["end_coord"], [960, 300])
        self.assertEqual(step["input"]["duration_seconds"], 0.45)


if __name__ == "__main__":
    unittest.main()
