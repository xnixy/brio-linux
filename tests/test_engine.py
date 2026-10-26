import errno
import unittest

from briod.engine import drift
from briod.models import brio
from tests.fakedev import fake_camera


class Apply(unittest.TestCase):
    def setUp(self):
        self.cam = fake_camera()
        self.dev = self.cam.dev

    def test_capabilities_and_firmware(self):
        caps = self.cam.capabilities()
        self.assertEqual(caps["fov"]["values"], [90, 78, 65])
        self.assertIn("mic", caps)
        self.assertEqual(self.cam.firmware, "2.0.68")

    def test_ghub_write_order(self):
        s = {t: dict(brio.DEFAULT_SETTINGS[t]) for t in brio.TABS}
        s["crop"]["fov"] = 78
        errs = self.cam.apply(s, device_settings={"antiFlicker": 50, "microphone": False})
        self.assertEqual(errs, [])
        names = [n for n, _ in self.dev.log]
        crop = names.index("fov")
        image = names.index("brightness")
        exposure = names.index("auto_exposure")
        focus = names.index("focus_automatic_continuous")
        self.assertLess(names.index("pan_absolute"), names.index("zoom_absolute"))
        self.assertLess(crop, image)
        self.assertLess(image, exposure)
        self.assertLess(exposure, focus)
        self.assertEqual(self.dev.xu[(10, brio.SEL_FOV)], 1)
        self.assertEqual(self.dev.values["power_line_frequency"], 1)
        self.assertEqual(self.dev.xu[(11, brio.SEL_MIC)], 0)

    def test_one_bad_control_does_not_stop_the_rest(self):
        self.dev.fail["brightness"] = [errno.EIO, errno.EIO]
        errs = self.cam.apply({"image": {"brightness": {"value": 10}, "contrast": {"value": 20}}})
        self.assertEqual(len(errs), 1)
        self.assertIn("brightness", errs[0])
        self.assertEqual(self.dev.values["contrast"], 20)

    def test_retry_once_on_ebusy(self):
        self.dev.fail["contrast"] = [errno.EBUSY]
        self.assertEqual(self.cam.apply({"image": {"contrast": {"value": 20}}}), [])
        self.assertEqual(self.dev.values["contrast"], 20)

    def test_no_retry_on_einval(self):
        self.dev.fail["contrast"] = [errno.EINVAL]
        self.assertEqual(len(self.cam.apply({"image": {"contrast": {"value": 20}}})), 1)

    def test_resolutions_filtered(self):
        ids = [r["id"] for r in self.cam.resolutions()]
        self.assertEqual(ids, ["RES_4Kp30", "RES_1080p60", "RES_1080p30", "RES_720p60", "RES_720p30",
                               "RES_360p30"])

    def test_drift(self):
        want = {"image": dict(brio.DEFAULT_SETTINGS["image"]),
                "focus": dict(brio.DEFAULT_SETTINGS["focus"])}
        self.cam.apply(want)
        self.assertEqual(drift(want, self.cam.snapshot()), {})
        self.dev.values["brightness"] = 50          # changed by another tool
        self.dev.values["focus_absolute"] = 100     # irrelevant while autofocus is on
        self.assertEqual(drift(want, self.cam.snapshot()), {"image": {"brightness": {"value": 50}}})


if __name__ == "__main__":
    unittest.main()
