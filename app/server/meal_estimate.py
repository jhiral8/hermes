"""Food estimates by Max: a typed description, a meal photo, or a nutrition label.

Craig allowed Max his health and food data on 2026-10-09. Max only estimates:
nothing is logged until Craig ticks the items and presses Log, and the app
(not Max) does the writing with its own key. Estimates are marked as such in
NutriTrace (brand "Estimated").
"""

import base64
import binascii
import json
import re
import secrets

from sources import SourceError

MAX_ITEMS = 12
MAX_TEXT = 500
MAX_IMAGE = 1_200_000  # bytes, after base64 decoding; the app shrinks photos first
CONF = ("high", "medium", "low")
NUTS = ("kcal", "protein", "carbs", "fat", "fibre")
CAPS = {"kcal": 3000, "protein": 300, "carbs": 500, "fat": 300, "fibre": 100}
DATA_URL = re.compile(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=\s]+)")

ITEM_RULES = (
    "Answer with one JSON object and nothing else, shaped like "
    '{"items":[{"name":"Scrambled eggs","amount":"2 eggs","grams":120,"kcal":180,"protein":13,'
    '"carbs":2,"fat":13,"fibre":0,"confidence":"medium"}],"note":"one short sentence"}. '
    "One item per food, at most 12. kcal and grams are for the whole amount eaten, not per 100 g. "
    "Use null for a value you can't estimate, never a guess dressed up as exact. confidence is "
    "high, medium or low. UK foods and portion sizes unless told otherwise. Don't log anything "
    "yourself: the app shows your estimate and Craig decides."
)
LABEL_RULES = (
    "Read the nutrition label in this photo. Answer with one JSON object and nothing else, shaped like "
    '{"name":"","brand":"","serving":"30 g","per100":{"kcal":0,"protein":0,"carbs":0,"fat":0,"fibre":0},'
    '"per_serving":{"kcal":null,"protein":null,"carbs":null,"fat":null,"fibre":null},"unsure":["fibre"]}. '
    "Copy the numbers as printed; if only kJ is shown, convert to kcal (divide by 4.184). Use null for "
    "anything not printed. List in unsure any value you couldn't read clearly. Don't guess the name if "
    "it isn't visible."
)


def _first_json(text):
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


def _num(v, cap):
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n < 0 or n > cap:
        return None
    return round(n, 1)


def clean_items(obj):
    items = []
    for it in (obj or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()[:80]
        if not name:
            continue
        row = {"name": name, "amount": str(it.get("amount") or "").strip()[:40] or None,
               "grams": _num(it.get("grams"), 3000),
               "confidence": it.get("confidence") if it.get("confidence") in CONF else "low"}
        for k in NUTS:
            row[k] = _num(it.get(k), CAPS[k])
        if row["kcal"] is None:
            continue  # nothing to log without calories
        items.append(row)
        if len(items) >= MAX_ITEMS:
            break
    return items


def clean_label(obj):
    if not isinstance(obj, dict):
        return None
    out = {"name": str(obj.get("name") or "").strip()[:120], "brand": str(obj.get("brand") or "").strip()[:80],
           "serving": str(obj.get("serving") or "").strip()[:60]}
    for basis in ("per100", "per_serving"):
        b = obj.get(basis) if isinstance(obj.get(basis), dict) else {}
        out[basis] = {k: _num(b.get(k), 2000 if k == "kcal" else 100) for k in NUTS}
    if out["per100"]["kcal"] is None and out["per_serving"]["kcal"] is None:
        return None
    out["unsure"] = [k for k in (obj.get("unsure") or []) if k in NUTS]
    out["source"] = "Nutrition label (read by Max)"
    return out


def image_from(body):
    """The photo as a data URL, checked: JPEG, PNG or WebP, and not too big."""
    v = body.get("image")
    m = DATA_URL.fullmatch(v.strip()) if isinstance(v, str) else None
    if not m:
        raise ValueError("Send the photo as a JPEG, PNG or WebP image.")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The photo didn't come through. Try again.")
    if len(raw) > MAX_IMAGE:
        raise ValueError("That photo is too big. Try again.")
    return f"data:image/{m.group(1)};base64," + base64.b64encode(raw).decode()


def _ask(chat, prompt, image=None):
    if chat is None:
        raise SourceError("Max isn't connected to the app yet.")
    chat._check_kill()
    answer = []
    resp = chat.backend.open("food-estimate-" + secrets.token_hex(4), prompt, image=image)
    for ev in chat.backend.events(resp):
        if ev["type"] == "text":
            answer.append(ev["delta"])
        elif ev["type"] == "error":
            raise SourceError("Max: " + ev["message"])
    return _first_json("".join(answer))


SAMPLE_ITEMS = [
    {"name": "Scrambled eggs", "amount": "2 eggs", "grams": 120, "kcal": 180, "protein": 13, "carbs": 2, "fat": 13,
     "fibre": 0, "confidence": "medium"},
    {"name": "Wholemeal toast with butter", "amount": "1 slice", "grams": 45, "kcal": 135, "protein": 4, "carbs": 16,
     "fat": 6, "fibre": 2.5, "confidence": "medium"},
    {"name": "Banana", "amount": "1 medium", "grams": 118, "kcal": 105, "protein": 1.3, "carbs": 27, "fat": 0.4,
     "fibre": 3.1, "confidence": "high"},
    {"name": "Coffee with milk", "amount": "1 mug", "grams": 250, "kcal": 30, "protein": 1.7, "carbs": 2.4,
     "fat": 1.6, "fibre": None, "confidence": "low"},
]
SAMPLE_LABEL = {"name": "Sample granola", "brand": "Sample Co", "serving": "45 g",
                "per100": {"kcal": 452, "protein": 9.8, "carbs": 61, "fat": 17, "fibre": 7.5},
                "per_serving": {"kcal": 203, "protein": 4.4, "carbs": 27, "fat": 7.7, "fibre": None},
                "unsure": ["fibre"]}


def describe(body, chat):
    text = " ".join(str(body.get("text") or "").split())
    if len(text) < 3:
        raise ValueError("Say what you ate, for example: 2 eggs, toast with butter and a banana.")
    if len(text) > MAX_TEXT:
        raise ValueError("That's a bit long. Keep it to one meal.")
    if chat is not None and getattr(chat.backend, "sample", False):
        chat._check_kill()
        return {"items": SAMPLE_ITEMS, "note": "Sample data: on the server Max writes this estimate.", "sample": True,
                "from": text}
    got = _ask(chat, "Estimate the nutrition of this meal for my food diary (Hermes app, Log food). "
               + ITEM_RULES + "\n\nWhat I ate: " + text)
    items = clean_items(got)
    if not items:
        raise SourceError("Max couldn't turn that into foods. Try describing it differently.")
    return {"items": items, "note": str((got or {}).get("note") or "")[:300], "from": text}


def photo(body, chat):
    kind = body.get("kind") or "meal"
    if kind not in ("meal", "label"):
        raise ValueError("kind must be meal or label.")
    image = image_from(body)
    if chat is not None and getattr(chat.backend, "sample", False):
        chat._check_kill()
        if kind == "label":
            return {"label": dict(clean_label(SAMPLE_LABEL)), "sample": True}
        return {"items": SAMPLE_ITEMS[:3], "note": "Sample data: on the server Max looks at the photo.", "sample": True}
    if kind == "label":
        got = _ask(chat, LABEL_RULES, image=image)
        label = clean_label(got)
        if not label:
            raise SourceError("Max couldn't read that label. Try again closer and in good light, or type it in.")
        return {"label": label}
    hint = " ".join(str(body.get("hint") or "").split())[:200]
    got = _ask(chat, "Estimate the nutrition of the meal in this photo for my food diary (Hermes app, Log food). "
               + ITEM_RULES + (f"\n\nWhat I said about it: {hint}" if hint else ""), image=image)
    items = clean_items(got)
    if not items:
        raise SourceError("Max couldn't make out the food in that photo. Try again, or describe it instead.")
    return {"items": items, "note": str((got or {}).get("note") or "")[:300]}


def log_items(body, hlog):
    """Log the estimate items Craig kept. Each becomes an "Estimated" food in NutriTrace (one serving
    = the amount eaten), then one serving is logged."""
    items = clean_items({"items": body.get("items")})
    if not items:
        raise ValueError("Tick at least one item to log.")
    done = []
    for it in items:
        # The calories are in the name so a different estimate never reuses an older food's values.
        label = it["name"] + (f" ({it['amount']})" if it["amount"] else "") + f" · {it['kcal']:g} kcal"
        made = hlog.add_food({"name": label[:200], "brand": "Estimated", "portion": it["grams"] or 1,
                              "unit": "g" if it["grams"] else "serving",
                              "nutrition": {k: it[k] for k in NUTS if it[k] is not None}})
        out = hlog.log_food({"food_id": made["food"]["id"], "meal": body.get("meal"), "quantity": 1,
                             "date": body.get("date")})
        done.append(it["name"])
    return {"date": out["date"], "meal": out["logged"]["meal"], "logged": done}
