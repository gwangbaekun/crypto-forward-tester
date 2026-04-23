"""
Discord 알림 서비스 — Webhook 기반.

환경 변수 DISCORD_WEBHOOK_URL 로 설정.
Discord는 Markdown 포맷을 사용하므로 Telegram HTML을 변환해 전송.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

import httpx


def _html_to_discord_md(text: str) -> str:
    """Telegram HTML 태그 → Discord Markdown 변환."""
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"_\1_", text, flags=re.DOTALL)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return text


class DiscordService:
    """Discord Webhook 메시지 전송 서비스 (싱글톤)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _webhook_url(self, strategy_key: Optional[str] = None) -> str | None:
        if strategy_key:
            strategy_env_prefix = strategy_key.upper().replace("-", "_")
            strategy_webhook_key = f"{strategy_env_prefix}_DISCORD_WEBHOOK_URL"
            if os.environ.get(strategy_webhook_key):
                return os.environ.get(strategy_webhook_key)
            return None
        return os.environ.get("DISCORD_WEBHOOK_URL") or None

    def send_message(self, message: str, strategy_key: Optional[str] = None) -> Tuple[bool, str]:
        """HTML 메시지를 Discord Markdown으로 변환 후 Webhook 전송.

        Returns:
            (성공 여부, 에러 메시지)
        """
        webhook_url = self._webhook_url(strategy_key)
        if not webhook_url:
            if strategy_key:
                strategy_env_prefix = strategy_key.upper().replace("-", "_")
                strategy_webhook_key = f"{strategy_env_prefix}_DISCORD_WEBHOOK_URL"
                return False, f"No webhook configured. Expected `{strategy_webhook_key}`."
            return False, "DISCORD_WEBHOOK_URL is not configured."

        content = _html_to_discord_md(message)
        if len(content) > 2000:
            content = content[:1997] + "…"

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(webhook_url, json={"content": content})

                if response.status_code in (200, 204):
                    return True, ""

                try:
                    err_json = response.json()
                    detail = err_json.get("message", response.text[:200])
                    return False, f"HTTP {response.status_code}: {detail}"
                except Exception:
                    return False, f"HTTP {response.status_code}: {response.text[:200]}"

        except httpx.TimeoutException:
            return False, "Timeout (over 10s)"
        except Exception as e:
            return False, f"Exception: {str(e)}"
