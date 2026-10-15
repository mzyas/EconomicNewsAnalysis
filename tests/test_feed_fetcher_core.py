"""Characterization tests for tools.feed_fetcher_core.

They pin the current behaviour of ``fetch_rss``, ``fetch_worldbank_api`` and the
CLI ``main`` (no network: ``urlopen`` and ``fetch_all`` are replaced), so the
functions can be restructured without changing what they do.
"""

import contextlib
import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.feed_fetcher_core import (
    Source,
    fetch_rss,
    fetch_worldbank_api,
    main,
)

URLOPEN = "tools.feed_fetcher_core.urllib.request.urlopen"


class FakeResponse:
    """urlopen() result; ``settimeout`` mimics ``resp.fp.raw._sock.settimeout``."""

    def __init__(self, payload, settimeout=None):
        self.payload = payload
        if settimeout is not None:
            self.fp = SimpleNamespace(
                raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=settimeout))
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def rss_doc(*items, root="rss"):
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><{root} version="2.0"><channel>'
        + "".join(items)
        + f"</channel></{root}>"
    ).encode()


def source(**kw):
    base = {"id": "s1", "name": "Source One", "type": "rss", "url": "https://example.com/rss"}
    base.update(kw)
    return Source(**base)


class FetchRssTests(unittest.TestCase):
    def fetch(self, payload, src=None, max_items=10, **urlopen_kw):
        with patch(URLOPEN, return_value=FakeResponse(payload), **urlopen_kw):
            return fetch_rss(src or source(), max_items, 3, 7)

    def test_item_fields_hash_and_source_metadata(self):
        payload = rss_doc(
            "<item><title> Fed holds </title><link>https://x/a</link>"
            "<description>&lt;b&gt;Rates&lt;/b&gt;  steady</description>"
            "<pubDate>Thu, 04 Jun 2026 16:00:00 +0000</pubDate></item>"
        )
        src = source(country="US", region="NA", language="en", category="official", tags=["rates"])
        items, error = self.fetch(payload, src)
        self.assertIsNone(error)
        (item,) = items
        self.assertEqual(
            (item.source_id, item.source_name, item.title, item.link, item.summary),
            ("s1", "Source One", "Fed holds", "https://x/a", "Rates steady"),
        )
        self.assertEqual(item.published, "2026-06-04T16:00:00+00:00")
        self.assertEqual(item.item_hash, hashlib.md5(b"Fed holdshttps://x/a").hexdigest()[:12])
        self.assertEqual(
            (item.source_tags, item.source_country, item.source_language, item.source_category),
            (["rates"], "US", "en", "official"),
        )
        self.assertTrue(item.fetched_at.endswith("+00:00"))

    def test_country_falls_back_to_region_then_empty(self):
        payload = rss_doc("<item><title>T</title></item>")
        self.assertEqual(self.fetch(payload, source(region="EU"))[0][0].source_country, "EU")
        self.assertEqual(self.fetch(payload, source())[0][0].source_country, "")

    def test_summary_and_date_fallback_order(self):
        payload = rss_doc(
            "<item><title>A</title><description>desc</description><pubTime>2026-06-01</pubTime></item>",
            "<item><title>B</title><summary>sum</summary><content>cont</content></item>",
            "<item><title>C</title><content>cont only</content></item>",
        )
        a, b, c = self.fetch(payload)[0]
        self.assertEqual((a.summary, a.published), ("desc", "2026-06-01T00:00:00+00:00"))
        self.assertEqual((b.summary, b.published), ("sum", ""))
        self.assertEqual((c.summary, c.published), ("cont only", ""))

    def test_items_without_title_are_skipped_and_do_not_count(self):
        payload = rss_doc(
            "<item><link>https://x/untitled</link></item>",
            "<item><title></title></item>",
            "<item><title>A</title></item>",
            "<item><title>B</title></item>",
            "<item><title>C</title></item>",
        )
        items, error = self.fetch(payload, max_items=2)
        self.assertIsNone(error)
        self.assertEqual([i.title for i in items], ["A", "B"])

    def test_www_link_gets_https_but_other_links_are_kept(self):
        payload = rss_doc(
            "<item><title>A</title><link>www.a.example/x</link></item>",
            "<item><title>B</title><link>http://b.example/x</link></item>",
            "<item><title>C</title></item>",
        )
        a, b, c = self.fetch(payload)[0]
        self.assertEqual(
            (a.link, b.link, c.link), ("https://www.a.example/x", "http://b.example/x", "")
        )

    def test_atom_feed_with_namespace(self):
        payload = (
            b'<feed xmlns="http://www.w3.org/2005/Atom">'
            b'<entry><title>Atom one</title><link href="https://a/1"/>'
            b"<summary>Sum <b>bold</b></summary><updated>2026-06-05T01:02:03Z</updated></entry>"
            b'<entry><title>Atom two</title><link href="https://a/2"/>'
            b"<content>Body text</content><published>2026-06-06</published></entry>"
            b"</feed>"
        )
        one, two = self.fetch(payload)[0]
        # ElementTree's ``.text`` stops at the first child element, so markup nested in an
        # Atom <summary> cuts the summary short ("Sum <b>bold</b>" -> "Sum").
        self.assertEqual(
            (one.link, one.summary, one.published),
            ("https://a/1", "Sum", "2026-06-05T01:02:03+00:00"),
        )
        self.assertEqual(
            (two.link, two.summary, two.published),
            ("https://a/2", "Body text", "2026-06-06T00:00:00+00:00"),
        )

    def test_atom_feed_without_namespace(self):
        payload = b'<feed><entry><title>Plain</title><link href="https://p/1"/></entry></feed>'
        items, error = self.fetch(payload)
        self.assertIsNone(error)
        self.assertEqual([(i.title, i.link) for i in items], [("Plain", "https://p/1")])

    def test_unknown_root_with_channel_items(self):
        payload = b"<data><channel><item><title>Odd</title><link>https://o/1</link></item></channel></data>"
        items, error = self.fetch(payload)
        self.assertIsNone(error)
        self.assertEqual([i.title for i in items], ["Odd"])

    def test_unknown_root_with_atom_entries(self):
        payload = (
            b'<data xmlns:a="http://www.w3.org/2005/Atom">'
            b'<a:entry><a:title>Entry</a:title><a:link href="https://o/2"/></a:entry></data>'
        )
        items, error = self.fetch(payload)
        self.assertIsNone(error)
        self.assertEqual([(i.title, i.link) for i in items], [("Entry", "https://o/2")])

    def test_rss_without_channel_is_empty_not_an_error(self):
        self.assertEqual(self.fetch(b'<rss version="2.0"></rss>'), ([], None))

    def test_gzip_payload_is_decompressed(self):
        items, error = self.fetch(gzip.compress(rss_doc("<item><title>Zipped</title></item>")))
        self.assertIsNone(error)
        self.assertEqual([i.title for i in items], ["Zipped"])

    def test_http_error(self):
        err = urllib.error.HTTPError("https://u", 503, "Unavailable", {}, None)
        with patch(URLOPEN, side_effect=err):
            self.assertEqual(fetch_rss(source(), 5, 1, 1), ([], "HTTP 503"))

    def test_url_error(self):
        with patch(URLOPEN, side_effect=urllib.error.URLError("name not resolved")):
            self.assertEqual(fetch_rss(source(), 5, 1, 1), ([], "Network: name not resolved"))

    def test_other_exception_is_reported_by_message(self):
        with patch(URLOPEN, side_effect=TimeoutError("read timed out")):
            self.assertEqual(fetch_rss(source(), 5, 1, 1), ([], "read timed out"))

    def test_invalid_xml(self):
        items, error = self.fetch(b"<rss><channel>")
        self.assertEqual(items, [])
        self.assertTrue(error.startswith("XML parse error: "), error)

    def test_request_headers_timeouts(self):
        calls = []
        with patch(
            URLOPEN, return_value=FakeResponse(rss_doc(), settimeout=calls.append)
        ) as urlopen:
            fetch_rss(source(url="https://example.com/feed"), 5, 3, 7)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.com/feed")
        self.assertEqual(request.get_header("User-agent"), "Hermes-Economic-News-Fetcher/1.0")
        self.assertEqual(urlopen.call_args.kwargs, {"timeout": 3})
        self.assertEqual(
            calls, [7]
        )  # read timeout is applied separately when the socket is reachable


WB_URL = "https://wb.example/api"


def wb_payload(documents, gz=False):
    raw = json.dumps({"documents": documents}).encode()
    return gzip.compress(raw) if gz else raw


WB_DOCS = {
    "d1": {
        "title": {"cdata!": "Press one"},
        "url": "https://x/en/news/press-release/2026/06/05/one",
        "cqpath": "/en/news",
    },
    "d2": {
        "title": {"#text": "Text title"},
        "url": "https://x/en/news/feature/2026/05/01/two",
        "cqpath": "",
    },
    "d3": {"title": "Plain title", "url": "https://x/news/other/no-date", "cqpath": "/en/foo"},
    "d4": {"title": None, "url": "https://x/en/news/speech/2026/01/02/four", "cqpath": "/en"},
    "d5": {
        "title": {"other": 1},
        "url": "https://x/en/news/speech/2026/01/03/five",
        "cqpath": "/en",
    },
    "skip_not_news": {"title": "N", "url": "https://x/en/topic/abc", "cqpath": "/en"},
    "skip_not_english": {
        "title": "F",
        "url": "https://x/fr/news/press-release/2026/06/05/f",
        "cqpath": "/fr/news",
    },
}


class FetchWorldBankTests(unittest.TestCase):
    def fetch(self, payload, src=None, max_items=10):
        src = src or source(id="wb", name="World Bank", type="worldbank_api", url=WB_URL)
        with patch(URLOPEN, return_value=FakeResponse(payload)) as urlopen:
            result = fetch_worldbank_api(src, max_items, 3, 7)
        return result, urlopen

    def test_titles_dates_and_filters(self):
        (items, error), _ = self.fetch(wb_payload(WB_DOCS))
        self.assertIsNone(error)
        self.assertEqual(
            [(i.title, i.link.rsplit("/", 1)[1], i.published) for i in items],
            [
                ("Press one", "one", "2026-06-05"),
                ("Text title", "two", "2026-05-01"),
                ("Plain title", "no-date", ""),
                ("", "four", "2026-01-02"),
                ("", "five", "2026-01-03"),
            ],
        )

    def test_item_metadata_and_hash(self):
        src = source(
            id="wb",
            name="World Bank",
            type="worldbank_api",
            url=WB_URL,
            language="en",
            category="mdb",
            tags=["wb"],
        )
        (items, _), _ = self.fetch(wb_payload({"d1": WB_DOCS["d1"]}), src)
        (item,) = items
        self.assertEqual((item.source_id, item.source_name, item.summary), ("wb", "World Bank", ""))
        self.assertEqual(
            (item.source_tags, item.source_language, item.source_category), (["wb"], "en", "mdb")
        )
        expected = hashlib.md5(("Press one" + WB_DOCS["d1"]["url"]).encode()).hexdigest()[:12]
        self.assertEqual(item.item_hash, expected)
        self.assertTrue(item.fetched_at.endswith("+00:00"))

    def test_country_falls_back_to_region_then_global(self):
        docs = {"d1": WB_DOCS["d1"]}

        def country_of(**kw):
            src = source(type="worldbank_api", url=WB_URL, **kw)
            (items, _), _ = self.fetch(wb_payload(docs), src)
            return items[0].source_country

        self.assertEqual(country_of(country="US"), "US")
        self.assertEqual(country_of(region="EU"), "EU")
        self.assertEqual(country_of(), "Global")

    def test_max_items_limit(self):
        (items, _), _ = self.fetch(wb_payload(WB_DOCS), max_items=2)
        self.assertEqual([i.title for i in items], ["Press one", "Text title"])

    def test_request_url_rows_and_timeouts(self):
        for max_items, rows in [(2, 30), (6, 30), (10, 50)]:
            _, urlopen = self.fetch(wb_payload({}), max_items=max_items)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, f"{WB_URL}?format=json&rows={rows}&order=desc&qdr=w")
            self.assertEqual(request.get_header("User-agent"), "Hermes-Economic-News-Fetcher/1.0")
            self.assertEqual(urlopen.call_args.kwargs, {"timeout": 3})

    def test_read_timeout_applied_when_socket_reachable(self):
        calls = []
        with patch(URLOPEN, return_value=FakeResponse(wb_payload({}), settimeout=calls.append)):
            fetch_worldbank_api(source(type="worldbank_api", url=WB_URL), 5, 3, 7)
        self.assertEqual(calls, [7])

    def test_gzip_payload(self):
        (items, error), _ = self.fetch(wb_payload({"d1": WB_DOCS["d1"]}, gz=True))
        self.assertIsNone(error)
        self.assertEqual([i.title for i in items], ["Press one"])

    def test_missing_documents_is_empty(self):
        self.assertEqual(self.fetch(b"{}")[0], ([], None))

    def test_errors(self):
        src = source(type="worldbank_api", url=WB_URL)
        cases = [
            (urllib.error.HTTPError(WB_URL, 429, "Too Many", {}, None), "HTTP 429"),
            (urllib.error.URLError("refused"), "Network: refused"),
            (OSError("reset by peer"), "reset by peer"),
        ]
        for exc, expected in cases:
            with self.subTest(expected=expected), patch(URLOPEN, side_effect=exc):
                self.assertEqual(fetch_worldbank_api(src, 5, 1, 1), ([], expected))

    def test_invalid_json_is_reported(self):
        (items, error), _ = self.fetch(b"not json")
        self.assertEqual(items, [])
        self.assertTrue(error.startswith("Expecting value"), error)


CONFIG = """\
sources:
  - {id: us-fed, name: Fed, type: rss, url: "https://fed/rss", country: US, language: en, tags: [monetary_policy, rates]}
  - {id: jp-boj, name: BOJ, type: rss, url: "https://boj/rss", country: JP, language: ja, tags: [boj, jpy]}
  - {id: eu-ecb, name: ECB, type: google_news_rss, url: "https://ecb/rss", region: EU, language: en, tags: [ecb]}
  - {id: cn-pbc, name: PBoC, type: official_page, url: "https://pbc", country: CN, language: zh}
"""

RESULTS = {
    "fetched_at": "2026-06-10T00:00:00+00:00",
    "total_items": 2,
    "sources_successful": 1,
    "sources_failed": 1,
    "sources_skipped": 1,
    "items": [
        {
            "source_id": "a",
            "title": "T1",
            "link": "https://l/1",
            "published": "P",
            "summary": "S",
            "source_name": "N",
            "item_hash": "h1",
            "fetched_at": "F",
        },
        {"title": "T2", "link": "https://l/2"},
    ],
    "errors": [{"id": "jp-boj", "name": "BOJ", "error": "HTTP 500"}],
    "skipped": [{"id": "cn-pbc"}],
}
CLEAN_RESULTS = {**RESULTS, "errors": [], "skipped": [], "sources_failed": 0, "sources_skipped": 0}


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = self.tmp / "sources.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")

    def run_main(self, *argv, results=RESULTS):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["feed_fetcher_core.py", *argv]),
            patch("tools.feed_fetcher_core.fetch_all", return_value=results) as fetch_all,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = main()
        return code, out.getvalue(), err.getvalue(), fetch_all

    def listed_ids(self, *flags):
        code, out, _, fetch_all = self.run_main("--source", str(self.config), "--list", *flags)
        self.assertEqual(code, 0)
        fetch_all.assert_not_called()
        return [line.split()[0] for line in out.splitlines()[3:-2] if line.strip()]

    def test_missing_config(self):
        code, out, err, fetch_all = self.run_main("--source", str(self.tmp / "missing.yaml"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), f"Config not found: {self.tmp / 'missing.yaml'}")
        fetch_all.assert_not_called()

    def test_list_prints_table_and_total(self):
        code, out, _, _ = self.run_main("--source", str(self.config), "--list")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[1].startswith("ID") and lines[1].endswith("NAME"))
        self.assertEqual(lines[2], "-" * 100)
        self.assertIn("us-fed", lines[3])
        self.assertIn("RSS", lines[3])
        self.assertIn("GNEWS", lines[5])
        self.assertIn("EU", lines[5])  # region is shown when there is no country
        self.assertIn("PAGE", lines[6])
        self.assertEqual(lines[-1], "Total: 4 sources")

    def test_filters(self):
        self.assertEqual(self.listed_ids("--country", "us"), ["us-fed"])
        self.assertEqual(
            self.listed_ids("--country", "jp, eu"), ["jp-boj", "eu-ecb"]
        )  # region matches too
        self.assertEqual(self.listed_ids("--tags", "MONETARY_POLICY"), ["us-fed"])
        self.assertEqual(self.listed_ids("--tags", "boj,ecb"), ["jp-boj", "eu-ecb"])
        self.assertEqual(self.listed_ids("--language", "ja,zh"), ["jp-boj", "cn-pbc"])
        self.assertEqual(self.listed_ids("--country", "us,jp", "--language", "ja"), ["jp-boj"])

    def test_fetch_defaults_and_stdout_json(self):
        code, out, _, fetch_all = self.run_main("--source", str(self.config))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), RESULTS)
        self.assertEqual(out, json.dumps(RESULTS, ensure_ascii=False, indent=2) + "\n")
        args, kwargs = fetch_all.call_args
        self.assertEqual([s.id for s in args[0]], ["us-fed", "jp-boj", "eu-ecb", "cn-pbc"])
        self.assertEqual(
            kwargs,
            {
                "max_per_source": 10,
                "connect_timeout": 10,
                "read_timeout": 40,
                "skip_types": {"official_page", "api"},
            },
        )

    def test_fetch_options_are_passed_through(self):
        _, _, _, fetch_all = self.run_main(
            "--source",
            str(self.config),
            "--max-per-source",
            "3",
            "--connect-timeout",
            "5",
            "--read-timeout",
            "7",
            "--include-official",
            "--country",
            "US",
        )
        args, kwargs = fetch_all.call_args
        self.assertEqual([s.id for s in args[0]], ["us-fed"])
        self.assertEqual(
            kwargs,
            {"max_per_source": 3, "connect_timeout": 5, "read_timeout": 7, "skip_types": set()},
        )

    def test_compact_keeps_only_summary_fields(self):
        _, out, _, _ = self.run_main("--source", str(self.config), "--compact")
        self.assertEqual(
            json.loads(out)["items"],
            [
                {
                    "title": "T1",
                    "source_name": "N",
                    "link": "https://l/1",
                    "published": "P",
                    "summary": "S",
                },
                {"title": "T2", "link": "https://l/2"},
            ],
        )

    def test_output_file_and_summary_lines(self):
        target = self.tmp / "out.json"
        code, out, _, _ = self.run_main("--source", str(self.config), "--output", str(target))
        self.assertEqual(code, 0)
        self.assertEqual(
            target.read_text(encoding="utf-8"), json.dumps(RESULTS, ensure_ascii=False, indent=2)
        )
        self.assertEqual(
            out.splitlines(),
            [
                f"Fetched 2 items from 1 sources → {target}",
                "  1 source(s) failed (see errors in JSON)",
                "  1 source(s) skipped (official_page/api)",
            ],
        )

    def test_output_file_without_errors_or_skips_prints_one_line(self):
        target = self.tmp / "clean.json"
        _, out, _, _ = self.run_main(
            "--source", str(self.config), "--output", str(target), results=CLEAN_RESULTS
        )
        self.assertEqual(out.splitlines(), [f"Fetched 2 items from 1 sources → {target}"])

    def test_log_entry_is_appended_and_parent_created(self):
        log = self.tmp / "nested" / "dir" / "fetch.jsonl"
        self.run_main("--source", str(self.config), "--log", str(log))
        self.run_main("--source", str(self.config), "--log", str(log), results=CLEAN_RESULTS)
        first, second = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(
            first,
            {
                "fetched_at": "2026-06-10T00:00:00+00:00",
                "sources_successful": 1,
                "sources_failed": 1,
                "total_items": 2,
                "failed_ids": ["jp-boj"],
            },
        )
        self.assertEqual(second["failed_ids"], [])

    def test_log_and_output_together_still_print_summary(self):
        log, target = self.tmp / "l.jsonl", self.tmp / "o.json"
        _, out, _, _ = self.run_main(
            "--source", str(self.config), "--log", str(log), "--output", str(target)
        )
        self.assertTrue(log.exists() and target.exists())
        self.assertTrue(out.startswith("Fetched 2 items"))


if __name__ == "__main__":
    unittest.main()
