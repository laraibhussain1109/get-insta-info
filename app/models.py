from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


class ValidationError(ValueError):
    """Raised when request input fails validation."""


class MetricSource(str, Enum):
    MOCK = "mock"
    OFFICIAL_API = "official_api"
    LICENSED_PROVIDER = "licensed_provider"
    FIRST_PARTY_IMPORT = "first_party_import"
    ESTIMATE = "estimate"
    CRAWLER = "crawler"


def normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not USERNAME_RE.fullmatch(value):
        raise ValidationError("username must be 1-30 characters and contain only letters, numbers, dots, or underscores")
    return value


def validate_window(window_days: int) -> int:
    if window_days < 7 or window_days > 90:
        raise ValidationError("window must be between 7 and 90 days")
    return window_days


@dataclass(slots=True)
class PublicPost:
    id: str
    timestamp: datetime
    media_type: str
    likes: int
    comments: int
    views: int | None = None
    caption: str = ""
    location: str | None = None


@dataclass(slots=True)
class PublicProfile:
    username: str
    follower_count: int
    following_count: int
    source: MetricSource
    full_name: str | None = None
    biography: str | None = None
    is_verified: bool = False
    posts: list[PublicPost] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class ProfileInsights:
    username: str
    follower_count: int
    avg_views: float
    avg_likes: float
    avg_comments: float
    engagement_rate: float
    gender_ratio: dict[str, float]
    location_ratio: dict[str, float]
    confidence: dict[str, float]
    source: str
    last_updated: datetime

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["last_updated"] = self.last_updated.isoformat()
        return payload


@dataclass(slots=True)
class BatchRequest:
    usernames: list[str]
    window: int = 30

    def __post_init__(self) -> None:
        if not 1 <= len(self.usernames) <= 100:
            raise ValidationError("batch usernames must contain between 1 and 100 items")
        self.usernames = [normalize_username(username) for username in self.usernames]
        self.window = validate_window(int(self.window))


@dataclass(slots=True)
class BatchResponse:
    job_id: str
    status: str
    requested: int
    results_url: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
