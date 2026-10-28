"""Build briefing emails and send them through a command (himalaya MML) or built-in SMTP."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .email_render import build_mml, render_email_bodies
from .smtp_sender import build_message, describe_refused, send_message

logger = logging.getLogger(__name__)

TRANSPORTS = ("command", "smtp")
DEFAULT_TIMEOUT_SECONDS = 120
STDERR_TAIL_LINES = 5


@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    mml_path: Path | None
    attachments: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None


def build_email_artifact(
    markdown_path: Path,
    email_config: dict[str, Any],
    output_path: Path | None = None,
    html_body: str | None = None,
    version: str = "",
) -> Path:
    required = ["from", "to", "subject"]
    missing = [key for key in required if not email_config.get(key)]
    if missing:
        raise ValueError(f"Missing email config fields: {', '.join(missing)}")
    output_path = output_path or markdown_path.with_suffix(".mml")
    mml = build_mml(
        markdown_path,
        from_addr=str(email_config["from"]),
        to_addr=str(email_config["to"]),
        subject=str(email_config["subject"]),
        attach_name=email_config.get("attachment_name"),
        html_body=html_body,
        version=version,
    )
    output_path.write_text(mml, encoding="utf-8")
    return output_path


def send_mml(mml_path: Path, email_config: dict[str, Any]) -> None:
    command = email_config.get("send_command", ["himalaya", "template", "send"])
    if not isinstance(command, list) or not command:
        raise ValueError("email.send_command must be a non-empty string array")
    timeout = _timeout_seconds(email_config)
    with mml_path.open("rb") as handle:
        try:
            subprocess.run(
                command, stdin=handle, stderr=subprocess.PIPE, check=True, timeout=timeout
            )
        except subprocess.CalledProcessError as exc:
            detail = _stderr_tail(exc.stderr)
            message = f"{command[0]} exited with status {exc.returncode}"
            raise RuntimeError(f"{message}: {detail}" if detail else message) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{command[0]} timed out after {timeout:g}s") from exc


def _timeout_seconds(email_config: dict[str, Any]) -> float:
    return float(email_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))


def _stderr_tail(stderr: bytes | None) -> str:
    """Last few stderr lines, so the server's reason reaches DeliveryResult.error."""
    text = (stderr or b"").decode("utf-8", "replace").strip()
    return " | ".join(text.splitlines()[-STDERR_TAIL_LINES:])


def send_email(
    *,
    email_config: dict[str, Any],
    subject: str,
    text_body: str,
    html_body: str | None = None,
    attachments: Sequence[Path] = (),
    artifact_path: Path | None = None,
) -> DeliveryResult:
    """Build and send one email over the route ``email.transport`` names.

    ``command`` (default) pipes MML into ``email.send_command``; ``smtp`` sends with the
    built-in client and never writes MML, so ``artifact_path`` only applies to ``command``.
    """
    required = ["from", "to"]
    missing = [key for key in required if not email_config.get(key)]
    if missing:
        return DeliveryResult(
            sent=False,
            mml_path=None,
            error=f"Missing email config fields: {', '.join(missing)}",
        )
    transport = email_config.get("transport", "command")
    if transport not in TRANSPORTS:
        return DeliveryResult(
            sent=False,
            mml_path=None,
            error=f"email.transport must be one of: {', '.join(TRANSPORTS)}",
        )

    valid: list[Path] = []
    warnings: list[str] = []
    for raw_path in attachments:
        path = Path(raw_path)
        try:
            is_valid = path.is_file() and path.stat().st_size > 0
        except OSError:
            is_valid = False
        if is_valid:
            valid.append(path)
        else:
            warnings.append(f"忽略无效附件: {path}")

    if transport == "smtp":
        return _send_via_smtp(
            email_config,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=tuple(valid),
            warnings=warnings,
        )
    return _send_via_command(
        email_config,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        attachments=tuple(valid),
        warnings=warnings,
        artifact_path=artifact_path,
    )


def _send_via_command(
    email_config: dict[str, Any],
    *,
    subject: str,
    text_body: str,
    html_body: str | None,
    attachments: tuple[Path, ...],
    warnings: list[str],
    artifact_path: Path | None,
) -> DeliveryResult:
    """Write the MML (to a temporary file unless ``artifact_path`` is given) and pipe it out."""
    temporary = artifact_path is None
    try:
        if temporary:
            handle = tempfile.NamedTemporaryFile(suffix=".mml", delete=False)
            handle.close()
            mml_path = Path(handle.name)
        else:
            mml_path = Path(artifact_path)
    except OSError as exc:
        return DeliveryResult(
            sent=False,
            mml_path=None,
            attachments=attachments,
            warnings=tuple(warnings),
            error=str(exc),
        )

    sent = False
    error: str | None = None
    try:
        mml = build_mml(
            None,
            from_addr=str(email_config["from"]),
            to_addr=str(email_config["to"]),
            subject=subject,
            html_body=html_body,
            version=str(email_config.get("version", "")),
            text_body=text_body,
            attachments=attachments,
        )
        mml_path.write_text(mml, encoding="utf-8")
        send_mml(mml_path, email_config)
        sent = True
    except Exception as exc:
        logger.exception("email delivery failed")
        error = str(exc)
    finally:
        if temporary:
            try:
                mml_path.unlink(missing_ok=True)
            except OSError as exc:
                warnings.append(f"临时 MML 清理失败: {exc}")

    return DeliveryResult(
        sent=sent,
        mml_path=None if temporary else mml_path,
        attachments=attachments,
        warnings=tuple(warnings),
        error=error,
    )


def _send_via_smtp(
    email_config: dict[str, Any],
    *,
    subject: str,
    text_body: str,
    html_body: str | None,
    attachments: tuple[Path, ...],
    warnings: list[str],
) -> DeliveryResult:
    """Build the message directly (no MML) and send it with the built-in SMTP client."""
    sent = False
    error: str | None = None
    try:
        plain_body, html_document = render_email_bodies(
            None,
            html_body=html_body,
            version=str(email_config.get("version", "")),
            text_body=text_body,
        )
        message = build_message(
            from_addr=str(email_config["from"]),
            to_addr=str(email_config["to"]),
            subject=subject,
            text_body=plain_body,
            html_document=html_document,
            attachments=attachments,
        )
        refused = send_message(
            message, email_config.get("smtp"), timeout=_timeout_seconds(email_config)
        )
        sent = True
        if refused:
            warnings.append(f"部分收件人被拒收: {describe_refused(refused)}")
    except Exception as exc:
        logger.exception("email delivery failed")
        error = str(exc)

    return DeliveryResult(
        sent=sent,
        mml_path=None,
        attachments=attachments,
        warnings=tuple(warnings),
        error=error,
    )
