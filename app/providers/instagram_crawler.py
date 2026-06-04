from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Protocol

from app.models import MetricSource, PublicPost, PublicProfile
from app.providers.base import ProfileProvider

COUNT_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<suffix>[KMB])?", re.IGNORECASE)
PROFILE_COUNT_RE = re.compile(
    r"(?P<followers>[\d.,]+\s*[KMB]?)\s+Followers,\s*"
    r"(?P<following>[\d.,]+\s*[KMB]?)\s+Following,\s*"
    r"(?P<posts>[\d.,]+\s*[KMB]?)\s+Posts",
    re.IGNORECASE,
)


class HtmlFetcher(Protocol):
    def fetch(self, url: str) -> str:
        """Fetch a URL and return decoded HTML."""


@dataclass(slots=True)
class UrllibHtmlFetcher:
    """Small, non-evasive HTML fetcher for public web pages."""

    timeout_seconds: int = 15

    def fetch(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
                "User-Agent": "get-insta-info/0.1 (+https://example.com/contact)",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            encoding = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(encoding, errors="replace")


@dataclass(slots=True)
class ParsedInstagramPage:
    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    json_ld: list[dict[str, object]] | None = None
    scripts: list[str] | None = None


class InstagramPageParser(HTMLParser):
    """Extract profile metadata and embedded JSON from Instagram profile HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.parsed = ParsedInstagramPage(json_ld=[], scripts=[])
        self._in_title = False
        self._in_script = False
        self._script_type: str | None = None
        self._script_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        if tag == "title":
            self._in_title = True
        if tag == "meta" and attributes.get("property") in {"og:description", "description"}:
            self.parsed.description = attributes.get("content")
        if tag == "meta" and attributes.get("name") == "description":
            self.parsed.description = attributes.get("content")
        if tag == "link" and attributes.get("rel") == "canonical":
            self.parsed.canonical_url = attributes.get("href")
        if tag == "script":
            self._in_script = True
            self._script_type = attributes.get("type")
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.parsed.title = (self.parsed.title or "") + data.strip()
        if self._in_script:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_script:
            body = "".join(self._script_chunks).strip()
            if body:
                self.parsed.scripts = self.parsed.scripts or []
                self.parsed.scripts.append(body)
                if self._script_type == "application/ld+json":
                    self._append_json_ld(body)
            self._in_script = False
            self._script_type = None
            self._script_chunks = []

    def _append_json_ld(self, body: str) -> None:
        try:
            loaded = json.loads(body)
        except json.JSONDecodeError:
            return
        self.parsed.json_ld = self.parsed.json_ld or []
        if isinstance(loaded, list):
            self.parsed.json_ld.extend(item for item in loaded if isinstance(item, dict))
        elif isinstance(loaded, dict):
            self.parsed.json_ld.append(loaded)


class PublicInstagramCrawlerProvider(ProfileProvider):
    """Crawler provider that fetches and parses a public Instagram profile page.

    This class performs a direct public-page fetch and HTML/embedded-JSON parse.
    It does not rotate proxies, use logged-in sessions, solve challenges, or bypass
    access controls. If Instagram withholds metrics from public HTML, those fields
    are left as estimates by the estimator layer.
    """

    def __init__(self, fetcher: HtmlFetcher | None = None) -> None:
        self.fetcher = fetcher or UrllibHtmlFetcher()

    async def fetch_profile(self, username: str, window_days: int) -> PublicProfile:
        url = f"https://www.instagram.com/{username}/"
        try:
            html = self.fetcher.fetch(url)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"could not fetch Instagram profile HTML: {exc}") from exc

        parser = InstagramPageParser()
        parser.feed(html)
        parsed = parser.parsed
        counts = self._extract_profile_counts(parsed.description or "")
        json_profile = self._extract_profile_from_json(parsed)
        posts = self._extract_posts(parsed, username)[:window_days]
        full_name = self._extract_full_name(parsed.title, username) or json_profile.get("full_name")
        biography = json_profile.get("biography") or parsed.description

        return PublicProfile(
            username=username,
            full_name=full_name,
            biography=biography,
            follower_count=int(json_profile.get("follower_count") or counts.get("followers") or 0),
            following_count=int(json_profile.get("following_count") or counts.get("following") or 0),
            is_verified=bool(json_profile.get("is_verified", False)),
            posts=posts,
            source=MetricSource.CRAWLER,
        )

    def _extract_profile_counts(self, description: str) -> dict[str, int]:
        match = PROFILE_COUNT_RE.search(description)
        if not match:
            return {}
        return {
            "followers": parse_count(match.group("followers")),
            "following": parse_count(match.group("following")),
            "posts": parse_count(match.group("posts")),
        }

    def _extract_profile_from_json(self, parsed: ParsedInstagramPage) -> dict[str, object]:
        scripts = parsed.scripts or []
        json_blobs = [item for item in parsed.json_ld or []]
        json_blobs.extend(self._json_objects_from_scripts(scripts))
        profile: dict[str, object] = {}

        for blob in json_blobs:
            profile.update(self._walk_for_profile_fields(blob))
        return profile

    def _extract_posts(self, parsed: ParsedInstagramPage, username: str) -> list[PublicPost]:
        posts: list[PublicPost] = []
        for blob in self._json_objects_from_scripts(parsed.scripts or []):
            posts.extend(self._walk_for_posts(blob, username))
        return dedupe_posts(posts)

    def _json_objects_from_scripts(self, scripts: list[str]) -> list[dict[str, object]]:
        objects: list[dict[str, object]] = []
        for script in scripts:
            for marker in ('"edge_owner_to_timeline_media"', '"edge_followed_by"', '"biography"'):
                if marker in script:
                    objects.extend(extract_balanced_json_objects(script))
                    break
        return objects

    def _walk_for_profile_fields(self, value: object) -> dict[str, object]:
        if isinstance(value, dict):
            fields: dict[str, object] = {}
            if "edge_followed_by" in value and isinstance(value["edge_followed_by"], dict):
                fields["follower_count"] = value["edge_followed_by"].get("count", 0)
            if "edge_follow" in value and isinstance(value["edge_follow"], dict):
                fields["following_count"] = value["edge_follow"].get("count", 0)
            for source_key, target_key in {
                "full_name": "full_name",
                "biography": "biography",
                "is_verified": "is_verified",
            }.items():
                if source_key in value:
                    fields[target_key] = value[source_key]
            for child in value.values():
                fields.update(self._walk_for_profile_fields(child))
            return fields
        if isinstance(value, list):
            fields: dict[str, object] = {}
            for item in value:
                fields.update(self._walk_for_profile_fields(item))
            return fields
        return {}

    def _walk_for_posts(self, value: object, username: str) -> list[PublicPost]:
        posts: list[PublicPost] = []
        if isinstance(value, dict):
            if "shortcode" in value and ("edge_liked_by" in value or "edge_media_preview_like" in value):
                posts.append(self._post_from_node(value, username))
            for child in value.values():
                posts.extend(self._walk_for_posts(child, username))
        elif isinstance(value, list):
            for item in value:
                posts.extend(self._walk_for_posts(item, username))
        return posts

    def _post_from_node(self, node: dict[str, object], username: str) -> PublicPost:
        likes = count_from_edge(node.get("edge_liked_by")) or count_from_edge(node.get("edge_media_preview_like"))
        comments = count_from_edge(node.get("edge_media_to_comment"))
        timestamp_value = node.get("taken_at_timestamp")
        timestamp = datetime.now(timezone.utc)
        if isinstance(timestamp_value, int | float):
            timestamp = datetime.fromtimestamp(timestamp_value, tz=timezone.utc)
        media_type = "video" if node.get("is_video") else "image"
        shortcode = str(node.get("shortcode") or node.get("id") or f"{username}-{timestamp.timestamp()}")
        return PublicPost(
            id=shortcode,
            timestamp=timestamp,
            media_type=media_type,
            likes=likes,
            comments=comments,
            views=count_from_edge(node.get("video_view_count")),
            caption=extract_caption(node),
            location=extract_location(node),
        )

    def _extract_full_name(self, title: str | None, username: str) -> str | None:
        if not title:
            return None
        cleaned = title.split("•")[0].strip()
        if cleaned.lower() == username.lower():
            return None
        return cleaned or None


def parse_count(raw: str) -> int:
    normalized = raw.replace(",", "").strip()
    match = COUNT_RE.search(normalized)
    if not match:
        return 0
    number = float(match.group("number"))
    suffix = (match.group("suffix") or "").upper()
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    return int(number * multiplier)


def count_from_edge(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        count = value.get("count")
        if isinstance(count, int):
            return count
    return 0


def extract_caption(node: dict[str, object]) -> str:
    captions = node.get("edge_media_to_caption")
    if not isinstance(captions, dict):
        return ""
    edges = captions.get("edges")
    if not isinstance(edges, list) or not edges:
        return ""
    first = edges[0]
    if not isinstance(first, dict):
        return ""
    child = first.get("node")
    if not isinstance(child, dict):
        return ""
    text = child.get("text")
    return text if isinstance(text, str) else ""


def extract_location(node: dict[str, object]) -> str | None:
    location = node.get("location")
    if isinstance(location, dict):
        name = location.get("name") or location.get("slug")
        return str(name) if name else None
    return None


def dedupe_posts(posts: list[PublicPost]) -> list[PublicPost]:
    seen: set[str] = set()
    unique: list[PublicPost] = []
    for post in posts:
        if post.id in seen:
            continue
        seen.add(post.id)
        unique.append(post)
    return unique


def extract_balanced_json_objects(text: str) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    starts = [index for index, char in enumerate(text) if char == "{"]
    for start in starts:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        loaded = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(loaded, dict):
                        objects.append(loaded)
                    break
    return objects
