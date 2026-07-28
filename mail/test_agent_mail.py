import tempfile
import threading
import time
import unittest
import os
import stat
import sys
from pathlib import Path

try:
    from .agent_mail import AgentMail, HandlerContext, HandlerRegistry, MailError, SubprocessHandler, Worker, WorkerConfig
except ImportError:
    from agent_mail import AgentMail, HandlerContext, HandlerRegistry, MailError, SubprocessHandler, Worker, WorkerConfig


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

    def worker(self, handler, **kwargs):
        registry = HandlerRegistry()
        registry.register("default", handler)
        return Worker(self.mailbox, "reviewer", registry, WorkerConfig(**kwargs))

    def test_worker_completes_one_message_and_records_timestamps(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "処理して")
        worker = self.worker(lambda item, context: None)
        self.assertTrue(worker.run_once())
        with self.mailbox.connection() as connection:
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        self.assertEqual(row["processing_status"], "completed")
        self.assertIsNotNone(row["processing_started_at"])
        self.assertIsNotNone(row["completed_at"])
        self.assertEqual(row["attempt_count"], 1)

    def test_oldest_and_stable_id_order_is_exclusive(self) -> None:
        first = self.mailbox.send("writer", "reviewer", "first")
        second = self.mailbox.send("writer", "reviewer", "second")
        with self.mailbox.connection() as connection:
            connection.execute("UPDATE messages SET created_at = ? WHERE id IN (?, ?)", ("2026-01-01T00:00:00+00:00", first, second))
        seen = []
        worker = self.worker(lambda item, context: seen.append(item.message_id))
        barrier = threading.Barrier(2)
        def run():
            barrier.wait()
            worker.run_once()
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(seen), [first, second])
        self.assertEqual(seen[0], min(first, second))

    def test_failure_retry_and_max_retry(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "fail")
        worker = self.worker(lambda item, context: (_ for _ in ()).throw(ValueError("bad")), max_retries=2)
        worker.run_once()
        with self.mailbox.connection() as connection:
            row = connection.execute("SELECT processing_status, attempt_count, last_error FROM messages WHERE id = ?", (message_id,)).fetchone()
        self.assertEqual((row["processing_status"], row["attempt_count"]), ("pending", 1))
        self.assertIn("bad", row["last_error"])
        worker.run_once()
        with self.mailbox.connection() as connection:
            row = connection.execute("SELECT processing_status, attempt_count FROM messages WHERE id = ?", (message_id,)).fetchone()
        self.assertEqual((row["processing_status"], row["attempt_count"]), ("failed", 2))

    def test_timeout_is_recorded(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "slow")
        worker = self.worker(lambda item, context: time.sleep(0.2), timeout=0.01, max_retries=1)
        worker.run_once()
        with self.mailbox.connection() as connection:
            row = connection.execute("SELECT processing_status, last_error FROM messages WHERE id = ?", (message_id,)).fetchone()
        self.assertEqual(row["processing_status"], "failed")
        self.assertIn("タイムアウト", row["last_error"])

    def test_stale_processing_recovery(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "stale")
        claimed = self.mailbox.claim_next("reviewer")
        self.assertEqual(claimed.message_id, message_id)
        with self.mailbox.connection() as connection:
            connection.execute("UPDATE messages SET processing_started_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", message_id))
        self.assertEqual(self.mailbox.recover_stale(1), 1)
        with self.mailbox.connection() as connection:
            self.assertEqual(connection.execute("SELECT processing_status FROM messages WHERE id = ?", (message_id,)).fetchone()[0], "pending")

    def test_handler_can_send_lineage_mail(self) -> None:
        self.mailbox.register_agent("manager", "manager")
        message_id = self.mailbox.send("writer", "reviewer", "ask", conversation_id="conversation-1")
        worker = self.worker(lambda item, context: context.send("manager", "escalate"))
        worker.run_once()
        with self.mailbox.connection() as connection:
            row = connection.execute("SELECT * FROM messages WHERE sender_id = 'reviewer'").fetchone()
        self.assertEqual(row["parent_message_id"], message_id)
        self.assertEqual(row["conversation_id"], "conversation-1")
        self.assertEqual(row["hop_count"], 1)

    def test_max_hop_escalates_without_loop(self) -> None:
        self.mailbox.register_agent("manager", "manager")
        root = self.mailbox.send("writer", "reviewer", "root")
        self.mailbox.reply("reviewer", root, "received")
        middle = self.mailbox.send("reviewer", "manager", "middle", parent_message_id=root)
        message_id = self.mailbox.send("manager", "reviewer", "loop", parent_message_id=middle)
        worker = self.worker(lambda item, context: None, max_hops=1, escalation_agent_id="manager")
        worker.run_once()
        with self.mailbox.connection() as connection:
            source = connection.execute("SELECT processing_status, last_error FROM messages WHERE id = ?", (message_id,)).fetchone()
            escalation = connection.execute("SELECT recipient_id, body FROM messages WHERE sender_id = 'reviewer' ORDER BY id DESC").fetchone()
        self.assertEqual(source["processing_status"], "failed")
        self.assertEqual(escalation["recipient_id"], "manager")
        self.assertIn("最大ホップ数", escalation["body"])

    def test_safe_stop(self) -> None:
        worker = self.worker(lambda item, context: None, poll_interval=0.01)
        thread = threading.Thread(target=worker.run)
        thread.start()
        time.sleep(0.03)
        worker.stop()
        thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_initialize_migrates_legacy_schema_idempotently(self) -> None:
        legacy = self.mailbox.database
        with self.mailbox.connection() as connection:
            connection.executescript("DROP TABLE messages; DROP TABLE agents;")
            connection.executescript("""CREATE TABLE agents (agent_id TEXT PRIMARY KEY, display_name TEXT, created_at TEXT NOT NULL);
                CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT NOT NULL, recipient_id TEXT NOT NULL, body TEXT NOT NULL, reply TEXT, recipient_read INTEGER NOT NULL DEFAULT 0, sender_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, replied_at TEXT);""")
            connection.execute("INSERT INTO agents VALUES ('a', 'a', '2020')")
            connection.execute("INSERT INTO agents VALUES ('b', 'b', '2020')")
            connection.execute("INSERT INTO messages(sender_id, recipient_id, body, created_at) VALUES ('a', 'b', 'legacy', '2020')")
        self.mailbox.initialize()
        self.mailbox.initialize()
        self.assertEqual(self.mailbox.claim_next("b").body, "legacy")

    def test_processing_completed_is_distinct_from_read_and_reply(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "work")
        self.worker(lambda item, context: None).run_once()
        with self.mailbox.connection() as connection:
            row = connection.execute("SELECT processing_status, recipient_read, reply FROM messages WHERE id = ?", (message_id,)).fetchone()
        self.assertEqual((row["processing_status"], row["recipient_read"], row["reply"]), ("completed", 0, None))

    def test_max_attempts_is_total_and_alias_is_compatible(self) -> None:
        self.assertEqual(WorkerConfig(max_attempts=3).max_retries, 3)
        self.assertEqual(WorkerConfig(max_retries=3).max_attempts, 3)
        with self.assertRaises(MailError): WorkerConfig(max_attempts=2, max_retries=3)
        message_id = self.mailbox.send("writer", "reviewer", "retry")
        calls = []
        worker = self.worker(lambda item, context: calls.append(item.message_id) or (_ for _ in ()).throw(ValueError("x")), max_attempts=3)
        for _ in range(3): worker.run_once()
        self.assertEqual(len(calls), 3)
        with self.mailbox.connection() as connection:
            self.assertEqual(connection.execute("SELECT processing_status, attempt_count FROM messages WHERE id = ?", (message_id,)).fetchone()[0:2], ("failed", 3))

    def test_stale_recovery_fences_old_owner(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "lease")
        first = self.mailbox.claim_next("reviewer")
        with self.mailbox.connection() as connection:
            connection.execute("UPDATE messages SET processing_started_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", message_id))
        self.assertEqual(self.mailbox.recover_stale(1), 1)
        second = self.mailbox.claim_next("reviewer")
        with self.assertRaises(MailError): self.mailbox.complete(first)
        self.mailbox.complete(second)

    def test_subprocess_timeout_returns_and_terminates_child(self) -> None:
        item = self.mailbox.send("writer", "reviewer", "slow")
        claimed = self.mailbox.claim_next("reviewer")
        handler = SubprocessHandler((sys.executable, "-c", "import time; time.sleep(2)"), timeout=0.05)
        with self.assertRaises(Exception): handler(claimed, HandlerContext(self.mailbox, claimed, "reviewer"))
        self.mailbox.fail(claimed, "timeout", retry=False)
        self.assertIsNotNone(item)

    def test_subprocess_handler_round_trip_is_utf8(self) -> None:
        message_id = self.mailbox.send("writer", "reviewer", "日本語のメール本文")
        item = self.mailbox.claim_next("reviewer")
        self.assertIsNotNone(item)
        handler = SubprocessHandler((sys.executable, "-c", "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write((data.decode('utf-8') + '・応答').encode('utf-8'))"))
        result = handler(item, HandlerContext(self.mailbox, item, "reviewer"))
        self.assertEqual(result, "日本語のメール本文・応答")
        self.assertIsNotNone(message_id)

    def test_auto_registration_does_not_overwrite_existing_attributes(self) -> None:
        self.mailbox.ensure_agent("other", "Original")
        self.mailbox.ensure_agent("other", "New")
        with self.mailbox.connection() as connection:
            self.assertEqual(connection.execute("SELECT display_name FROM agents WHERE agent_id = 'other'").fetchone()[0], "Original")
        with self.assertRaises(MailError): self.mailbox.register_agent("other", "Different")

    def test_lineage_is_derived_and_inconsistent_values_are_rejected(self) -> None:
        root = self.mailbox.send("writer", "reviewer", "root", thread_id="t1")
        child = self.mailbox.send("reviewer", "writer", "child", parent_id=root)
        with self.mailbox.connection() as connection:
            row = connection.execute("SELECT conversation_id, parent_message_id, hop_count FROM messages WHERE id = ?", (child,)).fetchone()
        self.assertEqual((row["conversation_id"], row["parent_message_id"], row["hop_count"]), ("t1", root, 1))
        with self.assertRaises(MailError): self.mailbox.send("reviewer", "writer", "bad", parent_id=root, thread_id="other")
        with self.assertRaises(MailError): self.mailbox.send("reviewer", "writer", "bad", parent_id=root, hop_count=9)

    def test_body_limit_rejects_overflow(self) -> None:
        mailbox = AgentMail(self.mailbox.database, max_body_length=4)
        with self.assertRaises(MailError): mailbox.send("writer", "reviewer", "12345")
        mailbox.send("writer", "reviewer", "1234")

    @unittest.skipUnless(os.name == "posix", "file mode is platform dependent")
    def test_new_database_is_owner_only(self) -> None:
        path = self.mailbox.database
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
