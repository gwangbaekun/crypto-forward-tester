# Polymarket (`src/features/strategy/polymarket/`)

> 전략 의도·파라미터 대조는 `fade/README.md` 가 정본.
> 이 문서는 **인프라 함정과 조용히 죽는 실패**만 적는다.

---

## 1. 실거래 스위치

주문은 `POLYMARKET_LIVE=true` **AND** 유효 hex PK + 자격증명이 전부 있어야 나간다
(`_data/executor.py`). 하나라도 없으면 `status: "skipped"` 로 **조용히 시뮬 모드**가 된다.

→ 배포 환경변수 이름 한 글자가 틀려도 에러가 안 난다. 라이브 전환 후에는
반드시 실제 체결을 확인한다. 로그 문자열은 `POLYMARKET_LIVE!=true → 주문 skip`.

---

## 2. fade 워치리스트 자동 스크리너 (`fade/screener.py`)

종목을 **이름/키워드가 아니라 가격 거동**으로 뽑는다. 이전엔 `watchlist.yaml` 수동 90개 +
이벤트 title 키워드 매칭이라 달이 바뀌면 신규 마켓을 전혀 못 잡았다.

### Gamma offset 상한을 슬라이스 분할로 우회

- Gamma `/markets` 의 `offset` 은 **2000 하드 상한** (초과 시 422). 단일 쿼리로는
  볼륨 상위 ~2,100개까지만 보인다.
- **`/markets/keyset` 은 쓰지 마라.** 커서가 사실상 끝없이 돌아 15분+ 무한 페이징한다
  (활성 마켓 36,700개+ — 스포츠·사다리가 대부분).
- 서버측 필터 `volume_num_min` / `end_date_min` / `end_date_max` 는 **실제로 동작한다**
  (무시되는 파라미터가 아님 — 실측 확인). 종료월 단위로 쪼개면 각 슬라이스가 상한 아래로
  내려가 **전수 열거**가 된다 → `client.screen_band_markets()`
- 실측 (vol≥$10k, 18개월): 마켓 4,609 · 5~15% 밴드 936~941 · 요청 58건 · 26초.
  **누적볼륨 상위만 보면 밴드 후보의 60%를 놓친다** (388 vs 941).
  "누적볼륨 상위"와 "지금 5~15%에 있는 마켓"은 다른 집합이다.
- `screen_band_markets` 는 상한에 걸린 슬라이스 라벨을 함께 반환한다 → **조용히 넘기지 마라.**
  vol≥$1k 까지 낮추면 일부 월이 상한에 걸린다 (주간 분할 필요).

### 히스토리는 병목이 아니다

- CLOB `/prices-history`: 동시 80 × 400건까지 **429 0건** (108~279 req/s).
  레이트리밋은 오히려 Gamma 쪽 (초당 ~40건에서 Cloudflare 429) → 페이지당 50ms 슬립.
- `interval=max` 는 **fidelity 무관 31일 캡**. 그 이상 과거는
  `fetch_curve_full(start_ts, end_ts)` 의 14일 청킹 경로.
- 스크리닝 자체엔 히스토리가 0건 든다 — Gamma 응답의 `outcomePrices` 로 밴드 판정.
  후보를 936개로 좁힌 뒤에만 곡선을 받는다 (`fetch_curve_batch`, 동시 20 → 59 req/s).

### WS 64KB 프레임 한계 — 로그에 아무것도 안 남는 실패 🔴

- subscribe 프레임은 토큰당 81B. **800토큰(63.3KB)부터 서버가 에러도 close 도 없이
  메시지만 흘리며 커버리지가 0.9%로 붕괴**한다.
- 500토큰/연결은 100% 커버 확인 → `ws_shard_size: 500` 으로 `_ws_loop` 가 연결을 분할한다.
- `_ws_generation` 카운터로 전 샤드가 일제히 재구독. **제거분도 세대를 올린다**
  (이전엔 추가만 감지해서 빠진 종목을 계속 구독했다).
- **`CLOBWSClient._connect()` (공유 클라이언트)는 아직 단일 연결이다.**
  다른 전략이 500토큰을 넘기면 같은 방식으로 조용히 죽는다.

### 수동/자동 구분 — 마이그레이션 없이

`watchlist.yaml` 에 있는 condition_id = **수동** (스크리너 불가침).
yaml 에 없는 DB 행 = 스크리너가 넣은 **자동** (편입·해제 대상).

자동분을 yaml 에 안 쓰는 이유: 매시간 수백 행을 커밋하면 yaml 이 수동 원장의 의미를 잃는다.
재배포로 사라져도 다음 스캔이 결정론적으로 복원한다.

상태값: `included` / `excluded`(유저가 직접 뺌 — 자동 편입이 절대 되살리지 않음) /
`dropped`(밴드 이탈로 자동 해제 — 밴드 복귀 시 자동 부활)

### 안전장치 (검증 완료)

- **열린 포지션 보유 마켓은 밴드를 이탈해도 해제하지 않는다.** 해제하면 `_yes_map` 에서
  빠져 청산 로직이 그 포지션을 영영 못 본다 (고아 포지션).
- 시드는 **병렬**이어야 한다. 순차면 270종목에 54초, 900종목이면 3분간 sweep 루프가 막혀
  타임아웃 청산이 통째로 밀린다. → 실측 270종목 3.1초.
- 스크리너 예외는 sweep 루프에서 삼킨다 — 스크리너가 죽으면 워치리스트가 낡을 뿐이지만
  엔진이 죽으면 청산까지 멈춘다.
- `screener.spike_*` 는 라이브 진입 조건(`lookback_s`/`spike_rel`/`spike_abs`)과 **같은 값**
  이어야 한다. 다르면 "편입은 됐는데 진입은 영영 안 되는" 종목이 쌓인다.

### 미해결 (의도적)

`auto_include: true` 라 워치리스트가 매시간 바뀐다 → 원장에 **생존자 편향**이 들어갈 여지.
편입 시점을 원장에 남기지 않으면 "사후에 좋아 보인 종목만" 집계된다.
`drawdown_signal` 의 `backfilled` 플래그와 같은 코호트 분리가 필요하다 (`LEDGER.md` §2).

---

## 3. logic_arb — 조합 차익 (`polymarket/logic_arb/`)

예측이 아니다. 사다리 시장의 **논리 관계(무위험 구조)** 만 거래한다.

- **포함관계 차익**: GT/LT 사다리에서 `ask(YES_sup) + ask(NO_sub) < 1` → 최소 페이오프 1 확정.
  `lo == hi` 면 `pair_hedge`(YES+NO<1)와 동일 — 그 시장 간 일반화다.
- **🔴 basis 게이트 (핵심)**: Polymarket BTC 사다리는 `reach/dip to`(TOUCH) 문구이고,
  `above/below`(TERMINAL)와 **해상도 규칙이 다르다.** 절대 교차 차익하지 않는다.
  `parse.py` 가 방향 + basis 를 산술 파싱한다 — **제목 유사성은 쓰지 않는다.**
- 기계적 검증: 동일 end_ts(±tol) 그룹핑 → ws best_ask 프리스크린 → REST `/book` 실 ask+size
  재확인 → fee_buffer.
- kill-criteria 계측: 위반 지속시간 로깅 (<60s 소멸 = 봇 선점 신호).
- 실측 (2026-07-21): EOY2026 reach 20단 등 7개 사다리, 가격 단조 → **현 차익 0건.**
  효율적 시장이고 논문과 일치한다.
- 기본 `enabled: false` (scan-only). 단독 테스터
  `scripts/polymarket_logic_arb_scan.py [--groups]`
