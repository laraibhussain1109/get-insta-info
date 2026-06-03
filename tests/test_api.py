import asyncio

import pytest

from app.models import BatchRequest, ValidationError
from app.providers import MockProvider
from app.repositories import InsightsRepository
from app.services.estimator import InsightsEstimator
from app.services.orchestrator import InsightsOrchestrator


def make_orchestrator() -> InsightsOrchestrator:
    return InsightsOrchestrator(MockProvider(), InsightsEstimator(), InsightsRepository())


def test_profile_insights() -> None:
    insights = asyncio.run(make_orchestrator().get_insights("natgeo", 30))

    assert insights.username == "natgeo"
    assert insights.follower_count > 0
    assert insights.avg_likes >= 0
    assert insights.avg_comments >= 0
    assert insights.avg_views >= 0
    assert insights.gender_ratio
    assert insights.location_ratio
    assert insights.source.endswith("+estimate")


def test_invalid_username_rejected() -> None:
    with pytest.raises(ValidationError):
        asyncio.run(make_orchestrator().get_insights("bad username", 30))


def test_cache_reuses_result() -> None:
    orchestrator = make_orchestrator()

    first = asyncio.run(orchestrator.get_insights("natgeo", 30))
    second = asyncio.run(orchestrator.get_insights("natgeo", 30))

    assert first is second


def test_batch_endpoint_model() -> None:
    request = BatchRequest(usernames=["natgeo", "instagram"], window=30)
    response = asyncio.run(make_orchestrator().create_batch(request))

    assert response.status == "queued"
    assert response.requested == 2
    assert response.results_url.startswith("/v1/jobs/")
