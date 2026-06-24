# Polymarket — LEGACY (scrapped 2026-06-24)

**Status: 폐기. 전 전략 `enabled: false`. 실거래 안 함 (`POLYMARKET_LIVE=false`).**
코드는 포트폴리오/참고용으로 보존. 대시보드는 계속 열람 가능.

## 왜 폐기했나 (결론)
Polymarket 저유동 시장에서 소자본 양의 EV는 **모든 진입로가 닫혔다**:

| 시도 | 결과 |
|---|---|
| favorite 매수 (late_convergence, latency_snipe) | 음의 EV. 라이브 paper 99건 승률 91.9%인데도 −6% (본전 94% 필요, 패1=승15) |
| 크립토 up/down | 효율적 — 엣지 +0.3%p ≈ 0 (n=90, 신뢰) |
| Dutch-book 무위험 차익 | 상위볼륨 399개에 0건 |
| latency B (정산 전 매도) | 호가창 구조상 불가 (결정난 승자는 ask=None) |
| underdog (양의 스큐) | 저유동 스프레드가 넓어 싸게 못 삼 (ask도 높음) |
| 마켓메이킹 | 거래 흐름이 없어 체결 안 됨 |

핵심: **베스트 케이스 = 본전, 워스트 = fat-tail 손실(음의 스큐).** 통계적으로 선 양의 엣지 0.
상세 연구 로그: crypto-backtester `src/polymarket/late_convergence/research/RESEARCH_LOG.md` (#01~05).

## 구성 (보존된 자산)
- `late_convergence/`, `pair_hedge/`, `bayesian_fomc/`, `latency_snipe/` — 전략 모듈 (전부 enabled:false)
- `latency_snipe/` — 마지막 실험. signal/engine/config + paper 사이징($100 시드, $2 고정)
- 대시보드: `/quant/polymarket/latency/dashboard` — scan-feed(시도 로그) + 진입가버킷 엣지 + $100 paper 포트폴리오
- `_data/client.py` `fetch_book` — 오더북(best_ask/best_bid+size)
- GCP/GCE 배포물 → `legacy/gcp/` 로 아카이브 (워크플로 비활성화). GCP 빌링 off로 인스턴스 이미 정지.

## 마스터 스위치
각 전략 `config.yaml` 의 `enabled`. runner는 하나라도 enabled거나 LIVE면 기동(`_any_strategy_enabled`).
지금은 전부 false → polymarket 루프 dormant. 재활성화하려면 해당 config enabled:true.
