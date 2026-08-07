# forwardtest_quant — Project Memory

> `tradingview_mcp/CLAUDE.md` 를 이 프로젝트 구조에 맞게 옮긴 버전.  
> 새 전략 전용 체크리스트·폴더 규칙은 **`.claude/QUANT_STRATEGIES.md`** 참고.

## Stack

- **Backend**: FastAPI (Python 3.11), asyncio
- **Data**: Binance Futures REST + WebSocket (마크 가격 캐시), Redis(선택) + 메모리 캐시
- **Frontend**: Jinja2 (`src/app/templates/`) + vanilla JS (`static/`)
- **DB**: SQLAlchemy — Forward test·전략 상태용 (도입 예정/확장 시)
- **실거래**: 아직 없음 (`binance_executor` / `sync_binance` 미연동)

---

## 레이아웃 (`PYTHONPATH=src`)

```
src/
  common/           # Binance WS/REST, liq 캐시, oi_liq_map, 검증 등
  features/
    home/           # 홈 대시보드, /api/market-stream, /api/charts/liq, /api/verify/…
    strategy/       # /api/strategy/* (liq-snapshot, market-snapshot)
    strategy/quant_strategies/   # (예정) atr_breakout, common …
  app/
    main.py         # FastAPI 엔트리
    templates/
```

---

## Architecture (데이터 흐름)

`tradingview_mcp` 의 **RealtimeDataHub + DataBundle** 은 여기서 **아직 동일 구현이 없음**.  
대신 다음이 있다:

```
BinancePriceWS (mark)  →  멀티 심볼 캐시
liq_series_cache       →  1h klines + OI + 테이커Δ → Redis/메모리, build_oi_liq_map
market_stream          →  프리미엄/펀딩/24h/OI/LSR/CVD 프록시 (폴링)
```

### 전략이 쓰게 될 데이터 (목표 계약)

멀티 TF 바(`sweep_by_tf` 형태)가 필요하면 **별도 어댑터**로 Binance `klines` 등에서 채우거나, 캐시 스키마를 확장한다.  
1h 시계열은 `GET /api/strategy/liq-snapshot?include_series=true` 또는 내부 `get_chart_payload_or_fetch` 와 정렬할 것.

---

## Key Files (forwardtest_quant)

| 경로 | 역할 |
|------|------|
| `src/app/main.py` | 앱 엔트리, 라우터 마운트, static, lifespan |
| `src/common/binance_price_ws.py` | 마크 가격 WS 캐시 |
| `src/common/liq_series_cache.py` | 1h 빌드, Redis, `build_oi_liq_map` |
| `src/common/oi_liq_map.py` | 청산 구간 (backtest 동일 알고리즘) |
| `src/features/home/router.py` | 홈, charts, verify |
| `src/features/strategy/router.py` | quant용 liq/market 스냅샷 API |
| `src/app/templates/` | Jinja 템플릿 |
| `src/features/strategy/polymarket/logic_arb/` | 조합 차익(포함관계/분할) — 사다리 무위험 구조. basis(TOUCH/TERMINAL) 교차 금지 |
| `src/features/auth/` | 접근 게이트 — admin 로그인 + 기간제 게스트 패스 (이력 공개용) |
| `src/features/site_index/` | 루트 `/` — 전체 페이지 인덱스 (라우트 자동 수집) |

전략 모듈 도입 후:

| 경로 | 역할 (예정) |
|------|-------------|
| `src/features/strategy/common/strategies_master.yaml` | 전략 메타 (enabled, timeframes, …) |
| `src/features/strategy/common/router_factory.py` | `make_router()` |
| `src/features/strategy/common/base_forward_test.py` | `BaseForwardTest` |
| `src/features/strategy/common/base_realtime_feed.py` | `build_state()` — 데이터 소스는 허브 대신 어댑터로 연결 |

---

## 새 전략/버전 시작 전 체크리스트

`tradingview_mcp` 와 동일한 8항목을 **여기서도** 먼저 결정한다.

| # | 항목 | 영향 |
|---|------|------|
| 1 | 종목 | `symbol` 기본값 |
| 2 | 진입 TF | timeframes, 윈도우 의미 |
| 3 | 진입 조건 | `compute_signal` 구조 |
| 4 | TP/SL 방식 | signal 반환 + 청산 분기 |
| 5 | 청산 조건 | `_check_exit_signal` |
| 6 | 레버리지 | PnL·수수료 |
| 7 | Binance live | (현재 미사용) 나중에 true면 executor·sync |
| 8 | 새 전략 vs 버전 | 새 디렉터리 vs `strategy_tag`만 |

---

## Gotchas (forwardtest_quant)

- Import: `from common.…`, `from features.…` (`app` 은 `app.main` 엔트리만).
- Liq 캐시는 **1h 위주**; 15m 등은 별도 fetch/캐시 설계 필요.
- `LIQ_ON_DEMAND_FETCH`: 캐시 없을 때 첫 요청이 REST로 채움.
- 검증: `GET /api/verify/liq-consistency` — 캐시 vs 재빌드, 마지막 봉은 시간차로 어긋날 수 있음.

---

## Related docs

- **`.claude/QUANT_STRATEGIES.md`** — 새 quant 전략 추가 시 폴더·네이밍·등록 절차
- **`README.md`** — 실행 방법, env, API 표

---

## 루트 페이지 = 사이트 인덱스 (`src/features/site_index/`)

`/` 는 **전체 페이지 인덱스**. (이전엔 strategies_master.yaml 편집기였음 →
`/admin/strategies` 로 이동, admin 전용.)

- **목록을 하드코딩하지 않는다.** `app.routes` 에서 `response_class=HTMLResponse` 인
  GET 라우트를 요청 시점에 수집 → 새 전략을 `strategies_master.yaml` 에 추가하면
  `router_registry` 가 라우터를 등록하는 순간 인덱스에도 자동으로 나타난다.
- path 파라미터가 있는 라우트(`/a/{code}`)는 `path != path_format` 으로 걸러진다.
- 카드 메타는 master config 에서: `emoji`, `label`, `enabled`, `monitoring`, `symbol`, `timeframes`.
  → **label/emoji 없는 전략은 경로를 Title Case 한 이름으로 뜬다** (예: `spc_oiaccel_combine`
  → "Spc Oiaccel Combine"). 예쁘게 하려면 yaml 에 `label`/`emoji` 추가.
- 섹션: strategy / polymarket / logs / admin. 정렬은 섹션 → enabled 우선 → 이름.
- 하위 페이지는 제목에 합친다 (`Polymarket · Fade`, `Value Scan · Mobile`) —
  같은 섹션에 동일 제목이 나란히 뜨면 고를 수 없기 때문.
- **역할 필터**: `_ADMIN_ONLY` 경로는 guest 인덱스에서 제외. 숨김 + 미들웨어 403 이중.
- `GET /api/site-index` 로 JSON 도 제공.

## 접근 제어 (`src/features/auth/`)

이력 공개용. 전체 앱이 `AccessGateMiddleware` 뒤에 있다.

| 역할 | 인증 | 권한 |
|------|------|------|
| admin | `ADMIN_PASSWORD` (env) | 전체. 세션 `AUTH_ADMIN_TTL_DAYS`(기본 30일) |
| guest | 발급된 패스코드 | **선택된 3개 대시보드 전용·읽기 전용** — 그 외 경로와 쓰기 요청 403 |

- 공개 경로: `/health`, `/login`, `/logout`, `/static/*`, `/favicon.ico`. 나머지 전부 게이트.
- 게스트 허용 화면: Spot-Perp CVD, OI Accel Breakout v2, Polymarket Fade. 루트 인덱스도
  이 3개만 표시하며, 필요한 읽기 API 외에는 직접 URL 접근도 403 (`features.auth.policy`).
- 세션: HMAC-SHA256 서명 쿠키 (`ft_session`, HttpOnly, SameSite=Lax, https면 Secure). **새 의존성 없음** (stdlib).
- 게스트 코드는 평문 저장 안 함 — `access_passes.code_hash` (HMAC). 발급 응답에서 1회만 노출.
- 매 요청마다 DB 재확인 → **폐기·만료가 즉시 반영**된다 (쿠키가 남아 있어도 차단).
- 관리 화면: `/admin/access` (발급·연장·폐기·사용이력).

### 재발급 문제 해결 (2026-07-25)

24h 고정이면 상대가 다음 날 잠기고 매번 새 코드를 보내야 함 → 두 경로:

1. **자동 연장 (sliding, 기본 ON)** — `sliding_hours` 설정 시 접속할 때마다
   `expires_at = now + sliding_hours`. 계속 보는 사람은 재발급 불필요, 발길 끊기면
   그 시점부터 24h 뒤 자동 소멸. `max_expires_at` 절대 상한(기본 14일)으로 무한 연장 방지.
   DB write 는 `_SLIDE_WRITE_THRESHOLD_SEC=60` 으로 스로틀.
   → sliding 패스는 쿠키 TTL 을 상한까지 길게 발급 (유효성 판단은 항상 DB).
2. **수동 연장** — `POST /api/access/passes/{id}/extend` : **같은 코드 유지**하고 만료만 +N시간.
   이미 만료된 코드도 되살림. 폐기된 것은 불가.

### 전달 방법 — 매직 링크

`GET /a/{code}` → 코드 검증 → 쿠키 심고 `/` 로 303. **링크 하나만 주면 클릭=로그인.**

- 미들웨어 `_PUBLIC_PREFIX` 에 `/a/` 포함 (URL 자체가 자격증명이라 게이트 앞).
  `"/api/…"`, `"/admin/…"` 는 앞 3글자가 `/ap`, `/ad` 라 충돌 없음.
- 즉시 리다이렉트 → 주소창·북마크에 코드가 안 남음. `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store` 로 외부 유출·캐시 차단.
- 실패 시 `/login?e=1` → 안내 문구만, 사유는 노출 안 함.
- **평문 코드는 발급 시점에만 존재** → 기존 패스의 매직 링크는 재구성 불가.
  링크를 잃어버리면 재발급하거나, 상대가 이미 갖고 있으면 `+24h` 로 그 링크를 살린다.
- 트레이드오프: URL 에 비밀값 → 브라우저 히스토리·프록시 로그에 남을 수 있음.
  읽기 전용 + 기간제 + 폐기 가능이라 감수. 채팅앱 링크 프리뷰 봇이 긁으면 `use_count` 가
  1 오르고 sliding 만료가 밀릴 수 있다 (무해하지만 통계는 부정확해짐).
- IP 기반 인증은 검토 후 기각 — CGNAT·VPN·NAT 로 오탐/미탐이 심하고 상대 IP 를 먼저
  물어봐야 해서 전달이 오히려 번거로워짐.

### Gotchas

- `AUTH_ENABLED` 는 **필수**. 없거나 true/false 가 아니면 `get_auth_config()` 가 import 시점에
  예외 → 부팅 실패. 의도된 fail-fast (무방비 공개보다 부팅 실패가 낫다).
- **docker: `.env` 변경은 reload 로 안 먹는다.** `env_file` 은 컨테이너 *생성* 시점에 읽히므로
  watchfiles 리로드는 옛 환경 그대로 → `AuthConfigError`. `up -d --force-recreate` 필요.
- `/login` 의 `next` 는 미인증 입력 → `_safe_next()` 로 같은 사이트 절대경로만 허용
  (오픈 리다이렉트 + `</script>` 탈출 차단). 템플릿에도 스크립트 대신 `data-next` 속성으로 전달.

---

## 드로다운 매수 (`src/features/strategy/drawdown_signal/`)

인기 종목 + Crypto + 지수 + 원자재 28종목, 52주 고점 −30% 진입 → −40/−50% 물타기 →
고점 회복 시 청산. **하루 1회 일봉**. 자세한 건 모듈 `README.md`.

- 구조는 `value_scan` 을 따른다: `engine / stats / router / cache` + `strategies_master.yaml` 등록
  → `router_registry` 가 `/quant/drawdown_signal/*` 자동 등록, 사이트 인덱스에도 자동 노출.
- **알림봇이 아니라 원장이다.** `backfilled` 플래그로 사후 발견분을 분리하지 않으면
  포워드 테스트가 아니라 백테스트가 된다. 모든 집계가 코호트별로 갈린다.
- 원장은 `DATABASE_URL` 있으면 DB(`drawdown_ledger` 단일 행 블롭)가 정본. 파일에만 두면
  Railway 재배포마다 `first_run` 이 되어 out-of-sample 이 영원히 0.
- 실행은 앱 안 `_drawdown_scheduler` (30분 폴링 + `last_run` catch-up). cron 없음.
  스케줄러와 대시보드 수동 실행은 `engine.run_exclusive` 락을 공유 — 단일 블롭이라
  동시 write 시 한쪽이 통째로 덮인다.
- 실측: 1회 스캔 **7.5초 · 요청 28건 · 계산 14ms**. 대시보드는 원장만 읽어 네트워크 0.

### 차트 기준 (다른 대시보드에도 적용할 것)

`backtest_quant/posts/ipo-two-ways-in/chart.py` 의 **리서치 차트 문법**을 따른다.
소셜 카드와 반대다 — 작은 활자, 얇은 선, 높은 밀도, 범례 상자 대신 **선 끝 직접 라벨**,
구간별 표본 수 `n` 표기, 낮은 채도(`#2e6fd4`/`#8a5cd0`, 네온 금지), 8:5, 하단 2줄 방법론.
외부 차트 라이브러리 없이 순수 SVG 로 그린다 (`renderCurveChart` / `renderEdgeChart`).

**미래 정보 누수 주의**: 보유 기간별 곡선에서 각 구간의 평단은 *그 시점까지 체결된 것만*
반영해야 한다 (`engine._return_path`). 전체 평단을 초반 구간에 쓰면 아직 일어나지 않은
매수를 소급 적용하는 셈이라 물타기가 실제보다 좋아 보인다.

---

## Polymarket fade 워치리스트 자동 스크리너 (`fade/screener.py`)

종목을 **이름/키워드가 아니라 가격 거동**으로 뽑는다. 이전엔 `watchlist.yaml` 수동 90개 +
이벤트 title 키워드 매칭이라 달이 바뀌면 신규 마켓을 전혀 못 잡았다.

### 핵심: Gamma offset 상한을 슬라이스 분할로 우회

- Gamma `/markets` 의 `offset` 은 **2000 하드 상한** (초과 시 422 + keyset 안내).
  단일 쿼리로는 볼륨 상위 ~2,100개까지만 보인다.
- **`/markets/keyset` 은 쓰지 마라.** 커서가 사실상 끝없이 돌아 15분+ 무한 페이징한다
  (활성 마켓 36,700개+ — 스포츠·사다리가 대부분).
- 대신 서버측 필터 `volume_num_min` / `end_date_min` / `end_date_max` 가 **실제로 동작한다**
  (무시되는 파라미터가 아님 — 실측 확인). 종료월 단위로 슬라이스를 쪼개면 각 슬라이스가
  상한 아래로 내려가 **전수 열거**가 된다. → `client.screen_band_markets()`
- 실측(vol≥$10k, 18개월): 마켓 4,609 · 5~15% 밴드 936~941 · 요청 58건 · 26초.
  **누적볼륨 상위만 보면 밴드 후보의 60%를 놓친다** (388 vs 941). "누적볼륨 상위"와
  "지금 5~15%에 있는 마켓"은 다른 집합이다.
- `screen_band_markets` 는 상한에 걸린 슬라이스 라벨을 함께 반환한다 → 조용히 넘기지 말 것.
  vol≥$1k 까지 낮추면 일부 월이 상한에 걸린다(주간 분할 필요).

### 히스토리는 병목이 아니다

- CLOB `/prices-history`: 동시 80 × 400건까지 **429 0건** (108~279 req/s). 레이트리밋은
  오히려 Gamma 쪽(초당 ~40건에서 Cloudflare 429) → 페이지당 50ms 슬립으로 회피.
- `interval=max` 는 **fidelity 무관 31일 캡** (1/60/180분 모두 31.0일). 그 이상 과거는
  `fetch_curve_full(start_ts, end_ts)` 의 14일 청킹 경로.
- 스크리닝 자체엔 히스토리가 0건 든다 — Gamma 응답의 `outcomePrices` 로 밴드 판정.
  후보를 936개로 좁힌 뒤에만 곡선을 받는다(`fetch_curve_batch`, 동시 20 → 59 req/s).

### WS 64KB 프레임 한계 → 샤딩 필수 (조용히 죽는 실패)

- subscribe 프레임은 토큰당 81B. **800토큰(63.3KB)부터 서버가 에러도 close 도 없이
  메시지만 흘리며 커버리지가 0.9%로 붕괴**한다. 로그에는 아무것도 안 남는다.
- 500토큰/연결은 100% 커버 확인 → `ws_shard_size: 500` 으로 `_ws_loop` 가 연결을 분할한다.
- `_ws_generation` 카운터로 전 샤드가 일제히 재구독. **제거분도 세대를 올린다**
  (이전엔 추가만 감지해서 빠진 종목을 계속 구독했다).
- `CLOBWSClient._connect()` (공유 클라이언트)는 **아직 단일 연결**이다. 다른 전략이
  500토큰을 넘기면 같은 방식으로 조용히 죽는다 — 이번 변경 범위 밖이라 손대지 않았다.

### 수동/자동 구분 — 마이그레이션 없이

`watchlist.yaml` 에 있는 condition_id = **수동**(스크리너 불가침).
yaml 에 없는 DB 행 = 스크리너가 넣은 **자동**(편입·해제 대상).
자동분을 yaml 에 안 쓰는 이유: 매시간 수백 행을 커밋하면 yaml 이 수동 원장의 의미를 잃는다.
재배포로 사라져도 다음 스캔이 결정론적으로 복원한다.

상태값: `included` / `excluded`(유저가 직접 뺌 — 자동 편입이 절대 되살리지 않음) /
`dropped`(밴드 이탈로 자동 해제 — 밴드 복귀 시 자동 부활).

### 안전장치 (검증 완료)

- **열린 포지션 보유 마켓은 밴드를 이탈해도 해제하지 않는다.** 해제하면 `_yes_map` 에서
  빠져 청산 로직이 그 포지션을 영영 못 본다(고아 포지션).
- 시드는 **병렬**이어야 한다. 순차면 270종목에 54초, 900종목이면 3분간 sweep 루프가 막혀
  타임아웃 청산이 통째로 밀린다. → 실측 270종목 3.1초.
- 스크리너 예외는 sweep 루프에서 삼킨다 — 스크리너가 죽으면 워치리스트가 낡을 뿐이지만
  엔진이 죽으면 청산까지 멈춘다.
- `screener.spike_*` 는 라이브 진입 조건(`lookback_s`/`spike_rel`/`spike_abs`)과 같은 값으로
  맞춰둘 것. 다르면 "편입은 됐는데 진입은 영영 안 되는" 종목이 쌓인다.

### 미해결 (의도적)

`auto_include: true` 라 워치리스트가 매시간 바뀐다. 포워드테스트 원장에 **생존자 편향**이
들어갈 여지가 있다 — 편입 시점을 원장에 남기지 않으면 "사후에 좋아 보인 종목만" 집계된다.
`drawdown_signal` 의 `backfilled` 플래그와 같은 코호트 분리가 필요하다.

## Session log (요약)

- **2026-08-07 — fade 워치리스트 자동 스크리너 (`fade/screener.py`).**
  - 계기: 워치리스트가 수동 90개에 고정 → 달이 바뀌면 신규 마켓을 못 잡음. 수집 경로도
    이벤트 title 키워드 매칭이라 주제 종속적이었다.
  - 실측으로 전제가 뒤집혔다: Gamma 서버측 필터가 동작해 **월 슬라이스 분할로 전수 열거 가능**
    (vol≥$10k → 마켓 4,609 / 밴드 936 / 58요청 / 26초). 누적볼륨 상위만 보면 60%를 놓친다.
  - 편입 기준(확정): 5~15% 밴드 · vol≥$10k · 체류율 70%+ · 스파이크 1회+. 1시간 주기.
    실측 1회차 936개 중 270개 편입, 17.4초.
  - **WS 64KB 프레임 한계 발견** — 800토큰부터 에러 없이 커버리지만 0.9%로 붕괴.
    `ws_shard_size: 500` 샤딩 + `_ws_generation` 재구독으로 해결(3샤드 99.6% 커버 확인).
  - 검증: 판정 4케이스 · 전수 열거(밴드밖 0/볼륨위반 0) · 안전장치 4항목(밴드유지/이탈/
    열린포지션 보호/수동 불가침) · 멱등성 3회 반복(행수 271 고정, 중복 0) · 멀티샤드 커버리지 ·
    기존 테스트 4 passed. 전부 스크래치 DB(`DATABASE_URL` 격리)에서 실행.
  - 남은 것: auto_include 로 워치리스트가 매시간 바뀌므로 원장에 편입 시점 코호트가 필요
    (생존자 편향). `CLOBWSClient` 는 여전히 단일 연결.

- forwardtest_quant: `src/` 레이아웃, 홈 + strategy API + liq 검증.
- `CLAUDE.md` / `.claude/QUANT_STRATEGIES.md` 는 `tradingview_mcp/CLAUDE.md` 기준으로 btc에 맞게 재작성.
- **2026-07-21 — `polymarket/logic_arb` 추가 (조합 차익, 3순위 전략).**
  - 예측 아님. 사다리 시장의 **논리 관계(무위험 구조)** 만 거래.
  - 포함관계 차익: GT/LT 사다리에서 `ask(YES_sup)+ask(NO_sub) < 1` → 최소 페이오프 1 확정.
    lo=hi면 `pair_hedge`(YES+NO<1)와 동일 = 그 시장 간 일반화.
  - **basis 게이트(핵심)**: Polymarket BTC 사다리는 `reach/dip to`(TOUCH) 문구 — `above/below`(TERMINAL)
    와 해상도 규칙이 달라 절대 교차 차익 안 함. `parse.py` 가 방향+basis 산술 파싱, 제목 유사성 미사용.
  - 기계적 검증: 동일 end_ts(±tol) 그룹핑 → ws best_ask 프리스크린 → REST `/book` 실 ask+size 재확인 → fee_buffer.
  - kill-criteria 계측: 위반 지속시간 로깅(<60s 소멸=봇 선점 신호).
  - 파일: `logic_arb/{parse,signal,engine,config}.py`, 수집기 `client.fetch_active_events_by_keyword`,
    단독 테스터 `scripts/polymarket_logic_arb_scan.py [--groups]`. `runner` gather/마스터스위치/resolver 등록.
  - 실측(2026-07-21): EOY2026 reach 20단 등 7개 사다리, 가격 단조 → 현 차익 0건 (효율적 시장, 논문과 일치).
  - 기본 `enabled: false` (scan-only). 주문은 `POLYMARKET_LIVE` 별도.
- **2026-07-25 — 접근 게이트 추가 (`src/features/auth/`).**
  - 이력 링크 공개용. admin(무기한) + 게스트 기간제 패스(기본 24h, 읽기 전용).
  - 계기: 루트 `/` 가 `POST /api/strategies-master`(전략 YAML 저장)를 무인증 노출 중이었음.
    게스트를 GET-only 로 묶어 이 경로와 포지션 청산·DB 리셋·주문 API 를 전부 차단.
  - 재발급 부담 해소: sliding 자동 연장 + 같은 코드 유지 수동 연장(`/extend`).
  - 신규 테이블 `access_passes` + `_ensure_access_passes_columns` safe-migrate.
  - 검증: 인증/권한/폐기/만료/위조/설정 fail-fast 44건 + 연장 25건 통과.
    `/login?next=` 의 `</script>` 탈출 XSS 발견 → 서버측 `_safe_next` + `data-next` 로 수정.
  - 전달은 **매직 링크** `/a/{code}` — 링크 하나 클릭이면 로그인.
- **2026-07-25 — 루트를 사이트 인덱스로 교체 (`src/features/site_index/`).**
  - 기존 루트(YAML 편집기)는 열람자에게 무의미하고 쓰기 권한이 필요 → `/admin/strategies` 로 이동.
  - 인덱스는 `app.routes` 자동 수집 — 전략 추가 시 목록 관리 불필요.
  - 33개 페이지 수집 확인, 역할 필터·깨진 링크 0 검증 완료.
- **2026-07-28 — `drawdown_signal` 모듈화 + 대시보드 (`src/features/strategy/drawdown_signal/`).**
  - 엔진만 있던 것을 `value_scan` 구조로 완성: `stats.py`(집계) · `router.py`(6개 엔드포인트) ·
    `cache.py`(TTL 120s) · `README.md` + `strategies_master.yaml` 등록.
  - `engine.run_exclusive()` 추가 — 스케줄러 틱과 대시보드 수동 실행이 원장을 동시에
    덮어쓰는 문제. `main.py` 스케줄러도 이 경로로 바꿨다.
  - `engine._return_path()` 추가 — 리플레이가 이미 든 가격으로 보유 기간별
    (래더 vs 1차 진입만) 수익률을 같이 계산. **네트워크 추가 비용 0**.
  - 차트 2종을 IPO 포스트 문법으로 SVG 직접 구현 (라이브러리 없음).
  - 리소스 실측: 스캔 1회 7.5s / 요청 28건 / 계산 14ms / RSS +32MB, 실패 0건.
  - 검증: 로컬 TestClient 6개 엔드포인트 200, 도커 컨테이너 안 TestClient 도 200,
    사이트 인덱스 카드 자동 노출 확인, 브라우저 콘솔 에러 0.
  - 함정: 컨테이너의 fastapi 0.139 는 `include_router` 결과를 `app.routes` 에
    `_IncludedRouter` 래퍼로 넣는다 → `r.path` 로 라우트 존재를 확인하면 빈 목록이 나온다.
    등록 여부는 TestClient 로 실제 요청해서 봐야 한다.
