"""Assess whether fetched source context is usable for grounded analysis."""

from __future__ import annotations

import re


SUBSTANTIVE_PATTERNS = (
    r"\b(?:rate|inflation|growth|employment|wage|gdp|cpi|ppi|pce|yield|forecast|decision)\b",
    r"(?:利率|通胀|增长|就业|工资|国内生产总值|收益率|预测|决定|同比|环比)",
    r"(?:金利|インフレ|成長|雇用|賃金|国内総生産|利回り|見通し|決定)",
)
BOILERPLATE_PATTERNS = (
    r"^\s*(?:speech|remarks|testimony)\s+(?:at|before)\b",
    r"\b(?:conference|symposium|forum)\b",
)


def assess_grounding_status(title: str, summary: str) -> str:
    compact = " ".join(str(summary or "").split())
    if not compact:
        return "title_only"

    lowered = compact.lower()
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in SUBSTANTIVE_PATTERNS):
        return "source_summary_available"

    title_compact = " ".join(str(title or "").split()).lower()
    is_boilerplate = any(
        re.search(pattern, lowered, flags=re.IGNORECASE)
        for pattern in BOILERPLATE_PATTERNS
    )
    if compact.lower() == title_compact or len(compact) < 40 or (
        len(compact) < 220 and is_boilerplate
    ):
        return "insufficient_source_context"

    return "source_summary_available"
