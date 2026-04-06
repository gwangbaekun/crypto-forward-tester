"""
전략 이벤트 → Telegram 알림 (tradingview_mcp app/quant_strategies/common/notifier.py 포팅).

- strategies_master.yaml 의 label / emoji 사용.
- telegram_alerts: true 이거나 binance_live: true 인 전략만 전송.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_REASON_LABEL: Dict[str, str] = {
    "closed_sl": "🛑 SL 도달",
    "closed_tp1": "🎯 TP1 도달",
    "closed_tp2": "🎯 TP2 도달",
    "closed_tp_final": "🏁 최종 Magnet 도달",
    "closed_trailing_sl": "🛑 Trailing SL",
    "closed_reversal": "🔄 반전 청산",
    "closed_zscore_reversal": "📉 Z-Score 반전",
    "closed_manual": "🖐 수동 청산",
}


def _local_prefix() -> str:
    return "" if os.environ.get("RAILWAY_ENVIRONMENT") else "[LOCAL] "


def _strategy_meta(strategy_key: str) -> tuple[str, str]:
    from features.strategy.common.config_loader import get_master_config

    cfg = get_master_config().get(strategy_key) or {}
    return cfg.get("label") or strategy_key, cfg.get("emoji") or "📊"


def _fmt_entry(
    ev: Dict, label: str, emoji: str, symbol: str, synced: Optional[bool] = None
) -> str:
    pos = ev.get("position") or {}
    side = pos.get("side", "").upper()
    ep = pos.get("entry_price")
    tpsl = pos.get("tpsl") or {}
    tp = pos.get("tp1") or pos.get("tp") or tpsl.get("tp1")
    sl = pos.get("sl") or tpsl.get("sl")
    tfs = pos.get("trigger_tfs") or pos.get("entry_tf") or pos.get("trigger_tf") or ""
    confidence = pos.get("confidence", 0)
    detail = pos.get("direction_detail") or pos.get("force_type") or ""

    side_e = "🟢" if side == "LONG" else "🔴"
    ep_s = f"${ep:,.2f}" if ep else "—"
    tp_s = f"${tp:,.2f}" if tp else "—"
    sl_s = f"${sl:,.2f}" if sl else "—"
    conf_s = "⭐" * int(min(confidence, 5)) if confidence else ""

    lines = [
        f"{emoji} <b>[{label}]  📌 진입  {side_e} {side}</b>",
        "",
        f"<b>{symbol}</b>  @  <code>{ep_s}</code>",
        "",
        f"TP :  <code>{tp_s}</code>",
        f"SL :  <code>{sl_s}</code>",
    ]
    if tfs:
        lines.append(f"TF :  <code>{tfs}</code>")
    if detail:
        lines.append(f"신호:  <code>{detail}</code>")
    if conf_s:
        lines.append(f"신뢰도:  {conf_s}")
    if synced is True:
        lines.append("바이낸스: ✅ 체결 확인")
    elif synced is False:
        lines.append("바이낸스: ❌ 미체결 — 확인 필요")
    return "\n".join(lines)


def _fmt_close(
    ev: Dict, label: str, emoji: str, symbol: str, synced: Optional[bool] = None
) -> str:
    trade = ev.get("trade") or {}
    side = trade.get("side", "").upper()
    ep = trade.get("entry_price")
    xp = trade.get("exit_price")
    pnl = trade.get("pnl_pct")
    reason = trade.get("exit_reason") or ""
    dur = trade.get("duration_min")
    note = trade.get("close_note") or ""

    side_e = "🟢" if side == "LONG" else "🔴"
    ep_s = f"${ep:,.2f}" if ep else "—"
    xp_s = f"${xp:,.2f}" if xp else "—"
    pnl_e = "✅" if (pnl or 0) >= 0 else "❌"
    pnl_s = (
        (("+" if (pnl or 0) >= 0 else "") + f"{pnl:.2f}%") if pnl is not None else "—"
    )
    r_label = _REASON_LABEL.get(
        reason, reason.replace("closed_", "").upper() if reason else "—"
    )
    dur_s = f"{int(dur)}분" if dur else ""

    lines = [
        f"{emoji} <b>[{label}]  🔔 청산  {side_e} {side}</b>",
        f"<b>{r_label}</b>",
        "",
        f"<b>{symbol}</b>  {ep_s}  →  <code>{xp_s}</code>",
        "",
        f"수익률:  {pnl_e} <code>{pnl_s}</code>",
    ]
    if dur_s:
        lines.append(f"보유시간:  <code>{dur_s}</code>")
    if note:
        lines.append(f"사유:  <code>{note}</code>")
    if synced is True:
        lines.append("바이낸스: ✅ 청산 확인")
    elif synced is False:
        lines.append("바이낸스: ❌ 청산 실패 — 수동 청산 필요")
    return "\n".join(lines)


def _fmt_tp_advance(
    ev: Dict, label: str, emoji: str, symbol: str, synced: Optional[bool] = None
) -> str:
    pos = ev.get("position") or {}
    side = pos.get("side", "").upper()
    old_tp = ev.get("old_tp1") or ev.get("old_tp")
    new_tp = ev.get("new_tp1") or ev.get("new_tp")

    old_s = f"${old_tp:,.2f}" if old_tp else "—"
    new_s = f"${new_tp:,.2f}" if new_tp else "—"

    lines = [
        f"{emoji} <b>[{label}]  ⏫ TP 전진  {side}</b>",
        "",
        f"<b>{symbol}</b>",
        f"TP :  <code>{old_s}</code>  →  <code>{new_s}</code>",
        "",
        f"<i>모멘텀 유지 — 다음 magnet으로 목표 전진</i>",
    ]
    if synced is True:
        lines.append("바이낸스: ✅ TP/SL 갱신 완료")
    elif synced is False:
        lines.append("바이낸스: ❌ TP/SL 갱신 실패 — 확인 필요")
    return "\n".join(lines)


def send_event_alerts(
    strategy_key: str,
    symbol: str,
    events: List[Dict[str, Any]],
    sync_info: Optional[Dict[str, Optional[bool]]] = None,
) -> None:
    """
    진입 / 청산 / TP전진 이벤트를 Telegram으로 즉시 전송.
    """
    if not events:
        return

    from features.strategy.common.config_loader import (
        is_telegram_alerts_enabled,
    )

    if not is_telegram_alerts_enabled(strategy_key):
        return

    label, emoji = _strategy_meta(strategy_key)
    si = sync_info or {}
    prefix = _local_prefix()

    try:
        from features.notifications.telegram_service import TelegramService

        ts = TelegramService()

        for ev in events:
            kind = ev.get("event")
            if kind == "entry":
                msg = _fmt_entry(ev, label, emoji, symbol, synced=si.get("entry"))
            elif kind == "close":
                msg = _fmt_close(ev, label, emoji, symbol, synced=si.get("close"))
            elif kind == "tp_advance":
                msg = _fmt_tp_advance(ev, label, emoji, symbol, synced=si.get("tp_advance"))
            else:
                continue
            ok, err = ts.send_message(prefix + msg)
            if not ok:
                print(f"[telegram_notifier] 전송 실패 ({strategy_key}): {err}")
    except Exception as e:
        print(f"[telegram_notifier] ❌ Telegram 전송 실패 ({strategy_key}): {e}")
