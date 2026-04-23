"""
전략 이벤트 → AlertDispatcher (Telegram + Discord) 알림.

- strategies_master.yaml 의 label / emoji 사용.
- telegram_alerts: true 이거나 binance_live: true 인 전략만 전송.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_REASON_LABEL: Dict[str, str] = {
    "closed_sl":              "🛑 SL Hit",
    "closed_sl_loss":         "🛑 SL Loss Cut",
    "closed_sl_profit":       "✅ SL Profit Exit (Ratchet)",
    "closed_tp":              "🎯 TP Hit (Magnet)",
    "closed_tp1":             "🎯 TP1 Hit",
    "closed_tp2":             "🎯 TP2 Hit",
    "closed_tp_final":        "🏁 Final Magnet Hit",
    "closed_structure_15m":   "📐 15m Structure Stop",
    "closed_trailing_sl":     "🛑 Trailing SL",
    "closed_reversal":        "🔄 Reversal Exit",
    "closed_zscore_reversal": "📉 Z-Score Reversal",
    "closed_manual":          "🖐 Manual Exit",
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
    max_score  = pos.get("max_score")
    detail     = pos.get("direction_detail") or pos.get("force_type") or ""
    tpsl_mode  = pos.get("tpsl_mode_label") or pos.get("tpsl_mode") or ""
    vol_ratio  = pos.get("vol_ratio")
    cvd_accel  = pos.get("cvd_accel")
    cvd_higher = pos.get("cvd_higher")
    cvd_htf    = pos.get("cvd_higher_tf") or ""
    m15_sup    = pos.get("m15_support")
    m15_res    = pos.get("m15_resistance")
    reasons    = pos.get("reasons") or []

    side_e = "🟢" if side == "LONG" else "🔴"
    ep_s = f"${ep:,.2f}" if ep else "—"
    tp_s = f"${tp:,.2f}" if tp else "—"
    sl_s = f"${sl:,.2f}" if sl else "—"

    score_s = f"{confidence}/{max_score}" if max_score else str(confidence)
    conf_s = "⭐" * int(min(confidence, 5)) if confidence else ""

    lines = [
        f"{emoji} <b>[{label}]  📌 Entry  {side_e} {side}</b>",
        "",
        f"<b>{symbol}</b>  @  <code>{ep_s}</code>",
        "",
        f"TP :  <code>{tp_s}</code>",
        f"SL :  <code>{sl_s}</code>",
    ]
    if tfs:
        lines.append(f"TF :  <code>{tfs}</code>")
    if tpsl_mode:
        lines.append(f"Mode:  <code>{tpsl_mode}</code>")
    if conf_s:
        lines.append(f"Confidence:  {conf_s}  <code>({score_s})</code>")
    if vol_ratio is not None:
        lines.append(f"Volume Ratio:  <code>{vol_ratio:.2f}x</code>")
    if cvd_accel is not None:
        accel_e = "📈" if cvd_accel > 0 else "📉"
        lines.append(f"CVD Accel:  {accel_e} <code>{cvd_accel:+.0f}</code>")
    if cvd_higher is not None and cvd_htf:
        hi_e = "📈" if cvd_higher > 0 else "📉"
        lines.append(f"CVD {cvd_htf}:  {hi_e} <code>{cvd_higher:+.0f}</code>")
    if m15_sup and m15_res:
        lines.append(f"15m Structure:  <code>${m15_sup:,.2f}</code> ~ <code>${m15_res:,.2f}</code>")
    if detail:
        lines.append(f"Signal:  <code>{detail}</code>")
    if reasons:
        lines.append("")
        lines.append("<b>📋 Signal Conditions</b>")
        for r in reasons:
            lines.append(f"  • {r}")
    if synced is True:
        lines.append("")
        lines.append("Binance: ✅ Fill Confirmed")
    elif synced is False:
        lines.append("")
        lines.append("Binance: ❌ Not Filled — Check Required")
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
    dur_s = f"{int(dur)}m" if dur else ""

    lines = [
        f"{emoji} <b>[{label}]  🔔 Exit  {side_e} {side}</b>",
        f"<b>{r_label}</b>",
        "",
        f"<b>{symbol}</b>  {ep_s}  →  <code>{xp_s}</code>",
        "",
        f"PnL:  {pnl_e} <code>{pnl_s}</code>",
    ]
    if dur_s:
        lines.append(f"Holding Time:  <code>{dur_s}</code>")
    if note:
        lines.append(f"Reason:  <code>{note}</code>")
    if synced is True:
        lines.append("Binance: ✅ Exit Confirmed")
    elif synced is False:
        lines.append("Binance: ❌ Exit Failed — Manual Exit Required")
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
        f"{emoji} <b>[{label}]  ⏫ TP Advanced  {side}</b>",
        "",
        f"<b>{symbol}</b>",
        f"TP :  <code>{old_s}</code>  →  <code>{new_s}</code>",
        "",
        f"<i>Momentum intact — advancing target to next magnet</i>",
    ]
    if synced is True:
        lines.append("Binance: ✅ TP/SL Updated")
    elif synced is False:
        lines.append("Binance: ❌ TP/SL Update Failed — Check Required")
    return "\n".join(lines)


def send_event_alerts(
    strategy_key: str,
    symbol: str,
    events: List[Dict[str, Any]],
    sync_info: Optional[Dict[str, Optional[bool]]] = None,
) -> None:
    """진입 / 청산 / TP전진 이벤트를 Telegram + Discord로 즉시 전송."""
    if not events:
        return

    from features.strategy.common.config_loader import is_alerts_enabled

    if not is_alerts_enabled(strategy_key):
        return

    label, emoji = _strategy_meta(strategy_key)
    si = sync_info or {}
    prefix = _local_prefix()

    try:
        from features.notifications.alert_dispatcher import AlertDispatcher

        dispatcher = AlertDispatcher()

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

            results = dispatcher.send_message(prefix + msg, strategy_key=strategy_key)
            for channel, (ok, err) in results.items():
                if not ok and "is not configured" not in err and "No webhook configured" not in err:
                    print(f"[notifier] {channel} send failed ({strategy_key}): {err}")

    except Exception as e:
        print(f"[notifier] ❌ alert send failed ({strategy_key}): {e}")
