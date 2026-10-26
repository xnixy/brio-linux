"""brio-probe: dump every standard control and XU selector.

Writes JSON with, per XU selector, GET_INFO/LEN/CUR/MIN/MAX/RES/DEF, so two
dumps can be diffed to find which selector a setting changes.
Read-only: never issues SET_CUR.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import uvc
from .models import brio

KNOWN = {
    brio.XU_VIDEOPIPE: "Videopipe", brio.XU_PERIPHERAL: "Peripheral",
    brio.XU_DEVICEINFO: "DeviceInfo",
}


def _q(dev, unit, sel, query, size):
    try:
        return dev.xu_query(unit, sel, query, size).hex(), None
    except uvc.UVCError as e:
        return None, e.strerror or str(e)


def probe(devnode: str, scan_all_selectors=False) -> dict:
    out = {"devnode": devnode, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "standard": {}, "xu": []}
    with uvc.Device(devnode) as dev:
        cap = dev.querycap()
        out["card"] = cap.card.decode()
        out["bus"] = cap.bus_info.decode()
        for name, c in dev.query_controls().items():
            entry = {"id": f"0x{c.id:08x}", "type": c.type, "min": c.minimum, "max": c.maximum,
                     "step": c.step, "default": c.default, "flags": c.flags}
            if c.menu:
                entry["menu"] = c.menu
            try:
                entry["value"] = dev.get(name) if name in uvc.CID else None
            except uvc.UVCError as e:
                entry["get_err"] = e.strerror
            out["standard"][name] = entry
        for xu in dev.extension_units():
            unit = {"unit": xu.unit_id, "guid": str(xu.guid).upper(),
                    "name": KNOWN.get(str(xu.guid).upper(), ""), "advertised": xu.selectors(),
                    "selectors": []}
            sels = range(1, 33) if scan_all_selectors else xu.selectors()
            for sel in sels:
                s = {"selector": sel}
                lh, err = _q(dev, xu.unit_id, sel, uvc.GET_LEN, 2)
                if err:
                    s["len_err"] = err
                    if scan_all_selectors and sel not in xu.selectors():
                        continue
                    unit["selectors"].append(s)
                    continue
                ln = int.from_bytes(bytes.fromhex(lh), "little")
                s["len"] = ln
                info, err = _q(dev, xu.unit_id, sel, uvc.GET_INFO, 1)
                s["info"] = info if info else err
                if info:
                    bits = int(info, 16)
                    s["info_str"] = ",".join(n for b, n in ((1, "GET"), (2, "SET"), (4, "disabled"),
                                                            (8, "autoupdate"), (16, "async")) if bits & b)
                for qname, q in (("cur", uvc.GET_CUR), ("min", uvc.GET_MIN), ("max", uvc.GET_MAX),
                                 ("res", uvc.GET_RES), ("def", uvc.GET_DEF)):
                    v, err = _q(dev, xu.unit_id, sel, q, ln)
                    s[qname] = v if v is not None else f"err: {err}"
                unit["selectors"].append(s)
            out["xu"].append(unit)
        out["formats"] = dev.enum_formats()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brio-probe", description=__doc__.splitlines()[0])
    ap.add_argument("devnode", nargs="?", help="default: first BRIO capture node")
    ap.add_argument("-o", "--output", help="write JSON here (default: stdout)")
    ap.add_argument("--all-selectors", action="store_true",
                    help="also try selectors 1..32 not advertised in bmControls")
    args = ap.parse_args(argv)
    node = args.devnode
    if not node:
        cams = uvc.find_cameras(brio.VID, brio.PIDS)
        if not cams:
            print("brio-probe: no BRIO found", file=sys.stderr)
            return 1
        node = cams[0].devnode
    data = probe(node, args.all_selectors)
    text = json.dumps(data, indent=1)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
