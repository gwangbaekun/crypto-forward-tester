"""
strategies_master.yaml 읽기 전용 뷰어 (admin 전용).

GET  /admin/strategies       → 뷰어 HTML
GET  /api/strategies-master  → YAML 텍스트 + 전략별 메타 + 바인딩된 페이지 URL

쓰기 경로는 없다. 저장은 컨테이너 파일시스템에만 남아 재배포 시 원복되고,
STRATEGY_REGISTRY / 라우터 등록은 임포트 시점에 고정이라 재시작 없이는 반영도
안 됐다. 설정 변경은 리포의 YAML 을 고쳐 커밋하는 경로만 유효하다.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template
from features.site_index.router import _is_html_route, _iter_routes

router = APIRouter()

_MASTER_PATH = pathlib.Path(__file__).resolve().parent / "strategies_master.yaml"
_MASTER_REL = "src/features/strategy/common/strategies_master.yaml"

_PAGE_LABELS = {
    "dashboard": "Dashboard",
    "signal/logs/view": "Signal Logs",
}


def _require_admin(request: Request) -> Optional[JSONResponse]:
    if getattr(request.state, "auth_role", None) != "admin":
        return JSONResponse({"ok": False, "error": "admin only"}, status_code=403)
    return None


def _prettify(text: str) -> str:
    return text.replace("_", " ").replace("-", " ").replace("/", " · ").title()


def _pages_by_base(app: Any) -> Dict[str, List[Dict[str, str]]]:
    """`/quant/{base}/…` HTML 라우트를 base 별로 모은다."""
    out: Dict[str, List[Dict[str, str]]] = {}
    for path, route in _iter_routes(app):
        if not _is_html_route(path, route):
            continue
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3 or parts[0] != "quant":
            continue
        base = parts[1]
        tail = "/".join(parts[2:])
        bucket = out.setdefault(base, [])
        if any(p["path"] == path for p in bucket):
            continue
        bucket.append({
            "path": path,
            "label": _PAGE_LABELS.get(tail) or _prettify(tail),
            "primary": tail == "dashboard",
        })
    for bucket in out.values():
        bucket.sort(key=lambda p: (not p["primary"], p["path"]))
    return out


def _strategies(master: Dict[str, Any], pages: Dict[str, List[Dict[str, str]]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for key, cfg in master.items():
        if not isinstance(cfg, dict):
            continue
        base = str(cfg.get("base_strategy") or key)
        tfs = cfg.get("timeframes")
        items.append({
            "key": key,
            "base": base,
            "label": str(cfg.get("label") or _prettify(key)),
            "emoji": str(cfg.get("emoji") or ""),
            "enabled": bool(cfg.get("enabled", False)),
            "monitoring": bool(cfg.get("monitoring", False)),
            "symbol": str(cfg.get("symbol") or ""),
            "timeframes": [str(t) for t in tfs] if isinstance(tfs, list) else [],
            "binance_live": bool(cfg.get("binance_live", False)),
            "ctrader_live": bool(cfg.get("ctrader_live", False)),
            "pages": pages.get(base, []),
        })
    items.sort(key=lambda s: (not s["enabled"], s["label"].lower(), s["key"]))
    return items


@router.get("/admin/strategies", response_class=HTMLResponse, include_in_schema=False)
async def strategies_viewer(request: Request):
    denied = _require_admin(request)
    if denied is not None:
        return denied
    return HTMLResponse(render_template("strategies_master.html"))


@router.get("/api/strategies-master")
async def get_master(request: Request):
    denied = _require_admin(request)
    if denied is not None:
        return denied

    try:
        content = _MASTER_PATH.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content) or {}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    if not isinstance(parsed, dict):
        return JSONResponse({"ok": False, "error": "YAML root must be a mapping"}, status_code=500)

    items = _strategies(parsed, _pages_by_base(request.app))
    return JSONResponse({
        "ok": True,
        "content": content,
        "path": _MASTER_REL,
        "strategies": items,
        "enabled": sum(1 for s in items if s["enabled"]),
        "total": len(items),
    })
