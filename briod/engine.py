"""Apply engine: turns per-tab settings into ordered device writes."""
from __future__ import annotations

import errno
import logging

from . import uvc
from .models import brio

log = logging.getLogger("briod.engine")

APPLY_ORDER = ("crop", "image", "exposure", "focus")   # _applyCameraSettings order


class Camera:
    """One connected BRIO: an open control fd plus capability data."""

    def __init__(self, info: uvc.CameraInfo, device_cls=uvc.Device):
        self.info = info
        self.dev = device_cls(info.devnode)
        self.ctrls = self.dev.query_controls()
        self.caps = {n: {"minimum": c.minimum, "maximum": c.maximum, "step": c.step,
                         "default": c.default, "menu": c.menu} for n, c in self.ctrls.items()}
        self.xu = {
            "fov": self.dev.xu_unit_for(brio.XU_VIDEOPIPE),
            "mic": self.dev.xu_unit_for(brio.XU_PERIPHERAL),
            "info": self.dev.xu_unit_for(brio.XU_DEVICEINFO),
        }
        self.firmware = ""
        if self.xu["info"] is not None:
            try:
                self.firmware = brio.format_firmware(
                    self.dev.xu_get(self.xu["info"], brio.SEL_FIRMWARE, 4))
            except uvc.UVCError as e:
                log.info("firmware read failed: %s", e)
        try:
            self.formats = self.dev.enum_formats()
        except uvc.UVCError:
            self.formats = []

    @property
    def slot(self) -> str:
        return self.info.slot

    def close(self):
        self.dev.close()

    # -------------------------------------------------------------- caps
    def capabilities(self) -> dict:
        """What the UI may render (a control renders only if the device has it)."""
        c = dict(self.caps)
        if self.xu["fov"] is not None:
            c["fov"] = {"values": sorted(brio.FOV_TO_RAW, reverse=True)}
        if self.xu["mic"] is not None:
            c["mic"] = {}
        return c

    def resolutions(self) -> list[dict]:
        """G HUB resolution list filtered by what the camera enumerates."""
        out = []
        for rid, label, w, h, fps in brio.RESOLUTIONS:
            for fmt in ("MJPG", "YUYV"):
                f = next((f for f in self.formats if f["fourcc"] == fmt), None)
                if not f:
                    continue
                size = next((s for s in f["sizes"] if s["width"] == w and s["height"] == h), None)
                if size and any(abs(r - fps) < 0.5 for r in size["fps"]):
                    out.append({"id": rid, "label": label, "width": w, "height": h,
                                "fps": fps, "fourcc": fmt})
                    break
        return out

    # ------------------------------------------------------------- writes
    def write(self, kind: str, name: str, raw: int):
        if kind == "v4l2":
            uvc.retry(self.dev.set, name, raw)
        elif kind == "xu":
            unit = self.xu.get(name)
            if unit is None:
                raise uvc.UVCError(errno.ENOTSUP, f"{name} extension unit not found")
            sel = brio.SEL_FOV if name == "fov" else brio.SEL_MIC
            uvc.retry(self.dev.xu_set, unit, sel, bytes([raw]))

    def read(self, kind: str, name: str) -> int:
        if kind == "v4l2":
            return self.dev.get(name)
        sel = brio.SEL_FOV if name == "fov" else brio.SEL_MIC
        return self.dev.xu_get(self.xu[name], sel, 1)[0]

    def apply(self, settings: dict[str, dict], tabs=APPLY_ORDER,
              device_settings: dict | None = None) -> list[str]:
        """Apply cards in G HUB order.  One failing control never stops the rest.
        Returns a list of error strings."""
        errors = []
        writes = []
        for tab in APPLY_ORDER:
            if tab in tabs and tab in settings:
                writes += brio.plan_writes(tab, settings[tab], self.caps)
        if device_settings is not None:
            af = device_settings.get("antiFlicker")
            if af in brio.ANTI_FLICKER and "power_line_frequency" in self.caps:
                writes.append(("v4l2", "power_line_frequency", brio.ANTI_FLICKER[af]))
            mic = device_settings.get("microphone")
            if mic is not None and self.xu["mic"] is not None:
                writes.append(("xu", "mic", 1 if mic else 0))
        for kind, name, raw in writes:
            try:
                self.write(kind, name, raw)
            except uvc.UVCError as e:
                msg = f"{name}={raw}: {e.strerror or e}"
                log.warning("%s: %s", self.slot, msg)
                errors.append(msg)
                continue
            if kind == "xu":
                try:
                    got = self.read(kind, name)
                    if got != raw:
                        errors.append(f"{name}: wrote {raw}, device reports {got}")
                except uvc.UVCError:
                    pass
        return errors

    def snapshot(self) -> dict[str, int]:
        """Current raw values of every control we manage (V4L2 + XU)."""
        vals = {}
        for name in self.caps:
            if name in uvc.CID:
                try:
                    vals[name] = self.dev.get(name)
                except uvc.UVCError:
                    pass
        for name in ("fov", "mic"):
            if self.xu[name] is not None:
                try:
                    vals[name] = self.read("xu", name)
                except uvc.UVCError:
                    pass
        return vals

    def watch_ctrl_events(self):
        self.dev.subscribe_ctrl_events([n for n in self.caps if n in uvc.CID])
        return self.dev.fd

    def pending_ctrl_events(self) -> list[tuple[str, int]]:
        return self.dev.dequeue_events()


def drift(expected: dict[str, dict], device_values: dict) -> dict[str, dict]:
    """Per-tab settings whose device value differs from what the profile wants.

    Only keys that differ are returned, so they can be layered over the card
    as a "manual adjustment" (G HUB's *_MANUAL_ADJUSTMENT behaviour).
    """
    actual = brio.settings_from_device(device_values)
    out: dict[str, dict] = {}
    for tab, exp in expected.items():
        act = actual.get(tab)
        if not act:
            continue
        for key, a in act.items():
            e = exp.get(key)
            if e is None:
                continue
            if _differs(tab, key, e, a):
                out.setdefault(tab, {})[key] = a
    return out


def _differs(tab, key, e, a) -> bool:
    em, am = brio.setting_mode(e, None), brio.setting_mode(a, None)
    ev, av = brio.setting_value(e), brio.setting_value(a)
    if em is not None and am is not None and em != am:
        return True
    if em == "AUTO":                       # value is irrelevant while auto
        return False
    if tab == "exposure" and key == "exposure":
        return brio.exposure_to_raw(ev) != brio.exposure_to_raw(av)
    if tab == "exposure" and key == "gain":
        return brio.gain_to_raw(ev) != brio.gain_to_raw(av)
    return ev != av
