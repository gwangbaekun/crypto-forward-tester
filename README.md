# crypto-forward-tester

Binance 선물 데이터(REST + WebSocket)를 기반으로 퀀트 전략을 **실시간 포워드 테스트**하는 서버.  
`crypto-backtester`의 전략 signal 함수를 그대로 재사용해 backtest와 동일한 스키마로 비교할 수 있다.

---

## 빠른 시작

```bash
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

---

## 스택

| 레이어 | 기술 |
|--------|------|
| Backend | FastAPI (Python 3.11), asyncio |
| Data | Binance Futures REST + WebSocket (마크 가격 캐시), Redis(선택) + 메모리 캐시 |
| Frontend | Jinja2 + vanilla JS |
| DB | SQLAlchemy (포워드 트레이드 이력) |
| 실거래 | cTrader 연동 (일부 전략), Binance executor (준비 중) |

---

## 폴더 구조 (`PYTHONPATH=src`)

```
src/
├─ app/
│  ├─ main.py                # FastAPI 엔트리, 라우터 마운트, lifespan
│  └─ templates/             # Jinja2 HTML 템플릿
├─ common/
│  ├─ binance_price_ws.py    # 마크 가격 WS 캐시 (멀티 심볼)
│  ├─ liq_series_cache.py    # 1h klines + OI + 테이커Δ → Redis/메모리
│  └─ oi_liq_map.py          # 청산 구간 (backtest 동일 알고리즘)
├─ features/
│  ├─ home/router.py         # 홈 대시보드, /api/market-stream, /api/charts/liq
│  ├─ strategy/
│  │  ├─ router.py           # /api/strategy/* (liq-snapshot, market-snapshot)
│  │  ├─ router_registry.py  # strategies_master.yaml → 자동 라우터 등록
│  │  └─ common/
│  │     ├─ strategies_master.yaml  # 전략 메타 (enabled, timeframes, tick 등)
│  │     ├─ router_factory.py       # make_router() → 표준 엔드포인트 자동 생성
│  │     ├─ base_forward_test.py    # BaseForwardTest (DB, tick, PnL)
│  │     ├─ base_realtime_feed.py   # get_state() — Binance 어댑터
│  │     ├─ strategy_loop.py        # 백그라운드 tick 루프
│  │     └─ signal_logger.py        # 시그널 로그
│  ├─ ctrader/               # cTrader OAuth + executor
│  └─ notifications/         # Discord / Telegram 알림
└─ db/                       # SQLAlchemy 모델 (ForwardTrade 등)
```

---

## 활성화된 전략 (`strategies_master.yaml`)

| 전략 | 심볼 | 진입 TF | 설명 |
|------|------|---------|------|
| **spot_perp_cvd** | ETHUSDT | 1h | 스팟/선물 CVD 괴리 — 실거래 연동 준비 |
| **oi_accel_breakout_v2** | BTCUSDT | 15m | OI 가속 돌파 v2 |
| **spc_oiaccel_combine** | ETH+BTC | — | spot_perp_cvd + oi_accel_breakout_v2 복합 |

전체 전략 목록은 `src/features/strategy/strategies_master.yaml` 참고.

---

## Quant Strategy API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/strategy/liq-snapshot?symbol=BTCUSDT` | 청산 구간·bias·현재가 |
| GET | `...&include_series=true` | 위 + 1h 시계열 |
| GET | `/api/strategy/market-snapshot?symbol=BTCUSDT` | 마크가/펀딩/OI/LSR/CVD |
| GET | `/quant/{strategy_key}/dashboard` | 전략 대시보드 (HTML) |
| GET | `/quant/{strategy_key}/realtime_state` | 실시간 시그널 상태 |
| GET | `/quant/{strategy_key}/forward_test/stats` | 포워드 테스트 통계 |
| GET | `/quant/{strategy_key}/forward_test/trades` | 트레이드 이력 |
| GET | `/quant/{strategy_key}/signal/explain` | 시그널 상세 |

---

## Liquidation Map

- **1h** 캔들 + **OI** + **CVD(테이커) 델타**
- 슬라이딩 **window=400**, **min_bars=50**
- Redis 없으면 프로세스 메모리 캐시 사용
- Cold start: `/api/charts/liq` 첫 요청 시 `LIQ_ON_DEMAND_FETCH`로 자동 채움 (기본 `true`)

데이터 일치 검증:

```
GET /api/verify/liq-consistency?symbol=BTCUSDT
```

---

## 환경 변수 (`ENV`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REDIS_URL` | (없음) | Redis URL; 없으면 메모리 캐시 |
| `LIQ_ON_DEMAND_FETCH` | `true` | 캐시 없을 때 첫 요청에 REST로 채움 |
| `LIQ_WINDOW` | `400` | 청산 구간 슬라이딩 윈도우 |
| `LIQ_RETAIN_BARS` | `800` | Redis 시계열 최대 보유 봉 수 |
| `POLYMARKET_LIVE` | `false` | Polymarket 실시간 활성화 |
| `CTRADER_*` | — | cTrader OAuth 설정 |

---

## 새 전략 추가 체크리스트

`.claude/QUANT_STRATEGIES.md` 에 8항목 체크리스트가 있다.  
요약:

1. 종목 (symbol)
2. 진입 TF
3. 진입 조건 (`compute_signal`)
4. TP/SL 방식
5. 청산 조건 (`_check_exit_signal`)
6. 레버리지
7. Binance live 여부
8. 새 전략 vs 기존 전략 버전업

---

## 관련 문서

| 문서 | 용도 |
|------|------|
| `CLAUDE.md` | 스택, 데이터 흐름, 키 파일 |
| `.claude/QUANT_STRATEGIES.md` | 새 전략 추가 절차 |
| `src/features/strategy/AI_GUIDE_STRATEGY_UPDATE_RULES.md` | AI 에이전트용 전략 수정 규칙 |
