from __future__ import annotations

import datetime as dt
import ftplib
import io
import re
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

HOST = "ftp.cmegroup.com"
REMOTE_DIR = "/daily_volume"
NAME_RE = re.compile(r"daily_volume_(\d{4})(\d{2})(\d{2})\.xlsx$")
DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
SHEET_HINT = "by Product"
SHEET_SKIP = "EUROPE"
MDTM_TZ = ZoneInfo("America/Chicago")
BTC_PER_CONTRACT = {("BTC", "F", "BITCOIN FUTURES"): 5.0, ("MBT", "F", "MICRO BITCOIN FUTURES"): 0.1}


def connect():
    ftp = ftplib.FTP(HOST, timeout=60)
    ftp.login()
    ftp.cwd(REMOTE_DIR)
    return ftp


def listing(ftp):
    names = []
    ftp.retrlines("NLST", names.append)
    out = sorted(n.split("/")[-1] for n in names if NAME_RE.search(n))
    return out


def mdtm(ftp, name):
    parts = ftp.sendcmd("MDTM " + name).split()
    if len(parts) < 2 or not parts[1][:14].isdigit():
        raise ValueError(f"{name}: MDTM 응답을 읽을 수 없다: {parts}")
    local = dt.datetime.strptime(parts[1][:14], "%Y%m%d%H%M%S").replace(tzinfo=MDTM_TZ)
    out = local.astimezone(dt.timezone.utc)
    return out


def download(ftp, name):
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {name}", buf.write)
    out = buf.getvalue()
    return out


def filename_date(name):
    m = NAME_RE.search(name)
    out = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return out


def clean(v):
    if v is None:
        return None
    s = str(v).replace("\n", " ").strip()
    return s or None


def sheet(wb, name):
    ws = wb[name]
    ws.reset_dimensions()
    return ws


def pick_sheet(wb):
    for sn in wb.sheetnames:
        if SHEET_HINT in sn and SHEET_SKIP not in sn.upper():
            return sn
    raise ValueError(f"'{SHEET_HINT}' sheet 가 없다: {wb.sheetnames}")


def read_trade_date(wb):
    for sn in wb.sheetnames:
        for row in sheet(wb, sn).iter_rows(min_row=1, max_row=10, values_only=True):
            for v in row:
                s = clean(v)
                m = DATE_RE.search(s) if s and "Trade Date" in s else None
                if m:
                    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    year = yy + 2000 if yy < 100 else yy
                    out = dt.date(year, mm, dd)
                    return out
    raise ValueError("Trade Date 가 없다")


def find_columns(header):
    idx = {}
    for i, name in enumerate(header):
        if not name:
            continue
        low = name.lower()
        if "commodity indicator" in low:
            idx["symbol"] = i
        elif "future/option indicator" in low:
            idx["fo"] = i
        elif "product description" in low:
            idx["product"] = i
        elif "open" in low and "interest" in low:
            idx["open_interest"] = i
    missing = {"symbol", "fo", "product", "open_interest"} - idx.keys()
    if missing:
        raise ValueError(f"열이 없다: {sorted(missing)}")
    return idx


def open_interest_btc(content, name):
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        trade_date = read_trade_date(wb)
        if trade_date != filename_date(name):
            raise ValueError(f"{name}: 파일 이름의 날짜와 안의 Trade Date {trade_date} 가 다르다")
        cols = None
        found = {}
        for row in sheet(wb, pick_sheet(wb)).iter_rows(values_only=True):
            vals = [clean(v) for v in row]
            if cols is None:
                if any(v and "commodity indicator" in v.lower() for v in vals):
                    cols = find_columns(vals)
                continue
            if max(cols.values()) >= len(vals):
                continue
            key = (vals[cols["symbol"]], vals[cols["fo"]], vals[cols["product"]])
            if key in BTC_PER_CONTRACT:
                if key in found:
                    raise ValueError(f"{name}: {key} 행이 2개다")
                found[key] = float(row[cols["open_interest"]])
    finally:
        wb.close()
    missing = BTC_PER_CONTRACT.keys() - found.keys()
    if missing:
        raise ValueError(f"{name}: 행이 없다: {sorted(missing)}")
    total = sum(found[k] * BTC_PER_CONTRACT[k] for k in BTC_PER_CONTRACT)
    return trade_date, total


def latest(count, known):
    ftp = connect()
    try:
        names = listing(ftp)[-count:]
        if len(names) < count:
            raise ValueError(f"FTP 에 파일이 {len(names)}개뿐이다. {count}개가 필요하다")
        rows = []
        for name in names:
            if name in known:
                rows.append(known[name])
                continue
            stamp = mdtm(ftp, name)
            trade_date, total = open_interest_btc(download(ftp, name), name)
            rows.append({"name": name, "trade_date": trade_date.isoformat(), "available_at": int(stamp.timestamp()),
                         "open_interest_btc": total})
    finally:
        ftp.quit()
    rows.sort(key=lambda r: r["available_at"])
    return rows
