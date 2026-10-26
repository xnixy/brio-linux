import unittest

from briod.models import brio
from tests.fakedev import BRIO_CTRLS

CAPS = {n: {"minimum": a, "maximum": b} for n, (a, b, _s, _d) in BRIO_CTRLS.items()}


class Conversions(unittest.TestCase):
    def test_exposure_table_matches_ghub_mac_column(self):
        self.assertEqual(brio.exposure_to_raw(2000), 5)
        self.assertEqual(brio.exposure_to_raw(60), 156)
        self.assertEqual(brio.exposure_to_raw(17), 2047)
        for ui, raw in brio.EXPOSURE_TABLE:
            self.assertEqual(brio.exposure_from_raw(raw), ui)

    def test_gain(self):
        self.assertEqual(brio.gain_to_raw(100), 0)
        self.assertEqual(brio.gain_to_raw(400), 48)
        self.assertEqual(brio.gain_to_raw(1600), 240)
        for ui in brio.GAIN_UI:
            self.assertEqual(brio.gain_from_raw(brio.gain_to_raw(ui)), ui)

    def test_percent_roundtrip(self):
        # 255/100*50 is 127.49999… in IEEE doubles, so G HUB's JS Math.round gives 127 too.
        self.assertEqual(brio.pct_to_raw(50, 0, 255), 127)
        self.assertEqual(brio.js_round(2.5), 3)          # half-up, not Python's half-even
        self.assertEqual(brio.raw_to_pct(128, 0, 255), 50)
        self.assertEqual(brio.pct_to_raw(0, 0, 255), 0)
        self.assertEqual(brio.pct_to_raw(100, 0, 255), 255)

    def test_firmware_format(self):
        self.assertEqual(brio.format_firmware(bytes.fromhex("00024400")), "2.0.68")


class PlanWrites(unittest.TestCase):
    def names(self, tab, s):
        return [(n, v) for _k, n, v in brio.plan_writes(tab, s, CAPS)]

    def test_auto_written_before_manual_value(self):
        w = self.names("image", {"whiteBalance": {"mode": "MANUAL", "value": 5000}})
        self.assertEqual(w, [("white_balance_automatic", 0), ("white_balance_temperature", 5000)])
        w = self.names("exposure", {"exposure": {"mode": "MANUAL", "value": 200}})
        self.assertEqual(w, [("auto_exposure", 1), ("exposure_time_absolute", 39)])
        w = self.names("focus", {"focus": {"mode": "MANUAL", "value": 50}})
        self.assertEqual(w, [("focus_automatic_continuous", 0), ("focus_absolute", 50)])

    def test_auto_skips_value(self):
        self.assertEqual(self.names("focus", {"focus": {"mode": "AUTO", "value": 50}}),
                         [("focus_automatic_continuous", 1)])

    def test_default_exposure_card(self):
        w = dict(self.names("exposure", brio.DEFAULT_SETTINGS["exposure"]))
        self.assertEqual(w, {"auto_exposure": 3, "exposure_dynamic_framerate": 1, "gain": 0,
                             "backlight_compensation": 1})

    def test_fov_goes_to_xu(self):
        w = brio.plan_writes("crop", {"fov": 65}, CAPS)
        self.assertEqual(w, [("xu", "fov", 2)])

    def test_partial_crop_only_writes_given_keys(self):
        self.assertEqual(self.names("crop", {"zoom": {"value": 200}}), [("zoom_absolute", 200)])

    def test_values_clamped_to_device_range(self):
        self.assertEqual(self.names("crop", {"pan": {"value": 72000}}), [("pan_absolute", 36000)])

    def test_missing_capability_skipped(self):
        caps = dict(CAPS)
        del caps["backlight_compensation"]
        w = [n for _k, n, _v in brio.plan_writes("exposure", {"hdr": True}, caps)]
        self.assertEqual(w, [])

    def test_settings_from_device_roundtrip(self):
        vals = {"zoom_absolute": 150, "pan_absolute": 0, "tilt_absolute": 0, "fov": 1,
                "auto_exposure": 1, "exposure_time_absolute": 39, "gain": 48,
                "backlight_compensation": 0, "exposure_dynamic_framerate": 0}
        s = brio.settings_from_device(vals)
        self.assertEqual(s["crop"]["fov"], 78)
        self.assertEqual(s["exposure"]["exposure"], {"mode": "MANUAL", "value": 200})
        self.assertEqual(s["exposure"]["gain"], {"value": 400})
        self.assertFalse(s["exposure"]["hdr"])
        self.assertEqual(s["exposure"]["priority"], brio.PRIORITY_FRAME_RATE)


if __name__ == "__main__":
    unittest.main()
