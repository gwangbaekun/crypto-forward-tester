# 원장 무결성 · 측정 프로토콜 (정본)

> 이 프로젝트가 존재하는 이유다. 전략보다 이게 먼저다.
> `drawdown_signal` 에서 확립했고, 모든 전략이 승계한다.

---

## 1. 알림봇이 아니라 원장이다

모든 신호는 **발생 시점에** 기록한다. 사후에 발견해서 채워 넣은 것은
`backfilled` 플래그로 분리한다. 분리하지 않으면 그건 포워드 테스트가 아니라 백테스트다.

모든 집계가 코호트별로 갈려야 한다. 합산 숫자 하나만 보여주는 대시보드는 거짓말을 한다.

---

## 2. 코호트를 갈라라

데이터 스키마나 수집 방식이 바뀐 시점은 **다른 코호트**다.

- OI 세션 정합성을 고치기 전/후
- 하루 1스냅이던 시절의 옵션 OI 는 한 세션 지연분이었다 → 장초 스냅 도입 이후만 집계
- 워치리스트가 자동으로 바뀌는 전략은 **편입 시점**을 원장에 남긴다 (생존자 편향)

섞으면 수정 효과와 전략 효과가 구분되지 않는다.

---

## 3. 미래 정보 누수 점검 3종

- [ ] 분위·평균·표준편차를 **전체 표본**으로 계산하고 있지 않은가 → 롤링으로
- [ ] 신호 시각의 데이터가 **그 시각에 실제로 존재**했는가 (옵션 OI 는 특히)
- [ ] 청산 가격이 진입 시점에 알 수 없는 정보를 쓰고 있지 않은가

물타기·래더형 전략 추가 항목: 보유 기간별 곡선에서 각 구간의 평단은 **그 시점까지
체결된 것만** 반영해야 한다 (`drawdown_signal.engine._return_path`). 전체 평단을 초반
구간에 쓰면 아직 일어나지 않은 매수를 소급 적용하는 셈이라 물타기가 실제보다 좋아 보인다.

---

## 4. 반증 조건을 먼저 적어라

각 전략의 폐기 조건을 **코드 주석이 아니라 원장에** 같이 기록한다.
나중에 조건을 완화하고 싶어지는 것이 정상이고, 그래서 미리 적는다.

---

## 5. 표본 최소치

`n < 30` 이면 어떤 결론도 내지 않는다. 표본이 쌓이는 데 1년 이상 걸리는 전략이 있다는
것을 감안하고 시작한다.

---

## 6. 저장 위치 — 파일에만 두면 원장이 사라진다

`DATABASE_URL` 이 있으면 **DB 가 정본**이다. 파일에만 두면 Railway 재배포마다
`first_run` 이 되어 out-of-sample 이 영원히 0 이 된다.

| 전략 | 정본 | 파일에 남는 것 |
|---|---|---|
| `drawdown_signal` | `drawdown_ledger` 단일 행 블롭 | — |
| `value_scan` | `value_scan_positions` ↔ `_lots`, `value_scan_closed_trades` ↔ `_closed_lots` | 스캔 스냅샷 `data/value_forward/scans/{date}_{market}.json`, `last_activity.json` |

`value_scan` 레거시 JSON(`positions.json`, `history.json`)은 DB 가 비고 json 만 있으면
앱 기동 시 1회 자동 이전 후 `.json.bak` 으로 rename 한다.
수동: `POST /quant/value_scan/migrate/json-to-db` · 상태: `GET /quant/value_scan/storage`

**단일 블롭 원장은 동시 write 에 취약하다.** 스케줄러 틱과 대시보드 수동 실행은
`engine.run_exclusive` 락을 공유해야 한다. 안 하면 한쪽이 통째로 덮인다.

---

## 7. 스케줄

cron 없이 앱 안에서 돈다.

| 전략 | 실행 |
|---|---|
| `drawdown_signal` | `_drawdown_scheduler` — 30분 폴링 + `last_run` catch-up |
| `value_scan` | 기동 후 + 10분마다 `should_run_catchup` → 오늘 안 돌린 시장만 (하루 1회) |

`value_scan` 거래일 기준 (`value_scan_market_meta`):

| 시장 | 스캔 가능 시각 | 거래일 |
|---|---|---|
| KOSPI | 평일 15:35 KST 이후 | `last_trading_date` (KST) |
| NASDAQ | 평일 16:05 ET 이후 | ET 날짜 |

`GET /quant/value_scan/scan/status` · `/scan/schedule`

---

## 8. 차트 기준 (모든 대시보드 공통)

`backtest_quant/posts/ipo-two-ways-in/chart.py` 의 **리서치 차트 문법**을 따른다.
소셜 카드와 반대다.

작은 활자 · 얇은 선 · 높은 밀도 · 범례 상자 대신 **선 끝 직접 라벨** · 구간별 표본 수 `n`
표기 · 낮은 채도(`#2e6fd4`/`#8a5cd0`, 네온 금지) · 8:5 · 하단 2줄 방법론.

외부 차트 라이브러리 없이 순수 SVG 로 그린다 (`renderCurveChart` / `renderEdgeChart`).
