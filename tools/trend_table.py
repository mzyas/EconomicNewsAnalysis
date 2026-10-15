"""Deterministic market trend table — no LLM involvement.

Builds a trend table from already-fetched ``MarketSnapshot`` objects,
computes direction symbols from weekly changes, and renders the result
as Markdown or HTML.

Two asset types are supported:

* **price** — changes displayed as percentages (%), direction thresholds
  are 0.3 % / 1.0 %.
* **yield** — changes displayed as basis points (bp), direction thresholds
  are 3 bp / 10 bp.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Direction thresholds ────────────────────────────────────────────────

_PRICE_UP_STRONG = 1.0
_PRICE_UP_WEAK = 0.3
_PRICE_DOWN_WEAK = -0.3
_PRICE_DOWN_STRONG = -1.0

_YIELD_BP_UP_STRONG = 10.0
_YIELD_BP_UP_WEAK = 3.0
_YIELD_BP_DOWN_WEAK = -3.0
_YIELD_BP_DOWN_STRONG = -10.0


# ── Data model ──────────────────────────────────────────────────────────


@dataclass
class TrendRow:
    """A single row in the trend table."""

    symbol_id: str
    ticker: str
    name: str
    asset_class: str
    asset_type: str  # "price" | "yield"
    region: str
    currency: str | None
    display_precision: int

    last_price: float | None
    as_of: str | None

    # Display values (pre-formatted for the renderer)
    change_1d: float | None  # pct for price, bp for yield
    change_1w: float | None
    change_1m: float | None
    unit_1d: str
    unit_1w: str
    unit_1m: str

    direction: str | None  # "↗" | "→" | "↘"
    direction_detail: str | None  # "strong_up" | "weak_up" | "flat" | "weak_down" | "strong_down"

    status: str = "success"
    error: str | None = None
    name_zh: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Public helpers ──────────────────────────────────────────────────────


def _compute_direction(
    change_1w_display: float | None,
    asset_type: str,
) -> tuple[str | None, str | None]:
    """Return ``(symbol, detail)`` for a weekly change value.

    *symbol* is one of ``↗``, ``→``, ``↘``.
    *detail* is one of ``strong_up``, ``weak_up``, ``flat``,
    ``weak_down``, ``strong_down`` (or ``None`` if the change is missing).
    """
    if change_1w_display is None:
        return None, None

    if asset_type == "yield":
        up_strong = _YIELD_BP_UP_STRONG
        up_weak = _YIELD_BP_UP_WEAK
        down_weak = _YIELD_BP_DOWN_WEAK
        down_strong = _YIELD_BP_DOWN_STRONG
    else:
        up_strong = _PRICE_UP_STRONG
        up_weak = _PRICE_UP_WEAK
        down_weak = _PRICE_DOWN_WEAK
        down_strong = _PRICE_DOWN_STRONG

    if change_1w_display >= up_strong:
        return "↗", "strong_up"
    if change_1w_display >= up_weak:
        return "↗", "weak_up"
    if change_1w_display > down_weak:
        return "→", "flat"
    if change_1w_display > down_strong:
        return "↘", "weak_down"
    return "↘", "strong_down"


def _display_values(
    change_pct: float | None,
    change_abs: float | None,
    asset_type: str,
    precision: int = 2,
) -> tuple[float | None, str]:
    """Convert raw change values to display values and units.

    For *price* assets the display value is the percentage change (rounded
    to *precision* decimals) and the unit is ``%``.

    For *yield* assets the display value is the absolute change multiplied
    by 100 (basis points, rounded to 0 decimals) and the unit is ``bp``.
    """
    if change_pct is None:
        return None, ""
    if asset_type == "yield":
        if change_abs is None:
            return None, ""
        return round(change_abs * 100, 0), "bp"
    return round(change_pct, precision), "%"


def build_trend_rows(
    snapshots: list[dict[str, Any]],
) -> list[TrendRow]:
    """Build a ``TrendRow`` list from a list of snapshot dicts.

    Each snapshot dict is the output of ``MarketSnapshot.to_dict()``.
    Failed snapshots produce rows with ``status="failed"`` and are still
    included so the table shows every configured asset.
    """
    rows: list[TrendRow] = []
    for snap in snapshots:
        asset_type = snap.get("asset_type", "price")
        precision = int(snap.get("display_precision", 2))

        change_1d_pct = snap.get("change_pct")
        change_1w_pct = snap.get("change_1w_pct")
        change_1m_pct = snap.get("change_1m_pct")
        change_1w_abs = snap.get("change_1w_abs")
        change_1m_abs = snap.get("change_1m_abs")

        d1, u1 = _display_values(change_1d_pct, None, asset_type, precision)
        d1w, u1w = _display_values(change_1w_pct, change_1w_abs, asset_type, precision)
        d1m, u1m = _display_values(change_1m_pct, change_1m_abs, asset_type, precision)

        direction, detail = _compute_direction(d1w, asset_type)

        rows.append(
            TrendRow(
                symbol_id=str(snap.get("symbol_id", "")),
                ticker=str(snap.get("ticker", "")),
                name=str(snap.get("name", "")),
                name_zh=snap.get("name_zh"),
                asset_class=str(snap.get("asset_class", "")),
                asset_type=asset_type,
                region=str(snap.get("region", "")),
                currency=snap.get("currency"),
                display_precision=precision,
                last_price=snap.get("last_price"),
                as_of=snap.get("as_of"),
                change_1d=d1,
                change_1w=d1w,
                change_1m=d1m,
                unit_1d=u1,
                unit_1w=u1w,
                unit_1m=u1m,
                direction=direction,
                direction_detail=detail,
                status=str(snap.get("status", "failed")),
                error=snap.get("error"),
            )
        )
    return rows


# ── Helpers for formatted display strings ──────────────────────────────


def _fmt_change(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "bp":
        return f"bp {value:+.0f}"
    return f"% {value:+.2f}"


def _fmt_price(value: float | None, precision: int) -> str:
    if value is None:
        return "—"
    return f"{value:,.{precision}f}"


# ── Markdown renderer ──────────────────────────────────────────────────


def render_markdown(
    rows: list[TrendRow],
    title: str | None = None,
    as_of: str | None = None,
) -> str:
    """Render the trend table as GitHub-flavoured Markdown."""
    lines: list[str] = []
    lines.append(f"## {title or '市场趋势表'}")
    if as_of:
        lines.append(f"*数据截至 {as_of}*\n")
    else:
        lines.append("")

    # Table header
    lines.append("| 资产 | 最新价 | 日变化 | 周变化 | 月变化 | 方向 |")
    lines.append("|------|-------|--------|--------|--------|------|")

    for row in rows:
        if row.status == "failed":
            name = row.name
            err = row.error or "数据获取失败"
            lines.append(f"| {name} | — | — | — | — | ⚠ {err} |")
            continue

        price = _fmt_price(row.last_price, row.display_precision)
        d1 = _fmt_change(row.change_1d, row.unit_1d)
        d1w = _fmt_change(row.change_1w, row.unit_1w)
        d1m = _fmt_change(row.change_1m, row.unit_1m)
        direction = row.direction or "—"

        lines.append(f"| {row.name} | {price} | {d1} | {d1w} | {d1m} | {direction} |")

    return "\n".join(lines) + "\n"


# ── HTML renderer ──────────────────────────────────────────────────────


def _direction_color(detail: str | None) -> str:
    if detail in ("strong_up",):
        return "#dc3545"  # red (hot)
    if detail in ("weak_up",):
        return "#e8833a"  # orange
    if detail in ("flat",):
        return "#6c757d"  # grey
    if detail in ("weak_down",):
        return "#3a8fe8"  # light blue
    if detail in ("strong_down",):
        return "#1a6fc4"  # blue (cold)
    return "#6c757d"


def _change_html(value: float | None, unit: str) -> str:
    if value is None:
        return "<span class='na'>—</span>"
    cls = "up" if value > 0 else ("down" if value < 0 else "flat")
    if unit == "bp":
        text = f"bp {value:+.0f}"
    else:
        text = f"% {value:+.2f}"
    return f"<span class='change-{cls}'>{text}</span>"


def render_html(
    rows: list[TrendRow],
    title: str | None = None,
    as_of: str | None = None,
) -> str:
    """Render the trend table as a self-contained HTML fragment."""
    parts: list[str] = []
    parts.append("<div class='trend-table'>")
    parts.append(f"<h3>{title or '市场趋势表'}</h3>")
    if as_of:
        parts.append(f"<p class='as-of'>数据截至 {as_of}</p>")

    parts.append("<table>")
    parts.append(
        "<thead><tr>"
        "<th>资产</th><th>最新价</th><th>日变化</th>"
        "<th>周变化</th><th>月变化</th><th>方向</th>"
        "</tr></thead>"
    )
    parts.append("<tbody>")

    for row in rows:
        if row.status == "failed":
            err = row.error or "数据获取失败"
            parts.append(
                f"<tr class='failed'>"
                f"<td>{row.name}</td><td colspan='4'>⚠ {err}</td>"
                f"<td>—</td></tr>"
            )
            continue

        price = _fmt_price(row.last_price, row.display_precision)
        d1_html = _change_html(row.change_1d, row.unit_1d)
        d1w_html = _change_html(row.change_1w, row.unit_1w)
        d1m_html = _change_html(row.change_1m, row.unit_1m)
        direction = row.direction or "—"
        color = _direction_color(row.direction_detail)

        parts.append(
            f"<tr>"
            f"<td>{row.name}</td>"
            f"<td class='price'>{price}</td>"
            f"<td>{d1_html}</td>"
            f"<td>{d1w_html}</td>"
            f"<td>{d1m_html}</td>"
            f"<td style='color:{color};font-weight:bold'>{direction}</td>"
            f"</tr>"
        )

    parts.append("</tbody></table>")
    parts.append("</div>")

    style = (
        "<style>\n"
        ".trend-table { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }\n"
        ".trend-table table { border-collapse: collapse; width: 100%; }\n"
        ".trend-table th, .trend-table td { padding: 6px 12px; text-align: right; border: 1px solid #ddd; }\n"
        ".trend-table th { background: #f5f5f5; font-weight: 600; }\n"
        ".trend-table td:first-child { text-align: left; font-weight: 500; }\n"
        ".trend-table .price { font-variant-numeric: tabular-nums; }\n"
        ".trend-table .change-up { color: #dc3545; }\n"
        ".trend-table .change-down { color: #1a6fc4; }\n"
        ".trend-table .change-flat { color: #6c757d; }\n"
        ".trend-table .na { color: #999; }\n"
        ".trend-table .as-of { color: #666; font-size: 0.9em; }\n"
        ".trend-table .failed td { color: #999; font-style: italic; }\n"
        "</style>\n"
    )

    return style + "\n".join(parts) + "\n"


# ── Section header for embedding in briefings ──────────────────────────


def trend_section_markdown(
    rows: list[TrendRow],
    as_of: str | None = None,
) -> str:
    """Return a compact trend summary suitable for embedding in a briefing.

    Only the weekly direction column is kept for brevity.
    """
    lines: list[str] = []
    lines.append("### 📊 市场趋势一览")
    if as_of:
        lines.append(f"*数据截至 {as_of}*\n")
    else:
        lines.append("")
    lines.append("| 资产 | 方向 | 周变化 |")
    lines.append("|------|------|--------|")

    for row in rows:
        if row.status == "failed":
            lines.append(f"| {row.name} | ⚠ | — |")
            continue
        direction = row.direction or "—"
        change = _fmt_change(row.change_1w, row.unit_1w)
        lines.append(f"| {row.name} | {direction} | {change} |")

    return "\n".join(lines) + "\n"


__all__ = [
    "TrendRow",
    "build_trend_rows",
    "render_markdown",
    "render_html",
    "trend_section_markdown",
    "_compute_direction",
    "_display_values",
]
