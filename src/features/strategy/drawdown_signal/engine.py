"""드로다운 매수 — 포워드 테스트 엔진 (일봉, 하루 1회).

백테스트: `backtest_quant/src/research/serve_drawdown_recovery.py` (에피소드 랩)
여기: 같은 규칙을 실시간으로 돌리고 **원장에 기록**한다.

────────────────────────────────────────────────────────────
알림봇이 아니라 원장인 이유
────────────────────────────────────────────────────────────
"신호 떴다"만 보내면 포워드 테스트가 아니라 알림이다. 포워드 테스트가 되려면
**결과를 모르는 시점에 기록이 남아야** 한다. 그래서 진입가·기준고점·날짜를 박고,
실행될 때마다 열린 에피소드의 추가 MAE·물밑 일수·현재 수익을 갱신한다.

`backfilled` 플래그가 핵심이다. 원장이 생기기 전에 이미 시작된 에피소드는
사후 발견이지 실시간 포착이 아니다. 섞으면 포워드 테스트가 오염되므로 분리해서 센다.

────────────────────────────────────────────────────────────
규칙 — 백테스트와 동일해야 의미가 있다
────────────────────────────────────────────────────────────
  진입   기준고점(252일 롤링) 대비 dd 가 TRIGGER(-30%) 하향 돌파
  래더   백테스트 기본값 `ladder2` 와 동일하게 **-30% / -40% / -50% 3회 등주수 체결**
         → 수익률은 첫 진입가가 아니라 **평단(avg_cost)** 기준
  청산   진입 시점 기준고점 회복
  재진입 dd 가 RESET(-10%) 위로 회복한 뒤에만 다음 에피소드 인정
  보유일 상한 없음 — 백테스트에서 상한이 결론을 왜곡한 전례가 있어 두지 않는다

두 번 어긋났던 곳 (둘 다 조용히 틀리는 종류라 기록해둔다):
  1. **auto_adjust=True** 여야 한다. 백테스트가 수정주가를 쓴다. 원주가로 재면 배당 종목에서
     롤링 고점·dd 가 달라져 트리거 날짜 자체가 어긋난다.
     (IPO 분석에서는 반대로 False 가 맞았다 — 거긴 과거 계약가격과 비교하니까.
      질문이 다르면 올바른 설정도 다르다.)
  2. **리셋 규칙**이 없으면 가격이 트리거 선을 넘나들 때 같은 하락을 여러 번 새 신호로 센다.
     삼성전자 2026-07: -32.7%(20일) → -28.6%(21일) → -31.2%(24일) → -38.6%(28일).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
import threading

import pandas as pd

logger = logging.getLogger(__name__)

# ── 규칙 파라미터 (백테스트와 동일하게 유지) ──────────────────
REF_WINDOW = 252            # 52주 롤링 고점
TRIGGER = -0.30             # 1차 진입
LADDER_STEP = 0.10          # 추가 체결 간격 (백테스트 ladder2 의 step)
LADDER_FILLS = 3            # 총 체결 횟수 → -30% / -40% / -50%
RESET = -0.10               # 재진입 허용 기준

FILL_LEVELS = [round(TRIGGER - k * LADDER_STEP, 6) for k in range(LADDER_FILLS)]

UNIVERSE: dict[str, str] = {
    "^KS11": "KOSPI", "^NDX": "Nasdaq 100", "^GSPC": "S&P 500", "^DJI": "Dow",
    "^RUT": "Russell 2000", "^GDAXI": "DAX", "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225", "^HSI": "Hang Seng",
    "GC=F": "Gold", "SI=F": "Silver", "CL=F": "WTI", "NG=F": "Nat Gas",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "AMZN": "Amazon",
    "GOOGL": "Alphabet", "META": "Meta", "TSLA": "Tesla", "AMD": "AMD",
    "NFLX": "Netflix", "INTC": "Intel",
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스",
    # 카카오(035720.KS) 제외 — 2021-09 고점 이후 회복 전망이 없어 에피소드가 영구 미완결이다.
    # 백테스트 2건 중 1건이 4,975일(13.6년) 보유·물밑 2,178일이고 나머지 1건은 진행 중.
}

LEDGER = pathlib.Path(__file__).resolve().parents[4] / "data" / "drawdown_forward_ledger.json"


# ═══════════════════════════════════════════════════════════
# 가격 · 신호
# ═══════════════════════════════════════════════════════════
def load_prices(symbol: str, lookback_days: int = 1300) -> pd.Series | None:
    import yfinance as yf
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=lookback_days)
    try:
        df = yf.Ticker(symbol).history(
            start=start.isoformat(), end=end.isoformat(),
            auto_adjust=True, actions=False, raise_errors=False,   # ★ 백테스트와 동일
        )
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df:
        return None
    px = df["Close"].dropna()
    if len(px) < REF_WINDOW:
        return None
    px.index = pd.to_datetime(px.index).tz_localize(None)
    return px


# 보유 기간별 수익률을 재는 지점 (신호일로부터 **거래일** 수).
# 달력일이 아니다 — 종목마다 휴장이 달라 달력일로 재면 같은 x 좌표가 서로 다른 봉 수가 된다.
PATH_BUCKETS = [0, 5, 20, 60, 120, 250]


def _return_path(prices, entry: int, fill_idx: list[int]) -> dict[str, dict]:
    """구간별 (래더 vs 1차 진입만) 수익률 — 리플레이가 이미 든 가격으로 같이 계산한다.

    핵심은 **그 시점까지 체결된 것만** 평단에 넣는 것이다. 전체 평단(-50% 체결 포함)을
    +5일 지점에 적용하면 아직 일어나지 않은 매수를 소급 적용하는 셈이라
    물타기가 실제보다 좋아 보인다 — 미래 정보 누수다.
    """
    out: dict[str, dict] = {}
    n = len(prices)
    first = float(prices[entry])
    for nd in PATH_BUCKETS:
        j = entry + nd
        if j >= n:
            break                                  # 아직 그만큼 안 지났다 → 구간 자체를 비움
        so_far = [float(prices[i]) for i in fill_idx if i <= j]
        avg = sum(so_far) / len(so_far)
        out[str(nd)] = {
            "ladder": round((float(prices[j]) / avg - 1) * 100, 2),
            "single": round((float(prices[j]) / first - 1) * 100, 2),
            "n_fills": len(so_far),
        }
    return out


def replay_open_episode(px: pd.Series) -> dict | None:
    """백테스트와 동일한 에피소드 로직을 이력에 재생해 지금 열린 에피소드를 반환.

    래더 체결까지 재생한다 — 평단이 달라지면 수익률이 달라지므로 첫 체결만 보면 안 된다.
    """
    ref = px.rolling(REF_WINDOW, min_periods=126).max()
    dd = (px / ref - 1).to_numpy()
    prices, refs, idx = px.to_numpy(), ref.to_numpy(), px.index
    n = len(px)

    i = 0
    while i < n:
        if pd.isna(dd[i]) or dd[i] > TRIGGER:
            i += 1
            continue

        entry, ref_at_entry = i, refs[i]
        fills = [{"date": idx[i].date().isoformat(), "price": float(prices[i]),
                  "level_pct": round(FILL_LEVELS[0] * 100, 1)}]
        fill_idx = [entry]                 # 체결 봉 위치 — 구간별 평단 계산에 필요
        next_lvl, exit_i = 1, None

        for j in range(entry + 1, n):
            if next_lvl < len(FILL_LEVELS) and not pd.isna(dd[j]) and dd[j] <= FILL_LEVELS[next_lvl]:
                fills.append({"date": idx[j].date().isoformat(), "price": float(prices[j]),
                              "level_pct": round(FILL_LEVELS[next_lvl] * 100, 1)})
                fill_idx.append(j)
                next_lvl += 1
            if prices[j] >= ref_at_entry:            # 기준고점 회복 → 청산
                exit_i = j
                break

        avg_cost = sum(f["price"] for f in fills) / len(fills)   # 등주수
        if exit_i is None:
            tail = prices[entry + 1:]
            return {
                "signal_date": fills[0]["date"],
                "entry_price": fills[0]["price"],
                "entry_dd_pct": round(float(dd[entry]) * 100, 2),
                "ref_high_at_entry": float(ref_at_entry),
                "fills": fills,
                "n_fills": len(fills),
                "avg_cost": round(avg_cost, 4),
                "mae_pct": round((float(tail.min()) / avg_cost - 1) * 100, 2) if len(tail) else 0.0,
                "underwater_days": int((tail < avg_cost).sum()) if len(tail) else 0,
                "path": _return_path(prices, entry, fill_idx),
            }

        i = exit_i + 1
        while i < n and (pd.isna(dd[i]) or dd[i] < RESET):       # 리셋 대기
            i += 1
    return None


# ═══════════════════════════════════════════════════════════
# 원장
# ═══════════════════════════════════════════════════════════
def _db_enabled() -> bool:
    import os
    return bool(os.getenv("DATABASE_URL", "").strip())


def load_ledger() -> tuple[dict, bool]:
    """(원장, 최초생성여부).

    DATABASE_URL 이 있으면 **DB 가 정본**이다. Railway 컨테이너의 파일시스템은
    재배포마다 초기화되므로 파일에만 두면 매번 first_run 이 되고
    열린 에피소드가 전부 backfilled 로 다시 잡힌다 → out-of-sample 이 영원히 0.
    로컬(개발)에서는 DATABASE_URL 없이 JSON 파일로 돈다.
    """
    if _db_enabled():
        try:
            from db.models import DrawdownLedger
            from db.session import get_session
            with get_session() as s:
                row = s.get(DrawdownLedger, 1)
                if row:
                    return json.loads(row.payload_json), False
            return {"started_at": dt.date.today().isoformat(), "episodes": []}, True
        except Exception as exc:                      # DB 장애 시 파일로 폴백하지 않는다
            raise RuntimeError(f"드로다운 원장 DB 읽기 실패: {exc}") from exc

    if LEDGER.exists():
        return json.loads(LEDGER.read_text()), False
    return {"started_at": dt.date.today().isoformat(), "episodes": []}, True


def save_ledger(led: dict) -> None:
    if _db_enabled():
        from db.models import DrawdownLedger
        from db.session import get_session
        payload = json.dumps(led, ensure_ascii=False)
        with get_session() as s:
            row = s.get(DrawdownLedger, 1)
            if row:
                row.payload_json = payload
                row.updated_at = dt.datetime.utcnow()
            else:
                s.add(DrawdownLedger(id=1, payload_json=payload))
            s.commit()
        return

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=1))


RUN_HOUR_UTC = 22           # 22:00 UTC = 07:00 KST — 미국장 마감 후, 한국장 개장 전


def is_due() -> bool:
    """오늘 아직 안 돌았고 실행 시각을 지났나.

    컨테이너 재시작이 잦아도 하루 1회를 보장하려면 sleep(24h) 이 아니라
    **마지막 실행일을 원장에 기록**하고 비교해야 한다. 재배포로 하루를 건너뛰면
    다음 폴링에서 바로 따라잡는다(catch-up).
    """
    led, first = load_ledger()
    if first:
        return True
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if led.get("last_run") == today:
        return False
    return dt.datetime.now(dt.timezone.utc).hour >= RUN_HOUR_UTC


def run(dry: bool = False) -> dict:
    """1회 실행 — 신규 신호 감지 + 열린 에피소드 갱신 + 알림. 스크립트와 스케줄러가 공유."""
    led, first_run = load_ledger()
    led["last_run"] = dt.datetime.now(dt.timezone.utc).date().isoformat()
    eps: list[dict] = led["episodes"]
    open_by_sym = {e["symbol"]: e for e in eps if e["status"] == "open"}
    new_signals, closed_now, errors = [], [], []

    for sym, name in UNIVERSE.items():
        px = load_prices(sym)
        if px is None:
            errors.append(sym)
            continue
        today = px.index[-1].date().isoformat()
        price = float(px.iloc[-1])
        ep = open_by_sym.get(sym)

        if ep:
            # 재생으로 갱신 — 래더 추가 체결이 붙었을 수 있어 매번 다시 계산한다
            cur = replay_open_episode(px)
            if cur is None or cur["signal_date"] != ep["signal_date"]:
                ep.update(status="closed", exit_date=today, exit_reason="고점회복",
                          last_date=today, last_price=price,
                          ret_pct=round((price / ep["avg_cost"] - 1) * 100, 2))
                closed_now.append((sym, name, ep))
            else:
                ep.update(cur, last_date=today, last_price=price,
                          ret_pct=round((price / cur["avg_cost"] - 1) * 100, 2))
            continue

        found = replay_open_episode(px)
        if found is None:
            continue
        ep = {
            "symbol": sym, "name": name, **found, "status": "open",
            "ret_pct": round((price / found["avg_cost"] - 1) * 100, 2),
            "last_date": today, "last_price": price,
            # 원장 생성 전에 시작됐으면 실시간 포착이 아니다
            "backfilled": bool(first_run or found["signal_date"] != today),
        }
        eps.append(ep)
        new_signals.append((sym, name, ep))

    save_ledger(led)

    if not dry:
        _notify(new_signals, closed_now)

    live = [e for e in eps if e["status"] == "open"]
    oos = [e for e in eps if not e["backfilled"]]
    return {
        "started_at": led["started_at"], "total": len(eps), "open": len(live),
        "out_of_sample": len(oos), "backfilled": len(eps) - len(oos),
        "new_signals": [e for _, _, e in new_signals if not e["backfilled"]],
        "closed": [e for _, _, e in closed_now], "errors": errors,
        "episodes": sorted(live, key=lambda x: x["ret_pct"]),
    }


# 스케줄러 틱과 대시보드 수동 실행이 겹칠 수 있다. 원장이 단일 JSON 블롭이라
# 동시에 read-modify-write 하면 나중에 저장한 쪽이 앞의 결과를 통째로 덮는다.
_run_lock = threading.Lock()


def is_running() -> bool:
    return _run_lock.locked()


def run_exclusive(dry: bool = False) -> dict | None:
    """이미 돌고 있으면 None 을 반환하고 건너뛴다. 스케줄러·라우터 공용 진입점."""
    if not _run_lock.acquire(blocking=False):
        logger.info("[Drawdown] 이미 실행 중 — 이번 호출은 건너뜀")
        return None
    try:
        return run(dry=dry)
    finally:
        _run_lock.release()


def _notify(new_signals, closed_now) -> None:
    from features.notifications.telegram_service import TelegramService
    tg = TelegramService()
    lv = " / ".join(f"{v * 100:.0f}%" for v in FILL_LEVELS)

    for sym, name, e in new_signals:
        if e["backfilled"]:
            continue                     # 사후 발견분은 신호가 아니므로 알리지 않는다
        ok, err = tg.send_message(
            f"<b>[드로다운 매수] 신호 발생</b>\n\n"
            f"종목: {name} ({sym})\n"
            f"드로다운: {e['entry_dd_pct']:.1f}% (52주 고점 대비)\n"
            f"1차 진입가: {e['entry_price']:,.2f}\n"
            f"래더: {lv} 3회 등주수 체결\n"
            f"기준고점: {e['ref_high_at_entry']:,.2f}\n\n"
            f"청산: 고점 회복 시 · 보유일 상한 없음")
        if not ok:
            logger.warning("[Drawdown] 전송 실패 %s: %s", sym, err)

    for sym, name, e in closed_now:
        tg.send_message(
            f"<b>[드로다운 매수] 청산</b>\n\n"
            f"종목: {name} ({sym})\n"
            f"진입 {e['signal_date']} → 청산 {e['exit_date']}\n"
            f"체결 {e['n_fills']}회 · 평단 {e['avg_cost']:,.2f}\n"
            f"수익: <b>{e['ret_pct']:+.2f}%</b>\n"
            f"최대 추가하락 {e['mae_pct']:+.2f}% · 물밑 {e['underwater_days']}일")
