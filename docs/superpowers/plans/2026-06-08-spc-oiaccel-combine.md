# spc_oiaccel_combine 합체 운용 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 검증된 두 전략(spot_perp_cvd, oi_accel_breakout_v2)을 단일 바이낸스 계좌에서 전략별 명목 비중(0.75x / 0.5x)으로 합산 운용하는 얇은 실행 레이어를 만든다.

**Architecture:** 합체 모듈은 신호를 모른다. 기존 엔진이 `engine.tick()`에서 뱉는 entry/close 이벤트를 `_execute_verify_notify`가 받을 때, 전략이 합체 그룹이면 coordinator로 위임해 `notional_ratio`로 단일 계좌 실주문 + `strategy="spc_oiaccel_combine"` 태그로 체결 기록. 신호·청산 로직 0줄 복제.

**Tech Stack:** Python, SQLAlchemy(ForwardTrade), httpx(binance_executor), YAML config. 이 repo는 pytest 인프라가 없으므로 순수 사이징 로직만 standalone 스크립트로 검증한다.

**제약:** btc_backtest 파일은 수정 금지. 개별 전략 engine 코드는 0줄 수정.

---

### Task 1: binance_executor.open_position에 notional_ratio 추가

**Files:**
- Modify: `src/common/binance_executor.py` (open_position, 약 190-232행)

- [ ] **Step 1: open_position 시그니처에 notional_ratio 파라미터 추가**

`async def open_position(...)` 시그니처를 아래로 변경 (기존 leverage 파라미터 뒤에 추가):

```python
    async def open_position(
        self,
        symbol: str,
        side: str,              # "long" | "short"
        current_price: float = 0,
        leverage: Optional[int] = None,
        notional_ratio: Optional[float] = None,
    ) -> Optional[Dict]:
```

- [ ] **Step 2: margin 계산부를 notional_ratio 기반으로 분기**

현재 `margin = balance * BALANCE_RATIO` 줄(약 229행)을 아래로 교체:

```python
        # notional_ratio 지정 시: 총자산(포지션 마진 포함) 기준으로 명목 계산.
        # 한 전략이 이미 포지션을 잡아 availableBalance가 줄어도 다른 전략 사이징이 흔들리지 않게 함.
        if notional_ratio is not None:
            equity = await self.get_total_equity()
            margin   = equity * float(notional_ratio)
        else:
            margin   = balance * BALANCE_RATIO  # 기존 동작 (하위 호환)
        notional = margin * lev             # 레버리지 적용 명목가치
```

- [ ] **Step 3: 문법 확인**

Run: `cd /Users/home/Developer/T/btc_forwardtest && python -c "import ast; ast.parse(open('src/common/binance_executor.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/common/binance_executor.py
git commit -m "feat(binance_executor): open_position notional_ratio 사이징 옵션"
```

---

### Task 2: config_loader에 합체 그룹/비중 조회 헬퍼 추가

**Files:**
- Modify: `src/features/strategy/common/config_loader.py`

- [ ] **Step 1: get_master_config 위치 확인 후 헬퍼 2개 추가**

`get_master_config()` 함수 바로 아래에 추가:

```python
def get_combine_group(strategy_id: str) -> Optional[str]:
    """전략이 속한 합체 그룹 태그. 없으면 None."""
    strat = (get_master_config() or {}).get(strategy_id, {})
    val = strat.get("combine_group")
    return str(val) if val else None


def get_notional_ratio(strategy_id: str) -> Optional[float]:
    """전략의 명목 사이징 비율. 미설정 시 None."""
    strat = (get_master_config() or {}).get(strategy_id, {})
    val = strat.get("notional_ratio")
    return float(val) if val is not None else None
```

`Optional`이 import 안 돼 있으면 파일 상단 typing import에 추가.

- [ ] **Step 2: 문법 확인**

Run: `cd /Users/home/Developer/T/btc_forwardtest && python -c "import ast; ast.parse(open('src/features/strategy/common/config_loader.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/features/strategy/common/config_loader.py
git commit -m "feat(config_loader): combine_group/notional_ratio 조회 헬퍼"
```

---

### Task 3: Coordinator — 순수 사이징 로직 + standalone 검증

**Files:**
- Create: `src/features/strategy/spc_oiaccel_combine/__init__.py`
- Create: `src/features/strategy/spc_oiaccel_combine/coordinator.py`
- Create: `src/features/strategy/spc_oiaccel_combine/_verify_sizing.py` (standalone 검증 스크립트)

- [ ] **Step 1: 검증 스크립트 먼저 작성 (순수 로직)**

`_verify_sizing.py`:

```python
"""순수 사이징/합산 로직 검증 — pytest 불필요, 직접 실행.
Run: python -m features.strategy.spc_oiaccel_combine._verify_sizing
"""
from features.strategy.spc_oiaccel_combine.coordinator import (
    account_contribution_pct,
    combined_equity_mdd,
)


def main() -> None:
    # 1) 계좌 기여% = pnl_pct × notional_ratio
    assert account_contribution_pct(4.40, 0.5) == 2.2, "oi TP 기여"
    assert account_contribution_pct(-1.60, 0.5) == -0.8, "oi SL 기여"
    assert account_contribution_pct(-3.0, 0.75) == -2.25, "spc SL 기여"

    # 2) 합산 equity/MDD — 두 손실 연속이면 복리로 누적
    trades = [
        {"pnl_pct": -1.60, "notional_ratio": 0.5},   # -0.8%
        {"pnl_pct": -3.0,  "notional_ratio": 0.75},  # -2.25%
        {"pnl_pct": 4.40,  "notional_ratio": 0.5},   # +2.2%
    ]
    comp, mdd = combined_equity_mdd(trades, fee_pct=0.0)
    # eq: 100 → 99.2 → 96.968 → 99.10...; peak 100 → mdd ≈ 3.03%
    assert abs(mdd - 3.03) < 0.05, f"MDD 기대 ~3.03, 실제 {mdd}"
    assert comp < 0, f"3트레이드 누적 음수 기대, 실제 {comp}"

    print("sizing verify: ALL PASS")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행해서 실패 확인 (coordinator 아직 없음)**

Run: `cd /Users/home/Developer/T/btc_forwardtest/src && python -m features.strategy.spc_oiaccel_combine._verify_sizing`
Expected: `ModuleNotFoundError` 또는 `ImportError` (coordinator 미구현)

- [ ] **Step 3: __init__.py 생성**

`__init__.py`: 빈 파일 (`# spc_oiaccel_combine package` 한 줄)

- [ ] **Step 4: coordinator.py 구현 — 순수 함수부터**

`coordinator.py`:

```python
"""spc_oiaccel_combine — 합체 실행 코디네이터.

기존 엔진 이벤트(entry/close)를 받아 전략별 notional_ratio로 단일 계좌 실주문하고
strategy='spc_oiaccel_combine' 태그로 체결을 기록한다. 신호 로직 없음.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

COMBINE_TAG = "spc_oiaccel_combine"
BINANCE_FEE_NOTIONAL_PCT = 0.065  # round-trip, backtest와 동일


def account_contribution_pct(pnl_pct: float, notional_ratio: float) -> float:
    """포지션 가격변동% → 계좌 기여%. (명목이 계좌의 notional_ratio배라서)"""
    return round(pnl_pct * notional_ratio, 4)


def combined_equity_mdd(trades: List[Dict[str, Any]], fee_pct: float = BINANCE_FEE_NOTIONAL_PCT):
    """체결 기록(시간순)으로 계좌 합산 compound%와 MDD% 계산.
    각 trade: {"pnl_pct": float, "notional_ratio": float}
    """
    eq = 100.0
    peak = 100.0
    mdd = 0.0
    for t in trades:
        contrib = account_contribution_pct(t["pnl_pct"], t["notional_ratio"]) - fee_pct * t["notional_ratio"]
        eq *= (1 + contrib / 100.0)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100.0)
    return round(eq - 100.0, 4), round(mdd, 4)
```

- [ ] **Step 5: 검증 스크립트 통과 확인**

Run: `cd /Users/home/Developer/T/btc_forwardtest/src && python -m features.strategy.spc_oiaccel_combine._verify_sizing`
Expected: `sizing verify: ALL PASS`

- [ ] **Step 6: Commit**

```bash
git add src/features/strategy/spc_oiaccel_combine/
git commit -m "feat(combine): coordinator 순수 사이징/합산 로직 + 검증"
```

---

### Task 4: Coordinator — 실행/기록 메서드 (DB + binance)

**Files:**
- Modify: `src/features/strategy/spc_oiaccel_combine/coordinator.py`

- [ ] **Step 1: handle() 메서드 추가 — events 받아 주문 + 기록**

`coordinator.py` 하단에 추가:

```python
async def handle(
    strategy_tag: str,
    events: List[Dict[str, Any]],
    symbol: str,
    current_price: Optional[float],
    notional_ratio: float,
    leverage: int,
) -> None:
    """개별 엔진 events를 받아 단일 계좌에 비중 주문 + 합산 기록.
    개별 엔진의 페이퍼 기록과 별개로 strategy='spc_oiaccel_combine' 행을 남긴다.
    """
    from common.binance_executor import get_executor

    try:
        ex = get_executor()
    except Exception as e:
        print(f"[{COMBINE_TAG}] executor 없음: {e}")
        ex = None

    for ev in events:
        kind = ev.get("event")
        if kind == "entry":
            pos = ev.get("position") or {}
            side = pos.get("side")
            if not side or not current_price:
                continue
            fill_price = current_price
            if ex:
                try:
                    result = await ex.open_position(
                        symbol, side, current_price,
                        leverage=leverage, notional_ratio=notional_ratio,
                    )
                    fp = float((result or {}).get("avgPrice") or 0)
                    if fp > 0:
                        fill_price = fp
                    tp, sl = pos.get("tp"), pos.get("sl")
                    if tp or sl:
                        await ex.place_tp_sl(symbol, side, tp=tp, sl=sl)
                except Exception as e:
                    print(f"[{COMBINE_TAG}] 진입 오류: {e}")
            _persist_open(symbol, side, fill_price, pos, strategy_tag, notional_ratio)

        elif kind == "close":
            trade = ev.get("trade") or {}
            side = trade.get("side")
            if not side:
                continue
            exit_price = trade.get("exit_price") or current_price
            if ex:
                try:
                    result = await ex.close_position(symbol, side)
                    fp = float((result or {}).get("avgPrice") or 0)
                    if fp > 0:
                        exit_price = fp
                except Exception as e:
                    print(f"[{COMBINE_TAG}] 청산 오류: {e}")
            _persist_close(symbol, side, exit_price, trade, notional_ratio)
```

- [ ] **Step 2: _persist_open / _persist_close 추가 (ForwardTrade 직접 기록)**

`coordinator.py` 하단에 추가:

```python
def _db():
    from db.session import get_session
    return get_session()


def _persist_open(symbol, side, entry_price, pos, strategy_tag, notional_ratio) -> Optional[int]:
    import json
    from db.models import ForwardTrade
    session = _db()
    try:
        now = datetime.utcnow()
        meta = {"notional_ratio": notional_ratio, "src_strategy": strategy_tag}
        row = ForwardTrade(
            symbol=symbol, side=side, entry_price=entry_price, opened_at=now,
            entry_state=str(pos.get("reasons", [])),
            trigger_tfs=pos.get("entry_tf", ""), confidence=pos.get("confidence", 0),
            direction_detail=f"{COMBINE_TAG}<-{strategy_tag}",
            sl_price=pos.get("sl"), tp1_price=pos.get("tp"),
            position_meta=json.dumps(meta),
            status="open", entry_source="combine", strategy=COMBINE_TAG,
        )
        session.add(row); session.commit(); session.refresh(row)
        return row.id
    except Exception as e:
        session.rollback(); print(f"[{COMBINE_TAG}] persist open err: {e}"); return None
    finally:
        session.close()


def _persist_close(symbol, side, exit_price, trade, notional_ratio) -> None:
    from db.models import ForwardTrade
    session = _db()
    try:
        row = (session.query(ForwardTrade)
               .filter(ForwardTrade.strategy == COMBINE_TAG,
                       ForwardTrade.symbol == symbol,
                       ForwardTrade.status == "open")
               .order_by(ForwardTrade.opened_at.desc()).first())
        if not row:
            return
        entry = row.entry_price or 0
        pnl = ((exit_price - entry) / entry * 100 if side == "long"
               else (entry - exit_price) / entry * 100) if entry > 0 else 0
        now = datetime.utcnow()
        row.status = trade.get("exit_reason", "closed")
        row.exit_price = exit_price
        row.pnl_pct = round(pnl, 4)
        row.pnl_pct_net = round(pnl - BINANCE_FEE_NOTIONAL_PCT, 4)
        row.closed_at = now
        row.duration_min = round((now - row.opened_at).total_seconds() / 60.0, 1)
        row.close_note = trade.get("exit_reason", "")
        session.commit()
    except Exception as e:
        session.rollback(); print(f"[{COMBINE_TAG}] persist close err: {e}")
    finally:
        session.close()
```

- [ ] **Step 3: 문법 확인 + 기존 검증 재실행**

Run: `cd /Users/home/Developer/T/btc_forwardtest/src && python -c "import ast; ast.parse(open('features/strategy/spc_oiaccel_combine/coordinator.py').read()); print('OK')" && python -m features.strategy.spc_oiaccel_combine._verify_sizing`
Expected: `OK` 그리고 `sizing verify: ALL PASS`

- [ ] **Step 4: Commit**

```bash
git add src/features/strategy/spc_oiaccel_combine/coordinator.py
git commit -m "feat(combine): handle() 단일계좌 주문 + 합산 ForwardTrade 기록"
```

---

### Task 5: _execute_verify_notify에 합체 라우팅 추가

**Files:**
- Modify: `src/features/strategy/common/base_realtime_feed.py` (_execute_verify_notify, 292행~)

- [ ] **Step 1: 함수 시작부에서 합체 그룹 여부 판단**

`_execute_verify_notify` 안에서 `_binance_leverage = ...` 줄(약 306행) 아래에 추가:

```python
    from features.strategy.common.config_loader import get_combine_group, get_notional_ratio
    _combine_group = get_combine_group(strategy_key)
    _notional_ratio = get_notional_ratio(strategy_key)
    if _combine_group:
        try:
            from features.strategy.spc_oiaccel_combine.coordinator import handle as _combine_handle
            await _combine_handle(
                strategy_key, events, symbol, current_price,
                notional_ratio=_notional_ratio if _notional_ratio is not None else 1.0,
                leverage=_binance_leverage,
            )
        except Exception as e:
            print(f"[{strategy_key}] 합체 핸들 오류: {e}")
```

> 합체 그룹 멤버는 `binance_live: false`라 아래 기존 개별 binance 주문 블록은 자동으로 스킵된다(executor None). 알림·페이퍼 기록은 그대로 흐른다. 합체 핸들은 추가 실행일 뿐 기존 흐름을 막지 않는다.

- [ ] **Step 2: 문법 확인**

Run: `cd /Users/home/Developer/T/btc_forwardtest && python -c "import ast; ast.parse(open('src/features/strategy/common/base_realtime_feed.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/features/strategy/common/base_realtime_feed.py
git commit -m "feat(combine): _execute_verify_notify 합체 그룹 라우팅"
```

---

### Task 6: strategies_master.yaml 설정 + 합체 모듈 config

**Files:**
- Modify: `src/features/strategy/common/strategies_master.yaml`
- Create: `src/features/strategy/spc_oiaccel_combine/config.yaml`

- [ ] **Step 1: 개별 두 전략에 combine_group + notional_ratio 추가, binance_live false**

`strategies_master.yaml`의 `spot_perp_cvd` 블록에서:
- `binance_live: true` → `binance_live: false`
- 아래 두 줄 추가 (binance_leverage 다음):
```yaml
  notional_ratio: 0.75
  combine_group: spc_oiaccel_combine
```

`oi_accel_breakout_v2` 블록에서:
- `binance_live: false` (이미 false) 유지
- 아래 두 줄 추가:
```yaml
  notional_ratio: 0.5
  combine_group: spc_oiaccel_combine
```

- [ ] **Step 2: 합체 모듈 config.yaml 생성 (메타 기록용)**

`src/features/strategy/spc_oiaccel_combine/config.yaml`:

```yaml
strategy_id: spc_oiaccel_combine
strategy_name: SPC + OI-Accel Combine (단일계좌 합산운용)
members:
  - strategy: oi_accel_breakout_v2
    symbol: BTCUSDT
    notional_ratio: 0.5
  - strategy: spot_perp_cvd
    symbol: ETHUSDT
    notional_ratio: 0.75
# 합산 백테스트 기준: MDD 8.4%, +173%/2yr (fee 0.065%)
# 실행: 개별 엔진 이벤트를 coordinator.handle() 이 단일 계좌로 사이징 주문
```

- [ ] **Step 3: YAML 파싱 확인**

Run: `cd /Users/home/Developer/T/btc_forwardtest && python -c "import yaml; yaml.safe_load(open('src/features/strategy/common/strategies_master.yaml')); yaml.safe_load(open('src/features/strategy/spc_oiaccel_combine/config.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: 헬퍼가 값을 제대로 읽는지 확인**

Run: `cd /Users/home/Developer/T/btc_forwardtest/src && python -c "from features.strategy.common.config_loader import get_combine_group, get_notional_ratio; print(get_combine_group('spot_perp_cvd'), get_notional_ratio('spot_perp_cvd'), get_combine_group('oi_accel_breakout_v2'), get_notional_ratio('oi_accel_breakout_v2'))"`
Expected: `spc_oiaccel_combine 0.75 spc_oiaccel_combine 0.5`

- [ ] **Step 5: Commit**

```bash
git add src/features/strategy/common/strategies_master.yaml src/features/strategy/spc_oiaccel_combine/config.yaml
git commit -m "feat(combine): strategies_master 합체 그룹 설정 + 모듈 config"
```

---

### Task 7: 합산 stats 조회 함수 + 최종 통합 확인

**Files:**
- Modify: `src/features/strategy/spc_oiaccel_combine/coordinator.py`

- [ ] **Step 1: get_combined_stats 추가 — DB 합산 기록으로 equity/MDD**

`coordinator.py` 하단에 추가:

```python
def get_combined_stats() -> Dict[str, Any]:
    """strategy='spc_oiaccel_combine' 체결 기록으로 합산 equity/MDD/일일손실."""
    from db.models import ForwardTrade
    session = _db()
    try:
        rows = (session.query(ForwardTrade)
                .filter(ForwardTrade.strategy == COMBINE_TAG,
                        ForwardTrade.status != "open")
                .order_by(ForwardTrade.opened_at.asc()).all())
        trades = []
        import json
        for r in rows:
            nr = 1.0
            if r.position_meta:
                try:
                    nr = float(json.loads(r.position_meta).get("notional_ratio", 1.0))
                except Exception:
                    pass
            trades.append({"pnl_pct": r.pnl_pct or 0.0, "notional_ratio": nr})
        comp, mdd = combined_equity_mdd(trades)
        wins = sum(1 for t in trades if account_contribution_pct(t["pnl_pct"], t["notional_ratio"]) > 0)
        n = len(trades)
        return {
            "strategy": COMBINE_TAG, "closed_trades": n,
            "win_rate": round(wins / n * 100, 1) if n else 0,
            "compound_pct": comp, "mdd_pct": mdd,
        }
    finally:
        session.close()
```

- [ ] **Step 2: import 검증 + 전체 모듈 로드 확인**

Run: `cd /Users/home/Developer/T/btc_forwardtest/src && python -c "from features.strategy.spc_oiaccel_combine.coordinator import handle, get_combined_stats, combined_equity_mdd, account_contribution_pct; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 3: 사이징 검증 스크립트 최종 재실행**

Run: `cd /Users/home/Developer/T/btc_forwardtest/src && python -m features.strategy.spc_oiaccel_combine._verify_sizing`
Expected: `sizing verify: ALL PASS`

- [ ] **Step 4: Commit**

```bash
git add src/features/strategy/spc_oiaccel_combine/coordinator.py
git commit -m "feat(combine): get_combined_stats 합산 성적 조회"
```

---

## 완료 후 수동 확인 (서버 기동 시)

1. 서버 기동 후 spc/oi 신호 발생 시 로그에 `[spot_perp_cvd] 합체 핸들` 흐름과 `[spc_oiaccel_combine]` 기록이 찍히는지
2. `get_combined_stats()` 가 두 전략 체결을 비중 반영해 합산 MDD를 반환하는지
3. 개별 전략 페이퍼 ForwardTrade(strategy='spot_perp_cvd' / 'oi_accel_breakout_v2')도 그대로 쌓이는지 (개별 검증 유지)

## 비범위 (YAGNI)
- 합체 전용 대시보드 UI (API 함수만 제공, UI 추후)
- 동적 사이징/켈리 (고정 0.5/0.75)
- 3번째 전략 슬롯
