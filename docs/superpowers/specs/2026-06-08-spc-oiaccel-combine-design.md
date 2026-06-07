# spc_oiaccel_combine — 합체 자본운용 Forward Test 설계 (v2 단순화)

날짜: 2026-06-08 (v2 개정)
대상: btc_forwardtest (backtest 수정 없음)

## 목적

두 검증 전략(`spot_perp_cvd`, `oi_accel_breakout_v2`)을 거래소 계좌에서 합산 운용한다.
각 전략은 **독립적으로 신호·포지션을 운영**하고(기존 엔진 그대로), combine은 그 이벤트를 받아
**venue별로 다른 사이징(notional_ratio)·레버리지로 주문만 fan-out**한다.

## 핵심 통찰 (v2의 단순화)

- 두 전략의 `pnl_pct`(가격변동%)는 사이징/레버리지와 무관하게 동일하다 — 같은 신호·같은 진입/청산가.
- combine이 바꾸는 건 **명목비중(notional_ratio)과 레버리지뿐**이다.
- 따라서 **combine은 자체 DB 기록이 필요 없다.** 계좌 합산 손익/MDD = Σ(개별 pnl × notional_ratio)로,
  기존 개별 forward_test 기록을 조회·합산하면 된다.
- v1에서 만든 combine 전용 ForwardTrade 기록(`_persist_open/close`, `get_combined_stats`,
  `spc_oiaccel_combine` 태그)은 전부 **제거**한다.

## MDD는 notional_ratio가 결정 (레버리지 아님)

레버리지는 증거금 효율·청산가 거리만 좌우. MDD는 명목비중(notional_ratio)에 비례.
백테스트 기준선: oi 0.5x / spc 0.75x → 합산 MDD 8.36%, 최악일 -3.21% (fee 0.065%, 2yr).

| venue | leverage | oi notional | spc notional | 목표 MDD | 백테스트 MDD |
|---|---|---|---|---|---|
| binance | 9 | 2.0 | 3.0 | ~30% | 30.1% (최악일 -12.8%) |
| ctrader (FTMO) | 3 | 0.42 | 0.62 | ~7% | ~7% (1차 enabled:false) |

binance 명목 합 5.0x ÷ lev 9 = 증거금 56% (둘 다 열려도 여유 44%, 청산 안전).

## config 구조

### strategies_master.yaml (개별 블록 — 라우팅 플래그만)
```yaml
spot_perp_cvd:
  binance_live: false            # 개별 실주문 OFF
  combine_group: spc_oiaccel_combine   # combine 대상 표시 (라우팅용)
  # notional_ratio 없음 — venue마다 다르므로 combine config로 이동

oi_accel_breakout_v2:
  binance_live: false
  combine_group: spc_oiaccel_combine
```

### spc_oiaccel_combine/config.yaml (venue × member 사이징)
```yaml
strategy_id: spc_oiaccel_combine
venues:
  binance:
    enabled: true
    leverage: 9
    members:
      oi_accel_breakout_v2: { notional_ratio: 2.0 }
      spot_perp_cvd:        { notional_ratio: 3.0 }
  ctrader:
    enabled: false
    leverage: 3
    account_id: 47198415
    members:
      oi_accel_breakout_v2: { notional_ratio: 0.42 }
      spot_perp_cvd:        { notional_ratio: 0.62 }
```

## 컴포넌트

### 1. `coordinator.py` (대폭 단순화, ~50줄)
- `handle(strategy_tag, events, symbol, current_price)`:
  combine config를 읽어 `enabled` venue를 순회. 각 venue에서 해당 member의 `notional_ratio`가
  있으면 그 venue executor로 `open_position(notional_ratio=, leverage=)` / `close_position` 호출.
- **DB 기록 없음.** v1의 `_persist_open/close`, `get_combined_stats`, 순수 합산 함수 제거.
- venue→executor: binance=`common.binance_executor.get_executor`, ctrader=`common.ctrader_executor.get_executor`.

### 2. `binance_executor.open_position(notional_ratio, leverage)`
- v1에서 이미 추가됨. 변경 없음.

### 3. `_execute_verify_notify` (base_realtime_feed)
- combine_group 멤버이면 `coordinator.handle(strategy_key, events, symbol, current_price)` 호출.
- v1의 notional_ratio/leverage 인자 전달 제거 — coordinator가 config에서 venue별로 직접 결정.

### 4. 대시보드 (`router.py` + `static/dashboard.html`)
- combine 전용 기록을 읽지 않는다. 대신 **개별 forward_test/stats를 symbol 맞춰 호출**해 합산.
- `/members`: combine config의 venue×member 사이징 + 각 멤버의 개별 stats(symbol 명시 조회) 반환.
- 합산 손익 = Σ(멤버 개별손익 × 해당 venue notional_ratio). MDD는 개별 trades 시계열 합산.
- 기존 공용 엔드포인트는 수정하지 않음(호출 시 `?symbol=` 명시로 BTCUSDT 기본값 버그 회피).

## 데이터 흐름
```
spc tick → events → _execute_verify_notify("spot_perp_cvd", events)
  ├─ 개별 engine 페이퍼 기록 (기존, 변경 없음 — 개별 검증 + 합산 소스)
  └─ combine_group이면 → coordinator.handle("spot_perp_cvd", events, "ETHUSDT", price)
        for venue in enabled:                 # binance(현재) [+ ctrader 추후]
          nr = config.venues[venue].members["spot_perp_cvd"].notional_ratio
          executor(venue).open/close(notional_ratio=nr, leverage=venue.leverage)
```

## 비범위 (YAGNI)
- ctrader 실거래 (config는 두되 enabled:false — 다음 사이클에서 동적 lot 사이징 추가)
- 동적/켈리 사이징 (config 고정값)
- 3번째 전략 슬롯

## v1 → v2 제거 목록 (정리 대상)
- coordinator: `_persist_open`, `_persist_close`, `get_combined_stats`, `account_contribution_pct`, `combined_equity_mdd`, `_db`
- router `/stats` (combine 기록 기반) → 개별 합산 기반으로 교체
- strategies_master 개별 블록의 `notional_ratio`
