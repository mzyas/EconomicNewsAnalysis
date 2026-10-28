import contextlib
import email
import logging
import os
import smtplib
import socket
import ssl
import tempfile
import threading
import unittest
from email import policy
from functools import partial
from pathlib import Path
from unittest.mock import patch

from tools.config_loader import ROOT
from tools.email_delivery import send_email
from tools.email_render import build_mml
from tools.schema_validation import SchemaValidationError, validate_file
from tools.smtp_sender import SmtpSendError, build_message, open_connection, send_message

PASSWORD = "app-pass-1234"


class FakeConnection:
    """An ``smtplib.SMTP`` stand-in; pass ``fake.connect`` as ``send_message(connect=...)``."""

    def __init__(self, *, connect_error=None, login_error=None, quit_error=None, refused=None):
        self.connect_error = connect_error
        self.login_error = login_error
        self.quit_error = quit_error
        self.refused = refused or {}
        self.opened = None
        self.calls = []
        self.messages = []

    def connect(self, security, host, port, timeout, context):
        if self.connect_error is not None:
            raise self.connect_error
        self.opened = (security, host, port, timeout)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.calls.append("quit")
        if self.quit_error is not None:
            raise self.quit_error

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username, password))
        if self.login_error is not None:
            raise self.login_error

    def send_message(self, message):
        self.messages.append(message)
        return dict(self.refused)


def auth_failure():
    reply = f"5.7.8 Username and Password not accepted ({PASSWORD})"
    return smtplib.SMTPAuthenticationError(535, reply.encode())


class PasswordFileTestCase(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.temp = Path(temp_dir.name)
        self.password_file = self.temp / "app_password"
        self.password_file.write_text(PASSWORD + "\n", encoding="utf-8")

    def smtp_cfg(self, **overrides):
        return {"host": "smtp.example.com", "password_file": str(self.password_file), **overrides}


class SendMessageTests(PasswordFileTestCase):
    def send(self, fake, **overrides):
        message = build_message(
            from_addr="Hermes <sender@example.com>",
            to_addr="a@example.com",
            subject="s",
            text_body="t",
            html_document="<p>h</p>",
        )
        return send_message(message, self.smtp_cfg(**overrides), timeout=30, connect=fake.connect)

    def test_starttls_logs_in_with_the_from_address_and_sends(self):
        fake = FakeConnection()
        self.assertEqual(self.send(fake), {})
        self.assertEqual(fake.opened, ("starttls", "smtp.example.com", 587, 30))
        self.assertEqual(
            fake.calls, ["starttls", ("login", "sender@example.com", PASSWORD), "quit"]
        )
        self.assertEqual(len(fake.messages), 1)

    def test_ssl_skips_starttls_and_defaults_to_port_465(self):
        fake = FakeConnection()
        self.send(fake, security="ssl", username="login@example.com")
        self.assertEqual(fake.opened, ("ssl", "smtp.example.com", 465, 30))
        self.assertEqual(fake.calls, [("login", "login@example.com", PASSWORD), "quit"])

    def test_password_env_wins_and_an_empty_one_falls_back_to_the_file(self):
        for env_value, expected in (("from-env", "from-env"), ("", PASSWORD)):
            with self.subTest(env_value=env_value):
                fake = FakeConnection()
                with patch.dict(os.environ, {"ENA_TEST_SMTP_PASSWORD": env_value}):
                    self.send(fake, password_env="ENA_TEST_SMTP_PASSWORD")
                self.assertEqual(fake.calls[1], ("login", "sender@example.com", expected))

    def test_missing_password_fails_before_connecting(self):
        self.password_file.write_text("", encoding="utf-8")
        fake = FakeConnection()
        with self.assertRaisesRegex(SmtpSendError, "no SMTP password"):
            self.send(fake)
        self.assertIsNone(fake.opened)

    def test_password_in_config_is_refused_before_connecting(self):
        fake = FakeConnection()
        with self.assertRaises(SmtpSendError) as caught:
            self.send(fake, password=PASSWORD)
        self.assertIn("email.smtp.password is not accepted", str(caught.exception))
        self.assertNotIn(PASSWORD, str(caught.exception))
        self.assertIsNone(fake.opened)

    def test_auth_failure_names_the_reply_without_the_password(self):
        fake = FakeConnection(login_error=auth_failure())
        with self.assertRaises(SmtpSendError) as caught:
            self.send(fake)
        error = str(caught.exception)
        self.assertTrue(
            error.startswith("SMTPAuthenticationError: 535 5.7.8 Username and Password"), error
        )
        self.assertIn("***", error)
        self.assertNotIn(PASSWORD, error)
        # The raw smtplib exception, which still holds the reply, is not chained.
        self.assertTrue(caught.exception.__suppress_context__)

    def test_connection_timeout_is_reported(self):
        fake = FakeConnection(connect_error=TimeoutError("timed out"))
        with self.assertRaisesRegex(SmtpSendError, "^TimeoutError: timed out$"):
            self.send(fake)

    def test_refused_recipients_are_returned(self):
        refused = {"b@example.com": (550, b"5.1.1 no such user")}
        self.assertEqual(self.send(FakeConnection(refused=refused)), refused)

    def test_failed_quit_after_acceptance_is_not_a_failure(self):
        fake = FakeConnection(quit_error=smtplib.SMTPResponseException(451, b"4.4.2 timeout"))
        self.assertEqual(self.send(fake), {})
        self.assertEqual(len(fake.messages), 1)


class OpenConnectionTests(unittest.TestCase):
    def test_starttls_opens_plain_smtp_and_ssl_starts_with_a_handshake(self):
        with socket.create_server(("127.0.0.1", 0)) as server:
            port = server.getsockname()[1]

            def greet_twice():
                for _ in range(2):
                    connection, _ = server.accept()
                    # The TLS client resets the connection when it gives up.
                    with connection, contextlib.suppress(ConnectionResetError):
                        connection.sendall(b"220 fake ready\r\n")
                        # Close only after the client does, so it always reads the greeting.
                        while connection.recv(1024):
                            pass

            greeter = threading.Thread(target=greet_twice, daemon=True)
            greeter.start()
            context = ssl.create_default_context()

            client = open_connection("starttls", "127.0.0.1", port, 5, context)
            self.assertIs(type(client), smtplib.SMTP)
            client.close()
            # A TLS client reading a plain-text greeting fails the handshake.
            with self.assertRaises(ssl.SSLError):
                open_connection("ssl", "127.0.0.1", port, 5, context)
            greeter.join(5)


class SendEmailSmtpTests(PasswordFileTestCase):
    def email_config(self, **overrides):
        return {
            "from": "Hermes <sender@example.com>",
            "to": "a@example.com, b@example.com",
            "transport": "smtp",
            "smtp": self.smtp_cfg(),
            **overrides,
        }

    def send(self, fake, config=None, **kwargs):
        kwargs.setdefault("subject", "📊 财经消息 日报")
        kwargs.setdefault("text_body", "纯文本正文")
        kwargs.setdefault("html_body", "<p>HTML 正文</p>")
        with patch(
            "tools.email_delivery.send_message", partial(send_message, connect=fake.connect)
        ):
            return send_email(email_config=config or self.email_config(), **kwargs)

    def test_sends_alternative_bodies_and_valid_attachments(self):
        report = self.temp / "日报 2026-10-28.md"
        report_bytes = "# 日报\n\n中文内容".encode()
        report.write_bytes(report_bytes)
        empty = self.temp / "empty.md"
        empty.write_bytes(b"")
        fake = FakeConnection()

        result = self.send(fake, attachments=(report, empty, self.temp))

        self.assertTrue(result.sent, result.error)
        self.assertIsNone(result.mml_path)
        self.assertEqual(result.attachments, (report,))
        self.assertEqual(len(result.warnings), 2)
        self.assertEqual(fake.opened, ("starttls", "smtp.example.com", 587, 120))

        # Round-trip the bytes smtplib would put on the wire.
        (sent,) = fake.messages
        parsed = email.message_from_bytes(sent.as_bytes(), policy=policy.default)
        self.assertEqual(parsed["Subject"], "📊 财经消息 日报")
        self.assertEqual(
            [address.addr_spec for address in parsed["To"].addresses],
            ["a@example.com", "b@example.com"],
        )
        self.assertEqual(parsed.get_content_type(), "multipart/mixed")
        self.assertEqual(parsed.get_body(("plain",)).get_content().strip(), "纯文本正文")
        self.assertIn("<p>HTML 正文</p>", parsed.get_body(("html",)).get_content())
        (attachment,) = parsed.iter_attachments()
        self.assertEqual(attachment.get_filename(), "日报 2026-10-28.md")
        self.assertEqual(attachment.get_payload(decode=True), report_bytes)
        self.assertEqual(attachment.get_content_charset(), "utf-8")

    def test_html_document_matches_the_mml_transport(self):
        fake = FakeConnection()
        self.assertTrue(self.send(fake).sent)
        html = fake.messages[0].get_body(("html",)).get_content()
        mml = build_mml(
            None,
            from_addr="a@example.com",
            to_addr="b@example.com",
            subject="s",
            html_body="<p>HTML 正文</p>",
            text_body="纯文本正文",
        )
        self.assertIn('class="ena-card"', html)
        self.assertIn(html.strip(), mml)

    def test_refused_recipients_become_a_warning(self):
        fake = FakeConnection(refused={"b@example.com": (550, b"5.1.1 no such user")})
        result = self.send(fake)
        self.assertTrue(result.sent, result.error)
        self.assertIn("部分收件人被拒收: b@example.com 550 5.1.1 no such user", result.warnings)

    def test_failure_is_returned_and_logged_without_the_password(self):
        with self.assertLogs("tools.email_delivery", level="ERROR") as logs:
            result = self.send(FakeConnection(login_error=auth_failure()))

        self.assertFalse(result.sent)
        self.assertTrue(result.error.startswith("SMTPAuthenticationError: 535"), result.error)
        logged = "\n".join(logging.Formatter().format(record) for record in logs.records)
        self.assertIn("SMTPAuthenticationError", logged)
        for text in (result.error, logged, repr(result)):
            self.assertNotIn(PASSWORD, text)

    def test_unknown_transport_is_rejected(self):
        fake = FakeConnection()
        result = self.send(fake, self.email_config(transport="carrier-pigeon"))
        self.assertFalse(result.sent)
        self.assertIn("email.transport must be one of: command, smtp", result.error)
        self.assertIsNone(fake.opened)


class SmtpSchemaTests(unittest.TestCase):
    SCHEMA = ROOT / "schemas" / "runtime_input.schema.json"
    SMTP = {"host": "smtp.example.com", "password_env": "ENA_SMTP_PASSWORD"}

    def test_smtp_block_validates(self):
        email_config = {
            "from": "a@example.com",
            "to": "b@example.com",
            "transport": "smtp",
            "timeout_seconds": 60,
            "smtp": {**self.SMTP, "security": "ssl", "port": 465},
        }
        validate_file({"email": email_config}, self.SCHEMA)

    def test_schema_rejects_a_password_in_config(self):
        email_config = {"transport": "smtp", "smtp": {**self.SMTP, "password": PASSWORD}}
        with self.assertRaises(SchemaValidationError):
            validate_file({"email": email_config}, self.SCHEMA)


if __name__ == "__main__":
    unittest.main()
