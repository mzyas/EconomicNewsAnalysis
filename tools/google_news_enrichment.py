"""Resolve and verify Google News discovery links without third-party packages."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from typing import Any


class _PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.paragraphs: list[str] = []
        self._in_paragraph = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "").strip()
            if key and content:
                self.meta[key] = content
        if tag.lower() == "p":
            self._in_paragraph = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p" and self._in_paragraph:
            text = " ".join("".join(self._parts).split())
            if text:
                self.paragraphs.append(text)
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._in_paragraph:
            self._parts.append(data)


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _publisher_name(parser: _PageMetadataParser, final_url: str) -> str:
    return _compact(parser.meta.get("og:site_name", "")) or urllib.parse.urlparse(final_url).netloc


def _page_summary(parser: _PageMetadataParser) -> str:
    description = _compact(
        parser.meta.get("description", "") or parser.meta.get("og:description", "")
    )
    if len(description) >= 80:
        return description
    return _compact(" ".join(parser.paragraphs[:4]))


def is_google_discovery(item: dict[str, Any]) -> bool:
    discovery = item.get("discovery", {})
    return isinstance(discovery, dict) and discovery.get("channel") == "google_news"


def enrich_google_news_items(
    items: list[dict[str, Any]], connect_timeout: int, read_timeout: int
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Resolve Google discovery links; retain unverified entries on every failure."""
    enriched: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for item in items:
        updated = dict(item)
        if not is_google_discovery(updated):
            enriched.append(updated)
            continue

        discovery = dict(updated["discovery"])
        original_url = str(discovery.get("original_url") or updated.get("url") or "")
        try:
            request = urllib.request.Request(
                original_url, headers={"User-Agent": "Hermes-Economic-News-Fetcher/1.0"}
            )
            with urllib.request.urlopen(request, timeout=connect_timeout) as response:
                try:
                    response.fp.raw._sock.settimeout(read_timeout)
                except AttributeError:
                    pass
                final_url = response.geturl()
                raw = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            parser = _PageMetadataParser()
            parser.feed(raw)
            summary = _page_summary(parser)
            if urllib.parse.urlparse(final_url).netloc.endswith("news.google.com"):
                raise ValueError("未解析到发布者页面")
            if len(summary) < 80:
                raise ValueError("发布页正文或摘要不足")
            publisher = _publisher_name(parser, final_url)
            hostname = urllib.parse.urlparse(final_url).netloc.lower().replace(".", "_")
            updated["url"] = final_url
            updated["summary"] = summary
            updated["source"] = {
                **updated.get("source", {}),
                "id": f"publisher_{hostname}",
                "name": publisher,
            }
            discovery["verification_status"] = "verified"
            discovery["error"] = None
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            message = str(exc)
            discovery["verification_status"] = "unverified"
            discovery["error"] = message
            errors.append({"id": str(updated.get("id", "")), "name": str(updated.get("title", "")), "error": message})
        updated["discovery"] = discovery
        enriched.append(updated)
    return enriched, errors
