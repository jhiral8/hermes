"""Expenditure estimator and weekly check-in for the Hermes health stack.

Replaces MacroFactor's coaching. Pure standard-library Python so it can run
on the server next to NutriTrace without extra packages.

Inputs are one record per calendar day:
    {"date": "2026-10-07", "intake": 2150 or None, "status": "complete",
     "weight": 80.4 or None}
status is one of: complete, estimated, partial, missing, fasting.

Design rules (from the Hermes Health Stack Plan):
- Every calculation uses real calendar days, so gaps never compress time
  (the bug in NutriTrace's Adaptive mode).
- Only complete, estimated and fasting days count as intake. Partial and
  missing days are left out, never read as low intake.
- When there is too little data, hold the previous estimate.
- Weekly target changes are capped and never imply losing more than 1% of
  body weight a week.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

KCAL_PER_KG = 7700.0
USABLE = {"complete", "estimated", "fasting"}


def _d(s):
    return s if isinstance(s, date) else date.fromisoformat(s)


@dataclass
class Settings:
    window_days: int = 28          # calendar days used for each estimate
    trend_alpha: float = 0.1       # per-day smoothing for trend weight
    min_intake_days: int = 14      # usable intake days needed in the window
    min_weigh_ins: int = 8         # weigh-ins needed in the window
    max_daily_move: float = 15.0   # max change in the estimate from one day to the next (~100 kcal/week)
    target_cap: float = 150.0      # max weekly change in calorie target
    max_loss_pct_week: float = 1.0 # never aim to lose faster than this


def trend_weights(days):
    """Time-aware exponential trend. Returns {date: trend_kg} for every day
    from the first weigh-in on, carrying the trend across days without a
    weigh-in. A gap of n days moves the trend as n days of smoothing would."""
    s = Settings()
    out, trend, last = {}, None, None
    for r in sorted(days, key=lambda r: _d(r["date"])):
        d = _d(r["date"])
        w = r.get("weight")
        if w is not None:
            if trend is None:
                trend = float(w)
            else:
                gap = (d - last).days
                k = 1 - (1 - s.trend_alpha) ** max(gap, 1)
                trend += k * (w - trend)
            last = d
        if trend is not None:
            out[d] = trend
    return out


def _slope(points):
    """Least-squares slope of (x, y); x is calendar days."""
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in points) / sxx


@dataclass
class Estimate:
    date: date
    expenditure: float | None
    raw: float | None
    held: bool
    reason: str
    intake_days: int
    weigh_ins: int
    longest_gap: int
    trend_kg: float | None
    trend_kg_per_week: float | None


def estimate_on(days, on, previous=None, settings=None):
    """Estimate expenditure using only data up to and including `on`."""
    s = settings or Settings()
    on = _d(on)
    start = on - timedelta(days=s.window_days - 1)
    window = [r for r in days if start <= _d(r["date"]) <= on]
    intake = [r["intake"] for r in window
              if r.get("status") in USABLE and r.get("intake") is not None]
    weighed = sorted(_d(r["date"]) for r in window if r.get("weight") is not None)

    # longest run of days without usable intake
    usable_dates = {_d(r["date"]) for r in window
                    if r.get("status") in USABLE and r.get("intake") is not None}
    gap = run = 0
    for i in range(s.window_days):
        if start + timedelta(days=i) in usable_dates:
            run = 0
        else:
            run += 1
            gap = max(gap, run)

    trends = trend_weights([r for r in days if _d(r["date"]) <= on])
    pts = [((d - start).days, t) for d, t in trends.items() if start <= d <= on]
    slope = _slope(pts) if len(pts) >= 2 else None
    trend_now = trends.get(on)

    base = dict(date=on, intake_days=len(intake), weigh_ins=len(weighed),
                longest_gap=gap, trend_kg=trend_now,
                trend_kg_per_week=None if slope is None else slope * 7)

    if len(intake) < s.min_intake_days or len(weighed) < s.min_weigh_ins or slope is None:
        why = []
        if len(intake) < s.min_intake_days:
            why.append(f"{len(intake)} usable intake days (need {s.min_intake_days})")
        if len(weighed) < s.min_weigh_ins:
            why.append(f"{len(weighed)} weigh-ins (need {s.min_weigh_ins})")
        return Estimate(expenditure=previous, raw=None, held=True,
                        reason="Holding: " + ", ".join(why or ["no trend"]), **base)

    raw = sum(intake) / len(intake) - slope * KCAL_PER_KG
    if previous is None:
        est = raw
    else:
        move = max(-s.max_daily_move, min(s.max_daily_move, raw - previous))
        est = previous + move
    return Estimate(expenditure=est, raw=raw, held=False, reason="Updated", **base)


def run_daily(days, first=None, last=None, seed=None, settings=None):
    """Replay day by day, as the app would have seen it. `seed` is a starting
    expenditure, for example MacroFactor's last value."""
    ds = sorted(_d(r["date"]) for r in days)
    first, last = _d(first or ds[0]), _d(last or ds[-1])
    prev, out = seed, []
    d = first
    while d <= last:
        e = estimate_on(days, d, prev, settings)
        out.append(e)
        prev = e.expenditure
        d += timedelta(days=1)
    return out


@dataclass
class CheckIn:
    date: date
    expenditure: float | None
    current_target: float
    proposed_target: float
    goal_kg_per_week: float
    note: str


def weekly_check_in(est: Estimate, current_target, goal_kg_per_week, settings=None):
    """Propose next week's calorie target. A proposal only: it is applied
    after Craig approves it."""
    s = settings or Settings()
    if est.held or est.expenditure is None:
        return CheckIn(est.date, est.expenditure, current_target, current_target,
                       goal_kg_per_week, "Keep the current target; collect more data. " + est.reason)
    goal = goal_kg_per_week
    if est.trend_kg and goal < 0:
        floor = -est.trend_kg * s.max_loss_pct_week / 100
        goal = max(goal, floor)
    ideal = est.expenditure + goal * KCAL_PER_KG / 7
    change = max(-s.target_cap, min(s.target_cap, ideal - current_target))
    proposed = round((current_target + change) / 10) * 10
    note = (f"Expenditure {est.expenditure:.0f} kcal/day from {est.intake_days} intake days "
            f"and {est.weigh_ins} weigh-ins; trend {est.trend_kg_per_week:+.2f} kg/week.")
    if abs(ideal - current_target) > s.target_cap:
        note += f" Change capped at {s.target_cap:.0f} kcal."
    return CheckIn(est.date, est.expenditure, current_target, proposed, goal, note)
