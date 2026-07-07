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


def spike_shape(pts: list[dict], i: int, p0: float,
                peak_window_s: int = 6 * 3600) -> dict:
    """스파이크 하나의 임펄스-감쇠 shape 계측 (상세 뷰용).

    - A(진폭)=peak−p0, t_up=상승 소요, t_down=peak→절반 되돌림 소요
    - grad=t_down/t_up (급등 후 완만 하강일수록 큼), reverted=p0쪽 복귀 비율
    - mono=하강 구간 단조성(재급등 없이 우하향), clean=fade 이상형 판정
    """
    n = len(pts)
    # 트리거 이후 6h 내 실제 고점
    hz = pts[i]["ts"] + peak_window_s
    pk = i
    for k in range(i, n):
        if pts[k]["ts"] > hz:
            break
        if pts[k]["price"] >= pts[pk]["price"]:
            pk = k
    peak = pts[pk]["price"]
    A = peak - p0
    # 상승 시작점: i 이전에서 p0 근처였던 마지막 지점
    us = i
    for k in range(i, -1, -1):
        if pts[k]["price"] <= p0 * 1.05 + 1e-9:
            us = k
            break
    t_up = max(1.0, pts[pk]["ts"] - pts[us]["ts"])
    # 하강: 절반 되돌림 도달 시각 + 최저가
    half = peak - 0.5 * A
    t_down = None
    rev_idx = n - 1
    mn = peak
    for k in range(pk, n):
        pr = pts[k]["price"]
        mn = min(mn, pr)
        if t_down is None and pr <= half:
            t_down = pts[k]["ts"] - pts[pk]["ts"]
            rev_idx = k
    reverted = min(1.0, (peak - mn) / A) if A > 0 else 0.0
    if t_down is None:                       # 절반도 안 되돌림
        t_down = pts[-1]["ts"] - pts[pk]["ts"]
        rev_idx = n - 1
    seg = [pts[k]["price"] for k in range(pk, rev_idx + 1)]
    if len(seg) >= 2:
        noninc = sum(1 for a, b in zip(seg, seg[1:]) if b <= a + 0.005)
        mono = noninc / (len(seg) - 1)
    else:
        mono = 1.0
    grad = t_down / t_up
    clean = A >= 0.03 and reverted >= 0.6 and grad >= 1.5 and mono >= 0.7
    return {
        "ts": pts[i]["ts"], "p0": round(p0, 4), "entry": round(pts[i]["price"], 4),
        "peak": round(peak, 4), "peak_ts": pts[pk]["ts"],
        "amplitude": round(A, 4), "t_up_h": round(t_up / 3600, 1),
        "t_down_h": round(t_down / 3600, 1), "grad": round(grad, 1),
        "reverted": round(reverted, 2), "mono": round(mono, 2), "clean": clean,
    }


def fade_sim(pts: list[dict], cfg: dict) -> dict:
    """그 종목만 순차 fade 백테스트 — 스파이크마다 NO 진입, 되돌림/손절/타임아웃 청산.

    fade_study.fade_backtest 의 단일 토큰 버전. 순차(열린 포지션 청산 전 재진입 금지).
    수익%=NO 기준 (진입−청산)/(1−진입).
    """
    retrace = cfg["retrace_pct"]
    stop_loss = cfg["stop_loss_pct"]
    timeout_s = cfg["timeout_hours"] * 3600
    trades = []
    last_exit_i = -1
    for i, p0, px in detect_spikes(pts, cfg):
        if i <= last_exit_i:
            continue
        target = px - retrace * (px - p0)
        stop_price = px + stop_loss * (1 - px) if stop_loss > 0 else None
        t_end = pts[i]["ts"] + timeout_s
        exit_px, exit_ts, exit_i, reason = pts[i]["price"], pts[i]["ts"], i, "보유중"
        for k in range(i, len(pts)):
            p = pts[k]
            if p["ts"] > t_end:
                reason = "타임아웃"
                break
            exit_px, exit_ts, exit_i = p["price"], p["ts"], k
            if p["price"] <= target:
                exit_px, reason = target, "되돌림"
                break
            if stop_price is not None and p["price"] >= stop_price:
                exit_px, reason = stop_price, "손절"
                break
        last_exit_i = exit_i
        ret = (px - exit_px) / (1 - px) if px < 1 else 0.0
        trades.append({
            "entry_ts": pts[i]["ts"], "p0": round(p0, 4), "entry": round(px, 4),
            "exit": round(exit_px, 4), "reason": reason,
            "hold_h": round((exit_ts - pts[i]["ts"]) / 3600, 1),
            "ret_pct": round(ret * 100, 2),
        })
    rets = [t["ret_pct"] for t in trades]
    n = len(rets)
    wins = sum(1 for r in rets if r > 0)
    return {
        "n": n, "wins": wins,
        "winrate": round(wins / n, 3) if n else None,
        "mean_pct": round(sum(rets) / n, 2) if n else None,
        "worst_pct": round(min(rets), 2) if n else None,
        "total_pct": round(sum(rets), 2) if n else None,
        "trades": trades,
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
