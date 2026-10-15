"""Write Runtime artifacts."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _slug(value: str) -> str:
    slug = re.sub(r"[^\w\u3400-\u9fff-]+", "-", value, flags=re.UNICODE)
    return slug.strip("-") or "economic-news"


def write_artifacts(
    output_dir: Path,
    topic: str,
    result: dict[str, Any],
    markdown: str | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{_slug(topic)}_{stamp}"
    json_path = output_dir / f"{base}.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    files = {"json": str(json_path)}
    if markdown is not None:
        markdown_path = output_dir / f"{base}.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        files["markdown"] = str(markdown_path)
    return files


def write_markdown_artifact(output_dir: Path, topic: str, markdown: str) -> Path:
    """Write one business Markdown artifact without creating Runtime JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{_slug(topic)}_{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path

