from __future__ import annotations

import argparse
import csv
import re
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from app.providers.instagram_crawler import (
    InstagramPageParser,
    UrllibHtmlFetcher,
    count_from_edge,
    extract_balanced_json_objects,
    parse_count,
)

INSTAGRAM_POST_RE = re.compile(r"^/(?:p|reel|tv)/(?P<shortcode>[A-Za-z0-9_-]+)/?")
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
    likes: int | None = None
    comments: int | None = None
    views: int | None = None
    shares: int | None = None
    saves: int | None = None
    reposts: int | None = None
    source: str = "public_html"
    error: str | None = None


def extract_shortcode(url: str) -> str:
    parsed = urlparse(url.strip())
    match = INSTAGRAM_POST_RE.match(parsed.path)
    if parsed.netloc and "instagram.com" not in parsed.netloc.lower():
        raise ValueError("URL must be an Instagram post, reel, or TV link")
    if not match:
        raise ValueError("URL must include /p/, /reel/, or /tv/ followed by a shortcode")
    return match.group("shortcode")


def fetch_public_post_metrics(url: str, fetcher: UrllibHtmlFetcher | None = None) -> PublicPostMetrics:
    shortcode = extract_shortcode(url)
    fetcher = fetcher or UrllibHtmlFetcher()
    try:
        html = fetcher.fetch(url)
    except urllib.error.URLError as exc:
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
    fetcher: UrllibHtmlFetcher | None = None,
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
    fetcher: UrllibHtmlFetcher | None = None,
) -> Path:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on runtime installation
        raise RuntimeError("Install openpyxl to process .xlsx files: pip install openpyxl") from exc

    source = Path(input_path)
    destination = Path(output_path) if output_path else source.with_name(f"{source.stem}_with_insights{source.suffix}")
    workbook = load_workbook(source)
    sheet = workbook.active

    headers = ["views", "likes", "comments", "shares", "saves", "reposts", "shortcode", "source", "error"]
    start_column = sheet.max_column + 1
    for offset, header in enumerate(headers):
        sheet.cell(row=1, column=start_column + offset, value=header)

    for row in range(first_data_row, sheet.max_row + 1):
        link = sheet[f"{url_column}{row}"].value
        if not link:
            continue
        try:
            metrics = fetch_public_post_metrics(str(link), fetcher=fetcher)
        except ValueError as exc:
            metrics = PublicPostMetrics(url=str(link), shortcode="", error=str(exc))
        values = [metrics.views, metrics.likes, metrics.comments, metrics.shares, metrics.saves, metrics.reposts, metrics.shortcode, metrics.source, metrics.error]
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
    fetcher: UrllibHtmlFetcher | None = None,
) -> Path:
    source = Path(input_path)
    destination = Path(output_path) if output_path else source.with_name(f"{source.stem}_with_insights{source.suffix}")
    url_index = _column_to_index(url_column)
    first_index = max(first_data_row - 1, 0)

    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    headers = ["views", "likes", "comments", "shares", "saves", "reposts", "shortcode", "source", "error"]
    if rows:
        rows[0].extend(headers)
    else:
        rows.append(headers)

    for index, row in enumerate(rows[1:], start=1):
        if index < first_index or url_index >= len(row) or not row[url_index]:
            row.extend([""] * len(headers))
            continue
        try:
            metrics = fetch_public_post_metrics(row[url_index], fetcher=fetcher)
        except ValueError as exc:
            metrics = PublicPostMetrics(url=row[url_index], shortcode="", error=str(exc))
        row.extend([
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
        "likes": ("edge_liked_by", "edge_media_preview_like", "like_count", "likes_count"),
        "comments": ("edge_media_to_comment", "comment_count", "comments_count"),
        "views": (
            "video_view_count",
            "video_play_count",
            "play_count",
            "view_count",
            "ig_play_count",
            "viewCount",
            "playCount",
            "videoViewCount",
        ),
        "shares": ("share_count", "shares_count", "shareCount", "reshare_count", "reshareCount"),
        "saves": ("save_count", "saved_count", "saves_count", "saveCount"),
        "reposts": ("repost_count", "repostCount", "reshare_count", "reshare_count_v2", "clips_reshare_count"),
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
    args = parser.parse_args(list(argv) if argv is not None else None)
    destination = update_spreadsheet_with_metrics(
        args.input,
        args.output,
        url_column=args.url_column,
        first_data_row=args.first_data_row,
    )
    print(f"Wrote public metrics to {destination}")


if __name__ == "__main__":
    main()
