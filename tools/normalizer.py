"""Normalize fetched or user-provided news items."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_news_item(item: dict[str, Any]) -> dict[str, Any]:
    title = _text(item.get("title"))
    link = _text(item.get("link") or item.get("url"))
    summary = _text(item.get("summary") or item.get("description"))
    item_id = _text(item.get("item_hash") or item.get("id"))
    if not item_id:
        item_id = hashlib.sha256(f"{title}\n{link}".encode("utf-8")).hexdigest()[:16]

    tags = item.get("source_tags") or item.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]

    source_id = _text(item.get("source_id"))
    discovery = item.get("discovery")
    if not isinstance(discovery, dict) and source_id.startswith("google_news_"):
        discovery = {
            "channel": "google_news",
            "feed_source_id": source_id,
            "feed_source_name": _text(item.get("source_name") or item.get("source")),
            "original_url": link,
            "verification_status": "unverified",
            "error": "尚未核验发布页",
        }

    normalized = {
        "id": item_id,
        "title": title,
        "translated_title": _text(item.get("translated_title")),
        "url": link,
        "summary": summary,
        "published_at": _text(item.get("published") or item.get("published_at")),
        "fetched_at": _text(item.get("fetched_at"))
        or datetime.now(timezone.utc).isoformat(),
        "source": {
            "id": source_id,
            "name": _text(item.get("source_name") or item.get("source")),
            "country": _text(item.get("source_country") or item.get("country")),
            "language": _text(item.get("source_language") or item.get("language")),
            "category": _text(item.get("source_category") or item.get("category")),
        },
        "tags": sorted({_text(tag) for tag in tags if _text(tag)}),
    }
    if isinstance(discovery, dict):
        normalized["discovery"] = {
            "channel": _text(discovery.get("channel")),
            "feed_source_id": _text(discovery.get("feed_source_id")),
            "feed_source_name": _text(discovery.get("feed_source_name")),
            "original_url": _text(discovery.get("original_url")),
            "verification_status": _text(discovery.get("verification_status")) or "unverified",
            "error": discovery.get("error"),
        }
    return normalized


def normalize_news_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [normalize_news_item(item) for item in items]
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in normalized:
        if not item["title"] or item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique
