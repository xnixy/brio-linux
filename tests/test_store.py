import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from briod.ghub_import import import_settings_db
from briod.store import Store, StoreError

SLOT = "brio.TEST1234"


class Cards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "profiles.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        self.assertEqual(self.store.assigned_card_id("default", SLOT, "crop"), "default:crop")
        s = self.store.effective_settings("default", SLOT)
        self.assertEqual(s["crop"]["fov"], 90)
        self.assertTrue(s["exposure"]["hdr"])

    def test_editing_readonly_copies_into_manual_card(self):
        self.store.assign("default", SLOT, "crop", "preset:fov78")
        card = self.store.edit("default", SLOT, "crop", "zoom.value", 150)
        self.assertEqual(card["id"], f"manual:default:{SLOT}:crop")
        self.assertEqual(self.store.assigned_card_id("default", SLOT, "crop"), card["id"])
        s = self.store.effective_settings("default", SLOT)["crop"]
        self.assertEqual((s["fov"], s["zoom"]["value"]), (78, 150))
        # built-in left untouched
        self.assertEqual(self.store.card("preset:fov78")["settings"]["zoom"]["value"], 100)

    def test_editing_user_preset_updates_in_place(self):
        c = self.store.create_card("image", "Mine", self.store.card("default:image")["settings"])
        self.store.assign("default", SLOT, "image", c["id"])
        edited = self.store.edit("default", SLOT, "image", "brightness.value", 10)
        self.assertEqual(edited["id"], c["id"])

    def test_delete_reassigns_default(self):
        c = self.store.create_card("image", "Mine", {})
        self.store.assign("default", SLOT, "image", c["id"])
        self.store.delete_card(c["id"])
        self.assertEqual(self.store.assigned_card_id("default", SLOT, "image"), "default:image")
        with self.assertRaises(StoreError):
            self.store.delete_card("default:image")

    def test_persistent_lock_overrides_profiles(self):
        p = self.store.create_profile("Calls", ["zoom"])
        self.store.assign(p["id"], SLOT, "crop", "preset:fov65")
        self.store.set_persistent(SLOT, True, "default")
        self.assertEqual(self.store.assigned_card_id(p["id"], SLOT, "crop"), "default:crop")
        self.store.edit(p["id"], SLOT, "crop", "fov", 78)
        self.assertEqual(self.store.effective_settings("default", SLOT)["crop"]["fov"], 78)
        self.store.set_persistent(SLOT, False, "default")
        self.assertEqual(self.store.assigned_card_id(p["id"], SLOT, "crop"), "preset:fov65")

    def test_match_profile(self):
        p = self.store.create_profile("Streaming", ["obs", "com.obsproject.Studio"])
        self.assertEqual(self.store.match_profile(["obs"]), p["id"])
        self.assertEqual(self.store.match_profile(["com.obsproject.Studio"]), p["id"])
        self.assertIsNone(self.store.match_profile(["firefox"]))

    def test_save_load_roundtrip(self):
        self.store.edit("default", SLOT, "crop", "fov", 65)
        self.store.save()
        again = Store(self.store.path)
        self.assertEqual(again.effective_settings("default", SLOT)["crop"]["fov"], 65)

    def test_corrupt_store_is_moved_aside(self):
        self.store.path.write_text("{nope")
        s = Store(self.store.path)
        self.assertEqual(s.data["selectedProfile"], "default")
        self.assertTrue(self.store.path.with_suffix(".json.corrupt").exists())


class GHubImport(unittest.TestCase):
    def test_import_from_settings_db(self):
        blob = {
            "applications": {"applications": [
                {"applicationId": "app-zoom", "name": "Zoom", "applicationPath": "C:\\Zoom\\Zoom.exe"}]},
            "profiles": {"profiles": [{"id": "prof-1", "name": "Zoom calls", "applicationId": "app-zoom"}]},
            "cards": {"cards": [
                {"id": "c1", "name": "Wide", "readOnly": False,
                 "webcamCrop": {"fov": 65, "zoom": {"value": 120}, "pan": {"value": 0}, "tilt": {"value": 0}}},
                {"id": "c2", "name": "Dark room", "readOnly": False,
                 "webcamExposure": {"exposure": {"mode": "MANUAL", "value": 30}, "gain": {"value": 800},
                                    "hdr": False, "priority": "PRIORITY_FRAME_RATE"}},
            ]},
            "assignments": [{"cardId": "c1", "profileId": "prof-1", "slotId": "brio_crop"},
                            {"cardId": "c2", "profileId": "prof-1", "slotId": "brio_exposure"}],
        }
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "settings.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE data (_id INTEGER PRIMARY KEY, _date_created TEXT, file BLOB)")
            con.execute("INSERT INTO data (file) VALUES (?)", (json.dumps(blob).encode(),))
            con.commit()
            con.close()
            store = Store(Path(d) / "profiles.json")
            summary = import_settings_db(str(db), store, [SLOT])
        self.assertEqual(summary, {"cards": 2, "profiles": 1, "assignments": 2})
        pid = store.match_profile(["Zoom"])
        s = store.effective_settings(pid, SLOT)
        self.assertEqual(s["crop"]["fov"], 65)
        self.assertEqual(s["exposure"]["exposure"], {"mode": "MANUAL", "value": 30})
        self.assertFalse(s["exposure"]["hdr"])


if __name__ == "__main__":
    unittest.main()
