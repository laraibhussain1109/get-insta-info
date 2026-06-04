from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import ProfileInsights


class InsightsRepository:
    """Simple TTL cache for profile insights.

    Replace with Redis/Postgres in production.
    """

    def __init__(self, ttl_hours: int = 24) -> None:
        self.ttl = timedelta(hours=ttl_hours)
        self._items: dict[tuple[str, int], ProfileInsights] = {}

    def get(self, username: str, window_days: int) -> ProfileInsights | None:
        item = self._items.get((username.lower(), window_days))
        if item is None:
            return None
        if datetime.now(timezone.utc) - item.last_updated > self.ttl:
            self._items.pop((username.lower(), window_days), None)
            return None
        return item

    def set(self, window_days: int, insights: ProfileInsights) -> None:
        self._items[(insights.username.lower(), window_days)] = insights
