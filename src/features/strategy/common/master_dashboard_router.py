"""
Root dashboard — strategies_master.yaml 브라우저 에디터.

GET  /                           → 에디터 HTML
GET  /api/strategies-master      → 현재 YAML 텍스트 반환
POST /api/strategies-master      → YAML 저장 후 캐시 초기화
"""
from __future__ import annotations

import pathlib

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from common.utils import render_template

router = APIRouter()

_MASTER_PATH = pathlib.Path(__file__).resolve().parent / "strategies_master.yaml"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root_dashboard(_: Request):
    return HTMLResponse(render_template("strategies_master.html"))


@router.get("/api/strategies-master")
async def get_master():
    try:
        content = _MASTER_PATH.read_text(encoding="utf-8")
        return JSONResponse({"ok": True, "content": content})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/api/strategies-master")
async def save_master(request: Request):
    try:
        body = await request.json()
        content: str = body.get("content", "")
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    try:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise ValueError("YAML root must be a mapping")
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"YAML parse error: {exc}"}, status_code=422)

    try:
        _MASTER_PATH.write_text(content, encoding="utf-8")
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"write error: {exc}"}, status_code=500)

    # lru_cache 초기화 — 다음 전략 틱부터 새 설정 반영
    try:
        from features.strategy.common.config_loader import get_master_config
        get_master_config.cache_clear()
    except Exception:
        pass

    strategy_count = len([k for k, v in parsed.items() if isinstance(v, dict)])
    enabled_count = len([k for k, v in parsed.items() if isinstance(v, dict) and v.get("enabled")])
    return JSONResponse({
        "ok": True,
        "strategies": strategy_count,
        "enabled": enabled_count,
    })
