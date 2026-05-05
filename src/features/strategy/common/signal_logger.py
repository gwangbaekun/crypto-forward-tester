"""
Signal Logger — 봉 마감 시 signal 판단 스냅샷을 JSONL에 append.

_tick_and_notify 에서 _fire_and_forget(log_signal_snapshot(...)) 으로 호출.
진입 여부와 무관하게 매 봉 마감마다 한 줄 기록.

로그 경로: logs/signal_logs/YYYY-MM/{strategy_key}.jsonl
"""
from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_LOG_ROOT = Path(os.getenv("SIGNAL_LOG_DIR", "logs/signal_logs"))


def _log_path(strategy_key: str) -> Path:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    d = _LOG_ROOT / month
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{strategy_key}.jsonl"


def _get_position_snapshot(strategy_key: str, symbol: str) -> Dict[str, Any]:
    try:
        from features.strategy.common.config_loader import get_master_config
        cfg = (get_master_config() or {}).get(strategy_key) or {}
        base = cfg.get("base_strategy") or strategy_key
        strategy_tag = cfg.get("strategy_tag") or strategy_key

        eng_mod = importlib.import_module(f"features.strategy.{base}.engine")
        if strategy_tag != base and hasattr(eng_mod, "get_engine_for"):
            eng = eng_mod.get_engine_for(strategy_tag)
        else:
            eng = eng_mod.get_engine()

        pos = eng.get_position()
        if not pos:
            return {"side": None, "entry_price": None, "tp": None, "sl": None, "pnl_pct": None}

        tpsl = pos.get("tpsl") or {}
        tp = pos.get("tp") or tpsl.get("tp1") or tpsl.get("tp2")
        sl = pos.get("sl") or tpsl.get("sl")
        return {
            "side":        pos.get("side"),
            "entry_price": pos.get("entry_price"),
            "tp":          tp,
            "sl":          sl,
            "pnl_pct":     pos.get("pnl_pct"),
        }
    except Exception:
        return {"side": None, "entry_price": None, "tp": None, "sl": None, "pnl_pct": None}


def read_signal_logs(
    strategy_key: str,
    symbol: Optional[str] = None,
    limit: int = 100,
    month: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """JSONL에서 최신 순으로 entries 반환. month 미지정 시 현재 월."""
    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    path = _LOG_ROOT / target_month / f"{strategy_key}.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[Dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if symbol and e.get("symbol") != symbol:
                continue
            entries.append(e)
            if len(entries) >= limit:
                break
        except Exception:
            continue
    return entries


async def log_signal_snapshot(
    strategy_key: str,
    symbol: str,
    state: Dict[str, Any],
) -> None:
    """봉 마감 시점에 fire-and-forget 으로 호출. 진입 여부 무관하게 항상 기록."""
    try:
        raw_sig = state.get("signal") or {}
        sig: Dict[str, Any] = raw_sig if isinstance(raw_sig, dict) else {"signal": raw_sig}

        position = _get_position_snapshot(strategy_key, symbol)

        entry: Dict[str, Any] = {
            "ts":         datetime.now(timezone.utc).isoformat(),
            "strategy":   strategy_key,
            "symbol":     symbol,
            "price":      state.get("current_price"),
            "signal":     sig.get("signal", "none"),
            "confidence": sig.get("confidence", 0),
            "position":   position,
            "reasons":    sig.get("reasons") or [],
        }

        for k, v in [
            ("bull_score",     sig.get("bull_score")),
            ("bear_score",     sig.get("bear_score")),
            ("max_score",      sig.get("max_score")),
            ("conf_threshold", sig.get("conf_threshold")),
            ("vol_ratio",      sig.get("vol_ratio")),
            ("is_explosion",   sig.get("is_explosion")),
            ("is_solo",        sig.get("is_solo")),
            ("cvd_accel",      sig.get("cvd_accel")),
            ("cvd_higher",     sig.get("cvd_higher")),
            ("cvd_higher_tf",  sig.get("cvd_higher_tf")),
            ("tpsl_mode",      sig.get("tpsl_mode")),
        ]:
            if v is not None:
                entry[k] = v

        path = _log_path(strategy_key)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        sig_val  = entry["signal"]
        pos_side = position.get("side") or "none"
        print(f"[SignalLogger] {strategy_key}/{symbol}  signal={sig_val}  position={pos_side}  price={entry['price']}")

    except Exception as e:
        print(f"[SignalLogger] {strategy_key} 로그 실패: {e}")
