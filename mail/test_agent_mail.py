import tempfile
import unittest
from pathlib import Path

try:
    from .agent_mail import AgentMail, MailError
except ImportError:
    from agent_mail import AgentMail, MailError


class AgentMailTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mailbox = AgentMail(Path(self.temp_dir.name) / "mail.db")
        self.mailbox.initialize()
        self.mailbox.register_agent("writer", "執筆AI")
        self.mailbox.register_agent("reviewer", "監査AI")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_mail_round_trip(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "第1話を監査して")

        self.assertEqual(
            self.mailbox.unread_count("reviewer"),
            {"received": 1, "replies": 0, "total": 1},
        )
        received = self.mailbox.check("reviewer")
        self.assertEqual(received[0].kind, "received")
        self.assertEqual(received[0].body, "第1話を監査して")

        self.mailbox.reply("reviewer", message_id, "矛盾はありません")
        self.assertEqual(self.mailbox.unread_count("reviewer")["total"], 0)
        self.assertEqual(
            self.mailbox.unread_count("writer"),
            {"received": 0, "replies": 1, "total": 1},
        )

        reply = self.mailbox.check("writer")[0]
        self.assertEqual(reply.kind, "reply")
        self.assertEqual(reply.reply, "矛盾はありません")

        self.mailbox.mark_reply_read("writer", message_id)
        self.assertEqual(self.mailbox.unread_count("writer")["total"], 0)

    def test_work_is_sorted_by_event_time(self) -> None:
        first = self.mailbox.send("writer", "reviewer", "一件目")
        second = self.mailbox.send("writer", "reviewer", "二件目")
        self.assertEqual(
            [item.message_id for item in self.mailbox.iter_work("reviewer")],
            [first, second],
        )

    def test_only_recipient_can_reply(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "確認して")
        with self.assertRaises(MailError):
            self.mailbox.reply("writer", message_id, "不正な返信")

    def test_duplicate_processing_is_rejected(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "確認して")
        self.mailbox.reply("reviewer", message_id, "確認済み")
        with self.assertRaises(MailError):
            self.mailbox.reply("reviewer", message_id, "二重返信")

        self.mailbox.mark_reply_read("writer", message_id)
        with self.assertRaises(MailError):
            self.mailbox.mark_reply_read("writer", message_id)

    def test_unknown_agent_is_rejected(self) -> None:
        with self.assertRaises(MailError):
            self.mailbox.send("writer", "unknown", "こんにちは")


if __name__ == "__main__":
    unittest.main()
