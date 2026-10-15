"""Validity checks and status derivation for briefing artifacts."""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from typing import Any


def _has_valid_markdown(value: Any) -> bool:
    """Return true when markdown contains content beyond headings/placeholders."""
    if not isinstance(value, str):
        return False
    for line in value.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if not stripped or re.fullmatch(r"#{1,6}\s+.*", stripped):
            continue
        if stripped in {"待补充", "暂无内容", "N/A", "--"}:
            continue
        if re.search(r"由\s*Economic News Analysis\b.*自动生成", stripped, re.IGNORECASE):
            continue
        if re.match(r"数据来源\s*[:：]", stripped):
            continue
        if re.search(r"[\w\u3400-\u9fff]", stripped):
            return True
    return False


def _has_valid_html(value: Any) -> bool:
    """Return true when HTML has substantive non-heading text."""
    if not isinstance(value, str):
        return False
    without_headings = re.sub(
        r"<h[1-6]\b[^>]*>.*?</h[1-6]>", " ", value, flags=re.IGNORECASE | re.DOTALL
    )
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", without_headings))
    text = re.sub(
        r"由\s*Economic News Analysis\b.*?自动生成",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"数据来源\s*[:：].*", " ", text, flags=re.IGNORECASE | re.DOTALL)
    return bool(re.search(r"[\w\u3400-\u9fff]", text))


def _html_to_plain(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _valid_attachment(path_value: Any) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        if path.suffix.lower() in {".md", ".markdown"}:
            if not _has_valid_markdown(path.read_text(encoding="utf-8-sig")):
                return None
    except (OSError, UnicodeError):
        return None
    return path


def _pipeline_status(state: dict[str, Any], *, has_content: bool) -> str:
    """Compute business-pipeline status without consulting output or delivery."""
    if not has_content:
        return "failed"
    gate = state.get("_quality_gate") or {}
    has_pipeline_issues = bool(
        state.get("errors")
        or int(state.get("_sources_failed", 0)) > 0
        or (gate and not gate.get("passed", False))
    )
    return "partial_success" if has_pipeline_issues else "success"


def _missing_sources(state: dict[str, Any]) -> list[str]:
    """Collect stable source labels for a partial-success report."""
    found: list[str] = []
    for entry in list(state.get("skipped_sources", [])) + list(state.get("errors", [])):
        if not isinstance(entry, dict):
            continue
        label = (
            entry.get("source_name")
            or entry.get("source_id")
            or entry.get("source")
            or entry.get("url")
        )
        if label and str(label) not in found:
            found.append(str(label))
    failed_count = int(state.get("_sources_failed", 0))
    if failed_count and not found:
        found.append(f"{failed_count} 个未标识来源")
    return found


def _partial_report_notice(state: dict[str, Any]) -> tuple[str, str, str]:
    missing = _missing_sources(state)
    details = "、".join(missing) if missing else "存在非致命错误，详见 warnings"
    warning = f"部分成功：缺失或跳过来源：{details}"
    markdown = f"> **部分成功**：本报告基于当前可用内容生成。\n> 缺失或跳过来源：{details}"
    html = (
        '<div style="padding:10px;border-left:4px solid #d97706;background:#fffbeb;">'
        f"<strong>部分成功</strong>：本报告基于当前可用内容生成。<br>"
        f"缺失或跳过来源：{details}</div>"
    )
    return markdown, html, warning
