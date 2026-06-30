"""
ctrader_accounts.yaml 로더.

단일 진실 소스: src/common/ctrader_accounts.yaml
enabled: true 인 계좌만 앱 시작 시 연결 + combine fan-out 대상.
"""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any, Dict

import yaml

_CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "ctrader_accounts.yaml"


@lru_cache(maxsize=1)
def load_ctrader_accounts() -> Dict[str, Any]:
    """전체 계좌 목록 반환 (프로세스당 1회 캐시)."""
    if not _CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}


def get_all_accounts() -> Dict[str, Dict[str, Any]]:
    """firm_key → account_cfg 전체 dict."""
    return (load_ctrader_accounts().get("accounts") or {})


def get_enabled_accounts() -> Dict[str, Dict[str, Any]]:
    """enabled: true 이고 account_id > 0 인 계좌만 반환."""
    return {
        k: v
        for k, v in get_all_accounts().items()
        if isinstance(v, dict) and v.get("enabled") and int(v.get("account_id") or 0) > 0
    }
