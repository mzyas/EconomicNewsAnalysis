import gzip
import unittest
from unittest.mock import patch

from tools.feed_fetcher_core import FeedItem, Source, _parse_pubdate, fetch_all, fetch_rss


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>Policy update</title>
<link>www.example.com/policy</link>
<description><![CDATA[<p>Rates unchanged.</p>]]></description>
<pubDate>4 Jun 2026 16:00:00 GMT</pubDate>
</item>
</channel></rss>"""

LONG_SUMMARY = "A" * 1200


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class FeedParserTests(unittest.TestCase):
    def test_gzip_rss_url_and_date_normalization(self):
        source = Source(
            id="test",
            name="Test",
            type="rss",
            url="https://example.com/rss",
            country="US",
            tags=["monetary_policy"],
        )
        with patch(
            "tools.feed_fetcher_core.urllib.request.urlopen",
            return_value=FakeResponse(gzip.compress(RSS)),
        ):
            items, error = fetch_rss(source, 5, 1, 1)
        self.assertIsNone(error)
        self.assertEqual(items[0].link, "https://www.example.com/policy")
        self.assertTrue(items[0].published.startswith("2026-06-04T16:00:00"))
        self.assertEqual(items[0].summary, "Rates unchanged.")

    def test_unknown_date_is_preserved(self):
        self.assertEqual(_parse_pubdate("not-a-date"), "not-a-date")

    def test_source_summary_is_not_truncated(self):
        rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Long summary</title>
<link>https://example.com/long</link>
<description>{LONG_SUMMARY}</description>
</item></channel></rss>""".encode()
        source = Source(
            id="long",
            name="Long",
            type="rss",
            url="https://example.com/rss",
        )
        with patch(
            "tools.feed_fetcher_core.urllib.request.urlopen",
            return_value=FakeResponse(rss),
        ):
            items, error = fetch_rss(source, 5, 1, 1)
        self.assertIsNone(error)
        self.assertEqual(items[0].summary, LONG_SUMMARY)

    def test_one_source_failure_does_not_stop_batch(self):
        sources = [
            Source(id="bad", name="Bad", type="rss", url="https://bad"),
            Source(id="good", name="Good", type="rss", url="https://good"),
        ]
        good_item = FeedItem(
            source_id="good",
            source_name="Good",
            title="Valid item",
            link="https://good/item",
            summary="",
            published="",
            fetched_at="2026-06-10T00:00:00+00:00",
            item_hash="abc",
        )
        with patch(
            "tools.feed_fetcher_core.fetch_rss",
            side_effect=[([], "HTTP 500"), ([good_item], None)],
        ):
            result = fetch_all(sources, 5, 1, 1, pause_seconds=0)
        self.assertEqual(result["sources_failed"], 1)
        self.assertEqual(result["sources_successful"], 1)
        self.assertEqual(result["total_items"], 1)


if __name__ == "__main__":
    unittest.main()
