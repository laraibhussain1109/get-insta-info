from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol
from urllib.parse import urlparse

from app.providers.instagram_crawler import (
    InstagramPageParser,
    UrllibHtmlFetcher,
    count_from_edge,
    extract_balanced_json_objects,
    parse_count,
)


class PostHtmlFetcher(Protocol):
    def fetch(self, url: str) -> str:
        """Fetch a public Instagram post URL and return HTML plus any captured JSON."""


class BrowserHtmlFetcher:
    """Render public post pages and capture public JSON responses with Playwright.

    This is a crawler mode for public pages only. It does not log in, set cookies,
    rotate proxies, solve challenges, or bypass access controls.
    """

    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, url: str) -> str:
        if importlib.util.find_spec("playwright") is None:
            raise RuntimeError(
                "Browser crawler mode requires Playwright. Install it with: "
                "pip install playwright && python -m playwright install chromium"
            )

        from playwright.sync_api import sync_playwright

        captured_json: list[object] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="get-insta-info/0.1 public metrics crawler",
                locale="en-US",
            )

            def capture_response(response: object) -> None:
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type.lower() and "graphql" not in response.url.lower():
                    return
                if not any(marker in response.url for marker in ("instagram.com", "graphql", "api")):
                    return
                body = response.text()
                if not any(marker in body for marker in PUBLIC_METRIC_MARKERS):
                    return
                try:
                    captured_json.append(json.loads(body))
                except json.JSONDecodeError:
                    return

            page.on("response", capture_response)
            page.goto(url, wait_until="networkidle", timeout=self.timeout_seconds * 1000)
            dom_metrics = self._extract_dom_metrics(page, url)
            if dom_metrics:
                captured_json.append(dom_metrics)
            html = page.content()
            browser.close()

        if not captured_json:
            return html
        json_scripts = "".join(
            f'<script type="application/json">{json.dumps(item)}</script>' for item in captured_json
        )
        return html + json_scripts

    def _extract_dom_metrics(self, page: object, url: str) -> dict[str, object]:
        shortcode = extract_shortcode(url)
        metrics = page.evaluate(
            r"""
            () => {
              const parseCount = (value) => {
                if (!value) return null;
                const normalized = String(value).replace(/,/g, '').trim();
                const match = normalized.match(/(\d+(?:\.\d+)?)\s*([KMB])?/i);
                if (!match) return null;
                const multipliers = {K: 1000, M: 1000000, B: 1000000000};
                return Math.round(Number(match[1]) * (multipliers[(match[2] || '').toUpperCase()] || 1));
              };

              const text = document.body ? document.body.innerText : '';
              const metricPatterns = {
                likes: /(\d[\d,.]*\s*[KMB]?)\s+likes?/i,
                comments: /(\d[\d,.]*\s*[KMB]?)\s+comments?/i,
                views: /(\d[\d,.]*\s*[KMB]?)\s+(?:views?|plays?)/i,
                shares: /(\d[\d,.]*\s*[KMB]?)\s+shares?/i,
                reposts: /(\d[\d,.]*\s*[KMB]?)\s+(?:reposts?|reshares?)/i,
              };
              const metrics = {};
              for (const [name, pattern] of Object.entries(metricPatterns)) {
                const match = text.match(pattern);
                const count = match ? parseCount(match[1]) : null;
                if (count !== null) metrics[name] = count;
              }

              const buttonCounts = Array.from(document.querySelectorAll('[role="button"]'))
                .map((node) => node.innerText || node.textContent || '')
                .map((value) => value.trim())
                .filter((value) => /^(?:\d[\d,.]*\s*[KMB]?)$/i.test(value))
                .map(parseCount)
                .filter((value) => value !== null);

              // Standalone action-bar numbers are unlabeled and can be confused with
              // unrelated buttons. Use them only for the third action count, which is
              // shown as repost/reshare in Instagram reel layouts. Likes/comments/views
              // are only filled from labeled text or structured JSON to avoid bad data.
              if (metrics.reposts == null && buttonCounts.length >= 3) metrics.reposts = buttonCounts[2];
              if (metrics.shares == null && buttonCounts.length >= 3) metrics.shares = buttonCounts[2];

              return metrics;
            }
            """
        )
        if not isinstance(metrics, dict):
            return {}
        metrics["shortcode"] = shortcode
        metrics["source"] = "browser_dom"
        return metrics


def build_fetcher(mode: str) -> PostHtmlFetcher:
    if mode == "http":
        return UrllibHtmlFetcher()
    if mode == "browser":
        return BrowserHtmlFetcher()
    raise ValueError("fetch mode must be 'http' or 'browser'")

INSTAGRAM_POST_RE = re.compile(r"^/(?:p|reel|tv)/(?P<shortcode>[A-Za-z0-9_-]+)/?")
YOUTUBE_SHORT_RE = re.compile(r"^/shorts/(?P<video_id>[A-Za-z0-9_-]{6,})")
DESCRIPTION_LIKES_RE = re.compile(r"(?P<count>[\d.,]+\s*[KMB]?)\s+likes?", re.IGNORECASE)
DESCRIPTION_COMMENTS_RE = re.compile(r"(?P<count>[\d.,]+\s*[KMB]?)\s+comments?", re.IGNORECASE)
PUBLIC_METRIC_MARKERS = (
    "edge_media_to_comment",
    "video_view_count",
    "video_play_count",
    "play_count",
    "view_count",
    "share_count",
    "reshare_count",
    "save_count",
)


@dataclass(slots=True)
class PublicPostMetrics:
    url: str
    shortcode: str
    platform: str = "instagram"
    likes: int | None = None
    comments: int | None = None
    views: int | None = None
    shares: int | None = None
    saves: int | None = None
    reposts: int | None = None
    source: str = "public_html"
    error: str | None = None


def detect_platform(url: str) -> str:
    host = urlparse(url.strip()).netloc.lower()
    if "youtu.be" in host or "youtube.com" in host:
        return "youtube"
    return "instagram"


def extract_shortcode(url: str) -> str:
    parsed = urlparse(url.strip())
    match = INSTAGRAM_POST_RE.match(parsed.path)
    if parsed.netloc and "instagram.com" not in parsed.netloc.lower():
        raise ValueError("URL must be an Instagram post, reel, or TV link")
    if not match:
        raise ValueError("URL must include /p/, /reel/, or /tv/ followed by a shortcode")
    return match.group("shortcode")



def extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        video_id = parsed.path.strip("/").split("/")[0]
    elif "youtube.com" in host:
        short_match = YOUTUBE_SHORT_RE.match(parsed.path)
        video_id = short_match.group("video_id") if short_match else urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    else:
        raise ValueError("URL must be a YouTube video or Shorts link")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,}", video_id):
        raise ValueError("URL must include a valid YouTube video id")
    return video_id


def fetch_public_content_metrics(url: str, fetcher: PostHtmlFetcher | None = None) -> PublicPostMetrics:
    if detect_platform(url) == "youtube":
        return fetch_youtube_metrics(url)
    return fetch_public_post_metrics(url, fetcher=fetcher)


def fetch_youtube_metrics(url: str) -> PublicPostMetrics:
    video_id = extract_youtube_video_id(url)
    api_key = os.getenv("YOUTUBE_API_KEY")
    if api_key:
        return _fetch_youtube_api_metrics(url, video_id, api_key)

    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        html = UrllibHtmlFetcher().fetch(watch_url)
    except urllib.error.URLError as exc:
        return PublicPostMetrics(url=url, shortcode=video_id, platform="youtube", error=f"could not fetch YouTube HTML: {exc}")
    return parse_youtube_metrics(url, video_id, html)


def _fetch_youtube_api_metrics(url: str, video_id: str, api_key: str) -> PublicPostMetrics:
    query = urllib.parse.urlencode({"part": "statistics", "id": video_id, "key": api_key})
    request = urllib.request.Request(f"https://www.googleapis.com/youtube/v3/videos?{query}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        return PublicPostMetrics(url=url, shortcode=video_id, platform="youtube", error=f"could not fetch YouTube API metrics: {exc}")
    items = payload.get("items") if isinstance(payload, dict) else None
    if not items:
        return PublicPostMetrics(url=url, shortcode=video_id, platform="youtube", error="YouTube API returned no video statistics")
    statistics = items[0].get("statistics", {})
    return PublicPostMetrics(
        url=url,
        shortcode=video_id,
        platform="youtube",
        views=_optional_int(statistics.get("viewCount")),
        likes=_optional_int(statistics.get("likeCount")),
        comments=_optional_int(statistics.get("commentCount")),
        source="youtube_api",
    )


def parse_youtube_metrics(url: str, video_id: str, html: str) -> PublicPostMetrics:
    metrics = PublicPostMetrics(url=url, shortcode=video_id, platform="youtube", source="youtube_public_html")
    patterns = {
        "views": (r'"viewCount"\s*:\s*"(?P<count>\d+)"', r'(?P<count>[\d,.]+\s*[KMB]?)\s+views?'),
        "likes": (r'(?P<count>[\d,.]+\s*[KMB]?)\s+likes?',),
        "comments": (r'"commentCount"\s*:\s*"(?P<count>\d+)"', r'(?P<count>[\d,.]+\s*[KMB]?)\s+comments?'),
    }
    for field, field_patterns in patterns.items():
        for pattern in field_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                setattr(metrics, field, parse_count(match.group("count")))
                break
    if metrics.views is None and metrics.likes is None and metrics.comments is None:
        metrics.error = "no public YouTube counts found; set YOUTUBE_API_KEY for official statistics"
    return metrics


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_public_post_metrics(url: str, fetcher: PostHtmlFetcher | None = None) -> PublicPostMetrics:
    shortcode = extract_shortcode(url)
    fetcher = fetcher or UrllibHtmlFetcher()
    try:
        html = fetcher.fetch(url)
    except (RuntimeError, urllib.error.URLError) as exc:
        return PublicPostMetrics(url=url, shortcode=shortcode, error=f"could not fetch public HTML: {exc}")

    return parse_public_post_metrics(url, shortcode, html)


def parse_public_post_metrics(url: str, shortcode: str, html: str) -> PublicPostMetrics:
    parser = InstagramPageParser()
    parser.feed(html)
    metrics = PublicPostMetrics(url=url, shortcode=shortcode)
    _merge_metrics(metrics, _metrics_from_description(parser.parsed.description or ""))

    for script in parser.parsed.scripts or []:
        if shortcode not in script and not any(marker in script for marker in PUBLIC_METRIC_MARKERS):
            continue
        for blob in extract_balanced_json_objects(script):
            _merge_metrics(metrics, _walk_for_shortcode_metrics(blob, shortcode))

    if all(getattr(metrics, field) is None for field in ("likes", "comments", "views", "shares", "saves", "reposts")):
        metrics.error = "no public counts found in page HTML"
    return metrics


def update_spreadsheet_with_metrics(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    url_column: str = "A",
    first_data_row: int = 2,
    fetcher: PostHtmlFetcher | None = None,
) -> Path:
    source = Path(input_path)
    if source.suffix.lower() == ".csv":
        return update_csv_with_metrics(source, output_path, url_column=url_column, first_data_row=first_data_row, fetcher=fetcher)
    return update_workbook_with_metrics(source, output_path, url_column=url_column, first_data_row=first_data_row, fetcher=fetcher)


def update_workbook_with_metrics(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    url_column: str = "A",
    first_data_row: int = 2,
    fetcher: PostHtmlFetcher | None = None,
) -> Path:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on runtime installation
        raise RuntimeError("Install openpyxl to process .xlsx files: pip install openpyxl") from exc

    source = Path(input_path)
    destination = Path(output_path) if output_path else source.with_name(f"{source.stem}_with_insights{source.suffix}")
    workbook = load_workbook(source)
    sheet = workbook.active

    headers = ["platform", "views", "likes", "comments", "shares", "saves", "reposts", "shortcode", "source", "error"]
    start_column = sheet.max_column + 1
    for offset, header in enumerate(headers):
        sheet.cell(row=1, column=start_column + offset, value=header)

    for row in range(first_data_row, sheet.max_row + 1):
        link = sheet[f"{url_column}{row}"].value
        if not link:
            continue
        try:
            metrics = fetch_public_content_metrics(str(link), fetcher=fetcher)
        except ValueError as exc:
            metrics = PublicPostMetrics(url=str(link), shortcode="", error=str(exc))
        values = [metrics.platform, metrics.views, metrics.likes, metrics.comments, metrics.shares, metrics.saves, metrics.reposts, metrics.shortcode, metrics.source, metrics.error]
        for offset, value in enumerate(values):
            sheet.cell(row=row, column=start_column + offset, value=value)

    workbook.save(destination)
    return destination



def update_csv_with_metrics(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    url_column: str = "A",
    first_data_row: int = 2,
    fetcher: PostHtmlFetcher | None = None,
) -> Path:
    source = Path(input_path)
    destination = Path(output_path) if output_path else source.with_name(f"{source.stem}_with_insights{source.suffix}")
    url_index = _column_to_index(url_column)
    first_index = max(first_data_row - 1, 0)

    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    headers = ["platform", "views", "likes", "comments", "shares", "saves", "reposts", "shortcode", "source", "error"]
    if rows:
        rows[0].extend(headers)
    else:
        rows.append(headers)

    for index, row in enumerate(rows[1:], start=1):
        if index < first_index or url_index >= len(row) or not row[url_index]:
            row.extend([""] * len(headers))
            continue
        try:
            metrics = fetch_public_content_metrics(row[url_index], fetcher=fetcher)
        except ValueError as exc:
            metrics = PublicPostMetrics(url=row[url_index], shortcode="", error=str(exc))
        row.extend([
            metrics.platform,
            _cell(metrics.views),
            _cell(metrics.likes),
            _cell(metrics.comments),
            _cell(metrics.shares),
            _cell(metrics.saves),
            _cell(metrics.reposts),
            metrics.shortcode,
            metrics.source,
            metrics.error or "",
        ])

    with destination.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return destination


def _column_to_index(column: str) -> int:
    value = 0
    for char in column.strip().upper():
        if not "A" <= char <= "Z":
            raise ValueError("column must contain letters only, for example A or AB")
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _cell(value: int | None) -> int | str:
    return value if value is not None else ""


def _metrics_from_description(description: str) -> dict[str, int]:
    found: dict[str, int] = {}
    likes = DESCRIPTION_LIKES_RE.search(description)
    comments = DESCRIPTION_COMMENTS_RE.search(description)
    if likes:
        found["likes"] = parse_count(likes.group("count"))
    if comments:
        found["comments"] = parse_count(comments.group("count"))
    return found


def _walk_for_shortcode_metrics(value: object, shortcode: str) -> dict[str, int]:
    if isinstance(value, dict):
        found: dict[str, int] = {}
        if _node_has_shortcode(value, shortcode):
            found.update(_collect_metrics_from_subtree(value))
        for child in value.values():
            found.update(_walk_for_shortcode_metrics(child, shortcode))
        return found
    if isinstance(value, list):
        found: dict[str, int] = {}
        for item in value:
            found.update(_walk_for_shortcode_metrics(item, shortcode))
        return found
    return {}


def _node_has_shortcode(value: dict[str, object], shortcode: str) -> bool:
    return value.get("shortcode") == shortcode or value.get("code") == shortcode


def _collect_metrics_from_subtree(value: object) -> dict[str, int]:
    if isinstance(value, dict):
        found = _metrics_from_node(value)
        for child in value.values():
            found.update(_collect_metrics_from_subtree(child))
        return found
    if isinstance(value, list):
        found: dict[str, int] = {}
        for item in value:
            found.update(_collect_metrics_from_subtree(item))
        return found
    return {}


def _metrics_from_node(node: dict[str, object]) -> dict[str, int]:
    metrics: dict[str, int] = {}
    candidates = {
        "likes": ("likes", "edge_liked_by", "edge_media_preview_like", "like_count", "likes_count"),
        "comments": ("comments", "edge_media_to_comment", "comment_count", "comments_count"),
        "views": (
            "video_view_count",
            "video_play_count",
            "play_count",
            "view_count",
            "ig_play_count",
            "viewCount",
            "playCount",
            "videoViewCount",
            "views",
        ),
        "shares": ("shares", "share_count", "shares_count", "shareCount", "reshare_count", "reshareCount"),
        "saves": ("saves", "save_count", "saved_count", "saves_count", "saveCount"),
        "reposts": ("reposts", "repost_count", "repostCount", "reshare_count", "reshare_count_v2", "clips_reshare_count"),
    }
    for metric_name, keys in candidates.items():
        value = _first_count_for_keys(node, keys)
        if value is not None:
            metrics[metric_name] = value
    return metrics


def _first_count_for_keys(node: dict[str, object], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = node.get(key)
        count = count_from_edge(value)
        if count:
            return count
        if isinstance(value, int | float):
            return int(value)
        if isinstance(value, str):
            parsed = parse_count(value)
            if parsed:
                return parsed
    return None


def _merge_metrics(target: PublicPostMetrics, updates: dict[str, int]) -> None:
    for key, value in updates.items():
        if value is not None:
            setattr(target, key, value)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Append public Instagram post counts to an Excel or CSV spreadsheet.")
    parser.add_argument("input", help="Path to the .xlsx or .csv file containing Instagram links")
    parser.add_argument("--output", help="Output path. Defaults to *_with_insights using the same extension")
    parser.add_argument("--url-column", default="A", help="Column containing Instagram links, e.g. A or B")
    parser.add_argument("--first-data-row", type=int, default=2, help="First row containing links")
    parser.add_argument(
        "--fetch-mode",
        choices=("http", "browser"),
        default="http",
        help="Use browser mode to render public pages and capture public JSON responses.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    destination = update_spreadsheet_with_metrics(
        args.input,
        args.output,
        url_column=args.url_column,
        first_data_row=args.first_data_row,
        fetcher=build_fetcher(args.fetch_mode),
    )
    print(f"Wrote public metrics to {destination}")


if __name__ == "__main__":
    main()
