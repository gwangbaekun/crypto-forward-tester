# Quant 전략 — 새로 만들 때 참고 (btc_forwardtest)

> `tradingview_mcp/CLAUDE.md` 의 「새 전략 추가」 절을 이 레포 구조에 맞게 옮김.  
> **변수명·모듈 경로는 tradingview 와 1:1이 아님** — 여기 기준으로 맞출 것.

## 패키지 위치

전략 코드는 아래에 둔다 (예정·합의된 트리):

```
src/features/strategy/quant_strategies/
  common/
    strategies_master.yaml    # 전략 메타 단일 소스 (도입 시)
    router_factory.py
    base_forward_test.py
    base_realtime_feed.py
    config_loader.py
    …
  atr_breakout/               # 예: 전략별 폴더
    config.yaml
    config_loader.py
    signal.py
    realtime_feed.py
    forward_test.py
    router.py
    STRATEGY.md
```

- Import 예: `from features.strategy.quant_strategies.common.router_factory import make_router`  
  (`features/strategy/__init__.py` 등으로 패키지 인식 필요.)

## 데이터 소스 (tradingview 와의 차이)

| tradingview_mcp | btc_forwardtest (현재) |
|-----------------|-------------------------|
| `RealtimeDataHub` → `DataBundle.sweep_by_tf` | **동일 허브 없음** — 1h는 `liq_series_cache`, 마크는 `BinancePriceWS` |
| `detect_sweep` 멀티 TF 바 | **15m/1h 바**는 전략 쪽에서 `klines` 어댑터 또는 캐시 확장으로 공급 |

새 전략을 짤 때 **어떤 TF를 어디서 채울지**를 먼저 `STRATEGY.md`에 적는다.

## 필수 파일 (새 전략 디렉터리 하나일 때)

1. **`strategies_master.yaml`** (common) — `enabled`, `timeframes`, `data_needs` 등 (도입 후)
2. **`signal.py`** — `compute_signal(...)` 시그니처는 허브/어댑터와 맞출 것
3. **`config.yaml`** — signal / tpsl 파라미터
4. **`realtime_feed.py`** — `build_state` 래핑 (또는 btc용 어댑터 호출)
5. **`router.py`** — `make_router("strategy_key", default_tfs="...")` (3줄)
6. **`forward_test.py`** — `BaseForwardTest` 서브클래스
7. **`<name>_dashboard.html`** — `app/templates/` (Jinja)
8. **`STRATEGY.md`** — 진입·청산·TP/SL·데이터 의존성 문서

## 등록

- `src/app/main.py` — `from features.strategy.quant_strategies.<name>.router import router` 후 `app.include_router(...)`
- (선택) 홈에서 전략 카드·링크 — `templates/` / `static/home/`

## Forward test + DB

- **저장소**: PostgreSQL/SQLite + SQLAlchemy 모델 (`ForwardTrade` 등) — 도입 시 `app/db` 또는 `src/db` 로 통일.
- **실거래 없음**: `sync_from_binance`, `/execute/status` 는 생략하거나 스텁.

## 새 전략 vs 버전만

- 로직 동일·TF/파라미터만 다름 → `strategies_master.yaml`에 태그 + `realtime_feed`에 함수 추가  
- 로직 구조가 다름 → **새 디렉터리** `quant_strategies/<name>/`

## 네이밍

- `STRATEGY_TAG` / 폴더명 / `strategies_master` 키는 **동일**하게 유지.
- 헷갈리는 약어 대신 `entry_timeframe`, `sweep_bars` 같이 **역할이 드러나는 이름** 권장 (기존 tradingview 일부 변수는 맥락 없이 짧음).

## 참고 원본

- `tradingview_mcp/CLAUDE.md` — 프로젝트 전체 메모리
- `tradingview_mcp/app/quant_strategies/STRATEGIES.md` — 전략별 요약
- 상위 레포 **`../CLAUDE.md`** — btc_forwardtest 스택·키 파일
