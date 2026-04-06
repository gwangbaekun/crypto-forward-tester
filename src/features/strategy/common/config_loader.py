"""
common/config_loader.py

전략 공통 Config 로드 유틸리티.

각 전략의 config_loader.py 는 이 베이스를 래핑해 사용.
lru_cache 는 전략 별로 직접 적용 (path 별로 따로 캐싱해야 하므로).

사용 예:
    # renaissance/config_loader.py 에서
    from features.strategy.common.config_loader import load_strategy_config

    _CONFIG_PATH = pathlib.Path(__file__).parent / "config.yaml"

    @lru_cache(maxsize=1)
    def load_config():
        return load_strategy_config(_CONFIG_PATH)
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any, Dict

import yaml


def load_strategy_config(config_path: pathlib.Path) -> Dict[str, Any]:
    """
    전략 폴더의 config.yaml 을 읽어 dict 반환.
    파일이 없으면 빈 dict.
    """
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_section(config: Dict[str, Any], section: str, default: Any = None) -> Any:
    """config dict 에서 특정 섹션을 안전하게 추출. 없거나 None 이면 default."""
    val = config.get(section)
    if val is None:
        return default if default is not None else {}
    return val


def get_nested(config: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """중첩 키 안전 추출. get_nested(cfg, "zscore", "5m", "window") 식으로 사용."""
    cur: Any = config
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


# ── strategies_master.yaml — 단일 진실 소스 ─────────────────────────────────
# strategies_config.yaml 은 삭제됨. 모든 설정은 strategies_master.yaml 에서만.

_MASTER_PATH = pathlib.Path(__file__).resolve().parent / "strategies_master.yaml"


@lru_cache(maxsize=1)
def get_master_config() -> Dict[str, Any]:
    """
    strategies_master.yaml 전체 로드 (프로세스 당 1회 캐시).
    키: 전략 id → {enabled, monitoring, tick_interval, timeframes, tick, data_needs}
    재로드가 필요하면 get_master_config.cache_clear() 호출.
    """
    return load_strategy_config(_MASTER_PATH)


def get_strategies_config() -> Dict[str, Any]:
    """하위 호환용 alias. realtime_data_hub 등 기존 코드가 data_needs를 읽을 때 사용."""
    return get_master_config()


def get_enabled_strategies() -> Dict[str, Any]:
    """enabled: true 인 전략만 반환."""
    return {k: v for k, v in get_master_config().items() if isinstance(v, dict) and v.get("enabled", True)}


def is_monitoring_start_by_default(strategy_id: str) -> bool:
    """해당 전략의 대시보드에서 모니터링을 기본으로 시작할지 여부."""
    strat = get_master_config().get(strategy_id)
    if not isinstance(strat, dict):
        return True
    return bool(strat.get("monitoring", True))


def is_binance_live_enabled(strategy_id: str) -> bool:
    """
    해당 전략이 Binance 실주문(Executor) 대상인지 여부.
    strategies_master.yaml 에서 binance_live: true 로 설정된 전략만 활성.
    """
    strat = get_master_config().get(strategy_id)
    return isinstance(strat, dict) and bool(strat.get("binance_live", False))


def is_telegram_alerts_enabled(strategy_id: str) -> bool:
    """
    진입/청산 Telegram 알림 여부.
    - strategies_master.yaml 에 telegram_alerts: true
    - 또는 binance_live: true (실주문 전략은 알림 동기화와 함께 켜짐)
    전역 끄기: TELEGRAM_DISABLE=1
    """
    import os

    if os.environ.get("TELEGRAM_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return False
    strat = get_master_config().get(strategy_id)
    if not isinstance(strat, dict):
        return False
    if strat.get("telegram_alerts"):
        return True
    return bool(strat.get("binance_live", False))
