import asyncio

from app.providers.instagram_crawler import PublicInstagramCrawlerProvider, parse_count


SAMPLE_HTML = """
<html>
  <head>
    <title>National Geographic • Instagram photos and videos</title>
    <meta property="og:description" content="284M Followers, 158 Following, 10K Posts - See Instagram photos and videos from National Geographic (@natgeo)">
    <script type="application/ld+json">{"@type":"Person","name":"National Geographic"}</script>
    <script>
      window.__fixtures = {
        "user": {
          "full_name": "National Geographic",
          "biography": "Experience the world through the eyes of National Geographic photographers.",
          "is_verified": true,
          "edge_followed_by": {"count": 284000000},
          "edge_follow": {"count": 158},
          "edge_owner_to_timeline_media": {
            "edges": [
              {"node": {
                "id": "1",
                "shortcode": "ABC123",
                "taken_at_timestamp": 1710000000,
                "is_video": true,
                "video_view_count": 1200,
                "edge_liked_by": {"count": 300},
                "edge_media_to_comment": {"count": 12},
                "edge_media_to_caption": {"edges": [{"node": {"text": "Hello from Delhi"}}]},
                "location": {"name": "India"}
              }},
              {"node": {
                "id": "2",
                "shortcode": "DEF456",
                "taken_at_timestamp": 1710000300,
                "is_video": false,
                "edge_media_preview_like": {"count": 500},
                "edge_media_to_comment": {"count": 20}
              }}
            ]
          }
        }
      };
    </script>
  </head>
  <body></body>
</html>
"""


class FixtureFetcher:
    def fetch(self, url: str) -> str:
        assert url == "https://www.instagram.com/natgeo/"
        return SAMPLE_HTML


def test_parse_count_suffixes() -> None:
    assert parse_count("12") == 12
    assert parse_count("1.2K") == 1200
    assert parse_count("284M") == 284_000_000


def test_crawler_provider_parses_public_profile_html() -> None:
    provider = PublicInstagramCrawlerProvider(fetcher=FixtureFetcher())

    profile = asyncio.run(provider.fetch_profile("natgeo", 30))

    assert profile.username == "natgeo"
    assert profile.full_name == "National Geographic"
    assert profile.follower_count == 284_000_000
    assert profile.following_count == 158
    assert profile.is_verified is True
    assert len(profile.posts) == 2
    assert profile.posts[0].id == "ABC123"
    assert profile.posts[0].views == 1200
    assert profile.posts[0].likes == 300
    assert profile.posts[0].comments == 12
    assert profile.posts[0].location == "India"
