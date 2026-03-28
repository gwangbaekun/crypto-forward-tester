"""
텔레그램 알림 서비스 — tradingview_mcp app/features/notifications/telegram_service.py 와 동일 동작.
환경 변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (Railway 등) 또는 data/telegram_config.json.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Tuple

import httpx


class TelegramService:
    """텔레그램 메시지 전송 서비스 (싱글톤)."""

    _instance = None
    API_BASE = "https://api.telegram.org/bot"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            root = Path(__file__).resolve().parent.parent.parent.parent
            cls._instance._config_path = root / "data" / "telegram_config.json"
            cls._instance._config_path.parent.mkdir(parents=True, exist_ok=True)
        return cls._instance

    def load_config(self) -> Dict:
        """텔레그램 설정 로드 (봇 토큰, Chat ID)."""
        default_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        default_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

        if self._config_path.exists():
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    config = json.load(f)
                    if default_bot_token and not config.get("bot_token"):
                        config["bot_token"] = default_bot_token
                    if default_chat_id and not config.get("chat_id"):
                        config["chat_id"] = default_chat_id
                    if config.get("enabled") is None:
                        config["enabled"] = True
                    return config
            except Exception:
                pass

        return {
            "bot_token": default_bot_token,
            "chat_id": default_chat_id,
            "enabled": True,
            "min_stars": 4,
            "last_sent": {},
        }

    def save_config(self, config: Dict) -> None:
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def send_message(self, message: str) -> Tuple[bool, str]:
        """HTML parse_mode 로 메시지 전송. (성공 여부, 에러 메시지)"""
        config = self.load_config()
        bot_token = config.get("bot_token")
        chat_id = config.get("chat_id")

        if not bot_token or not chat_id:
            return False, "텔레그램 봇 토큰 또는 Chat ID가 설정되지 않았습니다."

        try:
            url = f"{self.API_BASE}{bot_token}/sendMessage"
            params = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            }

            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)

                if response.status_code == 200:
                    result = response.json()
                    if result.get("ok"):
                        return True, ""
                    error_desc = result.get("description", "알 수 없는 오류")
                    error_code = result.get("error_code", "")
                    full_error = f"텔레그램 API 오류: {error_desc}"
                    if error_code:
                        full_error += f" (코드: {error_code})"
                    return False, full_error
                try:
                    error_json = response.json()
                    error_desc = error_json.get("description", response.text[:200])
                    return False, f"HTTP {response.status_code}: {error_desc}"
                except Exception:
                    return False, f"HTTP {response.status_code}: {response.text[:200]}"

        except httpx.TimeoutException:
            return False, "타임아웃 (10초 초과)"
        except Exception as e:
            return False, f"예외 발생: {str(e)}"

    def send_prediction_alert(
        self, symbol: str, direction: str, stars: int, tf: str, details: str = ""
    ) -> bool:
        """예측 알림 (별·중복 방지) — tradingview_mcp 호환."""
        config = self.load_config()

        if not config.get("enabled", False):
            return False

        if stars < config.get("min_stars", 4):
            return False

        last_key = f"{symbol}:{direction}:{tf}"
        last_sent = config.get("last_sent", {})
        if last_key in last_sent:
            if time.time() - last_sent[last_key] < 300:
                return False

        dir_label = "상승" if direction == "bull" else "하락"
        emoji = "🚀" if direction == "bull" else "📉"
        star_emoji = "⭐" * stars

        message = (
            f"<b>{emoji} {symbol} {tf} {dir_label} 예측 {stars}별</b>\n\n"
            f"{star_emoji}\n\n"
            f"<code>{details}</code>"
        )

        success, error = self.send_message(message)

        if success:
            config["last_sent"][last_key] = time.time()
            self.save_config(config)
        else:
            print(f"텔레그램 알림 전송 실패: {error}")

        return success
