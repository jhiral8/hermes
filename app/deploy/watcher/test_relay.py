import os
import unittest
from datetime import datetime

import relay
from relay import UK

DAY = datetime(2026, 10, 8, 12, 0, tzinfo=UK)
NIGHT = datetime(2026, 10, 8, 23, 0, tzinfo=UK)


class FakeCD:
    def __init__(self):
        self.w = {"u1": {"title": "Hermes releases", "url": "https://github.com/x/releases.atom",
                         "last_changed": 100, "last_error": None}}
        self.snaps = {"1": "v1.0\nold line", "2": "v1.1 security fix\nold line"}

    def watches(self):
        return self.w

    def history(self, uuid):
        return {k: f"/snap/{k}" for k in self.snaps}

    def snapshot(self, uuid, ts):
        return self.snaps[ts]


def fresh():
    return {"watches": {}, "queue": [], "sent": {}}


class RelayTests(unittest.TestCase):
    def setUp(self):
        os.environ["DAILY_CAP"] = "10"
        self.sent = []
        self.cd = FakeCD()
        self.state = fresh()
        relay.poll(self.cd, self.state, DAY, ask=self.never, send=self.sent.append)  # baseline

    def never(self, text):
        raise AssertionError("Max should not be called")

    def change(self):
        self.cd.w["u1"]["last_changed"] = 200

    def test_first_poll_is_baseline_only(self):
        self.assertEqual(self.sent, [])
        self.assertIn("u1", self.state["watches"])

    def test_no_change_no_call(self):
        ev, _ = relay.poll(self.cd, self.state, DAY, ask=self.never, send=self.sent.append)
        self.assertEqual(ev, [])

    def test_change_sends_summary(self):
        self.change()
        seen = {}
        def ask(text):
            seen["text"] = text
            return "Version 1.1 adds a security fix.", False
        relay.poll(self.cd, self.state, DAY, ask=ask, send=self.sent.append)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("security fix", self.sent[0])
        self.assertIn("+v1.1 security fix", seen["text"])
        self.assertNotIn("old line", seen["text"])  # only changed lines go to Max

    def test_tool_use_rejected(self):
        self.change()
        relay.poll(self.cd, self.state, DAY, ask=lambda t: ("I browsed it", True), send=self.sent.append)
        self.assertNotIn("I browsed it", self.sent[0])
        self.assertIn("+v1.1", self.sent[0])

    def test_max_down_falls_back(self):
        self.change()
        def boom(t):
            raise OSError("refused")
        relay.poll(self.cd, self.state, DAY, ask=boom, send=self.sent.append)
        self.assertIn("changed:", self.sent[0])

    def test_quiet_hours_queue_then_flush(self):
        self.change()
        relay.poll(self.cd, self.state, NIGHT, ask=lambda t: ("x", False), send=self.sent.append)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.state["queue"]), 1)
        relay.flush(self.state, DAY, send=self.sent.append)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state["queue"], [])

    def test_error_is_reported_not_silent(self):
        self.cd.w["u1"]["last_error"] = "Proxy denied"
        relay.poll(self.cd, self.state, DAY, ask=self.never, send=self.sent.append)
        self.assertIn("Monitor unavailable", self.sent[0])
        self.cd.w["u1"]["last_error"] = None
        relay.poll(self.cd, self.state, DAY, ask=self.never, send=self.sent.append)
        self.assertIn("working again", self.sent[1])

    def test_daily_cap(self):
        os.environ["DAILY_CAP"] = "1"
        relay.deliver(self.state, "a", DAY, send=self.sent.append)
        relay.deliver(self.state, "b", DAY, send=self.sent.append)
        self.assertEqual(self.sent, ["a"])
        self.assertEqual(self.state["queue"], ["b"])

    def test_parse_max_detects_tools(self):
        r = {"output": [{"type": "function_call", "name": "web_fetch"},
                        {"type": "message", "content": [{"type": "output_text", "text": "hi"}]}]}
        self.assertEqual(relay.parse_max(r), ("hi", True))
        r2 = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}
        self.assertEqual(relay.parse_max(r2), ("ok", False))


if __name__ == "__main__":
    unittest.main()
