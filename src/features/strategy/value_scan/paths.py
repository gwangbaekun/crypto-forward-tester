"""Value Scan 데이터 경로 (json 스냅샷·스캔 로그)."""
from pathlib import Path

# src/features/strategy/value_scan/paths.py → repo root = parents[4]
_PROJECT_ROOT = Path(__file__).resolve().parents[4]

DATA_DIR = _PROJECT_ROOT / "data" / "value_forward"
POSITIONS_FILE = DATA_DIR / "positions.json"
HISTORY_FILE = DATA_DIR / "history.json"
SCANS_NASDAQ_DIR = DATA_DIR / "scans" / "nasdaq"
SCANS_KOSPI_DIR = DATA_DIR / "scans" / "kospi"
LAST_ACTIVITY_FILE = DATA_DIR / "last_activity.json"


def get_scan_dir(market: str) -> Path:
    """시장별 스캔 저장 디렉터리."""
    if market == "nasdaq":
        return SCANS_NASDAQ_DIR
    if market == "kospi":
        return SCANS_KOSPI_DIR
    raise ValueError(f"unsupported market for scan dir: {market}")
