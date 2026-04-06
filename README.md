# btc_forwardtest

레이아웃은 `btc_backtest`와 같이 **`src/` 아래**에 둡니다.

- `src/common/` — Binance WS/REST, 템플릿 유틸
- `src/features/` — 기능별 라우터 (예: `features/home/`)
- `src/app/` — FastAPI 엔트리(`main.py`), Jinja 템플릿(`app/templates/`)

`PYTHONPATH`는 **`src`** 를 가리키면 `common`, `features`, `app` 패키지가 그대로 import 됩니다.

## 문서 (에이전트·개발 참고)

| 문서 | 용도 |
|------|------|
| **`CLAUDE.md`** (루트) | 스택, 디렉터리, 데이터 흐름, 키 파일 — `tradingview_mcp/CLAUDE.md` 의 btc 버전 |
| **`.claude/QUANT_STRATEGIES.md`** | 새 quant 전략 추가 시 체크리스트·폴더 규칙 (`features/strategy/`) |
| **`.claude/README.md`** | 위 문서 인덱스 |

> `tradingview_mcp/.claude` 아래에는 설정만 있고, 본문 가이드는 **`tradingview_mcp/CLAUDE.md`** 에 있었다. 여기서는 루트 + `.claude/` 로 나눔.

## 빠른 시작

```bash
cd btc_forwardtest
cp .env.example .env
pip install -r requirements.txt
PYTHONPATH=src python -m app.main
```

Docker:

```bash
docker compose up --build
```

- Health: [http://localhost:8000/health](http://localhost:8000/health)
- UI: [http://localhost:8000/](http://localhost:8000/)

## Liquidation map (backtest와 동일 요건)

`btc_backtest`의 `data/liq_cache_builder.py` / `serve_liq_cache` 기준:

- **1h** 캔들 + **OI** + **CVD(테이커) 델타**
- 슬라이딩 **window=400** (`LIQ_WINDOW`), **min_bars=50**
- Redis에 넣는 시계열 길이는 **window 최대 2배** = 기본 **800봉** (`LIQ_RETAIN_BARS`)

백그라운드가 Binance REST로 주기적으로 갱신하고, 홈에서는 **종가·OI·테이커 Δ** 차트를 순서대로 그린 뒤 최신 청산 구간을 표시합니다. `REDIS_URL`이 비어 있으면 프로세스 메모리 캐시만 사용합니다.

**Cold start:** Redis/메모리에 아직 없을 때 `/api/charts/liq` 첫 요청이 `build_payload_for_symbol`(REST)로 캐시를 채웁니다(`LIQ_ON_DEMAND_FETCH`, 기본 true). 끄려면 `LIQ_ON_DEMAND_FETCH=false`.

## Quant strategy API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/strategy/liq-snapshot?symbol=BTCUSDT` | 청산 구간·bias·`current_price` (고정 JSON 스키마, `schema_version: "1"`) |
| GET | `...&include_series=true` | 위 + `series_1h` 시계열 |
| GET | `/api/strategy/market-snapshot?symbol=BTCUSDT` | 실시간 마크/펀딩 등 (`build_market_stream_payload`, 홈 폴링과 동일 데이터) |

청산 구간은 **backtest `data/oi_liq_map.py`의 `build_oi_liq_map`** 과 동일 알고리즘; 입력만 DB 대신 Binance REST 1h+OI+테이커 프록시입니다.

- **데이터 일치 검증:** `GET /api/verify/liq-consistency?symbol=BTCUSDT` — (1) 캐시 vs REST 재빌드 일치율 (2) 캐시 마지막 12봉 종가 vs Binance `klines`. 홈 화면 **데이터 일치 확인** 버튼과 동일.
