"""Fade 전략 — 시그널 계산 (순수 함수).

btc_backtest/src/strategies/polymarket/news_lag/fade_study.py 의 detect_spikes()를
그대로 포팅. 스파이크(YES 급등)에 NO로 진입, 되돌림/타임아웃/손절로 청산.
백테스트로 검증된 파라미터(고정): retrace=0.80, timeout=72h, stop_loss=0.20.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FadeSignal:
    condition_id: str
    question:     str
    p0:           float   # 1h 전 가격
    entry_px:     float   # 스파이크 진입가 (YES)
    target_px:    float   # 되돌림 청산 목표가
    stop_px:      float   # 손절가
    timeout_ts:   int     # 타임아웃 시각(unix)
    yes_token_id: str
    no_token_id:  str


def detect_spikes(pts: list[dict], cfg: dict) -> list[tuple[int, float, float]]:
    """[(idx, p0, entry_px)] — p0 = 1h 전(또는 그 직전) 가격. 봉 간격 무관.

    fade_study.py detect_spikes() 와 동일 로직(pure function).
    """
    lookback_s = cfg.get("lookback_s", 3600)
    spike_rel = cfg["spike_rel"]
    spike_abs = cfg["spike_abs"]
    p0_lo, p0_hi = cfg["p0_lo"], cfg["p0_hi"]
    cooldown_s = cfg.get("cooldown_hours", 12) * 3600

    out, last, j = [], 0, 0
    for i in range(len(pts)):
        t0 = pts[i]["ts"] - lookback_s
        while j + 1 < len(pts) and pts[j + 1]["ts"] <= t0:
            j += 1
        if pts[j]["ts"] > t0:
            continue                      # 곡선 시작 직후 — 1h 전 데이터 없음
        p0, px = pts[j]["price"], pts[i]["price"]
        if not (p0_lo <= p0 <= p0_hi):
            continue
        if px < p0 * spike_rel or px - p0 < spike_abs:
            continue
        if pts[i]["ts"] - last < cooldown_s:
            continue
        last = pts[i]["ts"]
        out.append((i, p0, px))
    return out


def latest_status(pts: list[dict], cfg: dict) -> dict | None:
    """최신 캔들 기준 '지금 스파이크인가' 상태 — 대시보드 실시간 표시용.

    detect_spikes 와 동일한 판정(1h 전 대비 상대/절대)을 최신 지점에만 적용.
    """
    if len(pts) < 2:
        return None
    lookback_s = cfg.get("lookback_s", 3600)
    p0_lo, p0_hi = cfg["p0_lo"], cfg["p0_hi"]
    i = len(pts) - 1
    t0 = pts[i]["ts"] - lookback_s
    j = 0
    for k in range(len(pts)):
        if pts[k]["ts"] <= t0:
            j = k
        else:
            break
    p0 = pts[j]["price"]
    px = pts[i]["price"]
    rel = (px / p0) if p0 else 0.0
    abs_change = px - p0
    spike_now = (p0_lo <= p0 <= p0_hi) and (px >= p0 * cfg["spike_rel"]) and (abs_change >= cfg["spike_abs"])
    return {
        "p0": round(p0, 4), "price": round(px, 4),
        "rel_pct": round((rel - 1) * 100, 1), "abs_change": round(abs_change, 4),
        "spike_now": spike_now, "ts": pts[i]["ts"],
    }


def build_signal(
    market: dict, p0: float, entry_px: float, entry_ts: int, cfg: dict,
) -> FadeSignal:
    """스파이크 확정 시 진입/청산 레벨 계산."""
    retrace = cfg["retrace_pct"]
    stop_loss = cfg["stop_loss_pct"]
    timeout_h = cfg["timeout_hours"]

    target_px = entry_px - retrace * (entry_px - p0)
    stop_px = entry_px + stop_loss * (1 - entry_px)
    timeout_ts = int(entry_ts + timeout_h * 3600)

    return FadeSignal(
        condition_id=market.get("condition_id", ""),
        question=market.get("question", ""),
        p0=p0,
        entry_px=entry_px,
        target_px=target_px,
        stop_px=stop_px,
        timeout_ts=timeout_ts,
        yes_token_id=market.get("yes_token_id", ""),
        no_token_id=market.get("no_token_id", ""),
    )


def check_exit(current_px: float, now_ts: int, pos) -> tuple[float, str] | None:
    """현재가/시각 기준 청산 여부 판단. (exit_px, reason) 또는 None.

    우선순위: 되돌림 → 손절 → 타임아웃 (같은 tick 에 여러 조건이 겹치면
    실제로 먼저 도달했을 방향, 즉 되돌림/손절 둘 중 하나만 성립하므로 순서 무관).
    """
    if current_px <= pos.target_px:
        return pos.target_px, "되돌림"
    if current_px >= pos.stop_px:
        return pos.stop_px, "손절"
    if now_ts >= pos.timeout_ts:
        return current_px, "타임아웃"
    return None
