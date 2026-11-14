from __future__ import annotations

import bisect
import datetime as dt
from collections import deque

import numpy as np
import pandas as pd

from . import binance_feed, cme_feed
from .config_loader import needed

BAR = binance_feed.BAR
FETCH = {"open_interest": binance_feed.open_interest, "long_short_ratio": binance_feed.long_short_ratio,
         "spot_sell_volume": binance_feed.spot_sell_volume}
CME_FILES = 8
CME_TOLERANCE = 7 * 86400
CME_START_HOUR_UTC = 5
REL_OP = {"rel.venue.binance_vs_cme_open_interest.diff": "diff", "rel.venue.binance_vs_cme_open_interest.ratio": "ratio"}
DIRECT = {"metrics.count_long_short_ratio": "long_short_ratio", "klines.spot.sell_volume": "spot_sell_volume"}


class NotReady(Exception):
    pass


def robust_z(x, term):
    s = pd.Series(x)
    med = s.rolling(term, min_periods=term).median()
    mad = (s - med).abs().rolling(term, min_periods=term).median() * 1.4826
    z = (s - med) / mad.where(mad > 0)
    return z.to_numpy()


class Buffer:
    def __init__(self, symbol, sizes):
        self.symbol = symbol
        self.rows = {name: deque(maxlen=size) for name, size in sizes.items()}
        self.cme = {}

    def refresh(self, end_s):
        if end_s % BAR:
            raise ValueError(f"end_s {end_s} 가 5분 경계가 아니다")
        for name, rows in self.rows.items():
            missing = (end_s - rows[-1][0]) // BAR if rows else rows.maxlen
            if missing > 0:
                got = FETCH[name](self.symbol, min(missing, rows.maxlen), end_s)
                rows.extend(r for r in got if not rows or r[0] > rows[-1][0])
            if rows[-1][0] != end_s:
                raise NotReady(f"{name}: 마지막 행이 {rows[-1][0]} 이다. {end_s} 가 아직 없다")
        if self.cme_due(end_s):
            files = cme_feed.latest(CME_FILES, self.cme)
            self.cme = {r["name"]: r for r in files}

    def cme_due(self, end_s):
        if not self.cme:
            return True
        now = dt.datetime.fromtimestamp(end_s, dt.timezone.utc)
        if now.weekday() >= 5 or now.hour < CME_START_HOUR_UTC:
            return False
        newest = max(r["available_at"] for r in self.cme.values())
        out = dt.datetime.fromtimestamp(newest, dt.timezone.utc).date() < now.date()
        return out

    def cme_at(self, ts):
        files = sorted(self.cme.values(), key=lambda r: r["available_at"])
        stamps = [r["available_at"] for r in files]
        i = bisect.bisect_right(stamps, ts) - 1
        if i < 0 or ts - stamps[i] > CME_TOLERANCE:
            return None
        out = files[i]["open_interest_btc"]
        return out

    def values(self, sheet_id):
        if sheet_id in DIRECT:
            out = np.array([v for _, v in self.rows[DIRECT[sheet_id]]], dtype="float64")
            return out
        op = REL_OP[sheet_id]
        pairs = [(v, self.cme_at(ts)) for ts, v in self.rows["open_interest"]]
        if op == "diff":
            vals = [a - b for a, b in pairs if b is not None]
        else:
            vals = [a / b for a, b in pairs if b is not None and b != 0]
        out = np.array(vals, dtype="float64")
        return out

    def z(self, sheet):
        vals = self.values(sheet["id"])
        need = needed(sheet["term"])
        if len(vals) < need:
            raise ValueError(f"{sheet['id']}: 값이 {len(vals)}개다. {need}개가 필요하다")
        pair = robust_z(vals[-need:], sheet["term"])[-2:]
        out = tuple(None if np.isnan(z) else float(z) for z in pair)
        return out
