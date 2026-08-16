"""체결 실패 리포트 — 주문은 냈는데 안 붙은 건들과 그 이유.

원장의 `order_status='live'` 는 "호가에 얹혔을 뿐 체결 안 됨" 이다. 이 스크립트는
그 건들에 대해 **그때 시장이 실제로 얼마였는지**를 CLOB 히스토리로 되짚어
왜 안 붙었는지, 붙으려면 얼마였어야 하는지를 보여준다.

판정 원리 — NO 를 사려면 NO ask(= 1 − YES bid)를 내야 한다. 우리가 낸 값이 b 면
YES 가 (1 − b) 까지 올라와야 체결된다. 그 수준에 실제로 닿았는지를 본다.

    PYTHONPATH=src python3 scripts/fade_fill_failure_report.py
    PYTHONPATH=src python3 scripts/fade_fill_failure_report.py --csv out.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from features.strategy.polymarket._data import client as pc  # noqa: E402

BACKUP = Path(__file__).resolve().parents[1] / "data" / "fade_backup_20260815"
WINDOW_H = 24.0          # 주문 후 이만큼 안에 닿았는지 본다


def load_rows():
    """백업 원장 + (있으면) 현재 DB 를 합친다."""
    rows, watch = [], {}
    wp = BACKUP / "polymarket_fade_watch.csv"
    if wp.exists():
        for r in csv.DictReader(open(wp, encoding="utf-8")):
            watch[r["condition_id"]] = r
    pp = BACKUP / "polymarket_fade_positions.csv"
    if pp.exists():
        for r in csv.DictReader(open(pp, encoding="utf-8")):
            r["_src"] = "backup"
            rows.append(r)
    url = os.environ.get("DATABASE_URL", "")
    if url:
        try:
            import psycopg2
            c = psycopg2.connect(url, connect_timeout=20)
            cur = c.cursor()
            cur.execute("select condition_id,question,yes_token_id from polymarket_fade_watch")
            for cid, q, yt in cur.fetchall():
                watch.setdefault(cid, {"condition_id": cid, "question": q, "yes_token_id": yt})
            cur.execute("""select id,condition_id,question,p0,entry_px,target_px,entry_ts,
                           status,order_status,shares,entry_usd,exit_px,exit_reason,ret_pct
                           from polymarket_fade_positions""")
            cols = ["id", "condition_id", "question", "p0", "entry_px", "target_px", "entry_ts",
                    "status", "order_status", "shares", "entry_usd", "exit_px", "exit_reason", "ret_pct"]
            for t in cur.fetchall():
                d = {k: ("" if v is None else str(v)) for k, v in zip(cols, t)}
                d["_src"] = "live"
                rows.append(d)
        except Exception as e:                                   # noqa: BLE001
            print(f"  (현재 DB 조회 실패, 백업만 사용: {e})")
    return rows, watch


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="결과를 CSV 로도 저장")
    a = ap.parse_args()

    rows, watch = load_rows()
    unfilled = [r for r in rows if (r.get("order_status") or "") == "live"]
    print(f"원장 {len(rows)}행 · 미체결(order_status=live) {len(unfilled)}행\n")
    if not unfilled:
        print("미체결 건 없음."); return

    sem = asyncio.Semaphore(10)
    out = []

    async def one(r):
        cid = r["condition_id"]
        w = watch.get(cid) or {}
        tok = w.get("yes_token_id")
        try:
            entry_px = float(r["entry_px"]); ts = int(float(r["entry_ts"]))
        except Exception:
            return
        asked_no = round(1 - entry_px, 4)          # 옛 코드가 낸 값 = 1 − YES mid
        need_yes = round(1 - asked_no, 4)          # 체결되려면 YES 가 여기까지
        rec = {"id": r.get("id", "?"), "question": (r.get("question") or cid)[:44],
               "ts": datetime.fromtimestamp(ts, UTC).strftime("%m-%d %H:%M"),
               "entry_yes": entry_px, "asked_no": asked_no, "need_yes": need_yes,
               "reason": "", "mkt_max": None, "gap": None}
        if not tok:
            rec["reason"] = "yes_token 없음(워치리스트 이탈)"
            out.append(rec); return
        async with sem:
            pts = None
            try:
                # 시그니처: (token_id, fidelity, start_ts, end_ts)
                raw = await pc.fetch_curve_full(tok, 10, ts, ts + int(WINDOW_H * 3600))
                pts = [[int(p["ts"]), float(p["price"])] for p in (raw or [])]
            except Exception as e:                               # noqa: BLE001
                rec["reason"] = f"조회 오류: {str(e)[:40]}"
        if not pts:
            if not rec["reason"]:
                rec["reason"] = "히스토리 없음(종료 마켓 등)"
            out.append(rec); return
        mx = max(p[1] for p in pts)
        rec["mkt_max"] = round(mx, 4)
        rec["gap"] = round(need_yes - mx, 4)
        rec["reason"] = ("체결 가능했음(호가/타이밍 문제)" if mx >= need_yes
                         else "시장이 그 값에 안 닿음")
        out.append(rec)

    await asyncio.gather(*(one(r) for r in unfilled))
    out.sort(key=lambda x: (x["reason"], -(x["gap"] or 0)))

    print(f"{'id':>4s} {'시각':>11s} {'진입YES':>8s} {'낸값NO':>7s} {'필요YES':>8s} "
          f"{'24h최고':>8s} {'모자란폭':>8s}  사유")
    print("─" * 108)
    for r in out:
        print(f"{str(r['id']):>4s} {r['ts']:>11s} {r['entry_yes']:8.4f} {r['asked_no']:7.4f} "
              f"{r['need_yes']:8.4f} {str(r['mkt_max'] or '-'):>8s} {str(r['gap'] or '-'):>8s}  "
              f"{r['reason']}  {r['question'][:34]}")

    from collections import Counter
    print("\n=== 사유별 ===")
    for k, v in Counter(r["reason"] for r in out).most_common():
        print(f"  {k:34s} {v:4d}건")
    gaps = [r["gap"] for r in out if r["gap"] is not None and r["gap"] > 0]
    if gaps:
        import statistics as st
        print(f"\n  '안 닿음' 건의 모자란 폭: 중앙 {st.median(gaps):.4f} · "
              f"최소 {min(gaps):.4f} · 최대 {max(gaps):.4f}")
        print(f"  → 이만큼 더 얹었으면(=ask 에 걸었으면) 체결됐다는 뜻")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader(); w.writerows(out)
        print(f"\n  CSV 저장: {a.csv}")


if __name__ == "__main__":
    asyncio.run(main())
