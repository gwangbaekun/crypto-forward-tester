"""
공통 Realtime Feed 유틸리티.

새 전략의 realtime_feed.py에서 build_state()를 호출하면 됨:

    from features.strategy.common.base_realtime_feed import build_state

    async def get_state(symbol="BTCUSDT", tfs="15m,1h,4h"):
        from .signal import compute_signal
        return await build_state("my_strategy", symbol, tfs, compute_signal)

── Close-only stop 설계 ──────────────────────────────────────────────────────
backtest_runner(v1) 과 동일:
  - 신호 계산 + exit 체크 + 진입 → 1h 봉 마감 시 1회만
  - 봉 사이 구간(ws_only / signal_interval 캐시)에서는 tick 완전 비활성
  - check_exit 에 bar_high / bar_low (완성봉 OHLC) 전달 → wick 허위 손절 없음
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Callable, Dict, List, Optional

# GC로 인한 태스크 중간 취소 방지
_bg_tasks: set = set()

# 마지막으로 신호를 계산한 완성봉 타임 캐시
_signal_cache: Dict[str, Dict] = {}   # key: "{strategy_key}:{symbol}"
_last_bar_time: Dict[str, int] = {}   # key: "{strategy_key}:{symbol}"


def _fire_and_forget(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def build_state(
    strategy_key: str,
    symbol: str,
    tfs_str: str,
    compute_fn: Callable,
    extra_bundle_args: Optional[Callable] = None,
    ws_only: bool = False,
) -> Dict[str, Any]:
    """
    표준 realtime_feed 구현.

    Close-only stop 원칙:
      새 1h 봉이 감지될 때만 compute_fn + engine.tick() 실행.
      봉 사이 구간(ws_only / signal_interval)에서는 tick 을 호출하지 않음.
      → backtest_runner(v1) 과 exit 타이밍 동일.
    """
    from features.strategy.common.config_loader import get_master_config
    from features.strategy.common.kline_bundle import build_kline_bundle

    master_cfg = (get_master_config() or {}).get(strategy_key) or {}
    signal_interval: Optional[int] = master_cfg.get("signal_interval")
    cache_key = f"{strategy_key}:{symbol}"
    now = _time.time()

    # ── ws_only / signal_interval 구간: 다음 봉 마감 전까지 tick 비활성 ────
    # Close-only stop: 1h 봉 마감 시에만 exit/entry 체크 (backtest v1 동일).
    # 단, 다음 봉 마감 예상 시각(last_bar_ts + entry_tf_seconds - 30초 버퍼) 이후에는
    # full fetch로 fall-through → 새 봉 감지 → tick 실행.
    _entry_tf_sec = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
    }
    _etf = (get_master_config() or {}).get(strategy_key, {}).get("entry_tf") or "1h"
    _tf_sec = _entry_tf_sec.get(_etf, 3600)
    _last_bt = _last_bar_time.get(cache_key, 0)
    _next_bar_expected = _last_bt + _tf_sec  # 다음 봉 open_time (UTC sec)
    _near_bar_close = (now >= _next_bar_expected - 30)  # 30초 전부터 full fetch

    if ws_only and cache_key in _signal_cache and not _near_bar_close:
        return _signal_cache[cache_key]["state"]

    if signal_interval and cache_key in _signal_cache and not _near_bar_close:
        if now - _signal_cache[cache_key]["ts"] < signal_interval:
            return _signal_cache[cache_key]["state"]

    # ── Full fetch ───────────────────────────────────────────────────────────
    tfs_list = [x.strip() for x in tfs_str.split(",") if x.strip()]
    bundle   = await build_kline_bundle(symbol, tfs_list)

    sweep_by_tf: Dict[str, Any] = {
        tf: bundle.sweep_by_tf.get(tf) or {} for tf in tfs_list
    }

    entry_tf_key = master_cfg.get("entry_tf") or (
        tfs_list[1] if len(tfs_list) > 1 else tfs_list[0] if tfs_list else "1h"
    )
    _bars = (sweep_by_tf.get(entry_tf_key) or {}).get("data") or []

    # 완성봉 = [-2]  ([-1] 은 현재 형성 중인 봉)
    completed_bar_time = int(_bars[-2]["time"]) if len(_bars) >= 2 else 0
    new_bar_detected   = (completed_bar_time != _last_bar_time.get(cache_key, 0))

    sig: Dict[str, Any] = {}
    bar_close_price = bundle.price or 0.0
    bar_high = bar_close_price
    bar_low  = bar_close_price

    if bundle.price and new_bar_detected:
        # ── 새 1h 봉 마감 → 신호 계산 ──────────────────────────────────────
        # sweep 에서 형성 중인 봉 제거 → backtest sweep_builder 와 동일 조건
        completed_sweep: Dict[str, Any] = {
            tf: {
                **sweep_by_tf.get(tf, {}),
                "data": (sweep_by_tf.get(tf) or {}).get("data", [])[:-1],
            }
            for tf in tfs_list
        }
        bar_close_price = float(_bars[-2].get("close") or 0) or bundle.price
        bar_high        = float(_bars[-2].get("high")  or 0) or bundle.price
        bar_low         = float(_bars[-2].get("low")   or 0) or bundle.price

        kwargs: Dict[str, Any] = dict(
            current_price=bar_close_price,
            sweep_by_tf=completed_sweep,
            magnets=bundle.magnets,
        )
        if extra_bundle_args:
            kwargs.update(extra_bundle_args(bundle))
        try:
            import asyncio as _asyncio
            if _asyncio.iscoroutinefunction(compute_fn):
                sig = await compute_fn(**kwargs) or {}
            else:
                sig = compute_fn(**kwargs) or {}
        except Exception as e:
            sig = {"error": str(e)}

        _last_bar_time[cache_key] = completed_bar_time

    elif cache_key in _signal_cache:
        # 같은 봉 진행 중 — 진입 차단 (tick 도 호출하지 않으므로 사실상 무관)
        cached_sig = _signal_cache[cache_key]["state"].get("signal") or {}
        sig = {**cached_sig, "signal": "none"}

    entry_tf = master_cfg.get("entry_tf") or entry_tf_key
    state: Dict[str, Any] = {
        "symbol":        symbol,
        "current_price": bar_close_price,
        "signal":        sig,
        "by_tf":         {tf: {"signal": sig} for tf in tfs_list},
        "entry_tf":      entry_tf,
        # backtest 와 동일하게 완성봉 OHLC 전달 → check_exit 에서 wick 허위 손절 방지
        "bar_high":      bar_high,
        "bar_low":       bar_low,
    }

    _signal_cache[cache_key] = {"state": state, "ts": now}

    # tick 은 새 봉 마감 시에만 실행 (Close-only stop)
    if new_bar_detected and bundle.price:
        _tick_and_notify(strategy_key, symbol, bar_close_price, state)

    return state


def _tick_and_notify(
    strategy_key: str,
    symbol: str,
    current_price: Optional[float],
    state: Dict[str, Any],
) -> None:
    try:
        import importlib
        from features.strategy.common.config_loader import get_master_config
        master = get_master_config() or {}
        cfg    = master.get(strategy_key) or {}
        base   = cfg.get("base_strategy") or strategy_key
        strategy_tag = cfg.get("strategy_tag") or strategy_key

        ft = importlib.import_module(f"features.strategy.{base}.engine")

        if strategy_tag != base and hasattr(ft, "get_engine_for"):
            engine = ft.get_engine_for(strategy_tag)
        else:
            engine = ft.get_engine()

        # forward test: ws 현재가로 bar_high/bar_low/current_price 오버라이드.
        # backtest는 봉 전체 OHLC가 필요하지만 forward test는 "지금 가격이 SL/TP에 닿았는가"만 보면 된다.
        from common.binance_price_ws import get_cached_price
        ws_price = get_cached_price(symbol)
        if ws_price is None:
            return
        tick_state = {**state, "current_price": ws_price, "bar_high": ws_price, "bar_low": ws_price}

        tick_result = engine.tick(symbol, tick_state, report_text=None)
        if not tick_result:
            return
        events = tick_result.get("events") or []
        if not events:
            return
        _fire_and_forget(_execute_verify_notify(strategy_key, symbol, events, ws_price))
    except Exception as e:
        print(f"[_tick_and_notify:{strategy_key}] ❌ tick 오류: {e}")


def _resolve_tp_sl(pos: Dict[str, Any]):
    tpsl = pos.get("tpsl") or {}
    tp = pos.get("tp") or tpsl.get("tp1") or tpsl.get("tp2")
    sl = pos.get("sl") or tpsl.get("sl")
    return tp, sl


def _update_entry_price_in_db(trade_id: int, fill_price: float) -> None:
    try:
        from db.config import get_engine_url
        if not get_engine_url():
            return
    except Exception:
        return
    try:
        from db.session import get_session
        from db.models import ForwardTrade
        session = get_session()
        try:
            trade = session.query(ForwardTrade).filter(ForwardTrade.id == trade_id).first()
            if not trade or trade.status != "open":
                return
            old_entry = trade.entry_price
            trade.entry_price = fill_price
            session.commit()
            print(f"[entry_price_db] trade_id={trade_id} entry_price {old_entry} → {fill_price:.2f}")
        except Exception as e:
            session.rollback()
            print(f"[entry_price_db] DB 업데이트 오류: {e}")
        finally:
            session.close()
    except Exception as e:
        print(f"[entry_price_db] 세션 오류: {e}")


def _update_fill_price_in_db(trade_id: int, fill_price: float, entry_price: float, side: str) -> None:
    try:
        from db.config import get_engine_url
        if not get_engine_url():
            return
    except Exception:
        return
    try:
        from db.session import get_session
        from db.models import ForwardTrade
        session = get_session()
        try:
            trade = session.query(ForwardTrade).filter(ForwardTrade.id == trade_id).first()
            if not trade:
                return
            if trade.exit_price == fill_price:
                return
            entry = float(entry_price or trade.entry_price or 0)
            real_pnl = (
                (fill_price - entry) / entry * 100 if side == "long"
                else (entry - fill_price) / entry * 100
            ) if entry > 0 else 0.0
            old_exit = trade.exit_price
            old_pnl  = trade.pnl_pct
            trade.exit_price = fill_price
            trade.pnl_pct    = round(real_pnl, 4)
            session.commit()
            print(
                f"[fill_price_db] trade_id={trade_id} exit_price "
                f"{old_exit} → {fill_price:.2f}  pnl {old_pnl} → {real_pnl:.4f}%"
            )
        except Exception as e:
            session.rollback()
            print(f"[fill_price_db] DB 업데이트 오류: {e}")
        finally:
            session.close()
    except Exception as e:
        print(f"[fill_price_db] 세션 오류: {e}")


async def _execute_verify_notify(
    strategy_key: str,
    symbol: str,
    events: List[Dict],
    current_price: Optional[float],
) -> None:
    print(f"[{strategy_key}] events: {[e.get('event') for e in events]}")

    from features.strategy.common.config_loader import (
        is_binance_live_enabled,
        is_ctrader_live_enabled,
        get_master_config,
    )
    _binance_leverage = int((get_master_config() or {}).get(strategy_key, {}).get("binance_leverage") or 1)

    binance_executor = None
    if is_binance_live_enabled(strategy_key):
        try:
            from common.binance_executor import get_executor as _get_binance
            binance_executor = _get_binance()
        except Exception as e:
            print(f"[{strategy_key}] Binance executor 없음: {e}")

    ctrader_executor = None
    if is_ctrader_live_enabled(strategy_key):
        try:
            from common.ctrader_executor import get_executor as _get_ctrader
            ctrader_executor = _get_ctrader()
        except Exception as e:
            print(f"[{strategy_key}] cTrader executor 없음: {e}")

    sync_info: Dict[str, Optional[bool]] = {}

    for ev in events:
        kind = ev.get("event")

        if kind == "entry":
            pos  = ev.get("position") or {}
            side = pos.get("side")
            tp, sl = _resolve_tp_sl(pos)
            print(f"[{strategy_key}] 진입 — side={side} tp={tp} sl={sl}")

            # ── Binance ─────────────────────────────────────────────────────
            if binance_executor and side and current_price:
                try:
                    result     = await binance_executor.open_position(symbol, side, current_price, leverage=_binance_leverage)
                    fill_price = float((result or {}).get("avgPrice") or 0)
                    sync_info["entry"] = fill_price > 0
                    if fill_price > 0:
                        trade_id = pos.get("trade_id")
                        if trade_id is not None:
                            _update_entry_price_in_db(trade_id, fill_price)
                        ev["position"] = {**pos, "entry_price": fill_price}
                        print(
                            f"[{strategy_key}] ✅ 진입 체결가 보정 — "
                            f"엔진={current_price} → Binance={fill_price:.2f}"
                        )
                    if tp or sl:
                        await binance_executor.place_tp_sl(symbol, side, tp=tp, sl=sl)
                except Exception as e:
                    sync_info["entry"] = False
                    print(f"[{strategy_key}] ❌ 진입 Binance 오류: {e}")
            elif not binance_executor:
                sync_info["entry"] = None

            # ── cTrader (Binance와 독립 실행) ────────────────────────────────
            if ctrader_executor and side and current_price:
                try:
                    ct_result = await ctrader_executor.open_position(symbol, side, current_price)
                    ct_fill   = float((ct_result or {}).get("avgPrice") or 0)
                    ok = ct_fill > 0
                    sync_info["ctrader_entry"] = ok
                    ev["_ctrader_synced"] = ok
                    if tp or sl:
                        await ctrader_executor.place_tp_sl(symbol, side, tp=tp, sl=sl)
                except Exception as e:
                    sync_info["ctrader_entry"] = False
                    ev["_ctrader_synced"] = False
                    print(f"[{strategy_key}] ❌ 진입 cTrader 오류: {e}")

        elif kind == "close":
            trade  = ev.get("trade") or {}
            side   = trade.get("side")
            reason = trade.get("exit_reason", "")
            print(f"[{strategy_key}] 청산 — side={side} reason={reason}")

            # ── Binance ─────────────────────────────────────────────────────
            if binance_executor and side:
                try:
                    result     = await binance_executor.close_position(symbol, side)
                    fill_price = float((result or {}).get("avgPrice") or 0)
                    sync_info["close"] = fill_price > 0
                    if fill_price > 0:
                        trade_id    = trade.get("trade_id")
                        entry_price = float(trade.get("entry_price") or 0)
                        if trade_id is not None:
                            _update_fill_price_in_db(trade_id, fill_price, entry_price, side)
                        real_pnl = (
                            (fill_price - entry_price) / entry_price * 100 if side == "long"
                            else (entry_price - fill_price) / entry_price * 100
                        ) if entry_price > 0 else trade.get("pnl_pct", 0)
                        ev["trade"] = {
                            **trade,
                            "exit_price": fill_price,
                            "pnl_pct":    round(real_pnl, 4),
                        }
                        print(
                            f"[{strategy_key}] ✅ 체결가 보정 — "
                            f"엔진={trade.get('exit_price')} → Binance={fill_price:.2f}  "
                            f"pnl={real_pnl:.4f}%"
                        )
                except Exception as e:
                    sync_info["close"] = False
                    print(f"[{strategy_key}] ❌ 청산 Binance 오류: {e}")
            elif not binance_executor:
                sync_info["close"] = None

            # ── cTrader ─────────────────────────────────────────────────────
            if ctrader_executor and side:
                try:
                    ct_result = await ctrader_executor.close_position(symbol, side)
                    ct_fill   = float((ct_result or {}).get("avgPrice") or 0)
                    ok = ct_fill > 0
                    sync_info["ctrader_close"] = ok
                    ev["_ctrader_synced"] = ok
                except Exception as e:
                    sync_info["ctrader_close"] = False
                    ev["_ctrader_synced"] = False
                    print(f"[{strategy_key}] ❌ 청산 cTrader 오류: {e}")

        elif kind == "tp_advance":
            pos  = ev.get("position") or {}
            side = pos.get("side")
            tp, sl = _resolve_tp_sl(pos)
            print(f"[{strategy_key}] TP advance — side={side} new_tp={tp} sl={sl}")

            # ── Binance ─────────────────────────────────────────────────────
            if binance_executor and side:
                try:
                    await binance_executor.place_tp_sl(symbol, side, tp=tp, sl=sl)
                    sync_info["tp_advance"] = True
                except Exception as e:
                    sync_info["tp_advance"] = False
                    print(f"[{strategy_key}] ❌ TP/SL 갱신 오류: {e}")
            elif not binance_executor:
                sync_info["tp_advance"] = None

            # ── cTrader ─────────────────────────────────────────────────────
            if ctrader_executor and side:
                try:
                    await ctrader_executor.place_tp_sl(symbol, side, tp=tp, sl=sl)
                    sync_info["ctrader_tp_advance"] = True
                except Exception as e:
                    sync_info["ctrader_tp_advance"] = False
                    print(f"[{strategy_key}] ❌ cTrader TP/SL 갱신 오류: {e}")

    try:
        from features.strategy.common.notifier import send_event_alerts
        send_event_alerts(strategy_key, symbol, events, sync_info)
    except Exception as e:
        print(f"[{strategy_key}] Telegram 알림 오류: {e}")