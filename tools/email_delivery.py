"""Generate and optionally send himalaya MML messages."""

from __future__ import annotations

import tempfile
import subprocess
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from .email_render import build_mml


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
    with mml_path.open("rb") as handle:
        subprocess.run(command, stdin=handle, check=True)


def send_email(
    *,
    email_config: dict[str, Any],
    subject: str,
    text_body: str,
    html_body: str | None = None,
    attachments: Sequence[Path] = (),
    artifact_path: Path | None = None,
) -> DeliveryResult:
    """Build and send one email while owning any temporary MML lifecycle."""
    required = ["from", "to"]
    missing = [key for key in required if not email_config.get(key)]
    if missing:
        return DeliveryResult(
            sent=False,
            mml_path=None,
            error=f"Missing email config fields: {', '.join(missing)}",
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
            attachments=tuple(valid),
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
            attachments=valid,
        )
        mml_path.write_text(mml, encoding="utf-8")
        send_mml(mml_path, email_config)
        sent = True
    except Exception as exc:
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
        attachments=tuple(valid),
        warnings=tuple(warnings),
        error=error,
    )
