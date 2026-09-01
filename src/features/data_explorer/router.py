"""수집 데이터 탐색기 — "내가 지금 뭘 모으고 있는가"를 눈으로 보는 페이지.

옵션 체인은 캔들이 아니다. 캔들은 (시각 → OHLC) 한 줄이지만, 체인 스냅샷 하나는
**그 순간 살아있는 모든 계약의 목록**이다:

    한 행 = (만기, 행사가, 콜/풋) + 그 계약의 호가·미결제약정·그릭스

그래서 SPY 하나에 1만 4천 행이 나온다. 이 페이지는 그 구조를 그대로 보여준다.
읽기 전용, 집계는 전부 SQL 한 방 — 무거운 계산 없음.
"""
from __future__ import annotations

import logging
import os

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, text

from common.utils import render_template

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data", tags=["data_explorer"])
_engine = None


def _eng():
    global _engine
    if _engine is None:
        url = os.getenv("DATABASE_URL", "postgresql://btc:btc@localhost:5432/btc_forwardtest")
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


@router.get("/explorer", response_class=HTMLResponse)
async def explorer():
    return render_template("data_explorer.html")


@router.get("/api/inventory", response_class=JSONResponse)
async def inventory():
    """어떤 테이블에 몇 행이, 언제부터 언제까지 쌓여 있나."""
    specs = [
        ("us_options_chain", "snapshot_ts", "미국 옵션 체인 (Cboe, 하루 1회)"),
        ("deribit_chain", "snapshot_ts", "Deribit BTC/ETH 옵션 체인 (15분)"),
        ("us_etf_daily", "date", "미국 ETF 일봉"),
        ("binance_liquidations", "trade_ts", "바이낸스 강제청산 스트림"),
        ("binance_open_interest", "ts", "바이낸스 선물 미결제약정 (5분)"),
        ("binance_funding", "funding_ts", "바이낸스 펀딩비"),
    ]
    out = []
    with _eng().connect() as c:
        for tbl, tcol, label in specs:
            try:
                r = c.execute(text(
                    f"SELECT count(*) n, min({tcol})::text lo, max({tcol})::text hi FROM {tbl}"
                )).fetchone()
                out.append({"table": tbl, "label": label, "rows": r[0], "from": r[1], "to": r[2]})
            except Exception as e:
                out.append({"table": tbl, "label": label, "error": str(e)[:90]})
    return JSONResponse({"tables": out})


@router.get("/api/chain-snapshot", response_class=JSONResponse)
async def chain_snapshot(underlying: str = "SPY"):
    """가장 최근 스냅샷 하나의 구조 — 캔들과 뭐가 다른지 보여주는 핵심."""
    eng = _eng()
    with eng.connect() as c:
        ts = c.execute(text(
            "SELECT max(snapshot_ts) FROM us_options_chain WHERE underlying=:u"
        ), {"u": underlying.upper()}).scalar()
    if ts is None:
        return JSONResponse({"error": f"{underlying} 스냅샷 없음"}, status_code=404)

    q = text(
        "SELECT expiry, strike, option_type, bid, ask, last_trade_price, iv, delta, gamma, "
        "       open_interest, volume, underlying_price, option "
        "FROM us_options_chain WHERE underlying=:u AND snapshot_ts=:t"
    )
    df = pd.read_sql(q, eng, params={"u": underlying.upper(), "t": ts})
    spot = float(df.underlying_price.iloc[0])

    by_exp = (df.groupby("expiry")
                .agg(contracts=("strike", "size"), oi=("open_interest", "sum"),
                     volume=("volume", "sum"))
                .reset_index().sort_values("expiry").head(20))
    by_exp["expiry"] = by_exp.expiry.astype(str)

    near = df[(df.strike > spot * 0.94) & (df.strike < spot * 1.06)]
    prof = (near.pivot_table(index="strike", columns="option_type",
                             values="open_interest", aggfunc="sum")
                .fillna(0).reset_index().sort_values("strike"))
    prof.columns = [str(x) for x in prof.columns]

    sample = (df[(df.strike > spot * 0.99) & (df.strike < spot * 1.01)]
              .sort_values(["expiry", "strike", "option_type"]).head(12))
    sample["expiry"] = sample.expiry.astype(str)

    return JSONResponse({
        "underlying": underlying.upper(), "snapshot_ts": str(ts), "spot": spot,
        "n_contracts": int(len(df)), "n_expiries": int(df.expiry.nunique()),
        "n_strikes": int(df.strike.nunique()),
        "by_expiry": by_exp.to_dict("records"),
        "oi_profile": prof.to_dict("records"),
        "sample_rows": sample.round(4).to_dict("records"),
    })


@router.get("/api/deribit-snapshot", response_class=JSONResponse)
async def deribit_snapshot(currency: str = "BTC"):
    """같은 구조의 크립토 버전 — 이쪽은 15분마다 쌓인다."""
    eng = _eng()
    with eng.connect() as c:
        ts = c.execute(text(
            "SELECT max(snapshot_ts) FROM deribit_chain WHERE currency=:c"
        ), {"c": currency.upper()}).scalar()
        n_today = c.execute(text(
            "SELECT count(DISTINCT snapshot_ts) FROM deribit_chain "
            "WHERE currency=:c AND snapshot_ts::date = (SELECT max(snapshot_ts)::date FROM deribit_chain)"
        ), {"c": currency.upper()}).scalar()
    if ts is None:
        return JSONResponse({"error": "deribit 스냅샷 없음"}, status_code=404)

    df = pd.read_sql(text(
        "SELECT expiry, strike, option_type, mark_iv, mark_price, open_interest, underlying_price "
        "FROM deribit_chain WHERE currency=:c AND snapshot_ts=:t"
    ), eng, params={"c": currency.upper(), "t": ts})
    spot = float(df.underlying_price.iloc[0])

    by_exp = (df.groupby("expiry")
                .agg(contracts=("strike", "size"), oi=("open_interest", "sum"))
                .reset_index().sort_values("expiry").head(20))
    by_exp["expiry"] = by_exp.expiry.astype(str)

    # IV 스마일: 최근 만기의 행사가별 mark_iv — "캔들이 아니다"를 가장 잘 보여주는 그림
    front = sorted(df.expiry.unique())[0]
    smile = (df[df.expiry == front][["strike", "option_type", "mark_iv"]]
             .sort_values("strike"))
    return JSONResponse({
        "currency": currency.upper(), "snapshot_ts": str(ts), "spot": spot,
        "snapshots_today": int(n_today or 0),
        "n_contracts": int(len(df)), "n_expiries": int(df.expiry.nunique()),
        "by_expiry": by_exp.to_dict("records"),
        "front_expiry": str(front),
        "smile": smile.round(3).to_dict("records"),
    })
