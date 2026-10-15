"""Runtime adapter for source loading, filtering, and fetching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .feed_fetcher_core import Source, fetch_all, load_config


def filter_sources(sources: list[Source], filters: dict[str, Any]) -> list[Source]:
    countries = {
        str(value).upper() for value in filters.get("countries", []) if value
    }
    languages = {
        str(value).lower() for value in filters.get("languages", []) if value
    }
    tags = {str(value).lower() for value in filters.get("tags", []) if value}
    source_ids = {str(value) for value in filters.get("source_ids", []) if value}

    selected: list[Source] = []
    for source in sources:
        if source_ids and source.id not in source_ids:
            continue
        if countries and not (
            source.country.upper() in countries or source.region.upper() in countries
        ):
            continue
        if languages and source.language.lower() not in languages:
            continue
        if tags and not (tags & {tag.lower() for tag in source.tags}):
            continue
        selected.append(source)
    return selected


def fetch_from_config(config: dict[str, Any]) -> dict[str, Any]:
    source_path = Path(config["source_config"])
    sources = filter_sources(load_config(source_path), config.get("filters", {}))
    include_manual = bool(config.get("include_manual_sources", False))
    skip_types = set() if include_manual else {"official_page", "api"}
    return fetch_all(
        sources,
        max_per_source=int(config["max_items_per_source"]),
        connect_timeout=int(config["connect_timeout"]),
        read_timeout=int(config["read_timeout"]),
        skip_types=skip_types,
        pause_seconds=float(config.get("request_pause_seconds", 0.3)),
    )


def source_inventory(config: dict[str, Any]) -> list[dict[str, Any]]:
    source_path = Path(config["source_config"])
    sources = filter_sources(load_config(source_path), config.get("filters", {}))
    return [
        {
            "id": source.id,
            "name": source.name,
            "type": source.type,
            "country": source.country,
            "region": source.region,
            "language": source.language,
            "category": source.category,
            "tags": source.tags,
        }
        for source in sources
    ]
