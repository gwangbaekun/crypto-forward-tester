# 전략 현황

> 규칙은 `STRATEGY_RULES.md`. 이 문서는 **현재 상태**만 적는다.
> 전략을 만들거나 고치면 여기 한 줄을 갱신한다.
>
> 단일 출처: `src/features/strategy/common/strategies_master.yaml`
> 기준일: 2026-08-19

---

## A. 가동 중 (`enabled: true`)

| 전략 | 심볼 | 실거래 | 비고 |
|---|---|---|---|
| `spot_perp_cvd` | ETHUSDT 1h | **Binance live, lev 9** | 유일한 실거래. 텔레그램·디스코드 알림 ON |
| `deribit_gex_reversal` | BTCUSDT 1h | 없음 | **엣지 측정 전용.** 로드맵 §1 |
| `us_options_gex_pin` | SPY 1d | 없음 | **엣지 측정 전용.** 로드맵 §2 — 만기 필터 결함 있음 |
| `us_options_gamma_wall` | SPY 1d | 없음 | **원장 전용.** 벽 기록 → 다음 세션 존중 여부 채점. 로드맵 §5 |

---

## B. 정지 (`enabled: false`) — tick 루프 미포함

| 전략 | 심볼 / TF | 상태 |
|---|---|---|
| `cvd_explosion` | BTCUSDT 15m/4h | 코드 완비 |
| `eth_cvd_explosion` | ETHUSDT 1h/4h | cTrader(FTMO live) 설정 존재, `ctrader_live: false` |
| `eth_cvd_explosion_v2` | ETHUSDT 15m/1h | cTrader demo/live 계정 양쪽 설정, `ctrader_mode` 로 전환 |
| `oi_cvd_surge` | BTCUSDT 1h | 코드 완비 |
| `btc_liq_sweep_reversal` | BTCUSDT 1h | 코드 완비 |
| `oi_accel_breakout_v2` | BTCUSDT 15m | 백테 PF 는 선택편향 의심 — `../../DOCS/전략_검증기록.md` §3 |
| `atr_breakout` | 5m~4h | 초기 스캐폴드 |

---

## C. 스케줄러형 — tick 루프 밖에서 돈다

`enabled: false` 여도 **자체 스케줄러로 실행된다.** yaml 만 보고 "안 돈다"고 판단하지 마라.

| 전략 | 실행 | 원장 |
|---|---|---|
| `drawdown_signal` | `_drawdown_scheduler` 30분 폴링 + catch-up | `drawdown_ledger` 단일 블롭 (DB 정본) |
| `value_scan` | 기동 후 + 10분마다 catch-up (하루 1회, 시장별) | `value_scan_*` 테이블 (DB 정본) |

→ `LEDGER.md` §6, §7

---

## D. yaml 밖 — 별도 러너

| 모듈 | 상태 |
|---|---|
| `polymarket/fade` | 워치리스트 자동 스크리너 가동. 주문은 `POLYMARKET_LIVE` 별도 |
| `polymarket/logic_arb` | `enabled: false` (scan-only). 실측 차익 0건 |
| `btc_oi_accel_breakout` | yaml 미등록 — **사이트 인덱스에 안 뜬다.** 등록 여부 결정 필요 |

→ `POLYMARKET.md`

---

## E. 조합 스위치

`spc_oiaccel_combine` 은 tick 블록이 없어 `strategy_loop` 가 자동 제외한다.
`enabled: true` → 멤버 이벤트를 받아 주문 fan-out, `false` → 주문 스킵.
`members` 가 어떤 전략이 묶였는지의 단일 출처이고, venue 별 사이징은 `config.yaml` 의 `venues`.
현재 `members: []`.

---

## 미해결

- `us_options_gex_pin` 의 `max_days_to_expiry: 2` 는 SPY 상시 만기 때문에 사실상 매일 참 →
  §2 와 §5 의 성과가 서로를 오염시킨다 (`../../DOCS/전략_로드맵.md` §1).
- `polymarket/fade` 는 `auto_include` 로 워치리스트가 매시간 바뀐다 → 편입 시점 코호트 필요.
- `CLOBWSClient._connect()` 는 여전히 단일 연결 — 500토큰 초과 시 조용히 죽는다.
