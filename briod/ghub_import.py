"""Import webcam cards / profiles from a G HUB settings.db (best effort).

G HUB keeps its settings as one JSON blob in a SQLite table (`data.file`).  The
webcam card payloads use the proto3 JSON form of logi.protocol.webcam
{Crop,Exposure,Image,Focus}Settings, which is also our card format, so the
importer walks the blob for card-like objects instead of depending on the
surrounding layout, which changes between G HUB versions.
"""
from __future__ import annotations

import json
import sqlite3

from .models import brio
from .store import Store

TAB_KEYS = {
    "crop": {"fov", "zoom", "pan", "tilt"},
    "exposure": {"exposure", "gain", "hdr", "priority"},
    "image": {"brightness", "contrast", "saturation", "sharpness", "whiteBalance"},
    "focus": {"focus"},
}
PRIORITY_ALIASES = {"FRAME_RATE": brio.PRIORITY_FRAME_RATE, "EXPOSURE": brio.PRIORITY_EXPOSURE,
                    1: brio.PRIORITY_FRAME_RATE, 2: brio.PRIORITY_EXPOSURE}


def load_blob(path: str):
    if path.endswith(".json"):
        with open(path) as f:
            return json.load(f)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur]
        for table in (["data"] if "data" in tables else []) + tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            col = "file" if "file" in cols else next((c for c in cols if c not in ("_id", "id")), None)
            if not col:
                continue
            order = "_id" if "_id" in cols else "rowid"
            row = con.execute(f'SELECT "{col}" FROM "{table}" ORDER BY "{order}" DESC LIMIT 1').fetchone()
            if row and row[0]:
                raw = row[0].decode() if isinstance(row[0], bytes) else row[0]
                try:
                    return json.loads(raw)
                except ValueError:
                    continue
    finally:
        con.close()
    raise ValueError(f"no JSON settings blob found in {path}")


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _tab_of(settings: dict) -> str | None:
    keys = set(settings)
    best = max(TAB_KEYS, key=lambda t: len(keys & TAB_KEYS[t]))
    return best if keys & TAB_KEYS[best] else None


def _settings_payload(card: dict):
    """The nested settings dict inside a G HUB card, and its tab."""
    for k, v in card.items():
        if isinstance(v, dict) and k not in ("metadata",):
            tab = _tab_of(v)
            if tab:
                return tab, v
            for k2, v2 in v.items():          # one more level (e.g. {"webcam": {"crop": {...}}})
                if isinstance(v2, dict):
                    tab = _tab_of(v2)
                    if tab:
                        return tab, v2
    return None, None


def normalize(tab: str, s: dict) -> dict:
    out = {}
    for k in TAB_KEYS[tab]:
        if k not in s:
            continue
        v = s[k]
        if k == "priority":
            v = PRIORITY_ALIASES.get(v, v if isinstance(v, str) and v.startswith("PRIORITY_") else brio.PRIORITY_EXPOSURE)
        elif k == "hdr":
            v = bool(v)
        elif k == "fov":
            v = int(v)
        elif isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if kk in ("value", "mode")}
            v.setdefault("value", brio.setting_value(brio.DEFAULT_SETTINGS[tab].get(k), 0))
        else:
            v = {"value": v}
        out[k] = v
    merged = json.loads(json.dumps(brio.DEFAULT_SETTINGS[tab]))
    merged.update(out)
    return merged


def import_settings_db(path: str, store: Store, slots: list[str]) -> dict:
    blob = load_blob(path)
    imported_cards: dict[str, str] = {}
    for o in _walk(blob):
        if "id" not in o or "name" not in o:
            continue
        tab, payload = _settings_payload(o)
        if not tab:
            continue
        if o.get("readOnly") or str(o["id"]) in imported_cards:
            continue
        card = store.create_card(tab, f"{o['name']} (G HUB)", normalize(tab, payload))
        imported_cards[str(o["id"])] = card["id"]

    # Profiles: objects with an id + name + application reference.
    profile_map: dict[str, str] = {}
    app_names: dict[str, str] = {}
    for o in _walk(blob):
        aid = o.get("applicationId")
        if not aid or "profileId" in o or "id" in o:   # profiles also carry applicationId
            continue
        path_ = (o.get("applicationPath") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if path_ or aid not in app_names:
            app_names[aid] = path_ or o.get("name", "")
    for o in _walk(blob):
        if "id" in o and "name" in o and "applicationId" in o and o.get("id") not in profile_map:
            app = app_names.get(o["applicationId"], "")
            if not app:
                continue          # desktop / unknown → skip, the default profile covers it
            exe = app[:-4] if app.lower().endswith(".exe") else app
            p = store.create_profile(f"{o['name']} (G HUB)", [exe])
            profile_map[o["id"]] = p["id"]

    # Assignments: {cardId, profileId, slotId}
    assigned = 0
    for o in _walk(blob):
        cid, pid = str(o.get("cardId", "")), o.get("profileId")
        if cid in imported_cards and pid in profile_map:
            card = store.card(imported_cards[cid])
            for slot in slots:
                store.assign(profile_map[pid], slot, card["tab"], card["id"])
                assigned += 1
    return {"cards": len(imported_cards), "profiles": len(profile_map), "assignments": assigned}
