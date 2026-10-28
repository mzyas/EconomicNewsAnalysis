"""Send a rendered briefing over SMTP with the standard library (``email.transport: smtp``).

The password is read from the environment variable named by ``email.smtp.password_env``
or from ``email.smtp.password_file``, never from the config itself, and is kept out of
every error message this module raises.
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from collections.abc import Callable, Sequence
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any

DEFAULT_PORTS = {"starttls": 587, "ssl": 465}


class SmtpSendError(RuntimeError):
    """An SMTP send failed; the message names the cause and never holds the password."""


def build_message(
    *,
    from_addr: str,
    to_addr: str,
    subject: str,
    text_body: str,
    html_document: str,
    attachments: Sequence[Path] = (),
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    # An explicit domain keeps make_msgid from doing a slow FQDN lookup.
    domain = parseaddr(from_addr)[1].rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(text_body)
    message.add_alternative(html_document, subtype="html")
    for attachment in attachments:
        content_type = mimetypes.guess_type(attachment.name)[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(
            attachment.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.name,
            params={"charset": "utf-8"} if maintype == "text" else None,
        )
    return message


def open_connection(
    security: str, host: str, port: int, timeout: float, context: ssl.SSLContext
) -> smtplib.SMTP:
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    return smtplib.SMTP(host, port, timeout=timeout)


def send_message(
    message: EmailMessage,
    smtp_cfg: Any,
    *,
    timeout: float,
    connect: Callable[..., smtplib.SMTP] = open_connection,
) -> dict[str, Any]:
    """Send ``message``; return the recipients the server refused (empty when all accepted).

    ``connect`` takes ``open_connection``'s arguments and returns an ``smtplib.SMTP``-like
    client; the default opens a real connection.
    """
    if not isinstance(smtp_cfg, dict) or not smtp_cfg.get("host"):
        raise SmtpSendError("email.smtp.host is required when email.transport is 'smtp'")
    if "password" in smtp_cfg:
        raise SmtpSendError(
            "email.smtp.password is not accepted; use password_env or password_file"
        )
    security = smtp_cfg.get("security", "starttls")
    if security not in DEFAULT_PORTS:
        raise SmtpSendError("email.smtp.security must be 'starttls' or 'ssl'")

    host = str(smtp_cfg["host"])
    port = int(smtp_cfg.get("port") or DEFAULT_PORTS[security])
    username = smtp_cfg.get("username") or parseaddr(str(message["From"]))[1]
    password = _read_password(smtp_cfg)
    context = ssl.create_default_context()
    refused: dict[str, Any] | None = None
    try:
        with connect(security, host, port, timeout, context) as client:
            if security == "starttls":
                client.starttls(context=context)
            client.login(username, password)
            refused = client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # Once the server has accepted the message a failed QUIT must not read as a
        # failed send, or a resumed run would deliver the same email twice.
        if refused is None:
            # ``from None`` keeps the server's raw reply out of logged tracebacks; the
            # message below already carries the exception type and the redacted reply.
            raise SmtpSendError(_describe(exc, password)) from None
    return refused


def describe_refused(refused: dict[str, Any]) -> str:
    return "; ".join(
        f"{address} {code} {_decode(reply)}" for address, (code, reply) in refused.items()
    )


def _read_password(smtp_cfg: dict[str, Any]) -> str:
    env_name = smtp_cfg.get("password_env")
    if env_name:
        value = os.environ.get(str(env_name), "").strip()
        if value:
            return value
    password_file = smtp_cfg.get("password_file")
    if password_file:
        path = Path(str(password_file)).expanduser()
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SmtpSendError(
                f"cannot read email.smtp.password_file {path}: {exc.strerror or exc}"
            ) from exc
        if value:
            return value
    raise SmtpSendError(
        "no SMTP password: set the variable named by email.smtp.password_env "
        "or point email.smtp.password_file at a non-empty file"
    )


def _describe(exc: BaseException, password: str) -> str:
    if isinstance(exc, smtplib.SMTPResponseException):
        detail = f"{exc.smtp_code} {_decode(exc.smtp_error)}"
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        detail = describe_refused(exc.recipients)
    else:
        detail = str(exc)
    text = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return text.replace(password, "***")


def _decode(reply: Any) -> str:
    return reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)
