"""
Spot-Perp CVD Divergence — 청산 로직.

backtest engine.py 와 동일한 우선순위:
  1) Hard SL (bar_low/bar_high 기준) — trailing SL 포함
  2) TP (bar_high/bar_low 기준) — SL 이 없을 때만
  3) CVD 수렴 종료 (close 기준) — min_hold_bars 이후에만

Trailing Stop:
  position["hwm"] = 현재 봉까지의 high/low water mark
  trail SL = hwm × (1 - trail_pct/100)  for long
           = hwm × (1 + trail_pct/100)  for short
  position["sl"]을 직접 갱신 (Python dict pass-by-reference)

Min Hold Bars:
  position["hold_bars"] = 진입 후 경과 봉 수
  engine.py tick() 에서 매 봉 마감 시 +1

intrabar=True (봉 진행 중 WS 가격):
  SL 만 판정 (current_price 직접 비교) — CVD 수렴·TP 체크 안 함.
  backtest semantics: CVD exit 는 봉 마감 시 1회만.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from .config_loader import get_tpsl_params


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if not math.isnan(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sl_reason(position: Dict[str, Any], sl_price: float) -> str:
    side  = position.get("side")
    entry = _f(position.get("entry_price"))
    if entry <= 0:
        return "closed_sl"
    if side == "long":
        return "closed_sl_profit" if sl_price >= entry else "closed_sl_loss"
    if side == "short":
        return "closed_sl_profit" if sl_price <= entry else "closed_sl_loss"
    return "closed_sl"


def check_exit(
    position: Dict[str, Any],
    current_price: float,
    sig: Dict[str, Any],
    bar_high: Optional[float] = None,
    bar_low:  Optional[float] = None,
    intrabar: bool = False,
) -> Optional[tuple]:
    """
    Returns (exit_price, reason, close_note) 또는 None.

    intrabar=True: WS 가격으로 SL 만 판정.
    intrabar=False: trailing SL 갱신 → OHLC SL → TP → CVD 수렴 체크.

    Trailing SL 갱신은 position dict를 직접 수정 (pass-by-reference).
    """
    tp_p          = get_tpsl_params()
    trail_pct     = float(tp_p.get("trail_pct", 0.0))
    min_hold_bars = int(tp_p.get("min_hold_bars", 0))
    cvd_exit_thr  = float(tp_p.get("cvd_exit_threshold", 0.0))  # 0 = 0선 교차

    side = position.get("side")
    sl   = _f(position.get("sl"))
    tp   = _f(position.get("tp") or 0)
    hold = int(position.get("hold_bars", 0))

    if intrabar:
        # 봉 진행 중 — SL 직접 가격 판정만
        if side == "long"  and sl and current_price <= sl:
            return (sl, _sl_reason(position, sl), None)
        if side == "short" and sl and current_price >= sl:
            return (sl, _sl_reason(position, sl), None)
        return None

    # 봉 마감 — trailing SL 갱신 → OHLC SL → TP → CVD 수렴
    bh = bar_high if bar_high is not None else current_price
    bl = bar_low  if bar_low  is not None else current_price

    # ── Trailing Stop 갱신 ────────────────────────────────────────────────────
    if trail_pct > 0.0:
        if side == "long":
            hwm = max(float(position.get("hwm") or _f(position.get("entry_price"))), bh)
            position["hwm"] = hwm
            trail_sl = round(hwm * (1.0 - trail_pct / 100.0), 2)
            if trail_sl > sl:
                position["sl"] = trail_sl
                sl = trail_sl
        elif side == "short":
            lwm = min(float(position.get("hwm") or _f(position.get("entry_price"))), bl)
            position["hwm"] = lwm
            trail_sl = round(lwm * (1.0 + trail_pct / 100.0), 2)
            if sl <= 0 or trail_sl < sl:
                position["sl"] = trail_sl
                sl = trail_sl

    # ── 1) Hard SL (trailing SL 포함) ────────────────────────────────────────
    if side == "long"  and sl and bl <= sl:
        return (sl, _sl_reason(position, sl), None)
    if side == "short" and sl and bh >= sl:
        return (sl, _sl_reason(position, sl), None)

    # ── 2) TP ─────────────────────────────────────────────────────────────────
    if tp > 0.0:
        if side == "long"  and bh >= tp:
            return (tp, "closed_tp", f"tp={tp:.2f}")
        if side == "short" and bl <= tp:
            return (tp, "closed_tp", f"tp={tp:.2f}")

    # ── 3) CVD 수렴 종료 (min_hold_bars 이후에만) ─────────────────────────────
    if hold < min_hold_bars:
        return None  # 아직 최소 홀딩 기간 미충족

    sc = sig.get("spot_cvd_pct")
    pc = sig.get("perp_cvd_pct")
    if sc is None or pc is None:
        return None
    try:
        sc = float(sc)
        pc = float(pc)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(sc) or not math.isfinite(pc):
        return None

    # cvd_exit_thr > 0 이면 0선이 아니라 ±thr 까지 반전해야 청산 (완화 → 더 오래 보유)
    # backtest engine.py 와 동일.
    cvd_resolved = False
    if side == "long":
        cvd_resolved = (sc <= -cvd_exit_thr) or (pc >= cvd_exit_thr)
    elif side == "short":
        cvd_resolved = (sc >= cvd_exit_thr) or (pc <= -cvd_exit_thr)

    if cvd_resolved:
        return (
            current_price,
            "closed_cvd_exit",
            f"spot_cvd={sc:.3f} perp_cvd={pc:.3f}",
        )

    return None
