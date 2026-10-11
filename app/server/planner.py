"""Planner: Craig's Google Calendar, read-only, shown only in the app.

The app signs in once with the calendar.readonly scope, using its own token
file (same layout as the Gmail one). It only lists events; nothing here can
create, change or delete them, and nothing it reads goes to Max or any model.
Events are never cached on disk.
"""

import datetime
import re
import urllib.parse
from zoneinfo import ZoneInfo

from google_read import GoogleReadOnly

API = "https://www.googleapis.com/calendar/v3"
TZ = "Europe/London"
CAL_ID = re.compile(r"[A-Za-z0-9._@#-]{1,200}")
MAX_DAYS = 31
MAX_EVENTS = 250


def london_today():
    return datetime.datetime.now(ZoneInfo(TZ)).date()


def _window(start, days, today):
    if start:
        try:
            start = datetime.date.fromisoformat(start)
        except ValueError:
            raise ValueError("Bad start date.")
    else:
        start = today
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise ValueError("Bad number of days.")
    return start, max(1, min(days, MAX_DAYS))


def _event(e, cal):
    s, t = e.get("start") or {}, e.get("end") or {}
    all_day = "date" in s and "dateTime" not in s
    return {"id": e.get("id"), "title": e.get("summary") or "(no title)", "all_day": all_day,
            "start": s.get("date") if all_day else s.get("dateTime"),
            "end": t.get("date") if all_day else t.get("dateTime"),
            "location": e.get("location") or "", "calendar": cal}


def group_by_day(events, start, days):
    """Days from start, each with its events. An all-day event shows on every day it covers
    (its end date is exclusive); a timed event shows on the day it starts."""
    out = [{"date": (start + datetime.timedelta(days=i)).isoformat(), "events": []} for i in range(days)]
    idx = {d["date"]: d for d in out}
    for e in events:
        if e["all_day"]:
            try:
                a = datetime.date.fromisoformat(e["start"])
                b = datetime.date.fromisoformat(e["end"]) if e["end"] else a + datetime.timedelta(days=1)
            except (TypeError, ValueError):
                continue
            d = a
            while d < b:
                if d.isoformat() in idx:
                    idx[d.isoformat()]["events"].append(e)
                d += datetime.timedelta(days=1)
        elif e["start"] and e["start"][:10] in idx:
            idx[e["start"][:10]]["events"].append(e)
    for d in out:
        d["events"].sort(key=lambda e: (not e["all_day"], e["start"] or ""))
    return out


class Calendar(GoogleReadOnly):
    """Read-only Google Calendar client."""

    NAME = "Google Calendar"
    API = API
    NOT_FOUND = "couldn't find that calendar"

    def __init__(self, cfg, opener=None, clock=None, today=london_today):
        super().__init__(cfg, opener=opener, **({"clock": clock} if clock else {}))
        cals = cfg.get("calendars") or ["primary"]
        self.calendars = [c for c in cals if isinstance(c, str) and CAL_ID.fullmatch(c)]
        self._today = today

    def week(self, start=None, days=7):
        start, days = _window(start, days, self._today())
        tz = ZoneInfo(TZ)
        lo = datetime.datetime.combine(start, datetime.time(), tz)
        hi = lo + datetime.timedelta(days=days)
        events = []
        for cal in self.calendars:
            r = self._get(f"/calendars/{urllib.parse.quote(cal, safe='')}/events", timeMin=lo.isoformat(), timeMax=hi.isoformat(),
                          singleEvents="true", orderBy="startTime", maxResults=MAX_EVENTS, timeZone=TZ) or {}
            for e in r.get("items") or []:
                if e.get("status") != "cancelled":
                    events.append(_event(e, cal))
        return {"start": start.isoformat(), "days": group_by_day(events, start, days), "timezone": TZ}


class SampleCalendar:
    """Sample week for sample-data mode. Same answer shape as Calendar."""

    def __init__(self, today=london_today):
        self._today = today

    def week(self, start=None, days=7):
        start, days = _window(start, days, self._today())
        t = self._today()
        at = lambda d, hm: f"{(t + datetime.timedelta(days=d)).isoformat()}T{hm}:00+01:00"
        ev = [
            {"id": "s1", "title": "Gym: upper body", "all_day": False, "start": at(0, "07:00"), "end": at(0, "08:00"), "location": "PureGym", "calendar": "primary"},
            {"id": "s2", "title": "Dentist", "all_day": False, "start": at(1, "14:30"), "end": at(1, "15:00"), "location": "High Street Dental", "calendar": "primary"},
            {"id": "s3", "title": "Sunday lunch with Jordan", "all_day": False, "start": at(3, "13:00"), "end": at(3, "15:00"), "location": "", "calendar": "primary"},
            {"id": "s4", "title": "Bin day", "all_day": True, "start": (t + datetime.timedelta(days=2)).isoformat(),
             "end": (t + datetime.timedelta(days=3)).isoformat(), "location": "", "calendar": "primary"},
        ]
        return {"start": start.isoformat(), "days": group_by_day(ev, start, days), "timezone": TZ}
