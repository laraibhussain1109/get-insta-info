from __future__ import annotations

from collections import Counter
from statistics import mean

from app.models import MetricSource, ProfileInsights, PublicPost, PublicProfile


class InsightsEstimator:
    """Baseline estimator for public profile analytics.

    The estimates are intentionally transparent and conservative. Replace these
    heuristics with trained models once consented ground-truth labels are
    available.
    """

    def estimate(self, profile: PublicProfile, window_days: int) -> ProfileInsights:
        posts = profile.posts[: max(1, window_days)]
        avg_likes = self._avg([post.likes for post in posts])
        avg_comments = self._avg([post.comments for post in posts])
        avg_views = self._estimate_avg_views(posts, avg_likes)
        engagement_rate = ((avg_likes + avg_comments) / profile.follower_count) if profile.follower_count else 0
        location_ratio, location_confidence = self._location_ratio(posts)
        gender_ratio, gender_confidence = self._gender_ratio(profile)
        view_confidence = self._view_confidence(posts)

        return ProfileInsights(
            username=profile.username,
            follower_count=profile.follower_count,
            avg_views=round(avg_views, 2),
            avg_likes=round(avg_likes, 2),
            avg_comments=round(avg_comments, 2),
            engagement_rate=round(engagement_rate, 6),
            gender_ratio=gender_ratio,
            location_ratio=location_ratio,
            confidence={
                "gender_ratio": gender_confidence,
                "location_ratio": location_confidence,
                "avg_views": view_confidence,
            },
            source=f"{profile.source.value}+{MetricSource.ESTIMATE.value}",
            last_updated=profile.fetched_at,
        )

    def _estimate_avg_views(self, posts: list[PublicPost], avg_likes: float) -> float:
        direct_views = [post.views for post in posts if post.views is not None]
        if direct_views:
            return self._avg(direct_views)
        return avg_likes * 6.0

    def _view_confidence(self, posts: list[PublicPost]) -> float:
        if not posts:
            return 0.1
        direct = sum(1 for post in posts if post.views is not None)
        evidence_ratio = direct / len(posts)
        sample_score = min(len(posts) / 30, 1)
        return round(0.25 + 0.55 * evidence_ratio + 0.2 * sample_score, 3)

    def _location_ratio(self, posts: list[PublicPost]) -> tuple[dict[str, float], float]:
        locations = [post.location for post in posts if post.location]
        if not locations:
            return {"Other": 1.0}, 0.15
        counts = Counter(locations)
        total = sum(counts.values())
        ratios = {country: round(count / total, 4) for country, count in counts.items()}
        if sum(ratios.values()) < 1:
            ratios["Other"] = round(1 - sum(ratios.values()), 4)
        confidence = min(0.85, 0.25 + total / max(len(posts), 1))
        return ratios, round(confidence, 3)

    def _gender_ratio(self, profile: PublicProfile) -> tuple[dict[str, float], float]:
        # Placeholder prior. A production model should be trained on consented,
        # aggregated labels and should document fairness limitations.
        if profile.follower_count >= 100_000:
            return {"female": 0.52, "male": 0.46, "other": 0.02}, 0.2
        return {"female": 0.5, "male": 0.48, "other": 0.02}, 0.15

    def _avg(self, values: list[int | float]) -> float:
        return float(mean(values)) if values else 0.0
