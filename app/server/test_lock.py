import json
import tempfile
import unittest
from pathlib import Path

from lock import COOLDOWN, Lock


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LockTests(unittest.TestCase):
    def make(self, path=None):
        clock = FakeClock()
        return Lock(path, clock=clock), clock

    def test_no_pin_until_set(self):
        lk, _ = self.make()
        self.assertEqual(lk.status(), {"set": False})
        self.assertEqual(lk.check({"pin": "1234"}), {"ok": False, "set": False})
        self.assertEqual(lk.set_pin({"pin": "1234"}), {"set": True})
        self.assertEqual(lk.status(), {"set": True})
        self.assertEqual(lk.check({"pin": "1234"}), {"ok": True})

    def test_pin_shape_is_checked(self):
        lk, _ = self.make()
        for bad in ("123", "123456789", "12a4", "", None):
            with self.assertRaises(ValueError):
                lk.set_pin({"pin": bad})

    def test_change_needs_the_current_pin(self):
        lk, _ = self.make()
        lk.set_pin({"pin": "1234"})
        with self.assertRaises(ValueError):
            lk.set_pin({"pin": "9999", "current": "0000"})
        lk.set_pin({"pin": "9999", "current": "1234"})
        self.assertEqual(lk.check({"pin": "9999"}), {"ok": True})
        self.assertEqual(lk.check({"pin": "1234"}), {"ok": False})

    def test_wrong_tries_wait_a_minute(self):
        lk, clock = self.make()
        lk.set_pin({"pin": "1234"})
        for _ in range(4):
            self.assertEqual(lk.check({"pin": "0000"}), {"ok": False})
        self.assertEqual(lk.check({"pin": "0000"}), {"ok": False, "wait": COOLDOWN})
        self.assertEqual(lk.check({"pin": "1234"}), {"ok": False, "wait": COOLDOWN})
        clock.t += COOLDOWN + 1
        self.assertEqual(lk.check({"pin": "1234"}), {"ok": True})

    def test_pin_is_stored_as_a_hash_in_the_file(self):
        d = tempfile.mkdtemp()
        path = Path(d, "lock.json")
        lk, _ = self.make(path)
        lk.set_pin({"pin": "4321"})
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("4321", raw)
        rec = json.loads(raw)
        self.assertEqual(sorted(rec), ["hash", "salt"])
        again, _ = self.make(path)
        self.assertEqual(again.check({"pin": "4321"}), {"ok": True})


if __name__ == "__main__":
    unittest.main()
