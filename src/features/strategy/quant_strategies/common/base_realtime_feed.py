"""
공통 Realtime Feed 유틸리티.

새 전략의 realtime_feed.py에서 build_state()를 호출하면 됨:

    from features.strategy.quant_strategies.common.base_realtime_feed import build_state

    async def get_state(symbol="BTCUSDT", tfs="15m,1h,4h"):
        from .signal import compute_signal
        return await build_state("my_strategy", symbol, tfs, compute_signal)
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Callable, Dict, List, Optional

# GC로 인한 태스크 중간 취소 방지 — 참조 보관용 전역 set
# Python asyncio: create_task() 결과를 아무도 참조하지 않으면 GC가 태스크를 수거·취소함
_bg_tasks: set = set()

# signal_interval 캐시: full API fetch는 signal_interval 초마다만 수행
# 그 사이 tick은 WS price만 갱신 → SL/TP 체크만 실행
_signal_cache: Dict[str, Dict] = {}  # key: "{strategy_key}:{symbol}"


def _fire_and_forget(coro) -> asyncio.Task:
    """create_task + 참조 보관. 완료 시 자동 제거."""
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
) -> Dict[str, Any]:
    """
    표준 realtime_feed 구현.

    1. Binance klines로 sweep_by_tf 번들 구성
    2. compute_fn(current_price, sweep_by_tf, magnets, **extra) 호출
    3. state dict 구성 후 반환
    4. tick + Binance 주문 + Telegram 알림을 비동기 태스크로 트리거

    Args:
        strategy_key:      strategies_master.yaml 키 (e.g. "atr_breakout")
        symbol:            심볼 (e.g. "BTCUSDT")
        tfs_str:           쉼표 구분 TF 문자열 (e.g. "15m,1h,4h")
        compute_fn:        signal.py의 compute_signal 함수
        extra_bundle_args: bundle → dict 함수. macro/lsr 등 추가 인자가 필요한 전략에서 사용.
    """
    from features.strategy.quant_strategies.common.config_loader import get_master_config
    from features.strategy.quant_strategies.common.kline_bundle import build_kline_bundle

    # signal_interval 설정 읽기 (binance_live 전략 전용 최적화)
    master_cfg = (get_master_config() or {}).get(strategy_key) or {}
    signal_interval: Optional[int] = master_cfg.get("signal_interval")

    cache_key = f"{strategy_key}:{symbol}"
    now = _time.time()

    # signal 캐시가 유효하면 WS price만 갱신하고 tick (full API fetch 스킵)
    if signal_interval and cache_key in _signal_cache:
        cached = _signal_cache[cache_key]
        if now - cached["ts"] < signal_interval:
            try:
                from common.binance_price_ws import get_cached_price
                ws_price = get_cached_price(symbol)
                if ws_price:
                    state = {**cached["state"], "current_price": ws_price}
                    _tick_and_notify(strategy_key, symbol, ws_price, state)
                    return state
            except Exception:
                pass  # WS 실패 시 full fetch로 fallback

    tfs_list = [x.strip() for x in tfs_str.split(",") if x.strip()]
    bundle = await build_kline_bundle(symbol, tfs_list)

    sweep_by_tf: Dict[str, Any] = {
        tf: bundle.sweep_by_tf.get(tf) or {} for tf in tfs_list
    }

    sig: Dict[str, Any] = {}
    if bundle.price:
        kwargs: Dict[str, Any] = dict(
            current_price=bundle.price,
            sweep_by_tf=sweep_by_tf,
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

    # by_tf: 실제 fetch한 TF 전체를 반영 (이전엔 "15m"만 하드코딩)
    entry_tf = master_cfg.get("entry_tf") or (tfs_list[1] if len(tfs_list) > 1 else tfs_list[0] if tfs_list else "15m")
    by_tf: Dict[str, Any] = {tf: {"signal": sig} for tf in tfs_list}

    state: Dict[str, Any] = {
        "symbol":        symbol,
        "current_price": bundle.price,
        "signal":        sig,
        "by_tf":         by_tf,
        "entry_tf":      entry_tf,
    }

    # signal / state 캐시 갱신 (signal_interval 없어도 tick_interval 동안 유효)
    tick_interval: Optional[int] = master_cfg.get("tick_interval")
    effective_ttl = signal_interval or tick_interval
    if effective_ttl:
        _signal_cache[cache_key] = {"state": state, "ts": now}

    _tick_and_notify(strategy_key, symbol, bundle.price, state)
    return state


def _tick_and_notify(
    strategy_key: str,
    symbol: str,
    current_price: Optional[float],
    state: Dict[str, Any],
) -> None:
    try:
        import importlib
        from features.strategy.quant_strategies.common.config_loader import get_master_config
        master = get_master_config() or {}
        cfg = master.get(strategy_key) or {}
        base = cfg.get("base_strategy") or strategy_key
        strategy_tag = cfg.get("strategy_tag") or strategy_key

        ft = importlib.import_module(f"features.strategy.quant_strategies.{base}.forward_test")

        # base_strategy 패턴: get_engine_for(tag) 디스패치 우선
        if strategy_tag != base and hasattr(ft, "get_engine_for"):
            engine = ft.get_engine_for(strategy_tag)
        else:
            engine = ft.get_engine()

        tick_result = engine.tick(symbol, state, report_text=None)
        if not tick_result:
            return
        events = tick_result.get("events") or []
        if not events:
            return
        _fire_and_forget(_execute_verify_notify(strategy_key, symbol, events, current_price))
    except Exception as e:
        print(f"[_tick_and_notify:{strategy_key}] ❌ tick 오류: {e}")


def _resolve_tp_sl(pos: Dict[str, Any]):
    """flat(tp/sl) 및 중첩(tpsl.tp1/sl) 구조 모두 지원 — renaissance 호환."""
    tpsl = pos.get("tpsl") or {}
    tp = pos.get("tp") or tpsl.get("tp1") or tpsl.get("tp2")
    sl = pos.get("sl") or tpsl.get("sl")
    return tp, sl


def _update_entry_price_in_db(trade_id: int, fill_price: float) -> None:
    """
    Binance 진입 실체결가(avgPrice)로 DB의 entry_price 덮어쓰기.

    forward_test 엔진은 신호 포착 시점의 WS 가격으로 entry_price를 기록하지만,
    실제 Binance market 주문은 수십 초 후 다른 가격에 체결된다.
    이 함수가 호출되면 실체결가로 보정한다 (pnl_pct는 청산 시 재계산되므로 여기선 건드리지 않음).
    """
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
    """
    Binance 실체결가(avgPrice)로 DB의 exit_price + pnl_pct 덮어쓰기.

    forward_test 엔진은 SL/TP 설정값 또는 현재 WS 가격을 exit_price로 기록하지만,
    실제 Binance market close는 60초 폴링 지연 + 슬리피지로 다른 가격에 체결된다.
    이 함수가 호출되면 체결 완료 시점의 실가격으로 보정한다.
    """
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
                print(f"[fill_price_db] trade_id={trade_id} 없음 — 업데이트 스킵")
                return
            # 이미 다른 값으로 override된 경우 (재시도 방지)
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
    """
    Binance 실주문 → DB fill price 보정 → Telegram 알림.

    close 이벤트에서:
    1. executor.close_position() 호출 → avgPrice(실체결가) 수신
    2. _update_fill_price_in_db()로 DB exit_price / pnl_pct 덮어쓰기
    3. Telegram 이벤트 dict의 exit_price도 실체결가로 교체 (알림 정확도)
    """
    print(f"[{strategy_key}] events: {[e.get('event') for e in events]}")

    executor = None
    from features.strategy.quant_strategies.common.config_loader import is_binance_live_enabled
    if is_binance_live_enabled(strategy_key):
        try:
            from common.binance_executor import get_executor
            executor = get_executor()
        except Exception as e:
            print(f"[{strategy_key}] executor 없음: {e}")

    sync_info: Dict[str, Optional[bool]] = {}

    for ev in events:
        kind = ev.get("event")

        if kind == "entry":
            pos  = ev.get("position") or {}
            side = pos.get("side")
            tp, sl = _resolve_tp_sl(pos)
            print(f"[{strategy_key}] 진입 — side={side} tp={tp} sl={sl}")
            if executor and side and current_price:
                try:
                    result     = await executor.open_position(symbol, side, current_price)
                    fill_price = float((result or {}).get("avgPrice") or 0)
                    sync_info["entry"] = fill_price > 0
                    if fill_price > 0:
                        trade_id = pos.get("trade_id")
                        if trade_id is not None:
                            _update_entry_price_in_db(trade_id, fill_price)
                        # 알림 dict도 실체결가로 교체
                        ev["position"] = {**pos, "entry_price": fill_price}
                        print(
                            f"[{strategy_key}] ✅ 진입 체결가 보정 — "
                            f"엔진={current_price} → Binance={fill_price:.2f}"
                        )
                    if tp or sl:
                        await executor.place_tp_sl(symbol, side, tp=tp, sl=sl)
                except Exception as e:
                    sync_info["entry"] = False
                    print(f"[{strategy_key}] ❌ 진입 Binance 오류: {e}")
            else:
                sync_info["entry"] = None

        elif kind == "close":
            trade  = ev.get("trade") or {}
            side   = trade.get("side")
            reason = trade.get("exit_reason", "")
            print(f"[{strategy_key}] 청산 — side={side} reason={reason}")
            if executor and side:
                try:
                    result     = await executor.close_position(symbol, side)
                    fill_price = float((result or {}).get("avgPrice") or 0)
                    sync_info["close"] = fill_price > 0
                    if fill_price > 0:
                        trade_id    = trade.get("trade_id")
                        entry_price = float(trade.get("entry_price") or 0)
                        # DB 보정 (실체결가로 exit_price / pnl_pct 덮어쓰기)
                        if trade_id is not None:
                            _update_fill_price_in_db(trade_id, fill_price, entry_price, side)
                        # Telegram 알림 dict도 실체결가로 교체
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
            else:
                sync_info["close"] = None

        elif kind == "tp_advance":
            pos  = ev.get("position") or {}
            side = pos.get("side")
            tp, sl = _resolve_tp_sl(pos)
            print(f"[{strategy_key}] TP advance — side={side} new_tp={tp} sl={sl}")
            if executor and side:
                try:
                    await executor.place_tp_sl(symbol, side, tp=tp, sl=sl)
                    sync_info["tp_advance"] = True
                except Exception as e:
                    sync_info["tp_advance"] = False
                    print(f"[{strategy_key}] ❌ TP/SL 갱신 오류: {e}")
            else:
                sync_info["tp_advance"] = None

    try:
        from features.strategy.quant_strategies.common.telegram_notifier import (
            send_event_alerts,
        )

        send_event_alerts(strategy_key, symbol, events, sync_info)
    except Exception as e:
        print(f"[{strategy_key}] Telegram 알림 오류: {e}")
