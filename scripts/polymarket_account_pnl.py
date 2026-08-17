"""폴리마켓 계좌 손익 — 체결 원본(data-api/activity) 기준 현금흐름 집계.

앱 원장(polymarket_fade_positions)은 "주문을 낼 때" 기록하므로 부분체결·raise_cash
매도·재진입을 못 따라간다. 이 스크립트는 원장을 안 믿고 체인에 남은 체결만 센다.

    순손익 = 매도 수취 + 리딤 수취 + 현재 보유 평가 − 매수 지출

    PYTHONPATH=src python3 scripts/polymarket_account_pnl.py
    PYTHONPATH=src python3 scripts/polymarket_account_pnl.py --since 2026-08-16T06:55:14Z
    PYTHONPATH=src python3 scripts/polymarket_account_pnl.py --since 2026-08-16T06:55:14Z --csv out.csv

--since 는 그 시각 이후 체결만 센다. 보유 평가는 --since 이후 매수분에 해당하는
수량만 잡는다(그 이전에 산 물량까지 더하면 매수 지출 없이 평가만 생기는 착시).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

DATA_API = "https://data-api.polymarket.com"
TIMEOUT = 40.0
PAGE = 500


def wallet() -> str:
    addr = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    if not addr:
        raise SystemExit("POLYMARKET_WALLET_ADDRESS 환경변수 필요")
    return addr


def parse_since(s: str | None) -> int | None:
    if s is None:
        return None
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise SystemExit(f"--since 는 타임존이 필요하다 (예: 2026-08-16T06:55:14Z): {s}")
    return int(t.timestamp())


async def fetch_activity(cli: httpx.AsyncClient, addr: str) -> list[dict]:
    out, off = [], 0
    while True:
        r = await cli.get(f"{DATA_API}/activity",
                          params={"user": addr, "limit": PAGE, "offset": off})
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):
            raise SystemExit(f"activity 응답이 리스트가 아니다: {type(batch)}")
        out.extend(batch)
        if len(batch) < PAGE:
            return out
        off += PAGE


async def fetch_positions(cli: httpx.AsyncClient, addr: str) -> list[dict]:
    r = await cli.get(f"{DATA_API}/positions", params={"user": addr, "limit": PAGE})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise SystemExit(f"positions 응답이 리스트가 아니다: {type(data)}")
    return data


def aggregate(acts: list[dict], pos: list[dict], since: int | None) -> dict:
    kept = [a for a in acts if since is None or int(a["timestamp"]) >= since]

    buy_usd = sell_usd = redeem_usd = 0.0
    n_buy = n_sell = n_redeem = 0
    bought_sh: dict[str, float] = defaultdict(float)
    markets: dict[str, dict] = defaultdict(
        lambda: {"buy_usd": 0.0, "buy_sh": 0.0, "sell_usd": 0.0, "sell_sh": 0.0,
                 "redeem_usd": 0.0, "asset": None})

    for a in kept:
        typ = str(a.get("type") or "").upper()
        side = str(a.get("side") or "").upper()
        usd = float(a.get("usdcSize") or 0)
        sz = float(a.get("size") or 0)
        title = str(a.get("title") or a.get("conditionId") or "?")
        asset = str(a.get("asset") or "")
        m = markets[title]
        m["asset"] = m["asset"] or asset
        if typ == "TRADE" and side == "BUY":
            buy_usd += usd; n_buy += 1
            m["buy_usd"] += usd; m["buy_sh"] += sz
            bought_sh[asset] += sz
        elif typ == "TRADE" and side == "SELL":
            sell_usd += usd; n_sell += 1
            m["sell_usd"] += usd; m["sell_sh"] += sz
        elif typ in ("REDEEM", "CONVERSION"):
            redeem_usd += usd; n_redeem += 1
            m["redeem_usd"] += usd

    held_val = 0.0
    held_rows = []
    for p in pos:
        asset = str(p.get("asset") or "")
        size = float(p.get("size") or 0)
        val = float(p.get("currentValue") or 0)
        if size <= 0.01 or val <= 0.01:
            continue
        eligible = size if since is None else min(size, bought_sh.get(asset, 0.0))
        if eligible <= 0.01:
            continue
        v = val * eligible / size
        held_val += v
        held_rows.append({
            "title": str(p.get("title") or "?"), "outcome": str(p.get("outcome") or ""),
            "size": eligible, "avg": float(p.get("avgPrice") or 0),
            "cur": float(p.get("curPrice") or 0), "value": v,
        })

    return {
        "n_events": len(kept), "n_total": len(acts),
        "buy_usd": buy_usd, "sell_usd": sell_usd, "redeem_usd": redeem_usd,
        "n_buy": n_buy, "n_sell": n_sell, "n_redeem": n_redeem,
        "held_val": held_val, "held_rows": held_rows,
        "net": sell_usd + redeem_usd + held_val - buy_usd,
        "markets": markets,
        "first_ts": min((int(a["timestamp"]) for a in kept), default=None),
        "last_ts": max((int(a["timestamp"]) for a in kept), default=None),
    }


def render(r: dict, since: int | None) -> None:
    if since is not None:
        s = datetime.fromtimestamp(since, UTC)
        print(f"기준: {s:%Y-%m-%d %H:%M:%S}Z 이후 체결만 · "
              f"전체 {r['n_total']}건 중 {r['n_events']}건")
    else:
        print(f"기준: 전체 기간 · {r['n_events']}건")
    if r["first_ts"]:
        a = datetime.fromtimestamp(r["first_ts"], UTC)
        b = datetime.fromtimestamp(r["last_ts"], UTC)
        print(f"구간: {a:%Y-%m-%d %H:%M}Z ~ {b:%Y-%m-%d %H:%M}Z")
    print()
    print(f"  매수 지출      -${r['buy_usd']:>10,.2f}   ({r['n_buy']}건)")
    print(f"  매도 수취      +${r['sell_usd']:>10,.2f}   ({r['n_sell']}건)")
    print(f"  리딤/전환 수취  +${r['redeem_usd']:>10,.2f}   ({r['n_redeem']}건)")
    print(f"  현재 보유 평가  +${r['held_val']:>10,.2f}   ({len(r['held_rows'])}건)")
    print("  " + "─" * 40)
    print(f"  순손익         {'-' if r['net'] < 0 else '+'}${abs(r['net']):>10,.2f}")

    if r["held_rows"]:
        print("\n보유")
        for h in sorted(r["held_rows"], key=lambda x: -x["value"]):
            print(f"  {h['title'][:46]:46} {h['outcome']:4} {h['size']:>7.2f}주 "
                  f"avg={h['avg']:.4f} cur={h['cur']:.4f}  ${h['value']:>7.2f}")

    rows = []
    for title, m in r["markets"].items():
        held = sum(h["value"] for h in r["held_rows"] if h["title"] == title)
        net = m["sell_usd"] + m["redeem_usd"] + held - m["buy_usd"]
        straddle = m["sell_sh"] > m["buy_sh"] + 0.01 or (m["redeem_usd"] > 0 and m["buy_sh"] <= 0.01)
        closed = m["buy_sh"] > 0 and m["sell_sh"] >= m["buy_sh"] - 0.01
        state = "구간걸침" if straddle else ("청산" if closed else "보유")
        rows.append((net, title, m, held, state))
    rows.sort()

    print(f"\n마켓별 ({len(rows)}개)")
    print(f"  {'마켓':44} {'매수$':>9} {'매도$':>9} {'리딤$':>9} {'보유$':>8} {'순$':>9}  상태")
    for net, title, m, held, state in rows:
        print(f"  {title[:44]:44} {m['buy_usd']:>9.2f} {m['sell_usd']:>9.2f} "
              f"{m['redeem_usd']:>9.2f} {held:>8.2f} {net:>+9.2f}  {state}")

    strad = [x for x in rows if x[4] == "구간걸침"]
    if strad:
        leak = sum(x[0] for x in strad)
        print(f"\n  구간걸침 {len(strad)}개 — 매수는 기준 이전, 대금만 이후에 들어온 건이다.")
        print("  들어온 대금에 대응하는 매수 지출이 구간 밖이라 순손익이 아니다.")
        for net, title, m, held, _ in strad:
            print(f"    {title[:52]:52} {net:>+9.2f}")
        print(f"\n  구간걸침 제외 순손익  {'-' if r['net'] - leak < 0 else '+'}${abs(r['net'] - leak):>10,.2f}")


def write_csv(r: dict, path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["market", "buy_usd", "buy_shares", "sell_usd", "sell_shares",
                    "redeem_usd", "held_value_usd", "net_usd"])
        for title, m in r["markets"].items():
            held = sum(h["value"] for h in r["held_rows"] if h["title"] == title)
            w.writerow([title, round(m["buy_usd"], 4), round(m["buy_sh"], 2),
                        round(m["sell_usd"], 4), round(m["sell_sh"], 2),
                        round(m["redeem_usd"], 4), round(held, 4),
                        round(m["sell_usd"] + m["redeem_usd"] + held - m["buy_usd"], 4)])
    print(f"\ncsv → {path}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO8601 (예: 2026-08-16T06:55:14Z)")
    ap.add_argument("--csv", help="마켓별 집계를 csv 로 저장")
    args = ap.parse_args()

    since = parse_since(args.since)
    addr = wallet()
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        acts, pos = await asyncio.gather(fetch_activity(cli, addr), fetch_positions(cli, addr))

    print(f"지갑 {addr}\n")
    r = aggregate(acts, pos, since)
    render(r, since)
    if args.csv:
        write_csv(r, args.csv)


if __name__ == "__main__":
    asyncio.run(main())
