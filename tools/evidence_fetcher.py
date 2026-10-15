"""Evidence fetching from source URLs.

This module provides safe, isolated URL fetching for use as evidence
in LLM-based analysis. Failures are returned as structured dicts,
never raised as exceptions, so a single bad URL cannot break the
broader pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_RESPONSE_SIZE = 2 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024
REQUEST_TIMEOUT = 15
MAX_TEXT_LENGTH = 10000

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; EconomicNewsBot/1.0; "
        "+https://example.com/economic-news-analysis)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _extract_text(html: bytes) -> str:
    """Extract readable text from HTML bytes, returning a whitespace-normalized string."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return " ".join(text.split())


def fetch_evidence(url: str, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    """Fetch and extract text content from a URL.

    Args:
        url: The source URL to fetch.
        timeout: Request timeout in seconds. Split into a connect timeout of
            0.4x (at least 10s) and a read timeout of 1.5x (at least 30s), so
            slow Chinese government sites can finish sending.

    Returns:
        A dict with keys: url, content, error, truncated.
        On success, content is a string (possibly truncated) and error is None.
        On failure, content is None and error describes the failure.
    """
    result: dict[str, Any] = {
        "url": url,
        "content": None,
        "error": None,
        "truncated": False,
    }

    # Use separate connect vs read timeout: connect is shorter, read is longer
    # to handle slow but responsive sites (stats.gov.cn, pbc.gov.cn)
    connect_to = max(10, int(timeout * 0.4))
    read_to = max(30, int(timeout * 1.5))

    try:
        response = requests.get(url, timeout=(connect_to, read_to), headers=DEFAULT_HEADERS, stream=True)
    except requests.Timeout as exc:
        result["error"] = f"timeout: {exc}"
        logger.warning("evidence fetch timeout url=%s err=%s", url, exc)
        return result
    except requests.ConnectionError as exc:
        result["error"] = f"connection error: {exc}"
        logger.warning("evidence fetch connection error url=%s err=%s", url, exc)
        return result
    except requests.RequestException as exc:
        result["error"] = f"request error: {exc}"
        logger.warning("evidence fetch request error url=%s err=%s", url, exc)
        return result

    try:
        if response.status_code >= 400:
            result["error"] = f"http {response.status_code}"
            return result

        # Count bytes while streaming so an oversized body is abandoned
        # before it is fully downloaded into memory.
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_content(chunk_size=READ_CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_RESPONSE_SIZE:
                    result["error"] = (
                        f"response exceeds max size of {MAX_RESPONSE_SIZE} bytes"
                    )
                    return result
                chunks.append(chunk)
        except requests.RequestException as exc:
            result["error"] = f"read error: {exc}"
            logger.warning("evidence fetch read error url=%s err=%s", url, exc)
            return result

        text = _extract_text(b"".join(chunks))
        if not text or not text.strip():
            result["error"] = "empty body"
            return result

        truncated = len(text) > MAX_TEXT_LENGTH
        if truncated:
            text = text[:MAX_TEXT_LENGTH]

        result["content"] = text
        result["truncated"] = truncated
        return result
    finally:
        try:
            response.close()
        except Exception:
            pass


def batch_fetch(
    urls: list[str],
    max_items: int = 5,
    timeout: int = REQUEST_TIMEOUT,
) -> list[dict[str, Any]]:
    """Fetch multiple URLs, isolating failures.

    Only the first ``max_items`` URLs are processed. A failure on any
    single URL is captured in its result dict and does not affect the
    other URLs.
    """
    results: list[dict[str, Any]] = []
    limited = list(urls)[:max_items]
    for url in limited:
        try:
            results.append(fetch_evidence(url, timeout=timeout))
        except Exception as exc:
            logger.exception("evidence fetch unexpected error url=%s", url)
            results.append(
                {
                    "url": url,
                    "content": None,
                    "error": f"unexpected error: {exc}",
                    "truncated": False,
                }
            )
    return results
