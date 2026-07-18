"""
7-A 공모주 균등배정 — 청약 알림 (텔레그램)

38.co.kr 두 페이지를 종목명으로 병합해서 판단한다:
  o=r1  수요예측 결과 (경쟁률/의무보유확약) — 청약 시작 전에 발표됨. 필터의 핵심 데이터.
  o=k   청약 일정 (청약일 범위, 주관사)     — 실제로 언제 청약하면 되는지.
필터(경쟁률>=1000:1, 확약>=10%, 스팩 제외) 통과/미달이 결정되는 즉시(=수요예측 결과 발표 시)
텔레그램으로 1회 알림. data/ipo_alerted.json 으로 중복 방지.

실행: 매일 1회 (cron 예시 하단 참고)
    PYTHONPATH=src python scripts/ipo_equal_alloc_alert.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from features.notifications.telegram_service import TelegramService  # noqa: E402

MIN_COMPETITION = 1000.0   # 기관 수요예측 경쟁률 1000:1 이상
MIN_LOCKUP = 10.0          # 의무보유확약 10% 이상
STATE_FILE = pathlib.Path(__file__).resolve().parents[1] / "data" / "ipo_alerted.json"
H = {"User-Agent": "Mozilla/5.0"}


def _num(x: str):
    x = re.sub(r"[,%\s]|:1$", "", str(x))
    try:
        return float(x)
    except ValueError:
        return None


def _rows(o: str) -> list[list[str]]:
    r = requests.get("http://www.38.co.kr/html/fund/index.htm", params={"o": o}, headers=H, timeout=15)
    r.encoding = "euc-kr"
    out = []
    for rw in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        if not re.search(r"\d{4}\.\d{2}\.\d{2}", rw):
            continue
        out.append([re.sub(r"<[^>]+>", "", x).strip().replace("\xa0", "").replace("&nbsp;", "")
                   for x in re.findall(r"<td[^>]*>(.*?)</td>", rw, re.S)])
    return out


def fetch_deals() -> list[dict]:
    """o=r1(수요예측 결과) + o=k(청약일정) 을 종목명으로 병합."""
    # o=r1: [name, 발표일, band, 확정가, 공모금액, 경쟁률, 확약%, 주관사]
    demand = {}
    for c in _rows("r1"):
        if len(c) < 8:
            continue
        demand[c[0]] = {"price": _num(c[3]), "competition": _num(c[5]), "lockup": _num(c[6])}

    # o=k: [name, 청약일범위, 확정가(or '-'), band, 경쟁률(청약분, 무시), 주관사, _]
    deals = []
    for c in _rows("k"):
        if len(c) < 6:
            continue
        name = c[0]
        d = demand.get(name, {})
        deals.append({
            "name": name, "sub_date": c[1], "band": c[3], "underwriter": c[5],
            "price": d.get("price"), "competition": d.get("competition"), "lockup": d.get("lockup"),
            "is_spac": "스팩" in name,
        })
    return deals


def load_state() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_state(alerted: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(alerted), ensure_ascii=False, indent=1))


def main() -> None:
    deals = fetch_deals()
    alerted = load_state()
    tg = TelegramService()
    sent = 0

    for d in deals:
        key = f"{d['name']}:{d['sub_date']}"
        if key in alerted:
            continue
        if d["is_spac"] or d["competition"] is None or d["lockup"] is None:
            continue  # 스팩 제외, 수요예측 결과 미발표 딜은 다음 실행 때 재확인

        passed = d["competition"] >= MIN_COMPETITION and d["lockup"] >= MIN_LOCKUP
        emoji = "✅ 청약 추천" if passed else "❌ 필터 미달 (패스)"
        msg = (
            f"<b>[공모주 7-A] {emoji}</b>\n\n"
            f"종목: {d['name']}\n"
            f"청약일: {d['sub_date']}\n"
            f"확정공모가: {d['price']:,.0f}원 (밴드 {d['band']})\n"
            f"경쟁률: {d['competition']:.0f}:1  (기준 {MIN_COMPETITION:.0f}:1)\n"
            f"의무보유확약: {d['lockup']:.1f}%  (기준 {MIN_LOCKUP:.0f}%)\n"
            f"주관사: {d['underwriter']}"
        )
        ok, err = tg.send_message(msg)
        if ok:
            alerted.add(key)
            sent += 1
        else:
            print(f"전송 실패 ({d['name']}): {err}")

    save_state(alerted)
    print(f"청약일정 {len(deals)}건 확인, 신규 알림 {sent}건, 누적 알림완료 {len(alerted)}건")


if __name__ == "__main__":
    main()

# ------------------------------------------------------------------
# cron 등록 예시 (매일 오전 8시):
#   0 8 * * * cd /Users/home/Developer/T/forwardtest_quant && \
#     PYTHONPATH=src /usr/bin/python3 scripts/ipo_equal_alloc_alert.py >> logs/ipo_alert.log 2>&1
# ------------------------------------------------------------------
