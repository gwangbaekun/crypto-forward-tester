# 아키텍처 (정본)

> 스택·레이아웃·데이터 흐름·키 파일의 단일 출처.

---

## 1. 스택

| 층 | 내용 |
|---|---|
| Backend | FastAPI (Python 3.11), asyncio |
| 데이터 | Binance Futures REST + WS (마크 가격), Redis(선택) + 메모리 캐시 |
| Frontend | Jinja2 (`src/app/templates/`) + vanilla JS (`static/`) |
| DB | SQLAlchemy — `DATABASE_URL` 있으면 Postgres 가 정본, 없으면 파일 |
| 실거래 | Binance (`spot_perp_cvd` 만), cTrader/FTMO (설정만 존재), Polymarket (`POLYMARKET_LIVE`) |
| 배포 | Railway (앱) + Oracle VM (수집·릴레이) |

---

## 2. 레이아웃 (`PYTHONPATH=src`)

```
src/
  common/            Binance WS/REST, liq 캐시, oi_liq_map, 검증
  db/                SQLAlchemy 모델·세션
  features/
    auth/            접근 게이트 → ACCESS.md
    site_index/      루트 `/` — 라우트 자동 수집 인덱스
    home/            홈 대시보드, /api/market-stream, /api/charts/liq, /api/verify/…
    data_explorer/   수집 데이터 조회
    notifications/   Telegram · Discord
    ctrader/         FTMO cTrader 브릿지
    strategy/
      common/        strategies_master.yaml · router_factory · base_forward_test
      <strategy_id>/ 전략 폴더 → STRATEGY_RULES.md
      polymarket/    fade · logic_arb → POLYMARKET.md
      router_registry.py
  polymarket_worker/ 주문 워커
  research/          bithumb_mm 등
  app/main.py        FastAPI 엔트리 · 라우터 마운트 · lifespan 스케줄러
```

Import 는 `from common.…`, `from features.…`. `app` 은 `app.main` 엔트리에서만 쓴다.

---

## 3. 데이터 흐름

`backtest_quant` 의 `RealtimeDataHub + DataBundle` 에 해당하는 통합 허브는 **없다.**
대신 세 경로가 따로 돈다.

```
BinancePriceWS (mark)   →  멀티 심볼 가격 캐시
liq_series_cache        →  1h klines + OI + 테이커Δ  →  Redis/메모리  →  build_oi_liq_map
market_stream           →  프리미엄·펀딩·24h·OI·LSR·CVD 프록시 (폴링)
```

- liq 캐시는 **1h 전용**이다. 15m 등 다른 TF 가 필요하면 별도 fetch/캐시 설계가 먼저다.
- 슬라이딩 `window=400` (`LIQ_WINDOW`), `min_bars=50`, Redis 보관 `LIQ_RETAIN_BARS=800`.
  청산 구간 알고리즘은 backtest 의 `oi_liq_map.build_oi_liq_map` 과 동일하고, 입력만 REST 다.
- `LIQ_ON_DEMAND_FETCH=true` (기본): 캐시가 비면 첫 요청이 REST 로 채운다.
- 멀티 TF 바(`sweep_by_tf`)가 필요한 전략은 `klines` 어댑터를 자기 폴더에 둔다.

검증: `GET /api/verify/liq-consistency?symbol=BTCUSDT` — 캐시 vs 재빌드 일치율 + 최근 12봉
종가 대조. **마지막 봉은 시간차로 어긋나는 것이 정상이다.**

---

## 4. 키 파일

| 경로 | 역할 |
|---|---|
| `src/app/main.py` | 앱 엔트리, 라우터 마운트, lifespan 스케줄러 |
| `src/common/binance_price_ws.py` | 마크 가격 WS 캐시 |
| `src/common/liq_series_cache.py` | 1h 빌드, Redis, `build_oi_liq_map` |
| `src/common/oi_liq_map.py` | 청산 구간 (backtest 동일 알고리즘) |
| `src/features/strategy/common/strategies_master.yaml` | 전략 메타 **단일 출처** |
| `src/features/strategy/router_registry.py` | master 를 읽어 라우터 자동 등록 |
| `src/features/site_index/` | 루트 `/` 인덱스 |
| `src/features/auth/` | 접근 게이트 |

---

## 5. 루트 페이지 = 사이트 인덱스

`/` 는 전체 페이지 인덱스다. **목록을 하드코딩하지 않는다.**

- 요청 시점에 `app.routes` 에서 `response_class=HTMLResponse` 인 GET 라우트를 수집한다.
  → `strategies_master.yaml` 에 전략을 추가하면 `router_registry` 등록과 동시에 인덱스에 뜬다.
- path 파라미터 라우트(`/a/{code}`)는 `path != path_format` 으로 걸러진다.
- 카드 메타는 master 의 `emoji` `label` `enabled` `monitoring` `symbol` `timeframes`.
  **label/emoji 가 없으면 경로를 Title Case 한 이름**이 뜬다 (`spc_oiaccel_combine` → "Spc Oiaccel Combine").
- 섹션: strategy / polymarket / logs / admin. 정렬은 섹션 → enabled → 이름.
- 하위 페이지는 제목에 합친다 (`Polymarket · Fade`) — 같은 섹션에 동일 제목이 나란히 뜨면 고를 수 없다.
- `_ADMIN_ONLY` 경로는 guest 인덱스에서 제외 (숨김 + 미들웨어 403 이중).
- JSON: `GET /api/site-index`

---

## 6. 실행

```bash
cp .env.example .env
pip install -r requirements.txt
PYTHONPATH=src python -m app.main
```

```bash
docker compose up --build
```

Health `/health` · UI `/`.

---

## 7. Gotchas

- **docker 에서 `.env` 변경은 reload 로 안 먹는다.** `env_file` 은 컨테이너 *생성* 시점에
  읽히므로 watchfiles 리로드는 옛 환경 그대로다. `up -d --force-recreate` 가 필요하다.
- **`app.routes` 로 라우트 등록을 확인하지 마라.** 컨테이너의 fastapi 0.139 는
  `include_router` 결과를 `_IncludedRouter` 래퍼로 넣어서 `r.path` 가 빈 목록을 준다.
  등록 여부는 TestClient 로 실제 요청해서 확인한다.
- `REDIS_URL` 이 비면 프로세스 메모리 캐시만 쓴다 — 재배포마다 cold start.
