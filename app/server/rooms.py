"""Rooms: a few agent personas debate a question, then the Chair writes one recommendation.

A room runs two debate rounds. You can steer between them, and the Chair writes
the recommendation after round two. Disagreements stay visible in the result.
Each room has a cost cap. The real debate runs through CrewAI on the server,
which isn't installed yet; until then the sample backend gives scripted lines so
the flow can be tried.
"""

import copy
import datetime
import json
import secrets
import threading
from pathlib import Path

from sources import SourceError

MAX_ROOMS = 200
MAX_QUESTION = 300
MAX_STEER = 500
ROUNDS = 2
CAP_PENCE = 150  # each room stops at £1.50
SEAT_ROUND_PENCE = 5  # estimate per seat per round, until the real backend reports actual cost
CHAIR_PENCE = 10
MAX_SEATS = 4
SEATS = {
    "planner": ("Planner", "Turns the question into steps, times and a plan you could follow."),
    "sceptic": ("Sceptic", "Looks for what would go wrong and what is being assumed."),
    "cook": ("Cook", "Thinks about food, cost and time in the kitchen."),
    "coach": ("Coach", "Looks at training, recovery and the goal."),
}
TEMPLATES = {
    "food": ("Food choice", ["cook", "coach", "sceptic"]),
    "week": ("Weekly plan", ["planner", "coach", "sceptic", "cook"]),
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SampleDebate:
    """Scripted lines for sample-data mode."""

    sample = True

    def speak(self, room, persona, rnd, steer):
        name = SEATS[persona][0]
        nudge = f" Taking your steer into account: {steer}." if steer else ""
        return f"{name}, round {rnd}, on \"{room['question'][:60]}\": one point I'd press on.{nudge}"

    def chair(self, room):
        names = ", ".join(SEATS[s][0] for s in room["seats"])
        return (f"Chair: the room ({names}) agrees on the main point and still disagrees on the detail. "
                "Recommendation: start with the smallest version and check it after a week. "
                "This is sample text, not a real debate.")


class CrewDebate:
    """Placeholder until CrewAI is installed on the server."""

    sample = False

    def speak(self, room, persona, rnd, steer):
        raise SourceError("Rooms need CrewAI on the server, which isn't installed yet.")

    def chair(self, room):
        raise SourceError("Rooms need CrewAI on the server, which isn't installed yet.")


class Rooms:
    def __init__(self, path=None, backend=None, clock=None):
        self.path = Path(path) if path else None
        self.backend = backend or SampleDebate()
        self._clock = clock or _now
        self._lock = threading.Lock()
        self._rooms = None

    # ------------------------------------------------------------ storage

    def _load(self):
        if self._rooms is None:
            if self.path is None or not self.path.exists():
                self._rooms = []
            else:
                try:
                    self._rooms = json.loads(self.path.read_text(encoding="utf-8")).get("rooms", [])
                except (OSError, ValueError, AttributeError):
                    raise SourceError("The rooms file can't be read.")
        return self._rooms

    def _save(self, before):
        if self.path is None:
            return
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"rooms": self._rooms}, indent=1), encoding="utf-8")
            tmp.chmod(0o640)
            tmp.replace(self.path)
        except OSError:
            self._rooms = before
            raise SourceError("The room couldn't be saved on the server.")

    def _find(self, rid):
        for r in self._load():
            if r["id"] == rid:
                return r
        raise ValueError("That room isn't here any more.")

    def _write(self, fn):
        """Change the rooms under the lock, then save. fn works on a copy so a failed save changes nothing."""
        with self._lock:
            before = list(self._load())
            self._rooms = copy.deepcopy(before)
            try:
                out = fn()
            except BaseException:
                self._rooms = before
                raise
            self._save(before)
            return out

    # ------------------------------------------------------------ reading

    def list(self):
        with self._lock:
            rows = list(self._load())
        return {"rooms": [{"id": r["id"], "question": r["question"], "status": r["status"],
                           "round": r["round"], "spent_p": r["spent_p"], "updated": r["updated"]}
                          for r in sorted(rows, key=lambda r: r["updated"], reverse=True)],
                "seats": [{"key": k, "name": n, "role": d} for k, (n, d) in SEATS.items()],
                "templates": [{"key": k, "name": n, "seats": s} for k, (n, s) in TEMPLATES.items()],
                "cap_p": CAP_PENCE, "rounds": ROUNDS, "sample": getattr(self.backend, "sample", False)}

    def get(self, rid):
        with self._lock:
            return dict(self._find(rid))

    # ------------------------------------------------------------ changing

    def create(self, body):
        question = " ".join(str(body.get("question") or "").split())
        if not question:
            raise ValueError("Ask the room a question first.")
        if len(question) > MAX_QUESTION:
            raise ValueError(f"Keep the question under {MAX_QUESTION} characters.")
        template = body.get("template")
        if template:
            if template not in TEMPLATES:
                raise ValueError("Pick a template from the list.")
            seats = list(TEMPLATES[template][1])
        else:
            seats = body.get("seats") or []
            if not isinstance(seats, list) or not all(isinstance(s, str) for s in seats):
                raise ValueError("Pick the seats from the list.")
            seats = list(dict.fromkeys(seats))
        if not 2 <= len(seats) <= MAX_SEATS or any(s not in SEATS for s in seats):
            raise ValueError(f"Choose between 2 and {MAX_SEATS} seats from the list.")
        now = self._clock()
        room = {"id": secrets.token_hex(6), "question": question, "seats": seats, "status": "ready",
                "round": 0, "steers": [], "transcript": [], "result": None, "spent_p": 0,
                "created": now, "updated": now}

        def add():
            if len(self._rooms) >= MAX_ROOMS:
                raise ValueError("There are too many rooms. Delete some first.")
            self._rooms.append(room)
            return room
        self._write(add)
        return {"id": room["id"], "status": room["status"]}

    def run(self, rid):
        """Run the next step: round one, or round two then the Chair. Nothing runs past the cost cap."""
        def step():
            room = self._find(rid)
            if room["status"] not in ("ready", "steer"):
                raise ValueError("This room isn't waiting for a round.")
            rnd = room["round"] + 1
            cost = len(room["seats"]) * SEAT_ROUND_PENCE
            if room["spent_p"] + cost + CHAIR_PENCE * (rnd == ROUNDS) > CAP_PENCE:
                room["status"] = "capped"
                room["updated"] = self._clock()
                return dict(room)
            steer = room["steers"][-1] if room["steers"] and rnd > 1 else ""
            for seat in room["seats"]:
                text = self.backend.speak(room, seat, rnd, steer)
                room["transcript"].append({"round": rnd, "who": seat, "name": SEATS[seat][0], "text": text, "at": self._clock()})
            room["round"] = rnd
            room["spent_p"] += cost
            if rnd < ROUNDS:
                room["status"] = "steer"
            else:
                room["result"] = self.backend.chair(room)
                room["spent_p"] += CHAIR_PENCE
                room["transcript"].append({"round": rnd, "who": "chair", "name": "Chair", "text": room["result"], "at": self._clock()})
                room["status"] = "done"
            room["updated"] = self._clock()
            return dict(room)
        return self._write(step)

    def steer(self, rid, body):
        text = " ".join(str(body.get("text") or "").split())
        if not text:
            raise ValueError("Type your steer first.")
        if len(text) > MAX_STEER:
            raise ValueError(f"Keep the steer under {MAX_STEER} characters.")

        def add():
            room = self._find(rid)
            if room["status"] not in ("ready", "steer"):
                raise ValueError("This room isn't taking steers now.")
            room["steers"].append(text)
            room["transcript"].append({"round": room["round"], "who": "you", "name": "You", "text": text, "at": self._clock()})
            room["updated"] = self._clock()
            return {"ok": True, "status": room["status"]}
        return self._write(add)

    def stop(self, rid):
        def halt():
            room = self._find(rid)
            if room["status"] in ("done", "stopped"):
                raise ValueError("This room has already finished.")
            room["status"] = "stopped"
            room["updated"] = self._clock()
            return {"ok": True, "status": "stopped"}
        return self._write(halt)
