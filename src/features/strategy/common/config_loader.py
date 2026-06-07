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
from typing import Any, Dict, Optional

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


def get_combine_group(strategy_id: str) -> Optional[str]:
    """전략이 어느 combine 그룹의 members에 속하는지 역참조. 없으면 None.
    (combine 엔트리의 members 목록이 단일 출처 — 멤버 블록엔 표시 안 함)"""
    for key, cfg in (get_master_config() or {}).items():
        if isinstance(cfg, dict) and strategy_id in (cfg.get("members") or []):
            return key
    return None


def is_combine_enabled(combine_tag: str) -> bool:
    """combine 운영 스위치. enabled=false면 주문 fan-out 안 함."""
    cfg = (get_master_config() or {}).get(combine_tag, {})
    return bool(isinstance(cfg, dict) and cfg.get("enabled", False))


def get_combine_members(combine_tag: str) -> list:
    """combine 그룹에 묶인 멤버 전략 id 목록."""
    cfg = (get_master_config() or {}).get(combine_tag, {})
    return list(cfg.get("members") or []) if isinstance(cfg, dict) else []


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


def is_ctrader_live_enabled(strategy_id: str) -> bool:
    """
    해당 전략이 cTrader(FTMO) 실주문 대상인지 여부.
    strategies_master.yaml 에서 ctrader_live: true 인 전략만 활성.
    """
    strat = get_master_config().get(strategy_id)
    return isinstance(strat, dict) and bool(strat.get("ctrader_live", False))


def get_ctrader_config(strategy_id: str) -> dict:
    """
    전략별 cTrader 계좌 오버라이드 설정 반환.
    ctrader_accounts + ctrader_mode 구조가 있으면 mode에 맞는 계좌를 선택.
    없으면 flat 필드 그대로 사용 (하위 호환).
    """
    strat = get_master_config().get(strategy_id)
    if not isinstance(strat, dict):
        return {}
    accounts = strat.get("ctrader_accounts")
    if isinstance(accounts, dict):
        mode = strat.get("ctrader_mode", "demo")
        account_cfg = accounts.get(mode)
        if not isinstance(account_cfg, dict):
            raise ValueError(
                f"[{strategy_id}] ctrader_mode='{mode}' 이지만 "
                f"ctrader_accounts.{mode} 설정이 없습니다."
            )
        result = dict(account_cfg)
        if "ctrader_lot_size" in strat:
            result["ctrader_lot_size"] = strat["ctrader_lot_size"]
        return result
    return {
        k: strat[k]
        for k in ("ctrader_account_id", "ctrader_env", "ctrader_symbol_id", "ctrader_lot_size")
        if k in strat
    }


def is_alerts_enabled(strategy_id: str) -> bool:
    """하위 호환 공통 알림 게이트 (Telegram/Discord 중 하나라도 켜져 있으면 true)."""
    return is_telegram_alerts_enabled(strategy_id) or is_discord_alerts_enabled(strategy_id)


def is_discord_alerts_enabled(strategy_id: str) -> bool:
    """
    Discord 알림 여부.
    - strategies_master.yaml 의 discord_alerts 사용
    - 값이 없으면 telegram_alerts 값을 상속 (하위 호환)
    """
    strat = get_master_config().get(strategy_id)
    if not isinstance(strat, dict):
        return False
    if "discord_alerts" in strat:
        return bool(strat.get("discord_alerts"))
    return bool(strat.get("telegram_alerts", False))


def is_telegram_alerts_enabled(strategy_id: str) -> bool:
    """
    Telegram 알림 여부.
    - strategies_master.yaml 의 telegram_alerts 사용
    - binance_live: true 이면 기본적으로 true (실주문 상태 동기화 알림 보장)
    """
    strat = get_master_config().get(strategy_id)
    if not isinstance(strat, dict):
        return False
    if strat.get("telegram_alerts"):
        return True
    return bool(strat.get("binance_live", False))
