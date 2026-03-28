# btc_forwardtest — Project Memory

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

## Key Files (btc_forwardtest)

| 경로 | 역할 |
|------|------|
| `src/app/main.py` | 앱 엔트리, 라우터 마운트, static, lifespan |
| `src/common/binance_price_ws.py` | 마크 가격 WS 캐시 |
| `src/common/liq_series_cache.py` | 1h 빌드, Redis, `build_oi_liq_map` |
| `src/common/oi_liq_map.py` | 청산 구간 (backtest 동일 알고리즘) |
| `src/features/home/router.py` | 홈, charts, verify |
| `src/features/strategy/router.py` | quant용 liq/market 스냅샷 API |
| `src/app/templates/` | Jinja 템플릿 |

전략 모듈 도입 후:

| 경로 | 역할 (예정) |
|------|-------------|
| `src/features/strategy/quant_strategies/common/strategies_master.yaml` | 전략 메타 (enabled, timeframes, …) |
| `src/features/strategy/quant_strategies/common/router_factory.py` | `make_router()` |
| `src/features/strategy/quant_strategies/common/base_forward_test.py` | `BaseForwardTest` |
| `src/features/strategy/quant_strategies/common/base_realtime_feed.py` | `build_state()` — 데이터 소스는 허브 대신 어댑터로 연결 |

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

## Gotchas (btc_forwardtest)

- Import: `from common.…`, `from features.…` (`app` 은 `app.main` 엔트리만).
- Liq 캐시는 **1h 위주**; 15m 등은 별도 fetch/캐시 설계 필요.
- `LIQ_ON_DEMAND_FETCH`: 캐시 없을 때 첫 요청이 REST로 채움.
- 검증: `GET /api/verify/liq-consistency` — 캐시 vs 재빌드, 마지막 봉은 시간차로 어긋날 수 있음.

---

## Related docs

- **`.claude/QUANT_STRATEGIES.md`** — 새 quant 전략 추가 시 폴더·네이밍·등록 절차
- **`README.md`** — 실행 방법, env, API 표

---

## Session log (요약)

- btc_forwardtest: `src/` 레이아웃, 홈 + strategy API + liq 검증.
- `CLAUDE.md` / `.claude/QUANT_STRATEGIES.md` 는 `tradingview_mcp/CLAUDE.md` 기준으로 btc에 맞게 재작성.
