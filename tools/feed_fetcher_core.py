#!/usr/bin/env python3
"""Core economic-news source loading and fetching implementation.

Usage:
    python fetch_feeds.py                           # fetch all RSS sources
    python fetch_feeds.py --country JP,US            # filter by country
    python fetch_feeds.py --tags inflation,boj       # filter by tags
    python fetch_feeds.py --source path/to/config.yaml
    python fetch_feeds.py --output /tmp/feeds.json   # custom output path
    python fetch_feeds.py --max-per-source 5         # limit items per source
    python fetch_feeds.py --list                     # list sources without fetching

Output: JSON array of feed items with source metadata.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ── data model ──────────────────────────────────────────────────────────────


@dataclass
class Source:
    id: str
    name: str
    type: str
    url: str
    country: str = ""
    region: str = ""
    language: str = "en"
    category: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class FeedItem:
    source_id: str
    source_name: str
    title: str
    link: str
    summary: str
    published: str
    fetched_at: str
    item_hash: str
    source_tags: list[str] = field(default_factory=list)
    source_country: str = ""
    source_language: str = ""
    source_category: str = ""


# ── config loader ───────────────────────────────────────────────────────────


def load_config(path: Path) -> list[Source]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [Source(**s) for s in data.get("sources", [])]


# ── RSS / Atom parser (stdlib only) ─────────────────────────────────────────


def _find_text(el: ET.Element, tag: str, ns: str = "") -> str:
    """Find text in element, trying with and without namespace."""
    if ns:
        found = el.find(f"{{{ns}}}{tag}")
        if found is not None and found.text:
            return found.text.strip()
    # Try without namespace
    for child in el:
        if child.tag.endswith(tag) or child.tag == tag:
            if child.text:
                return child.text.strip()
    return ""


def _get_link(el: ET.Element) -> str:
    """Extract link from RSS <link> or Atom <link href='...'>."""
    # RSS: <link>url</link>
    link = _find_text(el, "link")
    if link:
        return link
    # Atom: <link href="url" />
    for child in el:
        if child.tag.endswith("link"):
            href = child.get("href", "")
            if href:
                return href
    return ""


def _parse_pubdate(raw: str) -> str:
    """Try to parse various date formats and return ISO 8601."""
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",  # RFC 2822
        "%a, %d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %Z",  # PBoC: "4 Jun 2026 16:00:00 GMT"
        "%d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",  # ISO 8601
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return raw.strip()


def _clean_html(raw: str) -> str:
    """Strip basic HTML tags."""
    import re

    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


_ATOM_NS = "http://www.w3.org/2005/Atom"


def _fetch_bytes(
    url: str, connect_timeout: int, read_timeout: int
) -> tuple[bytes | None, str | None]:
    """GET ``url`` (gzip-aware). Returns ``(body, None)`` or ``(None, error)``.

    ``error`` is ``str(exc)`` for unexpected failures, which is ``""`` for an exception
    without a message, so callers must test ``error is not None``.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Hermes-Economic-News-Fetcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=connect_timeout) as resp:
            # Set read timeout separately so slow transfers don't use connect timeout
            try:
                resp.fp.raw._sock.settimeout(read_timeout)
            except AttributeError:
                pass
            raw = resp.read()
            # Decompress gzip by magic bytes (more reliable than Content-Encoding header)
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"Network: {e.reason}"
    except Exception as e:
        return None, str(e)
    return raw, None


def _feed_entries(root: ET.Element) -> list[ET.Element]:
    """The <item> / <entry> elements of an RSS or Atom document."""
    # Find items/entries
    items_el = []
    if root.tag.endswith("rss"):
        channel = root.find("channel")
        if channel is not None:
            items_el = channel.findall("item")
    elif root.tag.endswith("feed"):
        items_el = root.findall(f"{{{_ATOM_NS}}}entry") or root.findall("entry")
    else:
        # Try both
        channel = root.find("channel")
        if channel is not None:
            items_el = channel.findall("item")
        if not items_el:
            items_el = root.findall(f"{{{_ATOM_NS}}}entry") or root.findall("entry")

    return items_el


def _parse_feed_entry(el: ET.Element, source: Source, now: str) -> FeedItem | None:
    """One feed entry as a FeedItem, or ``None`` when it has no title."""
    title = _find_text(el, "title", _ATOM_NS)
    link = _get_link(el)
    summary = (
        _find_text(el, "description")
        or _find_text(el, "summary", _ATOM_NS)
        or _find_text(el, "content", _ATOM_NS)
    )
    published_raw = (
        _find_text(el, "pubDate")
        or _find_text(el, "pubTime")
        or _find_text(el, "published", _ATOM_NS)
        or _find_text(el, "updated", _ATOM_NS)
    )
    published = _parse_pubdate(published_raw) if published_raw else ""
    summary_clean = _clean_html(summary)

    if not title:
        return None

    # Normalize link: prepend https:// if missing (some feeds like PBoC omit it)
    if link and link.startswith("www."):
        link = "https://" + link

    item_hash = hashlib.md5((title + (link or "")).encode()).hexdigest()[:12]

    return FeedItem(
        source_id=source.id,
        source_name=source.name,
        title=title,
        link=link,
        summary=summary_clean,
        published=published,
        fetched_at=now,
        item_hash=item_hash,
        source_tags=source.tags,
        source_country=source.country or source.region,
        source_language=source.language,
        source_category=source.category,
    )


def fetch_rss(
    source: Source, max_items: int, connect_timeout: int, read_timeout: int
) -> tuple[list[FeedItem], str | None]:
    """Fetch and parse an RSS/Atom feed. Returns (items, error)."""
    raw, error = _fetch_bytes(source.url, connect_timeout, read_timeout)
    if error is not None:
        return [], error

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return [], f"XML parse error: {e}"

    items_el = _feed_entries(root)

    now = datetime.now(timezone.utc).isoformat()
    items: list[FeedItem] = []
    count = 0

    for el in items_el:
        if count >= max_items:
            break
        item = _parse_feed_entry(el, source, now)
        if item is None:
            continue
        items.append(item)
        count += 1

    return items, None


# ── World Bank JSON API fetcher ─────────────────────────────────────────────


def _worldbank_item(doc: dict, source: Source, now: str) -> FeedItem | None:
    """One World Bank search hit as a FeedItem, or ``None`` when it is not an English news article."""
    import re

    title_raw = doc.get("title", {})
    if isinstance(title_raw, dict):
        title = title_raw.get("cdata!", "") or title_raw.get("#text", "")
    else:
        title = str(title_raw) if title_raw else ""

    url = doc.get("url", "")
    cqpath = doc.get("cqpath", "")

    # Filter: only English news articles
    if "/news/" not in url:
        return None
    if "/en/" not in cqpath and "/en/" not in url:
        return None

    # Extract date from URL: /news/press-release/2026/06/05/...
    date_match = re.search(r"/news/[\w-]+/(\d{4}/\d{2}/\d{2})/", url)
    if date_match:
        published = date_match.group(1).replace("/", "-")
    else:
        published = ""

    item_hash = hashlib.md5((title + url).encode()).hexdigest()[:12]

    return FeedItem(
        source_id=source.id,
        source_name=source.name,
        title=title,
        link=url,
        summary="",
        published=published,
        fetched_at=now,
        item_hash=item_hash,
        source_tags=source.tags,
        source_country=source.country or source.region or "Global",
        source_language=source.language,
        source_category=source.category,
    )


def fetch_worldbank_api(
    source: Source, max_items: int, connect_timeout: int, read_timeout: int
) -> tuple[list[FeedItem], str | None]:
    """Fetch World Bank news via search API JSON, convert to FeedItems."""
    api_url = f"{source.url}?format=json&rows={max(max_items * 5, 30)}&order=desc&qdr=w"
    raw, error = _fetch_bytes(api_url, connect_timeout, read_timeout)
    if error is not None:
        return [], error
    try:
        data = json.loads(raw)
    except Exception as e:
        return [], str(e)

    now = datetime.now(timezone.utc).isoformat()
    items: list[FeedItem] = []
    count = 0

    for doc in data.get("documents", {}).values():
        if count >= max_items:
            break
        item = _worldbank_item(doc, source, now)
        if item is None:
            continue
        items.append(item)
        count += 1

    return items, None


# ── main fetch loop ─────────────────────────────────────────────────────────


def fetch_all(
    sources: list[Source],
    max_per_source: int,
    connect_timeout: int,
    read_timeout: int,
    skip_types: set[str] | None = None,
    pause_seconds: float = 0.3,
) -> dict:
    if skip_types is None:
        skip_types = {"official_page", "api"}  # skip non-RSS by default

    results: dict = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_items": 0,
        "sources_successful": 0,
        "sources_failed": 0,
        "sources_skipped": 0,
        "items": [],
        "errors": [],
        "skipped": [],
    }

    for src in sources:
        if src.type in skip_types:
            results["skipped"].append(
                {
                    "id": src.id,
                    "name": src.name,
                    "type": src.type,
                    "reason": f"Source type '{src.type}' not fetched automatically (use web_extract for official pages, API calls for APIs).",
                }
            )
            results["sources_skipped"] += 1
            continue

        if src.type == "worldbank_api":
            items, error = fetch_worldbank_api(src, max_per_source, connect_timeout, read_timeout)
        else:
            items, error = fetch_rss(src, max_per_source, connect_timeout, read_timeout)

        if error:
            results["errors"].append({"id": src.id, "name": src.name, "error": error})
            results["sources_failed"] += 1
        else:
            results["items"].extend(asdict(it) for it in items)
            results["sources_successful"] += 1
            results["total_items"] += len(items)

        # Brief pause between sources to be polite
        if pause_seconds > 0:
            time.sleep(pause_seconds)

    return results


def list_sources(sources: list[Source]) -> None:
    """Print a formatted list of all sources."""
    type_labels = {
        "rss": "RSS",
        "google_news_rss": "GNEWS",
        "worldbank_api": "WB-API",
        "official_page": "PAGE",
        "api": "API",
    }
    print(f"\n{'ID':<35} {'TYPE':<7} {'REGION':<12} {'LANG':<5} NAME")
    print("-" * 100)
    for src in sources:
        label = type_labels.get(src.type, src.type)
        region = src.country or src.region or "-"
        print(f"{src.id:<35} {label:<7} {region:<12} {src.language:<5} {src.name}")
    print(f"\nTotal: {len(sources)} sources")


# ── CLI ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """The command-line interface."""
    default_config = Path(__file__).resolve().parent.parent / "sources" / "rss_sources.yaml"

    parser = argparse.ArgumentParser(
        description="Fetch economic news from configured RSS/data sources"
    )
    parser.add_argument(
        "--source", type=Path, default=default_config, help="Path to rss_sources.yaml"
    )
    parser.add_argument(
        "--output", type=Path, help="Output JSON file path (default: print to stdout)"
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=10,
        help="Max items to return per source (default: 10)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=10,
        help="Connect timeout per source in seconds (default: 10)",
    )
    parser.add_argument(
        "--read-timeout",
        type=int,
        default=40,
        help="Read timeout per source in seconds (default: 40)",
    )
    parser.add_argument(
        "--country", type=str, help="Comma-separated country/region filter (e.g. JP,US,CN)"
    )
    parser.add_argument(
        "--tags", type=str, help="Comma-separated tag filter (e.g. inflation,boj,jpy)"
    )
    parser.add_argument(
        "--language", type=str, help="Comma-separated language filter (e.g. en,zh,ja)"
    )
    parser.add_argument(
        "--include-official",
        action="store_true",
        help="Also attempt to fetch official_page sources (may fail, they are not RSS)",
    )
    parser.add_argument("--list", action="store_true", help="List all sources without fetching")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Compact output: only title, source_name, link, published, summary",
    )
    parser.add_argument(
        "--log", type=Path, help="Log fetch result to JSONL file for tracking fetch history"
    )
    return parser


def _filter_sources(sources: list[Source], args: argparse.Namespace) -> list[Source]:
    """Apply --country / --tags / --language."""
    if args.country:
        countries = {c.strip().upper() for c in args.country.split(",")}
        sources = [
            s for s in sources if (s.country.upper() in countries or s.region.upper() in countries)
        ]
    if args.tags:
        tag_set = {t.strip().lower() for t in args.tags.split(",")}
        sources = [s for s in sources if tag_set & {t.lower() for t in s.tags}]
    if args.language:
        langs = {lang.strip().lower() for lang in args.language.split(",")}
        sources = [s for s in sources if s.language in langs]
    return sources


def _compact_items(results: dict) -> None:
    """Keep only the summary fields of every item (--compact), in place."""
    compact = []
    for item in results["items"]:
        compact.append(
            {
                k: item[k]
                for k in ["title", "source_name", "link", "published", "summary"]
                if k in item
            }
        )
    results["items"] = compact


def _append_fetch_log(log_path: Path, results: dict) -> None:
    """Append one JSONL entry about this fetch (--log)."""
    log_entry = {
        "fetched_at": results["fetched_at"],
        "sources_successful": results["sources_successful"],
        "sources_failed": results["sources_failed"],
        "total_items": results["total_items"],
        "failed_ids": [e["id"] for e in results.get("errors", [])],
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def _emit_results(args: argparse.Namespace, results: dict, output: str) -> None:
    """Write the JSON to --output (with a summary) or print it."""
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        error_count = len(results["errors"])
        skipped_count = len(results["skipped"])
        print(
            f"Fetched {results['total_items']} items from "
            f"{results['sources_successful']} sources → {args.output}"
        )
        if error_count:
            print(f"  {error_count} source(s) failed (see errors in JSON)")
        if skipped_count:
            print(f"  {skipped_count} source(s) skipped (official_page/api)")
    else:
        print(output)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Config not found: {args.source}", file=sys.stderr)
        return 1

    sources = load_config(args.source)

    # Filters
    sources = _filter_sources(sources, args)

    if args.list:
        list_sources(sources)
        return 0

    skip_types = set() if args.include_official else {"official_page", "api"}
    results = fetch_all(
        sources,
        max_per_source=args.max_per_source,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        skip_types=skip_types,
    )

    if args.compact:
        _compact_items(results)

    output = json.dumps(results, ensure_ascii=False, indent=2)

    # Write log entry if --log specified
    if args.log:
        _append_fetch_log(args.log, results)

    _emit_results(args, results, output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
