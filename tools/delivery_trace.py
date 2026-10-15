"""Safe, stable delivery-configuration summaries for runtime tracing."""

from __future__ import annotations

from typing import Any


def _recipient_count(email_config: dict[str, Any]) -> int:
    recipients = email_config.get("to")
    if isinstance(recipients, str):
        return len([value for value in recipients.split(",") if value.strip()])
    if isinstance(recipients, (list, tuple, set)):
        return len([value for value in recipients if str(value).strip()])
    return 0


def effective_delivery_config(
    config: dict[str, Any], profile_config: str | None = None
) -> dict[str, Any]:
    """Return only non-sensitive delivery settings suitable for logs/results."""
    trace = config.get("_delivery_trace")
    if isinstance(trace, dict):
        return {
            "mode": str(trace.get("mode", config.get("mode", "briefing"))),
            "profile_config": trace.get("profile_config"),
            "deliver_email": bool(trace.get("deliver_email", False)),
            "email_send": bool(trace.get("email_send", False)),
            "recipient_count": max(0, int(trace.get("recipient_count", 0))),
        }

    email_config = config.get("email")
    email_config = email_config if isinstance(email_config, dict) else {}
    return {
        "mode": str(config.get("mode", "briefing")),
        "profile_config": profile_config,
        "deliver_email": bool(config.get("deliver_email", False)),
        "email_send": bool(email_config.get("send", False)),
        "recipient_count": _recipient_count(email_config),
    }
