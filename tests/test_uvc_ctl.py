import unittest
import uuid
from pathlib import Path

from briod import ctl, uvc
from briod.models import brio

FIXTURE = Path(__file__).parent / "fixtures" / "brio_descriptors.bin"


class Descriptors(unittest.TestCase):
    def test_brio_extension_units(self):
        units = {str(u.guid).upper(): u for u in uvc.parse_extension_units(FIXTURE.read_bytes())}
        self.assertEqual(units[brio.XU_VIDEOPIPE].unit_id, 10)
        self.assertEqual(units[brio.XU_PERIPHERAL].unit_id, 11)
        self.assertEqual(units[brio.XU_DEVICEINFO].unit_id, 8)
        self.assertIn(brio.SEL_FOV, units[brio.XU_VIDEOPIPE].selectors())
        self.assertIn(brio.SEL_MIC, units[brio.XU_PERIPHERAL].selectors())
        # DeviceInfo has no colour-code selector (0x0B) on this firmware
        self.assertNotIn(0x0B, units[brio.XU_DEVICEINFO].selectors())

    def test_ignores_non_videocontrol_cs_descriptors(self):
        # A CS_INTERFACE subtype 0x06 inside a non-VC interface must not parse as an XU.
        iface = bytes([9, 0x04, 0, 0, 0, 0x01, 0x01, 0, 0])            # audio control
        fake = bytes([24, 0x24, 0x06, 99]) + uuid.uuid4().bytes_le + bytes([0, 1, 0, 0])
        self.assertEqual(uvc.parse_extension_units(iface + fake), [])

    def test_ioctl_numbers(self):
        self.assertEqual(uvc.UVCIOC_CTRL_QUERY, 0xC0107521)
        self.assertEqual(uvc.VIDIOC_QUERYCTRL, 0xC0445624)
        self.assertEqual(uvc.VIDIOC_S_CTRL, 0xC008561C)


class CtlParsing(unittest.TestCase):
    def test_plan_example(self):
        changes, device = ctl.parse_assignments(["fov=78", "hdr=off", "zoom=150"])
        self.assertEqual(changes, [("crop", "fov", 78), ("exposure", "hdr", False),
                                   ("crop", "zoom.value", 150)])
        self.assertEqual(device, [])

    def test_auto_and_manual(self):
        changes, _ = ctl.parse_assignments(["wb=auto", "focus=40"])
        self.assertEqual(changes, [("image", "whiteBalance.mode", "AUTO"),
                                   ("focus", "focus.mode", "MANUAL"), ("focus", "focus.value", 40)])

    def test_device_keys(self):
        _, device = ctl.parse_assignments(["antiflicker=50", "mic=off"])
        self.assertEqual(device, [("antiFlicker", 50), ("microphone", False)])

    def test_unknown(self):
        with self.assertRaises(ValueError):
            ctl.parse_assignments(["bogus=1"])


if __name__ == "__main__":
    unittest.main()
