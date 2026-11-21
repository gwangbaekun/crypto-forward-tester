from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

DATA_DIR = _PROJECT_ROOT / "data" / "value_forward"
SCANS_NASDAQ_DIR = DATA_DIR / "scans" / "nasdaq"
SCANS_KOSPI_DIR = DATA_DIR / "scans" / "kospi"
LAST_ACTIVITY_FILE = DATA_DIR / "last_activity.json"


def get_scan_dir(market: str) -> Path:
    if market == "nasdaq":
        return SCANS_NASDAQ_DIR
    if market == "kospi":
        return SCANS_KOSPI_DIR
    raise ValueError(f"unsupported market for scan dir: {market}")
