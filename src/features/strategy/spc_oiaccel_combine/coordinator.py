"""spc_oiaccel_combine — 합체 실행 코디네이터 (v2, 얇은 fan-out).

멤버 전략 이벤트(entry/close)를 받아 config의 enabled venue마다
해당 멤버의 notional_ratio·leverage로 주문만 한다. 신호 로직·DB 기록 없음.
계좌 합산 성적은 개별 forward_test 기록을 조회·합산해서 본다(대시보드).
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml

COMBINE_TAG = "spc_oiaccel_combine"
_CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"


@lru_cache(maxsize=1)
def load_combine_config() -> Dict[str, Any]:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}


def _binance_executor():
    from common.binance_executor import get_executor
    return get_executor()


def _ctrader_executor(vcfg: Dict[str, Any]):
    from common.ctrader_executor import get_executor
    return get_executor(account_id=vcfg.get("account_id"))


def _get_executor(venue: str, vcfg: Dict[str, Any]):
    if venue == "binance":
        return _binance_executor()
    if venue == "ctrader":
        return _ctrader_executor(vcfg)
    return None


async def handle(
    strategy_tag: str,
    events: List[Dict[str, Any]],
    symbol: str,
    current_price: Optional[float],
) -> None:
    """멤버 이벤트를 enabled venue마다 venue별 사이징으로 주문 fan-out. 기록 없음."""
    venues = (load_combine_config().get("venues") or {})
    for venue, vcfg in venues.items():
        if not isinstance(vcfg, dict) or not vcfg.get("enabled"):
            continue
        member = (vcfg.get("members") or {}).get(strategy_tag)
        if not member or member.get("notional_ratio") is None:
            continue  # 이 venue는 이 멤버를 미러하지 않음
        nr = float(member["notional_ratio"])
        lev = int(vcfg.get("leverage") or 1)
        ex = None
        try:
            ex = _get_executor(venue, vcfg)
        except Exception as e:
            print(f"[{COMBINE_TAG}/{venue}] executor 없음: {e}")
        if ex is None:
            continue

        for ev in events:
            kind = ev.get("event")
            if kind == "entry":
                pos = ev.get("position") or {}
                side = pos.get("side")
                if not side or not current_price:
                    continue
                try:
                    await ex.open_position(symbol, side, current_price,
                                           leverage=lev, notional_ratio=nr)
                    tp, sl = pos.get("tp"), pos.get("sl")
                    if tp or sl:
                        await ex.place_tp_sl(symbol, side, tp=tp, sl=sl)
                    print(f"[{COMBINE_TAG}/{venue}] 진입 {side} {symbol} nr={nr} lev={lev}")
                except Exception as e:
                    print(f"[{COMBINE_TAG}/{venue}] 진입 오류: {e}")
            elif kind == "close":
                trade = ev.get("trade") or {}
                side = trade.get("side")
                if not side:
                    continue
                try:
                    await ex.close_position(symbol, side)
                    print(f"[{COMBINE_TAG}/{venue}] 청산 {side} {symbol}")
                except Exception as e:
                    print(f"[{COMBINE_TAG}/{venue}] 청산 오류: {e}")
