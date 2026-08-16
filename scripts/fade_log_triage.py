"""Polymarket fade 로그 분류 — stuck 루프 탐지용.

fade 엔진은 실패해도 다음 틱에 다시 시도한다. 그 설계 자체는 맞지만, 재시도로
절대 풀리지 않는 실패(지갑에 물량 0, 담보가 미체결 주문에 묶임, 최소수량 미달)가
섞이면 초당 수 회씩 영원히 돈다. 로그가 그걸로 덮여 정상 활동이 안 보이고,
그 행이 슬롯을 물고 있어 신규 진입이 막힌다.

이 스크립트는 **재시도로 풀리는 실패**와 **영원히 도는 실패**를 갈라낸다.

사용:
    railway logs --json | python3 scripts/fade_log_triage.py
    python3 scripts/fade_log_triage.py saved_logs.txt
    python3 scripts/fade_log_triage.py saved_logs.txt --min-attempts 5 --window-min 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime

# ── 분류 규칙 ────────────────────────────────────────────────────────────────
# (라벨, 정규식, 재시도로 풀리나)
KINDS: list[tuple[str, re.Pattern, bool]] = [
    ("청산매도 미체결",   re.compile(r"청산 매도 미체결"),            True),
    ("진입 미체결",       re.compile(r"진입 미체결"),                 True),
    ("add-on 미체결",     re.compile(r"add-on 미체결"),               True),
    ("지갑없음 정리",     re.compile(r"지갑에 물량 없음"),            False),
    ("현금확보 매도",     re.compile(r"현금 확보 매도"),              True),
    ("현금확보 실패",     re.compile(r"현금 확보 실패|매도 실패"),    True),
    ("호가조회 실패",     re.compile(r"호가 조회 실패"),              True),
    ("지갑조회 실패",     re.compile(r"지갑 조회 실패"),              True),
    ("진입 시도",         re.compile(r"SIGNAL 진입"),                 True),
    ("슬롯 교체",         re.compile(r"슬롯 교체"),                   True),
    ("ADD-ON 시도",       re.compile(r"ADD-ON"),                      True),
    ("스크리너",          re.compile(r"스크리너|screener"),           True),
    ("WS",                re.compile(r"\bWS\b|구독"),                 True),
]

# 재시도로 절대 안 풀리는 에러 — 사람이 개입해야 한다
HARD = [
    ("지갑 잔량 0",       re.compile(r"balance:\s*0\b")),
    ("담보가 주문에 묶임", re.compile(r"sum of active orders:\s*(?!0\b)\d+")),
    ("최소수량 미달",     re.compile(r"minimum:\s*[\d.]+")),
    ("잔액 부족",         re.compile(r"not enough balance")),
]

TS = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")
# "→ 포지션 유지: <질문> | {...}" / "정리(슬롯 반납): <질문>" 등에서 종목명 추출
MARKET = re.compile(r"(?:유지|반납|반영|스킵|정리)\s*[:：]\s*([^|{]+)")
MARKET2 = re.compile(r"\|\s*([^|]{8,60}?)\s*\|")


def parse(stream) -> list[dict]:
    out = []
    for raw in stream:
        raw = raw.rstrip("\n")
        if not raw.strip():
            continue
        msg, ts = raw, None
        if raw.lstrip().startswith("{"):
            try:
                o = json.loads(raw)
                msg = str(o.get("message", raw))
                ts = o.get("timestamp")
            except Exception:
                pass
        if ts is None:
            m = TS.search(msg)
            ts = m.group(1) if m else None
        out.append({"ts": ts, "msg": msg})
    return out


def to_dt(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def market_of(msg: str) -> str:
    m = MARKET.search(msg)
    if m:
        return m.group(1).strip()[:44]
    m = MARKET2.search(msg)
    if m:
        return m.group(1).strip()[:44]
    return "-"


def classify(msg: str):
    for label, rx, retryable in KINDS:
        if rx.search(msg):
            return label, retryable
    return None, None


def hard_reason(msg: str) -> str | None:
    for label, rx in HARD:
        if rx.search(msg):
            return label
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="로그 파일 (없으면 stdin)")
    ap.add_argument("--min-attempts", type=int, default=3, help="stuck 판정 최소 반복 (기본 3)")
    ap.add_argument("--window-min", type=float, default=3.0, help="stuck 판정 최소 지속 분 (기본 3)")
    a = ap.parse_args()

    src = open(a.path, encoding="utf-8", errors="replace") if a.path else sys.stdin
    rows = parse(src)
    fade = [r for r in rows if "[fade]" in r["msg"] or "polymarket" in r["msg"].lower()]
    fade = [r for r in fade if "HTTP/1.1" not in r["msg"]]

    dts = [d for d in (to_dt(r["ts"]) for r in fade) if d]
    print(f"전체 {len(rows)}줄 · fade {len(fade)}줄", end="")
    if dts:
        span = (max(dts) - min(dts)).total_seconds() / 60
        print(f" · {min(dts):%m-%d %H:%M} ~ {max(dts):%H:%M} ({span:.0f}분)")
    else:
        print()
    if not fade:
        print("\nfade 로그 없음."); return
    last_dt = max(dts) if dts else None

    groups = defaultdict(list)
    unknown = []
    for r in fade:
        kind, retryable = classify(r["msg"])
        if kind is None:
            unknown.append(r); continue
        groups[(kind, market_of(r["msg"]), retryable)].append(r)

    # ── stuck: 같은 (유형, 종목) 이 반복되고 지속 시간이 있는 것 ───────────────
    stuck = []
    for (kind, mkt, retryable), rs in groups.items():
        ds = [d for d in (to_dt(r["ts"]) for r in rs) if d]
        span = (max(ds) - min(ds)).total_seconds() / 60 if len(ds) > 1 else 0.0
        if len(rs) < a.min_attempts or span < a.window_min:
            continue
        reasons = {}
        for r in rs:
            h = hard_reason(r["msg"])
            if h:
                reasons[h] = reasons.get(h, 0) + 1
        ongoing = bool(ds and last_dt and (last_dt - max(ds)).total_seconds() < 180)
        stuck.append({"kind": kind, "mkt": mkt, "n": len(rs), "span": span,
                      "rate": len(rs) / span if span > 0 else 0,
                      "reasons": reasons, "ongoing": ongoing,
                      "first": min(ds) if ds else None, "last": max(ds) if ds else None,
                      "retryable": retryable,
                      "sample": rs[-1]["msg"]})
    stuck.sort(key=lambda s: -s["n"])

    hard_stuck = [s for s in stuck if s["reasons"]]
    soft_stuck = [s for s in stuck if not s["reasons"]]

    if hard_stuck:
        print(f"\n{'='*78}\n🔴 STUCK — 재시도로 안 풀린다. 개입 필요\n{'='*78}")
        for s in hard_stuck:
            live = " ◀ 지금도 진행중" if s["ongoing"] else ""
            print(f"\n  [{s['kind']}] {s['mkt']}{live}")
            print(f"    {s['n']}회 · {s['span']:.0f}분 · 분당 {s['rate']:.1f}회 "
                  f"· {s['first']:%H:%M}~{s['last']:%H:%M}")
            for rsn, c in sorted(s["reasons"].items(), key=lambda x: -x[1]):
                print(f"    → {rsn} ({c}회)")
    else:
        print("\n🔴 STUCK (개입 필요): 없음")

    if soft_stuck:
        print(f"\n{'='*78}\n⚠  반복 중 — 재시도로 풀릴 수 있음 (호가 대기 등)\n{'='*78}")
        for s in soft_stuck[:12]:
            live = " ◀ 진행중" if s["ongoing"] else ""
            print(f"  [{s['kind']}] {s['mkt']}{live}")
            print(f"    {s['n']}회 · {s['span']:.0f}분 · 분당 {s['rate']:.1f}회")

    print(f"\n{'='*78}\n유형별 집계\n{'='*78}")
    agg = defaultdict(int)
    for (kind, _, _), rs in groups.items():
        agg[kind] += len(rs)
    for k, n in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"  {k:18s} {n:5d}")

    if unknown:
        print(f"\n{'='*78}\n분류 미상 {len(unknown)}줄 — 규칙 추가 검토\n{'='*78}")
        seen = set()
        for r in unknown:
            key = re.sub(r"[\d.]+", "#", r["msg"])[:70]
            if key in seen:
                continue
            seen.add(key)
            print(f"  {r['msg'][:150]}")
            if len(seen) >= 10:
                break


if __name__ == "__main__":
    main()
