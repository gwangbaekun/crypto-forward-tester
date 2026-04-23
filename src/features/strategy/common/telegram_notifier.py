"""
하위 호환 shim — notifier.py 로 위임.

신규 코드에서는 features.strategy.common.notifier 를 직접 import 하세요.
"""
from features.strategy.common.notifier import send_event_alerts as send_event_alerts  # noqa: F401
