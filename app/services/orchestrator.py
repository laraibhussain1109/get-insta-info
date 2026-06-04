from __future__ import annotations

from uuid import uuid4

from app.models import BatchRequest, BatchResponse, ProfileInsights, normalize_username, validate_window
from app.providers.base import ProfileProvider
from app.repositories import InsightsRepository
from app.services.estimator import InsightsEstimator


class InsightsOrchestrator:
    def __init__(
        self,
        provider: ProfileProvider,
        estimator: InsightsEstimator,
        repository: InsightsRepository,
    ) -> None:
        self.provider = provider
        self.estimator = estimator
        self.repository = repository

    async def get_insights(self, username: str, window_days: int, refresh: bool = False) -> ProfileInsights:
        normalized_username = normalize_username(username)
        validated_window = validate_window(int(window_days))
        if not refresh:
            cached = self.repository.get(normalized_username, validated_window)
            if cached is not None:
                return cached

        profile = await self.provider.fetch_profile(normalized_username, validated_window)
        insights = self.estimator.estimate(profile, validated_window)
        self.repository.set(validated_window, insights)
        return insights

    async def create_batch(self, request: BatchRequest) -> BatchResponse:
        # MVP placeholder: production should enqueue jobs in Celery/RQ/SQS.
        job_id = uuid4().hex
        return BatchResponse(
            job_id=job_id,
            status="queued",
            requested=len(request.usernames),
            results_url=f"/v1/jobs/{job_id}",
        )
