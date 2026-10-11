import tempfile
import unittest
from pathlib import Path

from rooms import CAP_PENCE, MAX_STEER, Rooms, SampleDebate
from sources import SourceError


class CrewMissing:
    sample = False

    def speak(self, room, persona, rnd, steer):
        raise SourceError("Rooms need CrewAI on the server, which isn't installed yet.")

    def chair(self, room):
        raise SourceError("Rooms need CrewAI on the server, which isn't installed yet.")


class RoomsTests(unittest.TestCase):
    def make(self, path=None, backend=None):
        return Rooms(path, backend=backend or SampleDebate())

    def test_create_validates_question_and_seats(self):
        r = self.make()
        with self.assertRaises(ValueError):
            r.create({"question": "  "})
        with self.assertRaises(ValueError):
            r.create({"question": "Q?", "seats": ["planner"]})
        with self.assertRaises(ValueError):
            r.create({"question": "Q?", "seats": ["planner", "nobody"]})
        with self.assertRaises(ValueError):
            r.create({"question": "Q?", "template": "nope"})
        made = r.create({"question": "What should I eat on Tuesday?", "template": "food"})
        self.assertEqual(made["status"], "ready")
        self.assertEqual(r.get(made["id"])["seats"], ["cook", "coach", "sceptic"])

    def test_two_rounds_then_chair_with_steer_between(self):
        r = self.make()
        rid = r.create({"question": "Plan my gym week", "seats": ["planner", "coach"]})["id"]
        self.assertEqual(r.run(rid)["status"], "steer")
        r.steer(rid, {"text": "Keep it under an hour"})
        done = r.run(rid)
        self.assertEqual((done["status"], done["round"]), ("done", 2))
        who = [t["who"] for t in done["transcript"]]
        self.assertEqual(who, ["planner", "coach", "you", "planner", "coach", "chair"])
        self.assertIn("Keep it under an hour", done["transcript"][3]["text"])
        self.assertTrue(done["result"].startswith("Chair:"))
        with self.assertRaises(ValueError):
            r.run(rid)

    def test_cost_cap_stops_the_room(self):
        r = self.make()
        rid = r.create({"question": "Long one", "seats": ["planner", "coach", "sceptic", "cook"]})["id"]
        # 4 seats x 5p = 20p a round; two rounds plus the chair is 50p, so the cap is never hit here.
        r.run(rid)
        self.assertEqual(r.get(rid)["spent_p"], 20)
        r2 = self.make()
        rid2 = r2.create({"question": "x", "seats": ["planner", "coach"]})["id"]
        with r2._lock:
            r2._load()[0]["spent_p"] = CAP_PENCE - 1
        self.assertEqual(r2.run(rid2)["status"], "capped")

    def test_stop_and_steer_limits(self):
        r = self.make()
        rid = r.create({"question": "Stop me", "seats": ["planner", "coach"]})["id"]
        with self.assertRaises(ValueError):
            r.steer(rid, {"text": "x" * (MAX_STEER + 1)})
        self.assertEqual(r.stop(rid), {"ok": True, "status": "stopped"})
        with self.assertRaises(ValueError):
            r.run(rid)
        with self.assertRaises(ValueError):
            r.stop(rid)

    def test_real_backend_missing_leaves_the_room_as_it_was(self):
        r = self.make(backend=CrewMissing())
        rid = r.create({"question": "Needs CrewAI", "seats": ["planner", "coach"]})["id"]
        with self.assertRaises(SourceError):
            r.run(rid)
        room = r.get(rid)
        self.assertEqual((room["status"], room["round"], room["transcript"]), ("ready", 0, []))

    def test_rooms_survive_a_restart_in_a_file(self):
        path = Path(tempfile.mkdtemp(), "rooms.json")
        r = self.make(path)
        rid = r.create({"question": "Keep me", "seats": ["planner", "coach"]})["id"]
        r.run(rid)
        again = self.make(path)
        self.assertEqual(again.get(rid)["status"], "steer")
        self.assertEqual(len(again.list()["rooms"]), 1)


if __name__ == "__main__":
    unittest.main()
