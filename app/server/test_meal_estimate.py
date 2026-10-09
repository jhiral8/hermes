import base64
import io
import json
import unittest

import demo_health
import meal_estimate as me
from chat import ChatError, DemoMax, MaxChat
from sources import SourceError
from test_health_log import Recorder, setup

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


class FakeMax:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def open(self, cid, text, image=None):
        self.calls.append((cid, text, image))
        return self.reply

    def events(self, reply):
        yield {"type": "text", "delta": reply}
        yield {"type": "done"}


def chat_with(reply, tmp=None):
    import tempfile
    return MaxChat({"store_dir": tempfile.mkdtemp()}, backend=FakeMax(reply))


class Estimates(unittest.TestCase):
    def setUp(self):
        demo_health.reset()
        self.addCleanup(demo_health.reset)

    def test_describe_asks_max_and_cleans_the_answer(self):
        reply = 'Here you go: {"items":[{"name":"Eggs","amount":"2","grams":120,"kcal":180,"protein":13,"carbs":"x","fat":13,"confidence":"sure"},' \
                '{"name":"","kcal":50},{"name":"Mystery","kcal":null},{"name":"Toast","kcal":99999}],"note":"Rough."}'
        c = chat_with(reply)
        out = me.describe({"text": "two eggs  and toast"}, c)
        self.assertEqual([i["name"] for i in out["items"]], ["Eggs"])  # nameless, no-kcal and silly kcal dropped
        e = out["items"][0]
        self.assertEqual((e["carbs"], e["confidence"], e["fibre"]), (None, "low", None))
        self.assertIn("What I ate: two eggs and toast", c.backend.calls[0][1])
        self.assertIsNone(c.backend.calls[0][2])
        with self.assertRaises(ValueError):
            me.describe({"text": "x"}, c)
        with self.assertRaises(SourceError):
            me.describe({"text": "something odd"}, chat_with("no json here"))

    def test_photo_sends_the_image_and_reads_a_label(self):
        c = chat_with('{"name":"Granola","per100":{"kcal":450,"protein":10},"per_serving":{},"unsure":["fibre","nope"]}')
        out = me.photo({"kind": "label", "image": PNG}, c)
        self.assertEqual(out["label"]["per100"]["kcal"], 450)
        self.assertEqual(out["label"]["unsure"], ["fibre"])
        self.assertTrue(c.backend.calls[0][2].startswith("data:image/png;base64,"))
        with self.assertRaises(ValueError):
            me.photo({"kind": "meal", "image": "data:text/html;base64,PGI+"}, c)
        with self.assertRaises(ValueError):
            me.photo({"kind": "meal", "image": "data:image/png;base64," + "A" * 2_000_000}, c)
        with self.assertRaises(ValueError):
            me.photo({"kind": "other", "image": PNG}, c)

    def test_kill_switch_stops_estimates(self):
        import tempfile, pathlib
        flag = pathlib.Path(tempfile.mkdtemp(), "paused")
        flag.write_text("")
        c = MaxChat({"store_dir": tempfile.mkdtemp(), "kill_flag": str(flag)}, backend=FakeMax("{}"))
        with self.assertRaises(ChatError):
            me.describe({"text": "an apple"}, c)

    def test_sample_mode_and_logging_kept_items(self):
        import tempfile
        c = MaxChat({"store_dir": tempfile.mkdtemp()}, backend=DemoMax())
        est = me.describe({"text": "2 eggs, toast and a banana"}, c)
        self.assertTrue(est["sample"])
        hl, log = setup(Recorder())
        out = me.log_items({"items": est["items"][:2], "meal": 0}, log)
        self.assertEqual((out["meal"], len(out["logged"])), ("Breakfast", 2))
        names = [i["name"] for m in hl.food()["day"]["data"]["meals"] for i in m["items"]]
        self.assertIn("Scrambled eggs (2 eggs) · 180 kcal", names)
        with self.assertRaises(ValueError):
            me.log_items({"items": []}, log)


if __name__ == "__main__":
    unittest.main()
