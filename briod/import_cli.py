"""brio-import-ghub: import cards/profiles from a G HUB settings.db."""
from __future__ import annotations

import argparse
import json
import sys

from . import uvc
from .models import brio


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brio-import-ghub", description=__doc__)
    ap.add_argument("path", help="G HUB settings.db (or an exported settings .json), e.g. copied from "
                                 r"%%LOCALAPPDATA%%\LGHUB\settings.db")
    ap.add_argument("--dump", action="store_true", help="print the raw settings JSON and exit")
    args = ap.parse_args(argv)
    from .ghub_import import load_blob
    if args.dump:
        json.dump(load_blob(args.path), sys.stdout, indent=1)
        return 0
    from .ctl import _call, _proxy
    proxy = _proxy()
    if proxy:
        summary = json.loads(_call(proxy, "ImportGHub", "(s)", args.path))
    else:
        from .ghub_import import import_settings_db
        from .store import Store
        store = Store()
        summary = import_settings_db(args.path, store, [c.slot for c in uvc.find_cameras(brio.VID, brio.PIDS)])
        store.save()
    print("imported {cards} cards, {profiles} profiles, {assignments} assignments".format(**summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
