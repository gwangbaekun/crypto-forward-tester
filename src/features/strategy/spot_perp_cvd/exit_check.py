"""
Spot-Perp CVD Divergence — 청산 로직.

backtest engine.py 와 동일한 우선순위:
  1) Hard SL (bar_low/bar_high 기준)
  2) CVD 수렴 종료 (close 기준)

intrabar=True (봉 진행 중 WS 가격):
  SL 만 판정 (current_price 직접 비교) — CVD 수렴 체크 안 함.
  backtest semantics: CVD exit 는 봉 마감 시 1회만.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional


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
    intrabar=False: OHLC SL + CVD 수렴 체크.
    """
    side = position.get("side")
    sl   = _f(position.get("sl"))

    if intrabar:
        # 봉 진행 중 — SL 직접 가격 판정만
        if side == "long"  and sl and current_price <= sl:
            return (sl, _sl_reason(position, sl), None)
        if side == "short" and sl and current_price >= sl:
            return (sl, _sl_reason(position, sl), None)
        return None

    # 봉 마감 — OHLC + CVD 수렴
    bh = bar_high if bar_high is not None else current_price
    bl = bar_low  if bar_low  is not None else current_price

    # 1) Hard SL
    if side == "long"  and sl and bl <= sl:
        return (sl, _sl_reason(position, sl), None)
    if side == "short" and sl and bh >= sl:
        return (sl, _sl_reason(position, sl), None)

    # 2) CVD 수렴 종료 — sig 에 spot/perp CVD 값 없으면 패스
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

    cvd_resolved = False
    if side == "long":
        cvd_resolved = (sc <= 0.0) or (pc >= 0.0)
    elif side == "short":
        cvd_resolved = (sc >= 0.0) or (pc <= 0.0)

    if cvd_resolved:
        return (
            current_price,
            "closed_cvd_exit",
            f"spot_cvd={sc:.3f} perp_cvd={pc:.3f}",
        )

    return None
