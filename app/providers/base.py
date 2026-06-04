from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import PublicProfile


class ProfileProvider(ABC):
    """Interface for compliant Instagram profile data providers."""

    @abstractmethod
    async def fetch_profile(self, username: str, window_days: int) -> PublicProfile:
        """Fetch public/consented profile data for a username."""
