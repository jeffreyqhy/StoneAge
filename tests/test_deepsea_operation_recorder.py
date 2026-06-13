from __future__ import annotations

import unittest

from stoneage_studio.deepsea_operation_recorder import (
    TouchDeviceInfo,
    classify_operation,
    map_raw_point,
    parse_getevent_line,
    parse_surface_orientation,
    parse_touch_device,
)


GETEVENT_LP = """
add device 2: /dev/input/event1
  name:     "Xiaomi Input"
  events:
    ABS (0003): ABS_MT_SLOT           : value 1, min 0, max 31, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X     : value 0, min 0, max 1080, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 1920, fuzz 0, flat 0, resolution 0
                ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
  input props:
    INPUT_PROP_DIRECT
"""


class DeepSeaOperationRecorderTests(unittest.TestCase):
    def test_parse_touch_device_and_orientation(self) -> None:
        device = parse_touch_device(GETEVENT_LP, "SurfaceOrientation: 1")
        self.assertIsNotNone(device)
        self.assertEqual(device.path, "/dev/input/event1")
        self.assertEqual(device.max_x, 1080)
        self.assertEqual(device.max_y, 1920)
        self.assertEqual(device.orientation, 1)
        self.assertEqual(parse_surface_orientation("orientation: 3"), 3)

    def test_parse_getevent_line(self) -> None:
        self.assertEqual(parse_getevent_line("[ 1.0] EV_ABS ABS_MT_POSITION_X 00000320"), ("x", 800))
        self.assertEqual(parse_getevent_line("[ 1.0] EV_ABS ABS_MT_TRACKING_ID ffffffff"), ("tracking_id", -1))
        self.assertEqual(parse_getevent_line("[ 1.0] EV_KEY BTN_TOUCH DOWN"), ("touch", 1))
        self.assertEqual(parse_getevent_line("[ 1.0] EV_SYN SYN_REPORT 00000000"), ("syn", 0))

    def test_map_rotated_mumu_raw_point_to_screen(self) -> None:
        device = TouchDeviceInfo("/dev/input/event1", max_x=1080, max_y=1920, orientation=1)
        self.assertEqual(map_raw_point(1080, 0, device=device, screen_size=(1920, 1080)), [0, 0])
        self.assertEqual(map_raw_point(0, 1920, device=device, screen_size=(1920, 1080)), [1919, 1079])

    def test_classify_operation(self) -> None:
        self.assertEqual(classify_operation([[100, 100], [105, 108]], 0.1), "tap")
        self.assertEqual(classify_operation([[100, 100], [100, 240]], 0.2), "swipe")
        self.assertEqual(classify_operation([[100, 100], [105, 108]], 0.5), "swipe")


if __name__ == "__main__":
    unittest.main()
