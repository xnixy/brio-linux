"""brio-ctl: set controls / apply profiles from the command line.

Talks to briod over D-Bus when it is running (so the daemon records the change
in the active profile instead of treating it as drift); otherwise writes the
device directly.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import uvc
from .engine import Camera
from .models import brio
from .store import Store

# CLI key → (tab, settings key, converter)
def _onoff(v):
    if v.lower() in ("1", "on", "true", "yes"):
        return True
    if v.lower() in ("0", "off", "false", "no"):
        return False
    raise ValueError(f"expected on/off, got {v!r}")


def _auto_or_int(v):
    return ("mode", "AUTO") if v.lower() == "auto" else ("value", int(v.rstrip("Kk%")))


KEYS = {
    "fov": ("crop", "fov", int),
    "zoom": ("crop", "zoom.value", int),
    "pan": ("crop", "pan.value", int),
    "tilt": ("crop", "tilt.value", int),
    "exposure": ("exposure", "exposure", _auto_or_int),
    "shutter": ("exposure", "exposure", _auto_or_int),
    "gain": ("exposure", "gain.value", int),
    "iso": ("exposure", "gain.value", int),
    "hdr": ("exposure", "hdr", _onoff),
    "lowlight": ("exposure", "priority",
                 lambda v: brio.PRIORITY_EXPOSURE if _onoff(v) else brio.PRIORITY_FRAME_RATE),
    "priority": ("exposure", "priority",
                 lambda v: brio.PRIORITY_FRAME_RATE if v.lower().startswith("frame") else brio.PRIORITY_EXPOSURE),
    "brightness": ("image", "brightness.value", int),
    "contrast": ("image", "contrast.value", int),
    "saturation": ("image", "saturation.value", int),
    "sharpness": ("image", "sharpness.value", int),
    "wb": ("image", "whiteBalance", _auto_or_int),
    "whitebalance": ("image", "whiteBalance", _auto_or_int),
    "focus": ("focus", "focus", _auto_or_int),
}
DEVICE_KEYS = {"antiflicker": ("antiFlicker", int), "mic": ("microphone", _onoff)}


def parse_assignments(items) -> tuple[list, list]:
    """["fov=78", "wb=auto", "exposure=200"] → card changes, device changes."""
    changes, device = [], []
    for item in items:
        if "=" not in item:
            raise ValueError(f"expected key=value, got {item!r}")
        k, v = item.split("=", 1)
        k = k.strip().lower()
        if k in DEVICE_KEYS:
            key, conv = DEVICE_KEYS[k]
            device.append((key, conv(v)))
            continue
        if k not in KEYS:
            raise ValueError(f"unknown control {k!r}; known: {', '.join(sorted(KEYS) + sorted(DEVICE_KEYS))}")
        tab, key, conv = KEYS[k]
        val = conv(v)
        if isinstance(val, tuple):            # auto-capable entry
            field_, x = val
            if field_ == "mode":
                changes.append((tab, f"{key}.mode", "AUTO"))
            else:
                changes.append((tab, f"{key}.mode", "MANUAL"))
                changes.append((tab, f"{key}.value", x))
        else:
            changes.append((tab, key, val))
    return changes, device


def _proxy():
    try:
        from gi.repository import Gio, GLib
        from .daemon import BUS_NAME, IFACE, OBJECT_PATH
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)), None,
                              Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        if not owner:
            return None
        return Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                                      BUS_NAME, OBJECT_PATH, IFACE, None)
    except Exception:  # noqa: BLE001 - no session bus → direct mode
        return None


def _call(proxy, method, sig, *args):
    from gi.repository import Gio, GLib
    r = proxy.call_sync(method, GLib.Variant(sig, args) if sig else None, Gio.DBusCallFlags.NONE, 10000, None)
    return r.unpack()[0] if r is not None and len(r.unpack()) else None


def _pick_slot(slots, wanted):
    if wanted:
        for s in slots:
            if wanted in s:
                return s
        raise SystemExit(f"no camera matching {wanted!r}; have {slots}")
    if not slots:
        raise SystemExit("no BRIO connected")
    return slots[0]


def cmd_set(args):
    changes, device = parse_assignments(args.assignments)
    proxy = None if args.direct else _proxy()
    if proxy:
        state = json.loads(_call(proxy, "GetState", None))
        slot = _pick_slot([d["slot"] for d in state["devices"]], args.device)
        errs = []
        if changes:
            errs = json.loads(_call(proxy, "SetControls", "(sss)", args.profile or "", slot, json.dumps(changes)))
        for key, val in device:
            _call(proxy, "SetDeviceSetting", "(sss)", slot, key, json.dumps(val))
        print(f"{slot}: applied via briod" + (f" ({len(errs)} errors)" if errs else ""))
    else:
        cams = uvc.find_cameras(brio.VID, brio.PIDS)
        info = next((c for c in cams if c.slot == _pick_slot([c.slot for c in cams], args.device)))
        cam = Camera(info)
        current = brio.settings_from_device(cam.snapshot())
        from .store import set_path
        partial: dict[str, dict] = {}
        for tab, key, val in changes:
            top = key.split(".")[0]
            partial.setdefault(tab, {}).setdefault(top, json.loads(json.dumps(current[tab].get(top))))
            set_path(partial[tab], key, val)
        errs = cam.apply(partial, tuple(partial), dict(device) if device else None)
        print(f"{info.slot}: applied directly (briod not running)")
    for e in errs:
        print("  error:", e, file=sys.stderr)
    return 1 if errs else 0


def cmd_apply(args):
    proxy = None if args.direct else _proxy()
    if proxy:
        state = json.loads(_call(proxy, "GetState", None))
        match = [p for p in state["profiles"] if args.profile in (p["id"], p["name"])]
        if not match:
            raise SystemExit(f"no profile {args.profile!r}")
        _call(proxy, "SelectProfile", "(s)", match[0]["id"])
        errs = json.loads(_call(proxy, "ApplyNow", "(s)", ""))
        print(f"selected profile {match[0]['name']!r} via briod")
    else:
        store = Store()
        match = [p for p in store.data["profiles"] if args.profile in (p["id"], p["name"])]
        if not match:
            raise SystemExit(f"no profile {args.profile!r}")
        errs = []
        for info in uvc.find_cameras(brio.VID, brio.PIDS):
            cam = Camera(info)
            errs += cam.apply(store.effective_settings(match[0]["id"], info.slot),
                              device_settings=store.device_settings(info.slot))
            print(f"{info.slot}: applied {match[0]['name']!r}")
    for e in errs:
        print("  error:", e, file=sys.stderr)
    return 1 if errs else 0


def cmd_get(args):
    for info in uvc.find_cameras(brio.VID, brio.PIDS):
        cam = Camera(info)
        print(f"{info.slot}  {info.devnode}  fw {cam.firmware}")
        print(json.dumps(brio.settings_from_device(cam.snapshot()), indent=1))
    return 0


def cmd_profiles(args):
    proxy = _proxy()
    if proxy:
        state = json.loads(_call(proxy, "GetState", None))
        for p in state["profiles"]:
            mark = "*" if p["id"] == state["activeProfile"] else " "
            print(f"{mark} {p['name']:<24} {p['id']:<16} apps: {', '.join(p['apps']) or '-'}")
    else:
        for p in Store().data["profiles"]:
            print(f"  {p['name']:<24} {p['id']:<16} apps: {', '.join(p.get('apps', [])) or '-'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brio-ctl", description="Logitech BRIO controls")
    ap.add_argument("-d", "--device", help="serial / slot substring when several BRIOs are connected")
    ap.add_argument("--direct", action="store_true", help="bypass briod and write the device directly")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="set controls, e.g. fov=78 hdr=off zoom=150 wb=4500 focus=auto")
    s.add_argument("assignments", nargs="+")
    s.add_argument("-p", "--profile", help="profile to edit (default: active)")
    s.set_defaults(fn=cmd_set)
    a = sub.add_parser("apply", help="select + apply a profile by name or id")
    a.add_argument("profile")
    a.set_defaults(fn=cmd_apply)
    sub.add_parser("get", help="show current device settings").set_defaults(fn=cmd_get)
    sub.add_parser("profiles", help="list profiles").set_defaults(fn=cmd_profiles)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as e:
        ap.error(str(e))
    except uvc.UVCError as e:
        print(f"brio-ctl: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
