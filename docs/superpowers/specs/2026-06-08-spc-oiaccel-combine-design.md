# spc_oiaccel_combine — 합체 자본운용 Forward Test 설계

날짜: 2026-06-08
대상: btc_forwardtest (backtest 수정 없음)

## 목적

두 개의 검증된 전략(`spot_perp_cvd`, `oi_accel_breakout_v2`)을 **단일 바이낸스 선물 계좌**에서 각자의 명목 비중으로 합산 운용한다. 신호·청산 로직은 기존 엔진을 100% 재사용하고, 합체 모듈은 **이벤트를 받아 비중을 입혀 실행하는 얇은 레이어**다.

### 사이징 (backtest 검증값 기준)
- `oi_accel_breakout_v2`: 명목 0.5x (SL 1.5% → 계좌 0.75%/trade)
- `spot_perp_cvd`: 명목 0.75x (SL 3.0% → 계좌 2.25%/trade)
- 합산 백테스트: MDD 8.4%, +173%/2yr, 최악일 -3.21% (fee 0.065%, 2년)

## 핵심 통찰

합체 모듈은 **신호를 모른다.** 기존 두 엔진이 `engine.tick()`에서 entry/close 이벤트를 뱉으면, 합체는 그 이벤트를 받아 `notional_ratio`로 단일 계좌에 실행할 뿐. 신호·청산 로직 0줄 복제, 폴링 없음(이벤트 직결 → 지연 0).

## 컴포넌트

### 1. `spc_oiaccel_combine/coordinator.py` (새 파일, ~70줄)
- `_execute_verify_notify`로부터 `(strategy_tag, events)` 수신
- 전략별 `notional_ratio` 조회 → `binance_executor.open_position(notional_ratio=...)` 호출
- 체결 결과를 `strategy="spc_oiaccel_combine"` 태그로 `ForwardTrade` 기록 (합산 성적용)
- `notional_ratio`를 `position_meta`에 저장 → 합산 MDD 계산 시 비중 반영
- `get_combined_stats()`: 기존 `BaseForwardTest.get_stats` 패턴 재사용, 계좌 기여 손익 = `pnl_pct × notional_ratio`

### 2. `binance_executor.open_position(notional_ratio: float | None)`
- 현재: `margin = balance * BALANCE_RATIO` (0.95 고정)
- 변경: `notional_ratio` 전달 시 `margin = total_equity * notional_ratio`
  - `total_equity` = 포지션 마진 포함 총자산 (`get_total_equity`, fapi/v2/account `totalMarginBalance`)
  - 이유: 한 전략이 이미 포지션을 잡아 availableBalance가 줄어도, 다른 전략 사이징이 흔들리지 않게 총자산 기준으로 명목 계산
- `notional_ratio=None`이면 기존 `BALANCE_RATIO` 동작 유지 (하위 호환)

### 3. `base_realtime_feed._execute_verify_notify` (분기 몇 줄)
- `strategy_key`가 합체 그룹 멤버이면: 개별 binance 주문을 건너뛰고 `coordinator.handle(strategy_tag, events, current_price)`로 위임
- 합체 그룹 멤버는 `is_binance_live_enabled`가 false라 기존 경로에서 자동으로 실주문 안 함 → 알림/페이퍼 기록은 그대로 흐름

### 4. `strategies_master.yaml`
- `spot_perp_cvd`, `oi_accel_breakout_v2`: `binance_live: false` (페이퍼 + 신호만), `notional_ratio` 필드 추가 (0.75 / 0.5)
- 합체 그룹 정의: `combine_group: spc_oiaccel_combine` 필드로 멤버 표시
- 합체 모듈 자체는 별도 tick 불필요 (이벤트 직결이라 개별 tick에 올라탐)

## 데이터 흐름

```
spc tick → events(entry/close) → _execute_verify_notify("spot_perp_cvd", events)
  ├─ 개별 engine이 페이퍼 ForwardTrade 기록 (기존, 개별 성적용 — engine.tick 내부)
  ├─ 알림 전송 (기존)
  └─ 합체 그룹이면 → Coordinator.handle("spot_perp_cvd", events, price)
        → open_position(symbol, side, notional_ratio=0.75)   # 단일 계좌 실주문
        → ForwardTrade(strategy="spc_oiaccel_combine") 기록    # 합산 성적용
```

## 합산 추적 (A 페이퍼 + B 실거래 동시 충족)

- **B (실거래)**: coordinator가 단일 계좌에 비중 주문 → 실제 바이낸스 잔고가 ground-truth equity
- **A (페이퍼/모니터링)**: coordinator 기록(`spc_oiaccel_combine` 태그)을 `get_combined_stats`로 합산 → equity·MDD·일일손실. 개별 페이퍼 기록도 그대로 남아 **개별 검증 동시 가능**
- 합산 MDD가 백테스트 8.4%와 맞으려면 계좌 기여 손익에 `notional_ratio`를 곱해 복리 계산

## 변경 범위 요약

| 파일 | 변경 |
|---|---|
| `spc_oiaccel_combine/coordinator.py` | 신규 ~70줄 |
| `spc_oiaccel_combine/config.yaml`, `__init__.py` | 신규 (얇음) |
| `common/binance_executor.py` | `open_position`에 `notional_ratio` 파라미터 |
| `common/base_realtime_feed.py` | `_execute_verify_notify` 합체 라우팅 분기 |
| `features/strategy/common/strategies_master.yaml` | 개별 `binance_live: false`, `notional_ratio`, `combine_group` |
| 개별 전략 engine | **0줄** |

## 비범위 (YAGNI)

- 동적 사이징/켈리 — 고정 0.5/0.75
- 3번째 전략 슬롯 — 추후 별도
- 격리/교차 마진 전환 — 기존 ISOLATED 유지
- 합체 전용 대시보드 UI — 1차는 `get_combined_stats` API만, UI는 추후
