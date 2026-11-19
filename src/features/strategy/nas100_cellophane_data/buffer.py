from __future__ import annotations

import bisect
import datetime as dt

import numpy as np

from ..btc_cellophane_data.buffer import NotReady, robust_z
from . import cboe_feed, finra_feed
from .config_loader import needed

DAY = 86400
TOLERANCE = {"finra": 7 * DAY, "macro": 21 * DAY}
CBOE = {"VIXCLS": "VIX"}
FINRA_COLUMNS = {"short_volume", "short_exempt_volume", "total_volume"}
FINRA_DUE_HOUR_UTC = 20
BACKFILL_DAYS = 3


def source_of(sheet_id):
    parts = sheet_id.split(".")
    if parts[0] == "finra" and len(parts) == 3 and parts[2] in FINRA_COLUMNS:
        return "finra"
    if parts[0] == "macro" and len(parts) == 3 and parts[1] in CBOE and parts[2] == "value":
        return "macro"
    raise ValueError(f"모르는 장 {sheet_id}")


def dedupe(rows):
    ordered = sorted(rows, key=lambda r: (r[0], r[1]))
    last = {r[0]: r for r in ordered}
    out = [last[k] for k in sorted(last)]
    return out


class Buffer:
    def __init__(self, sheets):
        self.sheets = {s["id"]: s for s in sheets}
        self.rows = {s["id"]: [] for s in sheets}
        self.checked = {s["id"]: dt.date.min for s in sheets}

    def backfill_start(self, sheet, today):
        out = today - dt.timedelta(days=int(needed(sheet["term"]) * 7 / 5) + 14 * BACKFILL_DAYS)
        return out

    def finra(self, sheet, now):
        _, symbol, column = sheet["id"].split(".")
        sid = sheet["id"]
        have = self.rows[sid]
        today = now.date()
        start = max(have[-1][1], self.checked[sid]) + dt.timedelta(days=1) if have else self.backfill_start(sheet, today)
        end = today if now.hour >= FINRA_DUE_HOUR_UTC else today - dt.timedelta(days=1)
        if start > end:
            return have
        fresh = finra_feed.rows(symbol, column, start, end)
        self.checked[sid] = end if fresh and fresh[-1][1] == end or end < today else end - dt.timedelta(days=1)
        out = have + fresh
        return out

    def macro(self, sheet, now):
        name = CBOE[sheet["id"].split(".")[1]]
        have = self.rows[sheet["id"]]
        today = now.date()
        start = have[-1][1] + dt.timedelta(days=1) if have else self.backfill_start(sheet, today)
        got, published = cboe_feed.rows(name, start)
        if not have:
            fresh = [(cboe_feed.day_end_s(d), d, v) for d, v in got]
            return fresh
        fresh = [(max(cboe_feed.day_end_s(d), published), d, v) for d, v in got if d > have[-1][1]]
        out = have + fresh
        return out

    def refresh(self, end_s):
        now = dt.datetime.fromtimestamp(end_s, dt.timezone.utc)
        for sid, sheet in self.sheets.items():
            got = self.finra(sheet, now) if source_of(sid) == "finra" else self.macro(sheet, now)
            kept = dedupe([r for r in got if r[0] <= end_s])
            if len(kept) < needed(sheet["term"]):
                raise NotReady(f"{sid}: 값이 {len(kept)}개다. {needed(sheet['term'])}개가 필요하다")
            self.rows[sid] = kept[-needed(sheet["term"]) - 2:]

    def native(self, sheet):
        rows = self.rows[sheet["id"]]
        z = robust_z(np.array([v for _, _, v in rows], dtype="float64"), sheet["term"])
        out = [(r[0], float(x)) for r, x in zip(rows, z)]
        return out

    def z_at(self, sheet, ts):
        native = self.native(sheet)
        stamps = [t for t, _ in native]
        i = bisect.bisect_right(stamps, ts) - 1
        if i < 0 or ts - stamps[i] > TOLERANCE[source_of(sheet["id"])]:
            return None
        value = native[i][1]
        out = None if np.isnan(value) else value
        return out

    def z(self, sheet, prev_s, now_s):
        out = (self.z_at(sheet, prev_s), self.z_at(sheet, now_s))
        return out
