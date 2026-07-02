from pathlib import Path

import pytest

from app.excel_insights import (
    build_fetcher,
    extract_shortcode,
    extract_youtube_video_id,
    parse_public_post_metrics,
    parse_youtube_metrics,
    update_csv_with_metrics,
)

POST_HTML = """
<html><head>
<meta property="og:description" content="1,234 likes, 56 comments - Campaign post">
<script>
window.__post = {"items":[{"shortcode":"ABC123","is_video":true,"video_view_count":9876,
"edge_liked_by":{"count":1500},"edge_media_to_comment":{"count":60},"insights":{"video_play_count":"9,876","shareCount":"22","save_count":33,"repostCount":4}}]};
</script>
</head></html>
"""


class FixtureFetcher:
    def fetch(self, url: str) -> str:
        assert url == "https://www.instagram.com/p/ABC123/"
        return POST_HTML


def test_extract_shortcode_from_instagram_post_url() -> None:
    assert extract_shortcode("https://www.instagram.com/p/ABC123/?utm_source=x") == "ABC123"
    assert extract_shortcode("https://www.instagram.com/reel/XYZ_9/") == "XYZ_9"


def test_extract_youtube_video_id_from_watch_and_shorts_urls() -> None:
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=ABCdef_1234") == "ABCdef_1234"
    assert extract_youtube_video_id("https://www.youtube.com/shorts/XYZ987_abc") == "XYZ987_abc"
    assert extract_youtube_video_id("https://youtu.be/Qwerty_123") == "Qwerty_123"


def test_parse_youtube_metrics_from_public_html() -> None:
    html = '{"viewCount":"12345","commentCount":"67"} 89 likes'

    metrics = parse_youtube_metrics("https://www.youtube.com/watch?v=ABCdef_1234", "ABCdef_1234", html)

    assert metrics.platform == "youtube"
    assert metrics.views == 12345
    assert metrics.likes == 89
    assert metrics.comments == 67
    assert metrics.error is None


def test_build_fetcher_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        build_fetcher("invalid")


def test_parse_public_post_metrics_prefers_embedded_json_counts() -> None:
    metrics = parse_public_post_metrics("https://www.instagram.com/p/ABC123/", "ABC123", POST_HTML)

    assert metrics.shortcode == "ABC123"
    assert metrics.views == 9876
    assert metrics.likes == 1500
    assert metrics.comments == 60
    assert metrics.shares == 22
    assert metrics.saves == 33
    assert metrics.reposts == 4
    assert metrics.error is None


def test_parse_public_post_metrics_reads_browser_dom_metrics_script() -> None:
    html = '<html><head><script type="application/json">{"shortcode":"ABC123","views":"1.2K","shares":4,"reposts":4}</script></head></html>'

    metrics = parse_public_post_metrics("https://www.instagram.com/reel/ABC123/", "ABC123", html)

    assert metrics.views == 1200
    assert metrics.shares == 4
    assert metrics.reposts == 4
    assert metrics.error is None


def test_parse_public_post_metrics_uses_description_comments_without_json() -> None:
    html = '<html><head><meta property="og:description" content="2.5K likes, 101 comments - Campaign post"></head></html>'

    metrics = parse_public_post_metrics("https://www.instagram.com/p/ABC123/", "ABC123", html)

    assert metrics.likes == 2500
    assert metrics.comments == 101
    assert metrics.error is None


def test_update_csv_with_metrics(tmp_path: Path) -> None:
    import csv

    source = tmp_path / "links.csv"
    source.write_text("link\nhttps://www.instagram.com/p/ABC123/\n", encoding="utf-8")

    destination = update_csv_with_metrics(source, fetcher=FixtureFetcher())

    with destination.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][1:11] == ["platform", "views", "likes", "comments", "shares", "saves", "reposts", "shortcode", "source", "error"]
    assert rows[1][1:9] == ["instagram", "9876", "1500", "60", "22", "33", "4", "ABC123"]
