"""Logitech BRIO (046d:085e): control map, value conversions, built-in cards.

Values follow G HUB's capability data (devices_0002.json, modelId "brio") and
its card JSON shape (proto3 JSON of logi.protocol.webcam.*Settings).
"""
from __future__ import annotations

import math

VID, PIDS = "046d", ("085e",)
MODEL = "brio"
DISPLAY_NAME = "Logitech BRIO"

TABS = ("crop", "exposure", "image", "focus")

# --- extension units -------------------------------------------------------
XU_VIDEOPIPE = "49E40215-F434-47FE-B158-0E885023E51B"
XU_PERIPHERAL = "FFE52D21-8030-4E2C-82D9-F587D00540BD"
XU_DEVICEINFO = "69678EE4-410F-40DB-A850-7420D7D8240E"
SEL_FOV = 0x05
SEL_MIC = 0x10
SEL_FIRMWARE = 0x01

FOV_TO_RAW = {90: 0, 78: 1, 65: 2}
RAW_TO_FOV = {v: k for k, v in FOV_TO_RAW.items()}

# --- exposure / gain -------------------------------------------------------
# UI value (shutter 1/s) → exposure_time_absolute (100 µs units, G HUB "mac" column)
EXPOSURE_TABLE = [(2000, 5), (800, 10), (400, 20), (200, 39), (120, 78),
                  (60, 156), (30, 312), (24, 625), (20, 1250), (17, 2047)]
EXPOSURE_UI_TO_RAW = dict(EXPOSURE_TABLE)

GAIN_UI = list(range(100, 1700, 100))


def exposure_to_raw(ui: int) -> int:
    if ui in EXPOSURE_UI_TO_RAW:
        return EXPOSURE_UI_TO_RAW[ui]
    return min(EXPOSURE_TABLE, key=lambda e: abs(e[0] - ui))[1]


def exposure_from_raw(raw: int) -> int:
    return min(EXPOSURE_TABLE, key=lambda e: abs(e[1] - raw))[0]


def gain_to_raw(ui: int) -> int:
    ui = max(100, min(1600, int(ui)))
    return (ui - 100) // 100 * 16


def gain_from_raw(raw: int) -> int:
    return max(100, min(1600, round(raw / 16) * 100 + 100))


def js_round(x: float) -> int:
    """JavaScript Math.round (half up), as G HUB's UI uses; Python rounds half to even."""
    return math.floor(x + 0.5)


def pct_to_raw(pct: float, lo: int, hi: int) -> int:
    return js_round((hi - lo) / 100 * pct + lo)


def raw_to_pct(raw: int, lo: int, hi: int) -> int:
    return js_round((raw - lo) / (hi - lo) * 100) if hi != lo else 0


PRIORITY_FRAME_RATE = "PRIORITY_FRAME_RATE"
PRIORITY_EXPOSURE = "PRIORITY_EXPOSURE"
ANTI_FLICKER = {50: 1, 60: 2}

# Resolutions offered in the preview picker (G HUB list), filtered at runtime
# against VIDIOC_ENUM_FRAMESIZES/INTERVALS.  Frame rate is pinned explicitly:
# in the BRIO's 720p90 mode the firmware ignores zoom entirely.
RESOLUTIONS = [
    ("RES_4Kp30", "4K 30 fps", 3840, 2160, 30),
    ("RES_2Kp30", "1440p 30 fps", 2560, 1440, 30),
    ("RES_1080p60", "1080p 60 fps", 1920, 1080, 60),
    ("RES_1080p30", "1080p 30 fps", 1920, 1080, 30),
    ("RES_720p60", "720p 60 fps", 1280, 720, 60),
    ("RES_720p30", "720p 30 fps", 1280, 720, 30),
    ("RES_360p30", "360p 30 fps", 640, 360, 30),
    ("RES_240p30", "240p 30 fps", 320, 240, 30),
]
DEFAULT_RESOLUTION = "RES_720p30"

# --- cards -----------------------------------------------------------------
# A card holds the settings for one tab.  Entries that can be AUTO carry
# {"mode", "value"}; others are {"value"} or plain scalars, as in G HUB.
DEFAULT_SETTINGS = {
    "crop": {"fov": 90, "zoom": {"value": 100}, "pan": {"value": 0}, "tilt": {"value": 0}},
    "exposure": {"exposure": {"mode": "AUTO", "value": 60}, "gain": {"value": 100},
                 "hdr": True, "priority": PRIORITY_EXPOSURE},
    "image": {"brightness": {"value": 128}, "contrast": {"value": 128},
              "saturation": {"value": 128}, "sharpness": {"value": 128},
              "whiteBalance": {"mode": "AUTO", "value": 4000}},
    "focus": {"focus": {"mode": "AUTO", "value": 0}},
}


def _with(tab: str, **changes) -> dict:
    import copy
    s = copy.deepcopy(DEFAULT_SETTINGS[tab])
    for k, v in changes.items():
        s[k] = v
    return s


BUILTIN_CARDS = [
    {"id": "default:crop", "tab": "crop", "name": "Default", "readOnly": True,
     "settings": _with("crop")},
    {"id": "preset:fov78", "tab": "crop", "name": "FOV 78°", "readOnly": True,
     "settings": _with("crop", fov=78)},
    {"id": "preset:fov65", "tab": "crop", "name": "FOV 65°", "readOnly": True,
     "settings": _with("crop", fov=65)},
    {"id": "preset:zoom150", "tab": "crop", "name": "Zoom 1.5x", "readOnly": True,
     "settings": _with("crop", zoom={"value": 150})},
    {"id": "preset:zoom200", "tab": "crop", "name": "Zoom 2x", "readOnly": True,
     "settings": _with("crop", zoom={"value": 200})},
    {"id": "default:exposure", "tab": "exposure", "name": "Default", "readOnly": True,
     "settings": _with("exposure")},
    {"id": "preset:streaming", "tab": "exposure", "name": "Streaming", "readOnly": True,
     "settings": _with("exposure", priority=PRIORITY_FRAME_RATE)},
    {"id": "default:image", "tab": "image", "name": "Default", "readOnly": True,
     "settings": _with("image")},
    {"id": "preset:bw", "tab": "image", "name": "Black & White", "readOnly": True,
     "settings": _with("image", saturation={"value": 0})},
    {"id": "default:focus", "tab": "focus", "name": "Default", "readOnly": True,
     "settings": _with("focus")},
]

DEFAULT_DEVICE_SETTINGS = {
    "antiFlicker": 60,
    "stopVideoOnAppFocusLoss": True,
    "disabled": False,           # "Disable device in app" (broadcast mode)
    "microphone": None,          # None = leave untouched
    "preview": {"mirror": False, "grid": False, "preview": True,
                "resolution": DEFAULT_RESOLUTION},
}


def setting_value(entry, default=None):
    if isinstance(entry, dict):
        return entry.get("value", default)
    return entry if entry is not None else default


def setting_mode(entry, default="MANUAL"):
    if isinstance(entry, dict):
        return entry.get("mode", default)
    return default


def plan_writes(tab: str, s: dict, caps: dict) -> list[tuple[str, str, int]]:
    """Translate a card into ordered device writes: (kind, name, raw).

    kind is "v4l2" (name = V4L2 control) or "xu" (name = fov|mic).
    `caps` maps V4L2 control name → CtrlInfo-like dict (minimum/maximum);
    controls the device doesn't have are skipped.  Auto controls are written
    before the value they gate.
    """
    w: list[tuple[str, str, int]] = []

    def v4l2(name, raw):
        if name in caps:
            c = caps[name]
            raw = max(c["minimum"], min(c["maximum"], int(raw)))
            w.append(("v4l2", name, raw))

    # Only keys present in `s` are written, so a partial card applies one control.
    if tab == "crop":
        if "pan" in s:
            v4l2("pan_absolute", setting_value(s["pan"], 0))
        if "tilt" in s:
            v4l2("tilt_absolute", setting_value(s["tilt"], 0))
        if "zoom" in s:
            v4l2("zoom_absolute", setting_value(s["zoom"], 100))
        fov = s.get("fov")
        if fov in FOV_TO_RAW:
            w.append(("xu", "fov", FOV_TO_RAW[fov]))
    elif tab == "image":
        for key, ctl in (("brightness", "brightness"), ("contrast", "contrast"),
                         ("saturation", "saturation"), ("sharpness", "sharpness")):
            if key in s:
                v4l2(ctl, setting_value(s[key]))
        wb = s.get("whiteBalance")
        if wb is not None:
            auto = setting_mode(wb, "AUTO") == "AUTO"
            v4l2("white_balance_automatic", 1 if auto else 0)
            if not auto:
                v4l2("white_balance_temperature", setting_value(wb, 4000))
    elif tab == "exposure":
        ex = s.get("exposure")
        if ex is not None:
            auto = setting_mode(ex, "AUTO") == "AUTO"
            v4l2("auto_exposure", 3 if auto else 1)
            if not auto:
                v4l2("exposure_time_absolute", exposure_to_raw(setting_value(ex, 60)))
        if "priority" in s:
            v4l2("exposure_dynamic_framerate",
                 1 if s["priority"] == PRIORITY_EXPOSURE else 0)
        if "gain" in s:
            v4l2("gain", gain_to_raw(setting_value(s["gain"], 100)))
        if "hdr" in s:
            v4l2("backlight_compensation", 1 if s["hdr"] else 0)
    elif tab == "focus":
        f = s.get("focus")
        if f is not None:
            auto = setting_mode(f, "AUTO") == "AUTO"
            v4l2("focus_automatic_continuous", 1 if auto else 0)
            if not auto:
                v4l2("focus_absolute", setting_value(f, 0))
    return w


def settings_from_device(values: dict) -> dict[str, dict]:
    """Inverse of plan_writes: device raw values → per-tab settings (for drift)."""
    out = {}
    g = values.get
    if "zoom_absolute" in values:
        out["crop"] = {"pan": {"value": g("pan_absolute", 0)}, "tilt": {"value": g("tilt_absolute", 0)},
                       "zoom": {"value": g("zoom_absolute")}}
        if "fov" in values:
            out["crop"]["fov"] = RAW_TO_FOV.get(values["fov"], 90)
    if "auto_exposure" in values:
        auto = values["auto_exposure"] != 1
        out["exposure"] = {
            "exposure": {"mode": "AUTO" if auto else "MANUAL",
                         "value": exposure_from_raw(g("exposure_time_absolute", 156))},
            "gain": {"value": gain_from_raw(g("gain", 0))},
            "hdr": bool(g("backlight_compensation", 1)),
            "priority": PRIORITY_EXPOSURE if g("exposure_dynamic_framerate", 1) else PRIORITY_FRAME_RATE,
        }
    if "brightness" in values:
        out["image"] = {k: {"value": g(k)} for k in ("brightness", "contrast", "saturation", "sharpness")}
        out["image"]["whiteBalance"] = {"mode": "AUTO" if g("white_balance_automatic", 1) else "MANUAL",
                                        "value": g("white_balance_temperature", 4000)}
    if "focus_automatic_continuous" in values:
        out["focus"] = {"focus": {"mode": "AUTO" if g("focus_automatic_continuous") else "MANUAL",
                                  "value": g("focus_absolute", 0)}}
    return out


def format_firmware(raw: bytes) -> str:
    """DeviceInfo selector 1 (4 bytes).  Observed 00 02 44 00 on a BRIO; G HUB
    shows this style as major.minor.build → 2.0.68."""
    if len(raw) < 3:
        return raw.hex()
    return f"{raw[1]}.{raw[0]}.{raw[2] | (raw[3] << 8 if len(raw) > 3 else 0)}"
