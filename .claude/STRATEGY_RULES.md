# 전략 규칙 (정본)

> 전략을 만들거나 고치기 전에 끝까지 읽는다.
> 전략 규격·최적화 계약의 정본은 `backtest_quant/.claude/STRATEGY_RULES.md` 다.
> 이 문서는 **라이브 쪽 차이점**만 정의한다.

---

## 1. 트윈 아키텍처 — 무엇을 복사하고 무엇을 복사하지 않는가

backtest 와 forwardtest 는 같은 논리를 다른 실행 모델로 돌린다.
그래서 파일 이름을 일부러 똑같이 맞춰 뒀다. **이름이 같다고 내용이 같은 것은 아니다.**

| 파일 | 복사 | 이유 |
|---|---|---|
| `signal.py` | 🟢 **그대로** | 순수 함수. 통일 계약(`sweep_by_tf`, `magnets`)만 받으므로 DB 든 WS 든 상관없다 |
| `config.yaml` · `config_loader.py` | 🟢 거의 그대로 | **파라미터 이름이 같아야** 백테 대시보드 SAVE & APPLY 동기화가 된다 |
| `exit_check.py` | 🟡 개념만 이식 | 시그니처가 다르다 (§2) |
| `engine.py` / `forward_test_runner.py` | 🔴 **금지** | BT 는 과거 루프 `run()`, 라이브는 `tick()` 상태 머신 |
| `data.py` / `realtime_feed.py` | 🔴 **금지** | BT 는 DB, 라이브는 WS/REST 어댑터 |

**`signal.py` 를 고쳤으면 반대쪽도 같이 고친다.** 수동 동기화라 잊으면 조용히 갈라진다.

---

## 2. `exit_check.py` — intrabar ratchet 규칙 (라이브 전용, 가장 자주 나는 사고)

백테스트는 OHLCV 밖에 없어서 **봉 마감에서만** ratchet 을 돌린다.
라이브 exit tick 은 1초마다 WS 가격으로 발화한다.

`bar_high = bar_low = ws_price` 를 순진하게 넘기면 → 스파이크 한 번에 ratchet 이 당겨지고
→ 그 뒤 진동이 당겨진 SL 을 때린다 → **없어야 할 청산**이 난다.

규칙:

- `strategy_loop` 의 fast tick(`has_open_pos=True`)은 `state["intrabar"] = True` 를 넣는다.
- `intrabar=True` 일 때 `check_exit` 는
  1. ratchet / TP advance 루프를 **통째로 건너뛴다**
  2. **현재 보유 중인 SL** 만 `current_price` 로 직접 검사한다
  3. TP 터치는 다음 봉마감 tick 으로 미룬다 (거기선 `bar_high` 가 진짜 고가라 BT 와 같아진다)
- 엔진은 `intrabar=True` 일 때 **진입 분기도 건너뛴다.** 진입은 봉마감/pre-entry tick 에서만.

`magnet_rr` / `magnet_tp_rr` 처럼 ratchet 이 있는 전략을 새로 추가하면
`check_exit` 가 `intrabar` 를 받고 short-circuit 해야 하며, 엔진은
`state.get("intrabar", False)` 를 읽어 넘겨야 한다.

---

## 3. 폴더 구조

```
src/features/strategy/{strategy_id}/
├── __init__.py
├── config.yaml              signal / tpsl 파라미터 (BT 와 이름 동일)
├── config_loader.py
├── signal.py                BT 에서 그대로 복사
├── exit_check.py            intrabar 지원 (§2)
├── realtime_feed.py         build_state / get_state — 데이터 어댑터
├── forward_test_runner.py   BaseForwardTest 서브클래스 (또는 engine.py)
├── router.py                make_router("strategy_key", …)
└── README.md                진입·청산·TP/SL·데이터 의존성
```

`strategy_id` = 폴더명 = `strategies_master.yaml` 키 = `strategy_tag`.
**이 넷이 어긋나면 전부 어긋난다.**

일봉/스캔형 전략(`value_scan`, `drawdown_signal`, `us_options_gamma_wall`)은 tick 루프 대신
`engine / stats / router / cache` 구조를 쓴다. 새 스캔형은 `value_scan` 을 따른다.

---

## 4. 등록

`strategies_master.yaml` 에 한 블록 추가하면 끝이다. `router_registry` 가 라우터를 잡고,
사이트 인덱스에 자동으로 뜬다 (`ARCHITECTURE.md` §5). `main.py` 직접 수정은 필요 없다.

```yaml
my_strategy:
  enabled: false          # false 면 tick loop 미포함
  monitoring: false       # 대시보드 자동 polling
  symbol: "BTCUSDT"
  tick_interval: 60
  exit_tick_interval: 1.0 # 포지션 보유 시 fast tick (§2)
  timeframes: ["1h"]
  entry_tf: "1h"
  strategy_tag: my_strategy
  label: "My Strategy"    # 없으면 Title Case 로 뜬다
  emoji: "🔧"
  telegram_alerts: false
  discord_alerts: false
  binance_live: false
  tick:
    module: features.strategy.my_strategy.realtime_feed
    fn: get_state
```

---

## 5. 새 전략 시작 전 결정할 8가지

코드 이전에 답이 나와 있어야 한다. 특히 1·2 를 틀리면 그 위가 전부 무의미해진다.

| # | 항목 | 영향 |
|---|---|---|
| 1 | 종목 | `symbol` |
| 2 | 진입 TF | `timeframes`, 윈도우 의미, 어느 캐시에서 받는가 |
| 3 | 진입 조건 | `compute_signal` 구조 |
| 4 | TP/SL 방식 | signal 반환 + 청산 분기 |
| 5 | 청산 조건 | `check_exit` (+ intrabar 처리) |
| 6 | 레버리지 | PnL·수수료 |
| 7 | 실거래 여부 | `binance_live` / `ctrader_live` — 켜면 executor·sync 경로가 붙는다 |
| 8 | 새 전략인가 버전인가 | 로직 구조가 다르면 새 디렉터리, TF/파라미터만 다르면 `strategy_tag` |

신호 TF 는 **주기뿐 아니라 시각까지** 계약에 넣는다. 옵션 OI 처럼 하루 한 번 갱신되는
데이터는 받는 시각이 데이터의 의미를 바꾼다 (`../../DOCS/전략_로드맵.md` §5 데이터 계약).

---

## 6. 작업 완료 시

1. `strategies_master.yaml` 등록 확인 — 사이트 인덱스에 카드가 떴는가
2. `.claude/STRATEGIES.md` 현황 한 줄 갱신
3. 원장을 만들었으면 `LEDGER.md` 5원칙 통과 확인
4. 등록 검증은 **TestClient 로 실제 요청** (`ARCHITECTURE.md` §7)
