"""SQLite-based mailbox for asynchronous communication between AI agents."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


MAIL_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = MAIL_DIR / "agent_mail.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    display_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    body TEXT NOT NULL,
    reply TEXT,
    recipient_read INTEGER NOT NULL DEFAULT 0 CHECK (recipient_read IN (0, 1)),
    sender_read INTEGER NOT NULL DEFAULT 0 CHECK (sender_read IN (0, 1)),
    created_at TEXT NOT NULL,
    replied_at TEXT,
    FOREIGN KEY (sender_id) REFERENCES agents(agent_id),
    FOREIGN KEY (recipient_id) REFERENCES agents(agent_id),
    CHECK (sender_id <> recipient_id),
    CHECK (
        (recipient_read = 0 AND reply IS NULL AND replied_at IS NULL)
        OR
        (recipient_read = 1 AND reply IS NOT NULL AND replied_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_messages_recipient_unread
    ON messages(recipient_id, recipient_read, created_at, id);
CREATE INDEX IF NOT EXISTS idx_messages_sender_unread
    ON messages(sender_id, sender_read, replied_at, id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class WorkItem:
    kind: str
    message_id: int
    sender_id: str
    recipient_id: str
    body: str
    reply: str | None
    created_at: str
    event_at: str


class MailError(RuntimeError):
    """Raised when a mailbox operation cannot be completed."""


class AgentMail:
    def __init__(self, database: str | Path):
        self.database = Path(database)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def register_agent(self, agent_id: str, display_name: str | None = None) -> None:
        self._require_text(agent_id, "agent_id")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agents(agent_id, display_name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, agents.display_name)
                """,
                (agent_id, display_name, utc_now()),
            )

    def send(self, sender_id: str, recipient_id: str, body: str) -> int:
        self._require_text(body, "body")
        if sender_id == recipient_id:
            raise MailError("送信元と受信先には別のAIエージェントIDを指定してください")
        with self.connect() as connection:
            self._ensure_agents_exist(connection, sender_id, recipient_id)
            cursor = connection.execute(
                """
                INSERT INTO messages(sender_id, recipient_id, body, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (sender_id, recipient_id, body, utc_now()),
            )
            return int(cursor.lastrowid)

    def unread_count(self, agent_id: str) -> dict[str, int]:
        with self.connect() as connection:
            self._ensure_agents_exist(connection, agent_id)
            row = connection.execute(
                """
                SELECT
                    SUM(CASE
                        WHEN recipient_id = ? AND recipient_read = 0 THEN 1 ELSE 0
                    END) AS received,
                    SUM(CASE
                        WHEN sender_id = ? AND recipient_read = 1
                             AND sender_read = 0 THEN 1 ELSE 0
                    END) AS replies
                FROM messages
                """,
                (agent_id, agent_id),
            ).fetchone()
        received = int(row["received"] or 0)
        replies = int(row["replies"] or 0)
        return {"received": received, "replies": replies, "total": received + replies}

    def check(self, agent_id: str) -> list[WorkItem]:
        """Return all work for an agent in event-time order."""
        with self.connect() as connection:
            self._ensure_agents_exist(connection, agent_id)
            rows = connection.execute(
                """
                SELECT
                    id, sender_id, recipient_id, body, reply, created_at,
                    CASE
                        WHEN recipient_id = ? AND recipient_read = 0
                            THEN 'received'
                        ELSE 'reply'
                    END AS kind,
                    CASE
                        WHEN recipient_id = ? AND recipient_read = 0
                            THEN created_at
                        ELSE replied_at
                    END AS event_at
                FROM messages
                WHERE (recipient_id = ? AND recipient_read = 0)
                   OR (sender_id = ? AND recipient_read = 1 AND sender_read = 0)
                ORDER BY event_at ASC, id ASC
                """,
                (agent_id, agent_id, agent_id, agent_id),
            ).fetchall()
        return [
            WorkItem(
                kind=row["kind"],
                message_id=row["id"],
                sender_id=row["sender_id"],
                recipient_id=row["recipient_id"],
                body=row["body"],
                reply=row["reply"],
                created_at=row["created_at"],
                event_at=row["event_at"],
            )
            for row in rows
        ]

    def iter_work(self, agent_id: str) -> Iterator[WorkItem]:
        yield from self.check(agent_id)

    def reply(self, agent_id: str, message_id: int, reply: str) -> None:
        """Store a response and mark the recipient side read atomically."""
        self._require_text(reply, "reply")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE messages
                SET reply = ?, recipient_read = 1, replied_at = ?
                WHERE id = ? AND recipient_id = ? AND recipient_read = 0
                """,
                (reply, utc_now(), message_id, agent_id),
            )
            if cursor.rowcount != 1:
                raise MailError(
                    "未読の受信メールが見つかりません。ID、受信先、処理済み状態を確認してください"
                )

    def mark_reply_read(self, agent_id: str, message_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE messages
                SET sender_read = 1
                WHERE id = ? AND sender_id = ?
                  AND recipient_read = 1 AND sender_read = 0
                """,
                (message_id, agent_id),
            )
            if cursor.rowcount != 1:
                raise MailError(
                    "未読の返信が見つかりません。ID、送信元、処理済み状態を確認してください"
                )

    @staticmethod
    def _require_text(value: str, name: str) -> None:
        if not value or not value.strip():
            raise MailError(f"{name}を空にはできません")

    @staticmethod
    def _ensure_agents_exist(
        connection: sqlite3.Connection, *agent_ids: str
    ) -> None:
        for agent_id in agent_ids:
            row = connection.execute(
                "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                raise MailError(f"AIエージェントIDが登録されていません: {agent_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIエージェント用SQLiteメールシステム")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DATABASE),
        help=f"SQLiteデータベースのパス（既定: {DEFAULT_DATABASE}）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="データベースを初期化する")

    register = subparsers.add_parser("register", help="AIエージェントを登録する")
    register.add_argument("agent_id")
    register.add_argument("--name")

    send = subparsers.add_parser("send", help="新しいメールを送信する")
    send.add_argument("--from", dest="sender_id", required=True)
    send.add_argument("--to", dest="recipient_id", required=True)
    send.add_argument("--body", required=True)

    unread = subparsers.add_parser("unread", help="未読件数を回答する")
    unread.add_argument("--agent", required=True)

    check = subparsers.add_parser("check", help="未処理項目を時系列で取得する")
    check.add_argument("--agent", required=True)

    reply = subparsers.add_parser("reply", help="受信メールへ返信する")
    reply.add_argument("--agent", required=True)
    reply.add_argument("--message", type=int, required=True)
    reply.add_argument("--body", required=True)

    read_reply = subparsers.add_parser(
        "mark-reply-read", help="自分が送ったメールへの返信を既読にする"
    )
    read_reply.add_argument("--agent", required=True)
    read_reply.add_argument("--message", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mailbox = AgentMail(args.db)
    try:
        if args.command == "init":
            mailbox.initialize()
            result: object = {"database": str(mailbox.database), "initialized": True}
        elif args.command == "register":
            mailbox.register_agent(args.agent_id, args.name)
            result = {"agent_id": args.agent_id, "registered": True}
        elif args.command == "send":
            result = {
                "message_id": mailbox.send(
                    args.sender_id, args.recipient_id, args.body
                )
            }
        elif args.command == "unread":
            result = mailbox.unread_count(args.agent)
        elif args.command == "check":
            result = [asdict(item) for item in mailbox.check(args.agent)]
        elif args.command == "reply":
            mailbox.reply(args.agent, args.message, args.body)
            result = {"message_id": args.message, "replied": True}
        elif args.command == "mark-reply-read":
            mailbox.mark_reply_read(args.agent, args.message)
            result = {"message_id": args.message, "sender_read": True}
        else:
            raise AssertionError(f"unknown command: {args.command}")
    except (MailError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
