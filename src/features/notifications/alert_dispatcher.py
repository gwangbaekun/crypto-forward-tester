"""
AlertDispatcher — Telegram + Discord 동시 전송.

각 채널은 독립적으로 동작: 하나가 실패해도 나머지는 계속 전송됨.
설정되지 않은 채널은 자동으로 스킵.
"""
from __future__ import annotations

from typing import Dict, Tuple


class AlertDispatcher:
    """여러 알림 채널에 메시지를 동시 전송하는 디스패처 (싱글톤).

    지원 채널:
        - Telegram: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
        - Discord:  DISCORD_WEBHOOK_URL
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def send_message(self, message: str) -> Dict[str, Tuple[bool, str]]:
        """설정된 모든 채널에 메시지 전송.

        Args:
            message: Telegram HTML 형식 메시지 (Discord는 자동 변환).

        Returns:
            {"telegram": (ok, err), "discord": (ok, err)}
        """
        results: Dict[str, Tuple[bool, str]] = {}

        try:
            from features.notifications.telegram_service import TelegramService

            results["telegram"] = TelegramService().send_message(message)
        except Exception as e:
            results["telegram"] = (False, f"Exception: {e}")

        try:
            from features.notifications.discord_service import DiscordService

            results["discord"] = DiscordService().send_message(message)
        except Exception as e:
            results["discord"] = (False, f"Exception: {e}")

        return results
