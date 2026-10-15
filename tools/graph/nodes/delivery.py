"""Node that renders and sends the briefing email."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple

from ...delivery_trace import effective_delivery_config
from ...email_delivery import send_email
from ...email_render import build_combined_email_html, md_to_html, md_to_plain
from ...run_log import read_last_entry
from ._artifacts import (
    _has_valid_html,
    _has_valid_markdown,
    _html_to_plain,
    _pipeline_status,
    _valid_attachment,
)
from ._common import _now_iso

logger = logging.getLogger(__name__)


def _delivery_trace_fields(
    effective_config: dict[str, Any],
    delivery_requested: bool,
    *,
    email_sent: bool,
    email_send_attempted: bool,
    delivery_skip_reason: str | None,
) -> dict[str, Any]:
    fields = {
        "delivery_requested": delivery_requested,
        "email_send_attempted": email_send_attempted,
        "email_sent": email_sent,
        "delivery_skip_reason": delivery_skip_reason,
        "effective_delivery_config": effective_config,
    }
    logger.info("delivery trace: %s", fields)
    return fields


def _skip_delivery_update(
    state: dict[str, Any],
    config: dict[str, Any],
    warnings: list[str],
    output_files: dict[str, Any],
    delivery: dict[str, Any],
    pipeline_status: str,
    output_status: str,
    trace_fields: Any,
) -> dict[str, Any] | None:
    """Return the update for the already-sent / not-requested cases, else ``None``."""
    if delivery.get("email_sent"):
        run_id = state.get("run_id", "")
        warnings.append(f"run_id {run_id} 邮件已发送，跳过重复投递。")
        return {
            "warnings": warnings,
            "output_files": output_files,
            "delivery": delivery,
            **trace_fields(
                email_sent=True, email_send_attempted=False, delivery_skip_reason="already_sent"
            ),
            "email_sent": True,
            "pipeline_status": pipeline_status,
            "output_status": output_status,
            "delivery_status": "success",
            "delivery_kind": delivery.get("delivery_kind", state.get("delivery_kind", "none")),
            "_phase": "delivered",
        }

    if not config.get("deliver_email", False):
        warnings.append("deliver_email 未启用，跳过邮件投递。")
        return {
            "warnings": warnings,
            "output_files": output_files,
            **trace_fields(
                email_sent=False,
                email_send_attempted=False,
                delivery_skip_reason="delivery_not_requested",
            ),
            "delivery": delivery,
            "email_sent": False,
            "pipeline_status": pipeline_status,
            "output_status": output_status,
            "delivery_status": "skipped",
            "delivery_kind": "none",
            "_phase": "delivered",
        }

    return None


def _has_new_items(
    state: dict[str, Any], config: dict[str, Any], mode: str, warnings: list[str]
) -> bool:
    """False when every item was already sent in the previous run (appends a warning)."""
    items = list(state.get("ranked_items") or state.get("items") or [])
    has_new_items = bool(items)
    if has_new_items and mode != "trend":
        log_path = config.get("log_path")
        if log_path:
            last_entry = read_last_entry(Path(log_path))
            if last_entry and last_entry.get("item_urls"):
                prev_urls = set(last_entry.get("item_urls", []))
                cur_urls = {i.get("url") or i.get("link") or i.get("id", "") for i in items}
                cur_urls.discard("")
                if cur_urls and cur_urls <= prev_urls:
                    has_new_items = False
                    warnings.append("所有新闻条目已在上一轮发送，跳过简报区。")
    return has_new_items


class _ComposedEmail(NamedTuple):
    subject: str
    text_body: str
    combined_html: str
    kind: str
    attachment: Any
    pipeline_status: str
    output_status: str


def _compose_email(
    state: dict[str, Any],
    config: dict[str, Any],
    mode: str,
    skill_version: str,
    warnings: list[str],
    output_files: dict[str, Any],
    output_status: str,
) -> _ComposedEmail:
    """Build subject, bodies and attachment (or the failure notice)."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    jst = now.astimezone(timezone(timedelta(hours=9)))
    date_str = jst.strftime("%Y-%m-%d")
    subject = f"📊 财经消息 日报 {date_str}"

    briefing = state.get("briefing") or {}
    trend_table = state.get("trend_table") or {}
    trend_markdown = trend_table.get("markdown") or state.get("trend_markdown") or ""
    briefing_markdown = (
        state.get("briefing_markdown")
        or (briefing.get("markdown") if isinstance(briefing, dict) else "")
        or trend_markdown
        or ""
    )
    briefing_html = briefing.get("html") if isinstance(briefing, dict) else ""
    has_markdown = _has_valid_markdown(briefing_markdown)
    has_html = _has_valid_html(briefing_html)
    has_content = has_markdown or has_html
    pipeline_status = _pipeline_status(state, has_content=has_content)
    if not has_content:
        output_status = "skipped"

    trend_rows = list(trend_table.get("rows", []))
    has_new_items = _has_new_items(state, config, mode, warnings)

    attachment = _valid_attachment(output_files.get("markdown"))
    if has_content:
        kind = "report_with_attachment" if attachment else "report_without_attachment"
        text_body = (
            md_to_plain(str(briefing_markdown))
            if has_markdown
            else _html_to_plain(str(briefing_html))
        )
        try:
            combined_html = build_combined_email_html(
                trend_rows=trend_rows,
                briefing_markdown=str(briefing_markdown) if has_markdown else None,
                briefing_html=str(briefing_html) if has_html else None,
                as_of=trend_table.get("as_of"),
                has_news_items=has_new_items and mode != "trend",
                version=skill_version,
                report_date=date_str,
            )
        except Exception as exc:
            logger.exception("build_combined_email_html failed")
            combined_html = str(briefing_html) if has_html else md_to_html(str(briefing_markdown))
            warnings.append(f"组合邮件 HTML 构建失败，已降级: {exc}")
    else:
        kind = "failure_notification"
        subject = f"[失败通知] 财经消息日报 {date_str}"
        reasons = [str(error.get("message") or error) for error in state.get("errors", [])]
        reason_text = "；".join(reasons) or "流水线未生成任何有效业务正文。"
        text_body = f"财经消息日报生成失败。原因：{reason_text}"
        combined_html = f"<p>财经消息日报生成失败。</p><p>原因：{reason_text}</p>"

    return _ComposedEmail(
        subject, text_body, combined_html, kind, attachment, pipeline_status, output_status
    )


def _send_and_report(
    email_cfg: dict[str, Any],
    composed: _ComposedEmail,
    warnings: list[str],
    output_files: dict[str, Any],
    delivery: dict[str, Any],
    trace_fields: Any,
) -> dict[str, Any]:
    """Send the message and build the final node update."""
    subject, text_body, combined_html, kind, attachment, pipeline_status, output_status = composed

    result = send_email(
        email_config=email_cfg,
        subject=subject,
        text_body=text_body,
        html_body=combined_html,
        attachments=(attachment,) if attachment else (),
    )
    warnings.extend(result.warnings)
    sent = result.sent
    if not sent:
        warnings.append(f"邮件发送失败: {result.error or '未知错误'}")
    trace_fields = trace_fields(
        email_sent=sent, email_send_attempted=True, delivery_skip_reason=None
    )
    delivery.update(
        {
            "email_sent": sent,
            "delivery_status": "success" if sent else "failed",
            "delivery_kind": kind,
            **trace_fields,
        }
    )
    if sent:
        delivery["email_sent_at"] = _now_iso()

    return {
        "warnings": warnings,
        **trace_fields,
        "output_files": output_files,
        "delivery": delivery,
        "email_sent": sent,
        "pipeline_status": pipeline_status,
        "output_status": output_status,
        "delivery_status": "success" if sent else "failed",
        "delivery_kind": kind,
        "_phase": "delivered",
    }


def deliver_email_node(state: dict[str, Any]) -> dict[str, Any]:
    """Select and execute attachment, body-only, or failure-notice delivery."""
    config = state.get("config", {}) or {}
    warnings: list[str] = list(state.get("warnings", []))
    output_files = dict(state.get("output_files", {}))
    delivery = dict(state.get("delivery", {}))
    mode = state.get("mode", "")
    pipeline_status = str(state.get("pipeline_status", "running"))
    output_status = str(state.get("output_status", "skipped"))
    effective_config = effective_delivery_config(config)
    delivery_requested = bool(effective_config["deliver_email"])
    trace_fields = partial(_delivery_trace_fields, effective_config, delivery_requested)

    skipped = _skip_delivery_update(
        state,
        config,
        warnings,
        output_files,
        delivery,
        pipeline_status,
        output_status,
        trace_fields,
    )
    if skipped is not None:
        return skipped

    skill_version = config.get("_skill", {}).get("version", "")
    email_cfg = dict(config.get("email", {}) or {})
    if not email_cfg.get("send", False):
        warnings.append("email.send 未启用，跳过邮件投递。")
        return {
            "warnings": warnings,
            **trace_fields(
                email_sent=False,
                email_send_attempted=False,
                delivery_skip_reason="email_send_disabled",
            ),
            "output_files": output_files,
            "delivery": delivery,
            "email_sent": False,
            "pipeline_status": pipeline_status,
            "output_status": output_status,
            "delivery_status": "skipped",
            "delivery_kind": "none",
            "_phase": "delivered",
        }

    composed = _compose_email(
        state, config, mode, skill_version, warnings, output_files, output_status
    )
    return _send_and_report(email_cfg, composed, warnings, output_files, delivery, trace_fields)
