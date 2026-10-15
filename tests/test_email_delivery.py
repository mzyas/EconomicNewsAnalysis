import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.email_delivery import build_email_artifact, send_email
from tools.email_render import md_to_plain


class EmailDeliveryTests(unittest.TestCase):
    def test_build_mml_without_sending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "briefing.md"
            md_path.write_text("# 简报\n\n内容", encoding="utf-8")
            mml_path = build_email_artifact(
                md_path,
                {
                    "from": "from@example.com",
                    "to": "to@example.com",
                    "subject": "宏观简报",
                    "attachment_name": "宏观简报.md",
                },
                html_body='<table style="table-layout:fixed"><tr><td>正文</td></tr></table>',
            )
            text = mml_path.read_text(encoding="utf-8")
            self.assertIn("Subject: 宏观简报", text)
            self.assertIn('name="宏观简报.md"', text)
            self.assertIn('table-layout:fixed', text)

    def test_plain_text_email_hides_markdown_link_target(self):
        self.assertEqual(md_to_plain("新闻 [发布页](https://example.com/very-long)"), "新闻 发布页")

    def test_send_email_without_attachment_manages_temporary_mml(self):
        captured = {}

        def capture_send(path, _config):
            captured["mml"] = path.read_text(encoding="utf-8")
            captured["path"] = path

        with patch("tools.email_delivery.send_mml", side_effect=capture_send):
            result = send_email(
                email_config={"from": "a@b.com", "to": "c@d.com"},
                subject="无附件摘要",
                text_body="有效摘要正文",
                html_body="<p>有效摘要正文</p>",
            )

        self.assertTrue(result.sent)
        self.assertIn("Subject: 无附件摘要", captured["mml"])
        self.assertNotIn("filename=", captured["mml"])
        self.assertFalse(captured["path"].exists())

    def test_send_email_filters_invalid_attachments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            valid = Path(temp_dir) / "report.md"
            valid.write_text("# 报告\n\n有效正文", encoding="utf-8")
            empty = Path(temp_dir) / "empty.md"
            empty.write_text("", encoding="utf-8")
            directory = Path(temp_dir) / "folder"
            directory.mkdir()

            with patch("tools.email_delivery.send_mml"):
                result = send_email(
                    email_config={"from": "a@b.com", "to": "c@d.com"},
                    subject="报告",
                    text_body="有效正文",
                    attachments=(valid, empty, directory),
                )

        self.assertTrue(result.sent)
        self.assertEqual(result.attachments, (valid,))
        self.assertEqual(len(result.warnings), 2)

    def test_temporary_cleanup_failure_does_not_override_sent_result(self):
        captured = {}

        def capture_send(path, _config):
            captured["path"] = path

        with patch("tools.email_delivery.send_mml", side_effect=capture_send), patch.object(
            Path, "unlink", side_effect=OSError("locked")
        ):
            result = send_email(
                email_config={"from": "a@b.com", "to": "c@d.com"},
                subject="摘要",
                text_body="有效正文",
            )

        self.assertTrue(result.sent)
        self.assertIsNone(result.error)
        self.assertTrue(any("清理失败" in warning for warning in result.warnings))
        captured["path"].unlink(missing_ok=True)

    def test_temporary_mml_creation_failure_is_returned(self):
        with patch(
            "tools.email_delivery.tempfile.NamedTemporaryFile",
            side_effect=OSError("temp unavailable"),
        ):
            result = send_email(
                email_config={"from": "a@b.com", "to": "c@d.com"},
                subject="摘要",
                text_body="有效正文",
            )

        self.assertFalse(result.sent)
        self.assertIn("temp unavailable", result.error or "")



if __name__ == "__main__":
    unittest.main()
