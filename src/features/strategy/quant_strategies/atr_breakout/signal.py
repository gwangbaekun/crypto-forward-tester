"""
ATR Breakout — ATR 압축 + 방향 이탈 (Predictive)

핵심 아이디어:
  변동폭이 수축(ATR 압축)된 구간 → 방향 이탈 + CVD 확인 → 진짜 이탈로 판단 → 선진입

순환 지표:
  - magnet: 미사용 (magnets=false)

Confidence 채점 (7점 만점, ≥ 5점 진입):
  +2  ATR 압축 (recent_atr < hist_atr × atr_compress_ratio)
  +2  박스 이탈 발생 (close > box_high or close < box_low)
  +2  15m CVD 방향 일치
  +1  1h CVD 방향 일치

TP/SL (Measured Move):
  Long:  TP = close + (close - box_low)  × tp_multiplier
         SL = box_low
  Short: TP = close - (box_high - close) × tp_multiplier
         SL = box_high

청산:
  - SL/TP 도달
  - False breakout: 가격이 box 안쪽으로 재진입 → closed_false_breakout

파라미터: config.yaml → signal / tpsl 섹션
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

from .config_loader import get_signal_params, get_tpsl_params

_sig  = get_signal_params()
_tpsl = get_tpsl_params()

CONFIDENCE_THRESHOLD = _sig["confidence_threshold"]
ATR_COMPRESS_RATIO   = _sig["atr_compress_ratio"]
RECENT_ATR_WINDOW    = _sig["recent_atr_window"]
HIST_ATR_WINDOW      = _sig["hist_atr_window"]
BOX_WINDOW           = _sig["box_window"]
CVD_WINDOW           = _sig["cvd_window"]
TP_MULTIPLIER        = _tpsl["tp_multiplier"]


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


def _bars_from_sweep(sweep: Dict) -> List[Dict]:
    return sweep.get("data") or []


def _recent_atr(bars: List[Dict], window: int = RECENT_ATR_WINDOW) -> float:
    """최근 window봉 (high-low) 평균."""
    recent = bars[-window:] if len(bars) >= window else bars
    if not recent:
        return 0.0
    vals = [_f(b.get("high", 0)) - _f(b.get("low", 0)) for b in recent]
    return sum(vals) / len(vals) if vals else 0.0


def _hist_atr(bars: List[Dict],
              recent_window: int = RECENT_ATR_WINDOW,
              hist_window: int = HIST_ATR_WINDOW) -> float:
    """최근 recent_window 이전의 hist_window봉 (high-low) 평균."""
    start = max(0, len(bars) - recent_window - hist_window)
    end   = max(0, len(bars) - recent_window)
    hist  = bars[start:end]
    if not hist:
        return 0.0
    vals = [_f(b.get("high", 0)) - _f(b.get("low", 0)) for b in hist]
    return sum(vals) / len(vals) if vals else 0.0


def _box_range(bars: List[Dict], window: int = BOX_WINDOW) -> Tuple[float, float]:
    """최근 window봉의 close 기준 고점/저점 (consolidation box)."""
    recent = bars[-window:] if len(bars) >= window else bars
    closes = [_f(b.get("close", 0)) for b in recent if _f(b.get("close", 0)) > 0]
    if not closes:
        return 0.0, 0.0
    return max(closes), min(closes)


def _cvd_sum(bars: List[Dict], window: int = CVD_WINDOW) -> float:
    """최근 window봉 CVD delta 합계."""
    recent = bars[-window:] if len(bars) >= window else bars
    return sum(_f(b.get("cvd_delta", 0)) for b in recent)


# ── v2 magnet 헬퍼 ─────────────────────────────────────────────────────────

def _nearest_magnet_above(level_map: List[Dict], price: float) -> Optional[float]:
    """price 위 가장 가까운 magnet 가격."""
    candidates = [_f(m.get("price")) for m in level_map if _f(m.get("price")) > price]
    return min(candidates) if candidates else None


def _nearest_magnet_below(level_map: List[Dict], price: float) -> Optional[float]:
    """price 아래 가장 가까운 magnet 가격."""
    candidates = [_f(m.get("price")) for m in level_map if 0 < _f(m.get("price")) < price]
    return max(candidates) if candidates else None


def compute_signal(
    current_price: float,
    sweep_by_tf: Dict[str, Any],
    magnets: Dict[str, Any],
) -> Dict[str, Any]:
    """
    ATR 압축 + 방향 이탈 신호 계산.

    Returns:
        signal: "long" | "short" | "none"
        confidence: int (0~7)
        bull_score / bear_score: 세부 채점
        tp / sl: 가격
        reasons: [str]
    """
    bars_15m = _bars_from_sweep(sweep_by_tf.get("15m") or {})
    bars_1h  = _bars_from_sweep(sweep_by_tf.get("1h")  or {})

    if not bars_15m:
        return _no_signal("15m 데이터 없음")

    # ── ATR 압축 체크 ──────────────────────────────────────────────────
    r_atr = _recent_atr(bars_15m)
    h_atr = _hist_atr(bars_15m)
    is_compressed = (r_atr > 0 and h_atr > 0 and r_atr < h_atr * ATR_COMPRESS_RATIO)

    # ── Box 범위 & 현재 봉 ─────────────────────────────────────────────
    box_high, box_low = _box_range(bars_15m[:-1])   # 이탈 기준: 현재봉 직전까지
    current_close = _f((bars_15m[-1] if bars_15m else {}).get("close", 0)) or current_price

    breakout_up   = box_high > 0 and current_close > box_high
    breakout_down = box_low  > 0 and current_close < box_low

    # ── CVD ───────────────────────────────────────────────────────────
    cvd_15m = _cvd_sum(bars_15m)
    cvd_1h  = _cvd_sum(bars_1h)

    bull_score = 0
    bear_score = 0
    reasons: List[str] = []

    # +2: SETUP(압축 + 방향 이탈) — 이중 카운팅 방지
    if is_compressed and breakout_up:
        bull_score += 2
        reasons.append(
            f"[SETUP] 압축+상단이탈 ✅ close={current_close:.1f} > box_high={box_high:.1f}"
        )
    elif is_compressed and breakout_down:
        bear_score += 2
        reasons.append(
            f"[SETUP] 압축+하단이탈 ✅ close={current_close:.1f} < box_low={box_low:.1f}"
        )
    else:
        reasons.append(
            f"[SETUP] 미충족 (압축={is_compressed}, breakout={'up' if breakout_up else 'down' if breakout_down else 'none'})"
        )

    # +2: 15m CVD 방향
    if cvd_15m > 0:
        bull_score += 2
        reasons.append(f"[CVD] 15m CVD↑ {cvd_15m:.0f}")
    elif cvd_15m < 0:
        bear_score += 2
        reasons.append(f"[CVD] 15m CVD↓ {cvd_15m:.0f}")

    # +1: 1h CVD 방향
    if cvd_1h > 0:
        bull_score += 1
        reasons.append(f"[CVD] 1h CVD↑ {cvd_1h:.0f}")
    elif cvd_1h < 0:
        bear_score += 1
        reasons.append(f"[CVD] 1h CVD↓ {cvd_1h:.0f}")

    # ── 신호 판단 ──────────────────────────────────────────────────────
    if bull_score >= CONFIDENCE_THRESHOLD and bull_score > bear_score and box_low > 0:
        # Measured move: 이탈거리 × tp_multiplier (config.yaml)
        move    = current_close - box_low
        tp      = round(current_close + move * TP_MULTIPLIER, 2)
        sl      = round(box_low, 2)
        reasons.append(f"[진입] LONG  confidence={bull_score}/7  TP={tp:,.2f}  SL={sl:,.2f}")
        return {
            "signal": "long", "confidence": bull_score,
            "bull_score": bull_score, "bear_score": bear_score,
            "tp": tp, "sl": sl,
            "box_high": box_high, "box_low": box_low,
            "is_compressed": is_compressed,
            "cvd_15m": cvd_15m, "cvd_1h": cvd_1h,
            "signal_mode": "v1",
            "reasons": reasons,
        }

    if bear_score >= CONFIDENCE_THRESHOLD and bear_score > bull_score and box_high > 0:
        move    = box_high - current_close
        tp      = round(current_close - move * TP_MULTIPLIER, 2)
        sl      = round(box_high, 2)
        reasons.append(f"[진입] SHORT confidence={bear_score}/7  TP={tp:,.2f}  SL={sl:,.2f}")
        return {
            "signal": "short", "confidence": bear_score,
            "bull_score": bull_score, "bear_score": bear_score,
            "tp": tp, "sl": sl,
            "box_high": box_high, "box_low": box_low,
            "is_compressed": is_compressed,
            "cvd_15m": cvd_15m, "cvd_1h": cvd_1h,
            "signal_mode": "v1",
            "reasons": reasons,
        }

    reasons.append(
        f"[대기] bull={bull_score} bear={bear_score} — 임계값({CONFIDENCE_THRESHOLD}) 미달"
    )
    return _no_signal(None, extra={
        "bull_score": bull_score, "bear_score": bear_score,
        "box_high": box_high, "box_low": box_low,
        "is_compressed": is_compressed,
        "cvd_15m": cvd_15m, "cvd_1h": cvd_1h,
        "signal_mode": "v1",
        "reasons": reasons,
    })


def _no_signal(reason: Optional[str], extra: Dict = None) -> Dict[str, Any]:
    base = {"signal": "none", "confidence": 0, "tp": None, "sl": None, "reasons": [reason] if reason else []}
    if extra:
        base.update(extra)
    return base


# ── v2: magnet TP + level_map 반환 ─────────────────────────────────────────

def compute_signal_v2(
    current_price: float,
    sweep_by_tf: Dict[str, Any],
    magnets: Dict[str, Any],
    entry_tf: str = "15m",
    higher_tf: str = "1h",
) -> Dict[str, Any]:
    """
    ATR Breakout v2 — TF 파라미터화 버전.
    entry_tf: ATR 압축·박스·CVD(×2) 기준 타임프레임
    higher_tf: CVD 보조 확인(×1) 타임프레임
    """
    level_map: List[Dict] = list((magnets or {}).get("level_map") or [])

    bars_entry  = _bars_from_sweep(sweep_by_tf.get(entry_tf)  or {})
    bars_higher = _bars_from_sweep(sweep_by_tf.get(higher_tf) or {})

    if not bars_entry:
        return {**_no_signal(f"{entry_tf} 데이터 없음"), "level_map": level_map}

    r_atr = _recent_atr(bars_entry)
    h_atr = _hist_atr(bars_entry)
    is_compressed = (r_atr > 0 and h_atr > 0 and r_atr < h_atr * ATR_COMPRESS_RATIO)

    box_high, box_low = _box_range(bars_entry[:-1])
    current_close = _f((bars_entry[-1] if bars_entry else {}).get("close", 0)) or current_price

    breakout_up   = box_high > 0 and current_close > box_high
    breakout_down = box_low  > 0 and current_close < box_low

    cvd_entry  = _cvd_sum(bars_entry)
    cvd_higher = _cvd_sum(bars_higher)

    bull_score = bear_score = 0
    reasons: List[str] = []

    # +2: SETUP(압축 + 방향 이탈) — ATR/BRK 이중 집계 제거
    if is_compressed and breakout_up:
        bull_score += 2
        reasons.append(f"[SETUP] 압축+상단이탈 ✅ close={current_close:.1f} > box_high={box_high:.1f}")
    elif is_compressed and breakout_down:
        bear_score += 2
        reasons.append(f"[SETUP] 압축+하단이탈 ✅ close={current_close:.1f} < box_low={box_low:.1f}")
    else:
        reasons.append(
            f"[SETUP] 미충족 (압축={is_compressed}, breakout={'up' if breakout_up else 'down' if breakout_down else 'none'})"
        )

    if cvd_entry > 0:
        bull_score += 2; reasons.append(f"[CVD] {entry_tf} CVD↑ {cvd_entry:.0f}")
    elif cvd_entry < 0:
        bear_score += 2; reasons.append(f"[CVD] {entry_tf} CVD↓ {cvd_entry:.0f}")

    if cvd_higher > 0:
        bull_score += 1; reasons.append(f"[CVD] {higher_tf} CVD↑ {cvd_higher:.0f}")
    elif cvd_higher < 0:
        bear_score += 1; reasons.append(f"[CVD] {higher_tf} CVD↓ {cvd_higher:.0f}")

    common = {
        "bull_score": bull_score, "bear_score": bear_score,
        "box_high": box_high, "box_low": box_low,
        "is_compressed": is_compressed,
        "cvd_entry": cvd_entry, "cvd_higher": cvd_higher,
        "entry_tf": entry_tf, "higher_tf": higher_tf,
        "level_map": level_map,
        "signal_mode": "v2",
        "reasons": reasons,
    }

    if bull_score >= CONFIDENCE_THRESHOLD and bull_score > bear_score and box_low > 0:
        # v2 표준: TP/SL 모두 liquidation magnet만 사용 (entry 기준 nearest)
        tp_magnet = _nearest_magnet_above(level_map, current_close)
        sl_magnet = _nearest_magnet_below(level_map, current_close)
        if not tp_magnet or not sl_magnet:
            reasons.append("[대기] LONG magnet TP/SL 부족")
            return {**_no_signal(None, extra=common), "level_map": level_map}
        tp = round(tp_magnet, 2)
        sl = round(sl_magnet, 2)
        reasons.append(f"[진입] LONG confidence={bull_score}/7 TP={tp:,.2f}(magnet) SL={sl:,.2f}(magnet)")
        return {"signal": "long", "confidence": bull_score, "tp": tp, "sl": sl, **common}

    if bear_score >= CONFIDENCE_THRESHOLD and bear_score > bull_score and box_high > 0:
        # v2 표준: TP/SL 모두 liquidation magnet만 사용 (entry 기준 nearest)
        tp_magnet = _nearest_magnet_below(level_map, current_close)
        sl_magnet = _nearest_magnet_above(level_map, current_close)
        if not tp_magnet or not sl_magnet:
            reasons.append("[대기] SHORT magnet TP/SL 부족")
            return {**_no_signal(None, extra=common), "level_map": level_map}
        tp = round(tp_magnet, 2)
        sl = round(sl_magnet, 2)
        reasons.append(f"[진입] SHORT confidence={bear_score}/7 TP={tp:,.2f}(magnet) SL={sl:,.2f}(magnet)")
        return {"signal": "short", "confidence": bear_score, "tp": tp, "sl": sl, **common}

    reasons.append(f"[대기] bull={bull_score} bear={bear_score} — 임계값({CONFIDENCE_THRESHOLD}) 미달")
    return {**_no_signal(None, extra=common), "level_map": level_map}


def make_compute_signal_v2(entry_tf: str, higher_tf: str):
    """TF별 compute_signal_v2 클로저 팩토리."""
    def _fn(current_price, sweep_by_tf, magnets):
        return compute_signal_v2(current_price, sweep_by_tf, magnets,
                                 entry_tf=entry_tf, higher_tf=higher_tf)
    _fn.__name__ = f"compute_signal_v2_{entry_tf}_{higher_tf}"
    return _fn


# ── v3: OI + CVD 기반 Magnet Zone Breakout ──────────────────────────────────

OI_WINDOW = 5   # OI 비교 윈도우 (봉 수)


def _oi_increasing(bars: List[Dict], window: int = OI_WINDOW) -> bool:
    """최근 window봉 평균 OI > 이전 window봉 평균 OI → 포지션 빌드업 중."""
    if len(bars) < window * 2:
        return False
    recent = bars[-window:]
    prev   = bars[-window * 2:-window]
    avg_r  = sum(_f(b.get("oi", 0)) for b in recent) / window
    avg_p  = sum(_f(b.get("oi", 0)) for b in prev) / window
    if avg_p == 0:
        return False
    return avg_r > avg_p * 1.005   # 0.5% 이상 증가


def compute_signal_v3(
    current_price: float,
    sweep_by_tf: Dict[str, Any],
    magnets: Dict[str, Any],
    entry_tf: str = "15m",
) -> Dict[str, Any]:
    """
    ATR Breakout v3 — Magnet Zone 전략 (ATR/box 체크 없음).

    진입 조건:
      OI 증가 (포지션 빌드업 확인) + CVD 방향 일치 → 방향성 판단
    TP/SL:
      LONG:  TP = nearest_magnet_above, SL = nearest_magnet_below
      SHORT: TP = nearest_magnet_below, SL = nearest_magnet_above
    TP 전진:
      OI 아직 증가 + CVD 유지 → 다음 magnet으로 전진, old_tp = new_sl
    """
    level_map: List[Dict] = list((magnets or {}).get("level_map") or [])

    if not level_map:
        return {**_no_signal("magnet 데이터 없음"), "level_map": []}

    bars = _bars_from_sweep(sweep_by_tf.get(entry_tf) or {})
    if not bars:
        return {**_no_signal(f"{entry_tf} 데이터 없음"), "level_map": level_map}

    nearest_above = _nearest_magnet_above(level_map, current_price)
    nearest_below = _nearest_magnet_below(level_map, current_price)

    oi_up = _oi_increasing(bars)
    cvd   = _cvd_sum(bars)

    reasons: List[str] = [
        f"[MAG] above={nearest_above:,.1f}" if nearest_above else "[MAG] above=없음",
        f"[MAG] below={nearest_below:,.1f}" if nearest_below else "[MAG] below=없음",
        f"[OI]  {'증가 ✅' if oi_up else '감소/중립 ❌'}",
        f"[CVD] {cvd:+.0f} {'↑ ✅' if cvd > 0 else '↓ ✅' if cvd < 0 else '0 (중립)'}",
    ]

    common: Dict[str, Any] = {
        "level_map":      level_map,
        "nearest_above":  nearest_above,
        "nearest_below":  nearest_below,
        "oi_increasing":  oi_up,
        "cvd":            cvd,
        "entry_tf":       entry_tf,
        "signal_mode":    "v3",
    }

    if not oi_up:
        return {**_no_signal(None, extra=common), "reasons": reasons + ["[대기] OI 미증가 — 압축력 없음"]}

    if not nearest_above or not nearest_below:
        return {**_no_signal(None, extra=common), "reasons": reasons + ["[대기] magnet 양방향 없음"]}

    if cvd > 0:
        tp = round(nearest_above, 2)
        sl = round(nearest_below, 2)
        reasons.append(f"[진입] LONG  TP={tp:,.1f}  SL={sl:,.1f}")
        return {"signal": "long",  "tp": tp, "sl": sl, "cvd_aligned": True,  "reasons": reasons, **common}

    if cvd < 0:
        tp = round(nearest_below, 2)
        sl = round(nearest_above, 2)
        reasons.append(f"[진입] SHORT TP={tp:,.1f}  SL={sl:,.1f}")
        return {"signal": "short", "tp": tp, "sl": sl, "cvd_aligned": True,  "reasons": reasons, **common}

    return {**_no_signal(None, extra=common), "reasons": reasons + ["[대기] CVD 중립 — 방향성 없음"]}
