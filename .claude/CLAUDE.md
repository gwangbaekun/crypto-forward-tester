# forwardtest_quant

원장이지 알림봇이 아니다. 신호는 발생 시점에 기록하고, 사후 발견분은 따로 표시한다.

## 작업 전 반드시 읽을 문서

문서는 전부 `.claude/` 안에 있다. 여기 없으면 규칙이 아니다.

| 작업 | 파일 |
|---|---|
| 스택·레이아웃·데이터 흐름·실행 | `ARCHITECTURE.md` |
| 전략 생성·수정, backtest 동기화 | `STRATEGY_RULES.md` |
| 현재 전략 현황 | `STRATEGIES.md` |
| 원장 무결성·측정 프로토콜 | `LEDGER.md` |
| 접근 게이트·공개 링크 | `ACCESS.md` |
| Polymarket (fade · logic_arb) | `POLYMARKET.md` |

읽었다고 가정하지 말고 실제로 읽는다.

## 항상 적용

- 백테스트가 정본이다. 전략 규격·최적화 계약은 `backtest_quant/.claude/STRATEGY_RULES.md`
- `signal.py` 는 backtest 와 100% 동일하다. 한쪽을 고치면 양쪽을 고친다
- 라이브 exit tick 은 `intrabar=True` — ratchet 을 건너뛴다 (`STRATEGY_RULES.md` §2)
- 전략 등록은 `strategies_master.yaml` 한 곳. 라우트·사이트 인덱스는 자동으로 따라온다
- 데이터가 없으면 에러를 낸다. fallback 임의 생성 금지
- 코드에 주석 금지. 대시보드에 설명 문구 금지
- 지시하지 않은 것을 임의로 추가하지 않는다. 필요하면 먼저 묻는다

## 문서 밖 (읽기 전용 참조)

`.claude/` 는 **규칙**이다. 무엇을 알아냈는지는 레포 밖 `DOCS/` 에 있다.

| 내용 | 위치 |
|---|---|
| 무엇을 만들고 무엇을 안 만드나 | `../../DOCS/전략_로드맵.md` |
| 백테스트 검정 기록 | `../../DOCS/전략_검증기록.md` |
| 시스템 전체 진단 | `../../DOCS/종합진단_*.md` |
| 실행법·env·API 표 | `../README.md` |
| 전략별 상세 | `src/features/strategy/<id>/README.md` |
| Oracle VM 러너 | `oracle/deploy/RUNNER_SETUP.md` |
