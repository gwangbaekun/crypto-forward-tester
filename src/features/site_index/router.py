"""
사이트 인덱스 — 루트(`/`)에서 모든 페이지로 이동.

라우트를 하드코딩하지 않고 `app.routes` 에서 HTMLResponse GET 라우트를 실시간으로
긁어온다. 새 전략을 추가하면 `router_registry` 가 라우터를 등록하는 순간
인덱스에도 자동으로 나타난다 (목록을 따로 관리할 필요 없음).

GET /                 → 인덱스 HTML
GET /api/site-index   → 인덱스 JSON (역할에 따라 필터링됨)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.responses import HTMLResponse as _HTMLResponse

from common.utils import render_template
from features.auth.policy import GUEST_DASHBOARD_PATHS

router = APIRouter()

# 인덱스에 띄우지 않을 경로 (이미 로그인했거나 인덱스 자기 자신)
_HIDDEN = {"/", "/login", "/logout"}

# admin 에게만 보이는 경로
_ADMIN_ONLY = {"/admin/access", "/admin/strategies"}

_SECTION_ORDER = ["strategy", "polymarket", "logs", "admin"]
_SECTION_META = {
    "strategy":   {"title": "전략 대시보드", "desc": "실시간 상태 · 포지션 · 성과"},
    "polymarket": {"title": "Polymarket",   "desc": "예측시장 · 차익 · 로테이션"},
    "logs":       {"title": "시그널 로그",   "desc": "전략별 시그널 발생 이력"},
    "admin":      {"title": "관리",          "desc": "admin 전용"},
}


def _iter_routes(node: Any, prefix: str = "", _depth: int = 0):
    """
    (전체경로, route) 를 재귀적으로 뽑는다.

    FastAPI 버전에 따라 `app.routes` 구조가 다르다:
      - ~0.11x : include_router 가 하위 라우트를 app.routes 에 평탄화해서 넣음
      - 0.13x~ : `_IncludedRouter` 래퍼만 남고 실제 라우트는
                 `.original_router.routes` 안에 중첩됨 (+ include 시점 prefix 는
                 `.include_context.prefix` 에 따로 보관)

    평탄/중첩 둘 다 처리해야 로컬(구버전)과 컨테이너(신버전)에서 같게 동작한다.
    """
    if _depth > 10:  # 방어: 비정상 중첩에서 무한 재귀 방지
        return
    for route in getattr(node, "routes", None) or []:
        original = getattr(route, "original_router", None)
        if original is not None:
            ctx = getattr(route, "include_context", None)
            yield from _iter_routes(original, prefix + (getattr(ctx, "prefix", "") or ""), _depth + 1)
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        yield prefix + path, route


def _is_html_route(path: str, route: Any) -> bool:
    methods = getattr(route, "methods", None)
    if not methods or "GET" not in methods:
        return False
    if "{" in path:
        return False  # path 파라미터가 있는 라우트(/a/{code})는 인덱스 대상 아님
    rc = getattr(route, "response_class", None)
    if rc is None:
        return False
    return rc is _HTMLResponse or getattr(rc, "media_type", "") == "text/html"


def _master_by_base() -> Dict[str, Dict[str, Any]]:
    """base_strategy(=URL 세그먼트) → 설정. 같은 base 를 여러 키가 쓰면 첫 번째만."""
    try:
        from features.strategy.common.config_loader import get_master_config
        master = get_master_config() or {}
    except Exception:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for key, cfg in master.items():
        if not isinstance(cfg, dict):
            continue
        base = str(cfg.get("base_strategy") or key)
        out.setdefault(base, {**cfg, "_key": key})
    return out


def _prettify(segment: str) -> str:
    return segment.replace("_", " ").replace("-", " ").title()


def _classify(path: str) -> str:
    if path in _ADMIN_ONLY:
        return "admin"
    if path.startswith("/quant/polymarket/"):
        return "polymarket"
    if path.endswith("/signal/logs/view"):
        return "logs"
    return "strategy"


def _describe(path: str, name: str, master: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """라우트 하나 → 인덱스 항목."""
    item: Dict[str, Any] = {
        "path": path,
        "section": _classify(path),
        "title": _prettify(name or path),
        "emoji": "",
        "subtitle": "",
        "enabled": None,
        "monitoring": None,
        "symbol": "",
        "timeframes": [],
    }

    if path in _ADMIN_ONLY:
        meta = {
            "/admin/access":     ("🔑", "접근 패스", "게스트 코드 발급 · 연장 · 폐기"),
            "/admin/strategies": ("⚙️", "전략 설정", "strategies_master.yaml (read-only)"),
        }[path]
        item.update(emoji=meta[0], title=meta[1], subtitle=meta[2])
        return item

    parts = [p for p in path.split("/") if p]
    base = parts[1] if len(parts) > 1 and parts[0] == "quant" else ""
    cfg = master.get(base, {})

    if cfg:
        item["emoji"] = str(cfg.get("emoji") or "")
        item["title"] = str(cfg.get("label") or _prettify(base))
        item["enabled"] = bool(cfg.get("enabled"))
        item["monitoring"] = bool(cfg.get("monitoring"))
        item["symbol"] = str(cfg.get("symbol") or "")
        tfs = cfg.get("timeframes")
        item["timeframes"] = [str(t) for t in tfs] if isinstance(tfs, list) else []
    elif base:
        item["title"] = _prettify(base)

    # 같은 전략의 보조 페이지 구분
    tail = "/".join(parts[2:]) if len(parts) > 2 else ""
    if tail == "signal/logs/view":
        item["subtitle"] = "시그널 로그"
    elif tail and tail != "dashboard":
        # 하위 페이지는 제목에 합친다 — 같은 섹션에 동일 제목이 나란히 뜨면
        # (Polymarket ×5, Value Scan ×2) 어느 걸 눌러야 할지 알 수 없다.
        item["title"] = f"{item['title']} · {_prettify(tail.replace('/dashboard', ''))}"

    if not item["emoji"]:
        item["emoji"] = {"logs": "📜", "polymarket": "🎲"}.get(item["section"], "📊")
    return item


def build_index(app: Any, role: str) -> List[Dict[str, Any]]:
    """역할에 맞는 인덱스 항목 목록. guest 는 admin 페이지를 못 본다."""
    master = _master_by_base()
    seen: set = set()
    items: List[Dict[str, Any]] = []

    for path, route in _iter_routes(app):
        if not _is_html_route(path, route):
            continue
        if path in _HIDDEN or path in seen:
            continue
        if path in _ADMIN_ONLY and role != "admin":
            continue
        if role == "guest" and path not in GUEST_DASHBOARD_PATHS:
            continue
        seen.add(path)
        items.append(_describe(path, getattr(route, "name", ""), master))

    items.sort(key=lambda i: (
        _SECTION_ORDER.index(i["section"]) if i["section"] in _SECTION_ORDER else 99,
        not bool(i["enabled"]),          # enabled 우선
        i["title"].lower(),
        i["path"],
    ))
    return items


def _role_of(request: Request) -> str:
    return str(getattr(request.state, "auth_role", "guest") or "guest")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def site_index(request: Request):
    role = _role_of(request)
    items = build_index(request.app, role)

    sections = []
    for key in _SECTION_ORDER:
        group = [i for i in items if i["section"] == key]
        if group:
            sections.append({**_SECTION_META[key], "key": key, "items": group})

    enabled = sum(1 for i in items if i.get("enabled"))
    return HTMLResponse(render_template(
        "site_index.html",
        sections=sections,
        role=role,
        total_pages=len(items),
        enabled_count=enabled,
    ))


@router.get("/api/site-index")
async def api_site_index(request: Request):
    role = _role_of(request)
    return JSONResponse({"ok": True, "role": role, "items": build_index(request.app, role)})
