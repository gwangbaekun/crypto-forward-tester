"""드로다운 매수 포워드 테스트 — 수동 실행기.

    PYTHONPATH=src python scripts/drawdown_signal_alert.py          # 실행 + 알림
    PYTHONPATH=src python scripts/drawdown_signal_alert.py --dry    # 알림 없이 갱신만

평상시에는 앱 안의 스케줄러(`_drawdown_scheduler`, main.py lifespan)가 하루 1회 돌린다.
Railway 배포본에서 그대로 동작하므로 cron 이 필요 없다. 이 스크립트는 수동 확인·디버깅용.

로직은 전부 `features.strategy.drawdown_signal.engine` 에 있다 — 스케줄러와 같은 코드를 쓴다.
"""
from __future__ import annotations

import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from features.strategy.drawdown_signal.engine import FILL_LEVELS, UNIVERSE, run  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    r = run(dry="--dry" in sys.argv)

    lv = " / ".join(f"{v * 100:.0f}%" for v in FILL_LEVELS)
    print(f"원장 시작 {r['started_at']} · 유니버스 {len(UNIVERSE)}종목 · 래더 {lv}")
    print(f"전체 {r['total']}건 (열림 {r['open']} · "
          f"out-of-sample {r['out_of_sample']} · backfilled {r['backfilled']})")
    if r["errors"]:
        print(f"  ⚠️ 가격 조회 실패: {', '.join(r['errors'])}")
    for e in r["episodes"]:
        tag = " [backfilled]" if e["backfilled"] else ""
        print(f"  {e['name']:<12} {e['symbol']:<11} {e['signal_date']} "
              f"dd{e['entry_dd_pct']:+6.1f}% 체결{e['n_fills']} 평단 {e['avg_cost']:>11,.2f} "
              f"→ {e['ret_pct']:+7.2f}% (MAE {e['mae_pct']:+.2f}%, 물밑 {e['underwater_days']}d){tag}")
    print(f"신규 신호 {len(r['new_signals'])}건, 청산 {len(r['closed'])}건")
