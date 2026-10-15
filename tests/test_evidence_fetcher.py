"""Tests for evidence fetcher (URL -> text evidence)."""

import unittest
from unittest.mock import patch, MagicMock

from tools.evidence_fetcher import (
    fetch_evidence,
    batch_fetch,
    MAX_RESPONSE_SIZE,
    MAX_TEXT_LENGTH,
    READ_CHUNK_SIZE,
)


class FetchEvidenceTests(unittest.TestCase):
    def test_returns_structured_dict_with_required_keys(self):
        result = fetch_evidence("https://example.com/x")
        self.assertIn("url", result)
        self.assertIn("content", result)
        self.assertIn("error", result)
        self.assertIn("truncated", result)
        self.assertEqual(result["url"], "https://example.com/x")

    @patch("tools.evidence_fetcher.requests.get")
    def test_http_200_returns_extracted_text(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [(
            b"<html><head><title>News</title></head>"
            b"<body><article>Federal Reserve raised rates by 25bps.</article></body></html>"
        )]
        mock_response.apparent_encoding = "utf-8"
        mock_get.return_value = mock_response

        result = fetch_evidence("https://example.com/news")

        self.assertIsNone(result["error"])
        self.assertIn("Federal Reserve raised rates", result["content"])
        self.assertFalse(result["truncated"])

    @patch("tools.evidence_fetcher.requests.get")
    def test_http_404_returns_structured_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.iter_content.return_value = [b""]
        mock_get.return_value = mock_response

        result = fetch_evidence("https://example.com/missing")

        self.assertIsNone(result["content"])
        self.assertIsNotNone(result["error"])
        self.assertIn("404", result["error"])
        self.assertFalse(result["truncated"])

    @patch("tools.evidence_fetcher.requests.get")
    def test_timeout_returns_structured_error_no_exception(self, mock_get):
        import requests as real_requests

        mock_get.side_effect = real_requests.Timeout("read timed out")

        result = fetch_evidence("https://example.com/slow", timeout=1)

        self.assertIsNone(result["content"])
        self.assertIsNotNone(result["error"])
        self.assertIn("timeout", result["error"].lower())
        self.assertFalse(result["truncated"])

    @patch("tools.evidence_fetcher.requests.get")
    def test_connection_error_returns_structured_error(self, mock_get):
        import requests as real_requests

        mock_get.side_effect = real_requests.ConnectionError("dns failure")

        result = fetch_evidence("https://broken.invalid/")

        self.assertIsNone(result["content"])
        self.assertIsNotNone(result["error"])
        self.assertIn("connection", result["error"].lower())

    @patch("tools.evidence_fetcher.requests.get")
    def test_empty_body_returns_structured_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b"<html><body></body></html>"]
        mock_get.return_value = mock_response

        result = fetch_evidence("https://example.com/empty")

        self.assertIsNone(result["content"])
        self.assertIsNotNone(result["error"])
        self.assertIn("empty", result["error"].lower())

    @patch("tools.evidence_fetcher.requests.get")
    def test_oversized_response_truncates_and_marks_flag(self, mock_get):
        big_text = "A" * (MAX_TEXT_LENGTH + 500)
        html = f"<html><body><p>{big_text}</p></body></html>".encode("utf-8")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [html]
        mock_get.return_value = mock_response

        result = fetch_evidence("https://example.com/big")

        self.assertIsNone(result["error"])
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["content"]), MAX_TEXT_LENGTH)

    @patch("tools.evidence_fetcher.requests.get")
    def test_response_over_max_size_is_rejected_as_structured_error(self, mock_get):
        chunk = b"x" * READ_CHUNK_SIZE
        total_chunks = MAX_RESPONSE_SIZE // READ_CHUNK_SIZE + 10
        consumed = []

        def stream(chunk_size):
            for _ in range(total_chunks):
                consumed.append(1)
                yield chunk

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.side_effect = stream
        mock_get.return_value = mock_response

        result = fetch_evidence("https://example.com/huge")

        self.assertIsNone(result["content"])
        self.assertIsNotNone(result["error"])
        self.assertIn("size", result["error"].lower())
        # Reading stops at the first chunk past the limit.
        self.assertEqual(len(consumed), MAX_RESPONSE_SIZE // READ_CHUNK_SIZE + 1)

    @patch("tools.evidence_fetcher.requests.get")
    def test_error_while_reading_body_returns_structured_error(self, mock_get):
        import requests as real_requests

        def stream(chunk_size):
            yield b"<html><body>partial"
            raise real_requests.exceptions.ChunkedEncodingError("connection reset")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.side_effect = stream
        mock_get.return_value = mock_response

        result = fetch_evidence("https://example.com/reset")

        self.assertIsNone(result["content"])
        self.assertIn("read error", result["error"])


class BatchFetchTests(unittest.TestCase):
    @patch("tools.evidence_fetcher.fetch_evidence")
    def test_returns_list_of_results(self, mock_fetch):
        mock_fetch.side_effect = lambda url, **kw: {
            "url": url,
            "content": f"body of {url}",
            "error": None,
            "truncated": False,
        }

        results = batch_fetch(
            ["https://a.com", "https://b.com", "https://c.com"],
            max_items=5,
        )

        self.assertEqual(len(results), 3)
        for result in results:
            self.assertIsNone(result["error"])
            self.assertIsNotNone(result["content"])

    @patch("tools.evidence_fetcher.fetch_evidence")
    def test_respects_max_items(self, mock_fetch):
        mock_fetch.side_effect = lambda url, **kw: {
            "url": url,
            "content": "x",
            "error": None,
            "truncated": False,
        }

        urls = [f"https://example.com/{i}" for i in range(10)]
        results = batch_fetch(urls, max_items=3)

        self.assertEqual(len(results), 3)
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("tools.evidence_fetcher.fetch_evidence")
    def test_single_failure_does_not_block_others(self, mock_fetch):
        def side_effect(url, **kw):
            if "bad" in url:
                return {
                    "url": url,
                    "content": None,
                    "error": "timeout: bad",
                    "truncated": False,
                }
            return {
                "url": url,
                "content": f"ok {url}",
                "error": None,
                "truncated": False,
            }

        mock_fetch.side_effect = side_effect

        results = batch_fetch(
            [
                "https://good.com/1",
                "https://bad.com/1",
                "https://good.com/2",
            ],
            max_items=5,
        )

        self.assertEqual(len(results), 3)
        ok = [r for r in results if r["error"] is None]
        err = [r for r in results if r["error"] is not None]
        self.assertEqual(len(ok), 2)
        self.assertEqual(len(err), 1)
        self.assertIn("bad.com", err[0]["url"])

    @patch("tools.evidence_fetcher.fetch_evidence")
    def test_unexpected_exception_in_single_url_is_isolated(self, mock_fetch):
        def side_effect(url, **kw):
            if "boom" in url:
                raise RuntimeError("unexpected")
            return {
                "url": url,
                "content": "ok",
                "error": None,
                "truncated": False,
            }

        mock_fetch.side_effect = side_effect

        results = batch_fetch(
            ["https://ok.com", "https://boom.com", "https://ok2.com"],
            max_items=5,
        )

        self.assertEqual(len(results), 3)
        err = [r for r in results if r["error"] is not None]
        self.assertEqual(len(err), 1)
        self.assertIn("boom.com", err[0]["url"])
        self.assertIn("unexpected", err[0]["error"].lower())

    @patch("tools.evidence_fetcher.fetch_evidence")
    def test_empty_url_list_returns_empty(self, mock_fetch):
        results = batch_fetch([], max_items=5)
        self.assertEqual(results, [])
        mock_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
