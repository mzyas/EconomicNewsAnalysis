import unittest
from unittest.mock import patch

from tools.google_news_enrichment import enrich_google_news_items


class _Response:
    def __init__(self, url, body):
        self._url = url
        self._body = body.encode("utf-8")
        self.headers = type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self._url

    def read(self):
        return self._body


class GoogleNewsEnrichmentTests(unittest.TestCase):
    def _item(self):
        return {
            "id": "g1", "title": "Discovery title", "url": "https://news.google.com/item", "summary": "",
            "source": {"id": "google_news_test", "name": "Google News", "country": "US", "language": "en"},
            "discovery": {"channel": "google_news", "feed_source_id": "google_news_test", "feed_source_name": "Google News", "original_url": "https://news.google.com/item", "verification_status": "unverified", "error": "尚未核验发布页"},
        }

    @patch("tools.google_news_enrichment.urllib.request.urlopen")
    def test_success_replaces_source_and_keeps_discovery_metadata(self, mocked_open):
        summary = "This verified source summary is long enough to satisfy the required verification threshold for testing."
        mocked_open.return_value = _Response("https://publisher.example/story", f"<meta property='og:site_name' content='Publisher'><meta name='description' content='{summary}'>")
        items, errors = enrich_google_news_items([self._item()], 5, 5)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["discovery"]["verification_status"], "verified")
        self.assertEqual(items[0]["source"]["name"], "Publisher")
        self.assertEqual(items[0]["url"], "https://publisher.example/story")
        self.assertEqual(items[0]["summary"], summary)

    @patch("tools.google_news_enrichment.urllib.request.urlopen", side_effect=OSError("blocked"))
    def test_failure_is_retained_and_reported(self, mocked_open):
        items, errors = enrich_google_news_items([self._item()], 5, 5)
        self.assertEqual(items[0]["discovery"]["verification_status"], "unverified")
        self.assertEqual(len(errors), 1)
        self.assertIn("blocked", errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
