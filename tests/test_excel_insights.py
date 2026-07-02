from pathlib import Path

from app.excel_insights import extract_shortcode, parse_public_post_metrics, update_csv_with_metrics

POST_HTML = """
<html><head>
<meta property="og:description" content="1,234 likes, 56 comments - Campaign post">
<script>
window.__post = {"items":[{"shortcode":"ABC123","is_video":true,"video_view_count":9876,
"edge_liked_by":{"count":1500},"edge_media_to_comment":{"count":60},"share_count":22,"save_count":33,"repost_count":4}]};
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
    assert rows[0][1:10] == ["views", "likes", "comments", "shares", "saves", "reposts", "shortcode", "source", "error"]
    assert rows[1][1:8] == ["9876", "1500", "60", "22", "33", "4", "ABC123"]
