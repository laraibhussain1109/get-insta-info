from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.models import MetricSource, PublicPost, PublicProfile
from app.providers.base import ProfileProvider


class MockProvider(ProfileProvider):
    """Deterministic provider for local development and tests.

    This adapter does not call Instagram. Production deployments should replace it
    with official API, licensed provider, or first-party import adapters.
    """

    async def fetch_profile(self, username: str, window_days: int) -> PublicProfile:
        seed = int(hashlib.sha256(username.encode("utf-8")).hexdigest()[:8], 16)
        follower_count = 1_000 + seed % 500_000
        post_count = max(8, min(50, window_days))
        now = datetime.now(timezone.utc)
        posts: list[PublicPost] = []

        for index in range(post_count):
            likes = 50 + ((seed // (index + 1)) % max(250, follower_count // 20))
            comments = 5 + likes // (20 + index % 12)
            is_video = index % 3 == 0
            views = int(likes * (4.5 + (seed % 30) / 10)) if is_video else None
            posts.append(
                PublicPost(
                    id=f"{username}-{index}",
                    timestamp=now - timedelta(days=index),
                    media_type="video" if is_video else "image",
                    likes=likes,
                    comments=comments,
                    views=views,
                    caption=f"Sample post {index} for {username}",
                    location="US" if index % 4 == 0 else "IN" if index % 7 == 0 else None,
                )
            )

        return PublicProfile(
            username=username,
            full_name=username.replace(".", " ").title(),
            biography="Mock profile generated for local development.",
            follower_count=follower_count,
            following_count=seed % 3_000,
            is_verified=seed % 11 == 0,
            posts=posts,
            source=MetricSource.MOCK,
        )
