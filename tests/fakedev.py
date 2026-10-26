"""A fake V4L2/UVC device that mimics the BRIO's reported controls (Linux 7.3)."""
import errno

from briod import uvc
from briod.models import brio

BRIO_CTRLS = {
    # name: (min, max, step, default)
    "brightness": (0, 255, 1, 128), "contrast": (0, 255, 1, 128), "saturation": (0, 255, 1, 128),
    "white_balance_automatic": (0, 1, 1, 1), "gain": (0, 255, 1, 0),
    "power_line_frequency": (0, 2, 1, 2), "white_balance_temperature": (2000, 7500, 10, 4000),
    "sharpness": (0, 255, 1, 128), "backlight_compensation": (0, 1, 1, 1),
    "auto_exposure": (0, 3, 1, 3), "exposure_time_absolute": (3, 2047, 1, 250),
    "exposure_dynamic_framerate": (0, 1, 1, 0), "pan_absolute": (-36000, 36000, 3600, 0),
    "tilt_absolute": (-36000, 36000, 3600, 0), "focus_absolute": (0, 255, 5, 0),
    "focus_automatic_continuous": (0, 1, 1, 1), "zoom_absolute": (100, 500, 1, 100),
}
AUTO_GATED = {"white_balance_temperature": ("white_balance_automatic", 1),
              "exposure_time_absolute": ("auto_exposure", 3),
              "focus_absolute": ("focus_automatic_continuous", 1)}


class FakeDevice:
    def __init__(self, devnode="/dev/fake0"):
        self.devnode = devnode
        self.fd = -1
        self.values = {n: c[3] for n, c in BRIO_CTRLS.items()}
        self.xu = {(10, brio.SEL_FOV): 0, (11, brio.SEL_MIC): 1}
        self.log = []                 # ordered writes: (name, value)
        self.fail = {}                # name → list of errnos to raise, consumed in order

    def close(self):
        pass

    def query_controls(self):
        return {n: uvc.CtrlInfo(uvc.CID[n], n, 1, a, b, s, d, 0) for n, (a, b, s, d) in BRIO_CTRLS.items()}

    def xu_unit_for(self, guid):
        return {brio.XU_VIDEOPIPE: 10, brio.XU_PERIPHERAL: 11, brio.XU_DEVICEINFO: 8}.get(guid)

    def xu_get(self, unit, sel, size=None):
        if (unit, sel) == (8, brio.SEL_FIRMWARE):
            return bytes.fromhex("00024400")
        return bytes([self.xu[(unit, sel)]])

    def xu_set(self, unit, sel, data):
        name = "fov" if sel == brio.SEL_FOV else "mic"
        self._maybe_fail(name)
        self.xu[(unit, sel)] = data[0]
        self.log.append((name, data[0]))

    def enum_formats(self):
        return [{"fourcc": "MJPG", "description": "Motion-JPEG", "sizes": [
            {"width": 3840, "height": 2160, "fps": [30.0]},
            {"width": 1920, "height": 1080, "fps": [60.0, 30.0]},
            {"width": 1280, "height": 720, "fps": [90.0, 60.0, 30.0]},
            {"width": 640, "height": 360, "fps": [30.0]}]}]

    def _maybe_fail(self, name):
        errs = self.fail.get(name)
        if errs:
            e = errs.pop(0)
            raise uvc.UVCError(e, f"fake {errno.errorcode[e]}")

    def get(self, name):
        return self.values[name]

    def set(self, name, value):
        self._maybe_fail(name)
        gate = AUTO_GATED.get(name)
        if gate and self.values[gate[0]] == gate[1]:
            raise uvc.UVCError(errno.EACCES, "inactive while auto is on")
        self.values[name] = value
        self.log.append((name, value))

    def subscribe_ctrl_events(self, names):
        pass

    def dequeue_events(self):
        return []


def fake_camera(**kw):
    from briod.engine import Camera
    info = uvc.CameraInfo("/dev/fake0", ["/dev/fake0", "/dev/fake1"], "046d", "085e",
                          "TEST1234", "Logitech BRIO", "/sys/fake")
    return Camera(info, device_cls=FakeDevice)
