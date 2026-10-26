"""Profile / card store (~/.config/brio-control/profiles.json), G HUB model.

- A *card* holds one tab's settings (crop, exposure, image, focus).
- A *profile* assigns one card per tab per device slot ("brio.<serial>").
- Built-in cards are read-only.  Editing a control while a read-only card is
  assigned copies it into the profile's writable manual card and assigns that.
- The persistent lock replaces every profile's assignments for a slot.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path

from .models import brio

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "brio-control"
STORE_PATH = CONFIG_DIR / "profiles.json"

DEFAULT_PROFILE = "default"
PERSISTENT = "persistent"


def _empty() -> dict:
    return {
        "version": 1,
        "cards": [],
        "profiles": [{"id": DEFAULT_PROFILE, "name": "Default", "apps": [], "assignments": {}}],
        "selectedProfile": DEFAULT_PROFILE,
        "persistent": {},
        "devices": {},
        "adjustments": {},
        "options": {"driftPolicy": "device", "autoSwitch": True, "focusSwitch": False,
                    "trayIcon": True},
    }


class StoreError(ValueError):
    pass


class Store:
    def __init__(self, path: Path = STORE_PATH):
        self.path = Path(path)
        self.data = _empty()
        self.load()

    # ------------------------------------------------------------ persistence
    def load(self):
        try:
            loaded = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            backup = self.path.with_suffix(".json.corrupt")
            try:
                self.path.rename(backup)
            except OSError:
                pass
            print(f"briod: store unreadable ({e}); moved to {backup}")
            return
        base = _empty()
        base.update(loaded)
        for k, v in _empty()["options"].items():
            base["options"].setdefault(k, v)
        if not any(p["id"] == DEFAULT_PROFILE for p in base["profiles"]):
            base["profiles"].insert(0, _empty()["profiles"][0])
        self.data = base

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ cards
    def all_cards(self) -> list[dict]:
        return copy.deepcopy(brio.BUILTIN_CARDS) + copy.deepcopy(self.data["cards"])

    def card(self, card_id: str) -> dict | None:
        for c in brio.BUILTIN_CARDS:
            if c["id"] == card_id:
                return c
        for c in self.data["cards"]:
            if c["id"] == card_id:
                return c
        return None

    def _user_card(self, card_id: str) -> dict:
        for c in self.data["cards"]:
            if c["id"] == card_id:
                return c
        raise StoreError(f"no writable card {card_id!r}")

    def create_card(self, tab: str, name: str, settings: dict, manual=False, card_id=None) -> dict:
        if tab not in brio.TABS:
            raise StoreError(f"bad tab {tab!r}")
        c = {"id": card_id or f"user:{uuid.uuid4().hex[:12]}", "tab": tab, "name": name,
             "readOnly": False, "manualCard": manual, "settings": copy.deepcopy(settings)}
        self.data["cards"].append(c)
        return c

    def rename_card(self, card_id: str, name: str):
        c = self._user_card(card_id)
        if c.get("manualCard"):
            raise StoreError("the manual card cannot be renamed")
        c["name"] = name

    def duplicate_card(self, card_id: str, name: str | None = None) -> dict:
        src = self.card(card_id)
        if src is None:
            raise StoreError(f"no card {card_id!r}")
        return self.create_card(src["tab"], name or f"{src['name']} copy", src["settings"])

    def delete_card(self, card_id: str):
        c = self._user_card(card_id)
        if c.get("manualCard"):
            raise StoreError("the manual card cannot be deleted")
        self.data["cards"].remove(c)
        default = f"default:{c['tab']}"
        for asg in self._all_assignment_maps():
            for slot_map in asg.values():
                for tab, cid in list(slot_map.items()):
                    if cid == card_id:
                        slot_map[tab] = default

    def _all_assignment_maps(self):
        for p in self.data["profiles"]:
            yield p["assignments"]
        yield {s: v.get("assignments", {}) for s, v in self.data["persistent"].items()}

    # --------------------------------------------------------------- profiles
    def profile(self, profile_id: str) -> dict:
        for p in self.data["profiles"]:
            if p["id"] == profile_id:
                return p
        raise StoreError(f"no profile {profile_id!r}")

    def create_profile(self, name: str, apps=()) -> dict:
        p = {"id": f"p:{uuid.uuid4().hex[:10]}", "name": name, "apps": list(apps), "assignments": {}}
        self.data["profiles"].append(p)
        return p

    def rename_profile(self, profile_id: str, name: str):
        self.profile(profile_id)["name"] = name

    def set_profile_apps(self, profile_id: str, apps):
        self.profile(profile_id)["apps"] = [a.strip() for a in apps if a.strip()]

    def delete_profile(self, profile_id: str):
        if profile_id == DEFAULT_PROFILE:
            raise StoreError("the default profile cannot be deleted")
        p = self.profile(profile_id)
        self.data["profiles"].remove(p)
        self.data["cards"] = [c for c in self.data["cards"]
                              if not c["id"].startswith(f"manual:{profile_id}:")]
        if self.data["selectedProfile"] == profile_id:
            self.data["selectedProfile"] = DEFAULT_PROFILE

    def match_profile(self, identifiers) -> str | None:
        ids = [i.lower() for i in identifiers if i]
        for p in self.data["profiles"]:
            for pat in p.get("apps", []):
                pat = pat.lower()
                if any(i == pat or (len(pat) > 3 and pat in i) for i in ids):
                    return p["id"]
        return None

    # ----------------------------------------------------------- assignments
    def persistent_enabled(self, slot: str) -> bool:
        return bool(self.data["persistent"].get(slot, {}).get("enabled"))

    def _assignment_owner(self, profile_id: str, slot: str) -> tuple[str, dict]:
        """(owner key used in manual-card ids, slot assignment map)."""
        if self.persistent_enabled(slot):
            return PERSISTENT, self.data["persistent"][slot].setdefault("assignments", {})
        p = self.profile(profile_id)
        return profile_id, p["assignments"].setdefault(slot, {})

    def assigned_card_id(self, profile_id: str, slot: str, tab: str) -> str:
        _, m = self._assignment_owner(profile_id, slot)
        cid = m.get(tab)
        if cid is None or self.card(cid) is None:
            return f"default:{tab}"
        return cid

    def assignments(self, profile_id: str, slot: str) -> dict[str, str]:
        return {t: self.assigned_card_id(profile_id, slot, t) for t in brio.TABS}

    def assign(self, profile_id: str, slot: str, tab: str, card_id: str):
        c = self.card(card_id)
        if c is None or c["tab"] != tab:
            raise StoreError(f"card {card_id!r} is not a {tab} card")
        _, m = self._assignment_owner(profile_id, slot)
        m[tab] = card_id

    def set_persistent(self, slot: str, enabled: bool, seed_profile: str):
        entry = self.data["persistent"].setdefault(slot, {"enabled": False, "assignments": {}})
        if enabled and not entry.get("assignments"):
            entry["assignments"] = dict(self.assignments(seed_profile, slot))
        entry["enabled"] = bool(enabled)

    def effective_settings(self, profile_id: str, slot: str) -> dict[str, dict]:
        return {t: copy.deepcopy(self.card(self.assigned_card_id(profile_id, slot, t))["settings"])
                for t in brio.TABS}

    def edit(self, profile_id: str, slot: str, tab: str, key: str, value) -> dict:
        """Change one setting under G HUB card rules; returns the edited card."""
        owner, m = self._assignment_owner(profile_id, slot)
        card = self.card(self.assigned_card_id(profile_id, slot, tab))
        if card.get("readOnly"):
            manual_id = f"manual:{owner}:{slot}:{tab}"
            try:
                manual = self._user_card(manual_id)
                manual["settings"] = copy.deepcopy(card["settings"])
            except StoreError:
                manual = self.create_card(tab, "Manual", card["settings"], manual=True, card_id=manual_id)
            m[tab] = manual_id
            card = manual
        set_path(card["settings"], key, value)
        return card

    # ------------------------------------------------- device settings, drift
    def device_settings(self, slot: str) -> dict:
        d = copy.deepcopy(brio.DEFAULT_DEVICE_SETTINGS)
        stored = self.data["devices"].get(slot, {})
        for k, v in stored.items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                d[k].update(v)
            else:
                d[k] = v
        return d

    def set_device_setting(self, slot: str, key: str, value):
        set_path(self.data["devices"].setdefault(slot, {}), key, value)

    def adjustments(self, slot: str) -> dict:
        return self.data["adjustments"].setdefault(slot, {})

    def clear_adjustments(self, slot: str, tab: str | None = None):
        a = self.data["adjustments"].get(slot, {})
        if tab is None:
            a.clear()
        else:
            a.pop(tab, None)


def set_path(d: dict, key: str, value):
    """Set d[a][b]... for key "a.b"; a dotted path into nested settings."""
    parts = key.split(".")
    for p in parts[:-1]:
        nxt = d.get(p)
        if not isinstance(nxt, dict):
            nxt = {} if nxt is None else {"value": nxt}
            d[p] = nxt
        d = nxt
    d[parts[-1]] = value


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
