"""SQLite mailbox and wait-polling one-message-at-a-time agent workers."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence


MAIL_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = MAIL_DIR / "agent_mail.db"
SCHEMA_VERSION = 3
DEFAULT_MAX_BODY_LENGTH = 100_000
STATUSES = ("pending", "processing", "completed", "failed")

BASE_SCHEMA = """
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
    CHECK ((recipient_read = 0 AND reply IS NULL AND replied_at IS NULL)
        OR (recipient_read = 1 AND reply IS NOT NULL AND replied_at IS NOT NULL))
);
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
    processing_status: str = "pending"
    attempt_count: int = 0
    last_error: str | None = None
    conversation_id: str | None = None
    parent_message_id: int | None = None
    hop_count: int = 0
    lease_token: str | None = None
    lease_generation: int = 0


@dataclass(frozen=True, init=False)
class WorkerConfig:
    max_attempts: int
    timeout: float
    poll_interval: float
    stale_after: float
    max_hops: int
    escalation_agent_id: str | None

    def __init__(
        self,
        max_attempts: int | None = None,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
        stale_after: float = 900.0,
        max_hops: int = 10,
        escalation_agent_id: str | None = None,
        *,
        max_retries: int | None = None,
    ) -> None:
        if max_attempts is not None and max_retries is not None and max_attempts != max_retries:
            raise MailError("max_attemptsとmax_retriesに矛盾する値を同時指定できません")
        attempts = max_attempts if max_attempts is not None else max_retries
        self._validate_positive_int(attempts if attempts is not None else 3, "max_attempts")
        if timeout <= 0 or poll_interval < 0 or stale_after <= 0:
            raise MailError("timeoutは正、poll_intervalは0以上、stale_afterは正で指定してください")
        self._validate_nonnegative_int(max_hops, "max_hops")
        object.__setattr__(self, "max_attempts", attempts if attempts is not None else 3)
        object.__setattr__(self, "timeout", float(timeout))
        object.__setattr__(self, "poll_interval", float(poll_interval))
        object.__setattr__(self, "stale_after", float(stale_after))
        object.__setattr__(self, "max_hops", max_hops)
        object.__setattr__(self, "escalation_agent_id", escalation_agent_id)

    @property
    def max_retries(self) -> int:
        """Compatibility alias; the value means total attempts including the first."""
        return self.max_attempts

    @staticmethod
    def _validate_positive_int(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise MailError(f"{name}は1以上の整数で指定してください")

    @staticmethod
    def _validate_nonnegative_int(value: int, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MailError(f"{name}は0以上の整数で指定してください")


class MailError(RuntimeError):
    """Raised when a mailbox operation cannot be completed."""


class AgentMail:
    def __init__(self, database: str | Path, *, max_body_length: int = DEFAULT_MAX_BODY_LENGTH):
        self.database = Path(database)
        if isinstance(max_body_length, bool) or not isinstance(max_body_length, int) or max_body_length < 1:
            raise MailError("max_body_lengthは1以上の整数で指定してください")
        self.max_body_length = max_body_length

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        """Compatibility API; callers own and must close this connection."""
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        existed = self.database.exists()
        with self.connection() as connection:
            connection.execute("BEGIN")
            connection.executescript(BASE_SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
            additions = {
                "processing_status": "TEXT NOT NULL DEFAULT 'pending'",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "last_error": "TEXT",
                "processing_started_at": "TEXT",
                "completed_at": "TEXT",
                "updated_at": "TEXT",
                "conversation_id": "TEXT",
                "parent_message_id": "INTEGER",
                "hop_count": "INTEGER NOT NULL DEFAULT 0",
                "escalation_sent": "INTEGER NOT NULL DEFAULT 0",
                "lease_token": "TEXT",
                "lease_generation": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
            connection.execute("UPDATE messages SET updated_at = COALESCE(updated_at, created_at)")
            connection.execute("UPDATE messages SET conversation_id = COALESCE(conversation_id, 'legacy-' || id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_processing ON messages(recipient_id, processing_status, created_at, id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_stale ON messages(processing_status, processing_started_at)")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        if not existed:
            self._secure_database_files()

    def register_agent(self, agent_id: str, display_name: str | None = None) -> None:
        self._require_text(agent_id, "agent_id")
        with self.connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT display_name FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
            if row is not None and display_name is not None and row["display_name"] not in (None, display_name):
                raise MailError(f"登録済みエージェントの表示名が異なります: {agent_id}")
            if row is None:
                connection.execute("INSERT INTO agents(agent_id, display_name, created_at) VALUES (?, ?, ?)", (agent_id, display_name, utc_now()))
            connection.commit()

    def ensure_agent(self, agent_id: str, display_name: str | None = None) -> None:
        """Register only when absent; never overwrite an existing agent."""
        self._require_text(agent_id, "agent_id")
        with self.connection() as connection:
            connection.execute("BEGIN")
            connection.execute("INSERT OR IGNORE INTO agents(agent_id, display_name, created_at) VALUES (?, ?, ?)", (agent_id, display_name, utc_now()))
            connection.commit()

    def send(
        self, sender_id: str, recipient_id: str, body: str,
        *, conversation_id: str | None = None, parent_message_id: int | None = None,
        hop_count: int | None = None, thread_id: str | None = None, parent_id: int | None = None,
    ) -> int:
        self._require_text(body, "body")
        if len(body) > self.max_body_length:
            raise MailError(f"本文が最大長を超えています: {self.max_body_length}文字")
        if thread_id is not None:
            if conversation_id is not None and conversation_id != thread_id:
                raise MailError("conversation_idとthread_idが一致しません")
            conversation_id = thread_id
        if parent_id is not None:
            if parent_message_id is not None and parent_message_id != parent_id:
                raise MailError("parent_message_idとparent_idが一致しません")
            parent_message_id = parent_id
        if sender_id == recipient_id:
            raise MailError("送信元と受信先には別のAIエージェントIDを指定してください")
        if hop_count is not None and hop_count < 0:
            raise MailError("hop_countは0以上で指定してください")
        with self.connection() as connection:
            connection.execute("BEGIN")
            self._ensure_agents_exist(connection, sender_id, recipient_id)
            if parent_message_id is not None:
                parent = connection.execute("SELECT * FROM messages WHERE id = ?", (parent_message_id,)).fetchone()
                if parent is None:
                    raise MailError(f"親メールが存在しません: {parent_message_id}")
                if sender_id != parent["recipient_id"]:
                    raise MailError("派生メールの送信元は親メールの受信先でなければなりません")
                if conversation_id is not None and conversation_id != parent["conversation_id"]:
                    raise MailError("派生メールの系列IDが親メールと一致しません")
                expected_hop = parent["hop_count"] + 1
                if hop_count is not None and hop_count != expected_hop:
                    raise MailError("派生メールのホップ数が親メールから連続していません")
                self._ensure_parent_chain(connection, parent_message_id)
                conversation_id = parent["conversation_id"]
                hop_count = expected_hop
            elif hop_count not in (None, 0):
                raise MailError("親メールなしの新規メールのhop_countは0でなければなりません")
            else:
                hop_count = 0
            now = utc_now()
            cursor = connection.execute(
                "INSERT INTO messages(sender_id, recipient_id, body, created_at, updated_at, conversation_id, parent_message_id, hop_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sender_id, recipient_id, body, now, now, conversation_id or str(uuid.uuid4()), parent_message_id, hop_count),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def unread_count(self, agent_id: str) -> dict[str, int]:
        with self.connection() as connection:
            self._ensure_agents_exist(connection, agent_id)
            row = connection.execute(
                "SELECT SUM(CASE WHEN recipient_id = ? AND recipient_read = 0 THEN 1 ELSE 0 END) received, "
                "SUM(CASE WHEN sender_id = ? AND recipient_read = 1 AND sender_read = 0 THEN 1 ELSE 0 END) replies FROM messages",
                (agent_id, agent_id),
            ).fetchone()
        received, replies = int(row["received"] or 0), int(row["replies"] or 0)
        return {"received": received, "replies": replies, "total": received + replies}

    def check(self, agent_id: str) -> list[WorkItem]:
        with self.connection() as connection:
            self._ensure_agents_exist(connection, agent_id)
            rows = connection.execute(
                "SELECT id, sender_id, recipient_id, body, reply, created_at, processing_status, attempt_count, last_error, "
                "conversation_id, parent_message_id, hop_count, recipient_read, lease_token, lease_generation, "
                "CASE WHEN recipient_id = ? AND recipient_read = 0 THEN 'received' ELSE 'reply' END kind, "
                "CASE WHEN recipient_id = ? AND recipient_read = 0 THEN created_at ELSE replied_at END event_at "
                "FROM messages WHERE (recipient_id = ? AND recipient_read = 0) OR (sender_id = ? AND recipient_read = 1 AND sender_read = 0) "
                "ORDER BY event_at ASC, id ASC",
                (agent_id, agent_id, agent_id, agent_id),
            ).fetchall()
        return [self._work_item(row) for row in rows]

    def iter_work(self, agent_id: str) -> Iterator[WorkItem]:
        yield from self.check(agent_id)

    def reply(self, agent_id: str, message_id: int, reply: str) -> None:
        self._require_text(reply, "reply")
        if len(reply) > self.max_body_length:
            raise MailError(f"本文が最大長を超えています: {self.max_body_length}文字")
        with self.connection() as connection:
            connection.execute("BEGIN")
            cursor = connection.execute(
                "UPDATE messages SET reply = ?, recipient_read = 1, replied_at = ?, updated_at = ? "
                "WHERE id = ? AND recipient_id = ? AND recipient_read = 0",
                (reply, utc_now(), utc_now(), message_id, agent_id),
            )
            if cursor.rowcount != 1:
                raise MailError("未読の受信メールが見つかりません")
            connection.commit()

    def mark_reply_read(self, agent_id: str, message_id: int) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN")
            cursor = connection.execute(
                "UPDATE messages SET sender_read = 1, updated_at = ? WHERE id = ? AND sender_id = ? AND recipient_read = 1 AND sender_read = 0",
                (utc_now(), message_id, agent_id),
            )
            if cursor.rowcount != 1:
                raise MailError("未読の返信が見つかりません")
            connection.commit()

    def claim_next(self, agent_id: str) -> WorkItem | None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM messages WHERE recipient_id = ? AND recipient_read = 0 AND processing_status = 'pending' "
                "ORDER BY created_at ASC, id ASC LIMIT 1", (agent_id,)
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = utc_now()
            lease_token = str(uuid.uuid4())
            cursor = connection.execute(
                "UPDATE messages SET processing_status = 'processing', attempt_count = attempt_count + 1, "
                "processing_started_at = ?, updated_at = ?, lease_token = ?, lease_generation = lease_generation + 1 "
                "WHERE id = ? AND processing_status = 'pending'",
                (now, now, lease_token, row["id"]),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
            return self._work_item(row)

    def finish(self, message_id: int) -> None:
        raise MailError("finishには取得時のlease_tokenとlease_generationが必要です")

    def complete(self, item: WorkItem) -> None:
        self._transition(item, "completed", None)

    def fail(self, item: WorkItem, error: str, *, retry: bool) -> None:
        self._transition(item, "pending" if retry else "failed", error)

    def recover_stale(self, stale_after: float, agent_id: str | None = None) -> int:
        if stale_after < 0:
            raise MailError("stale_afterは0以上で指定してください")
        cutoff = time.time() - stale_after
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT id, processing_started_at FROM messages WHERE processing_status = 'processing'" + (" AND recipient_id = ?" if agent_id else ""), ((agent_id,) if agent_id else ())).fetchall()
            ids = [row["id"] for row in rows if row["processing_started_at"] and self._timestamp_seconds(row["processing_started_at"]) <= cutoff]
            for message_id in ids:
                connection.execute("UPDATE messages SET processing_status = 'pending', processing_started_at = NULL, lease_token = NULL, lease_generation = lease_generation + 1, updated_at = ? WHERE id = ? AND processing_status = 'processing'", (utc_now(), message_id))
            connection.commit()
        return len(ids)

    def worker_status(self, agent_id: str | None = None) -> dict[str, int]:
        with self.connection() as connection:
            query = "SELECT processing_status, COUNT(*) count FROM messages"
            params: tuple[object, ...] = ()
            if agent_id:
                query += " WHERE recipient_id = ?"; params = (agent_id,)
            query += " GROUP BY processing_status"
            result = {status: 0 for status in STATUSES}
            for row in connection.execute(query, params): result[row["processing_status"]] = row["count"]
        return result

    def _transition(self, item: WorkItem, status: str, error: str | None) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN")
            now = utc_now()
            completed = now if status == "completed" else None
            cursor = connection.execute(
                "UPDATE messages SET processing_status = ?, last_error = ?, completed_at = ?, updated_at = ? "
                "WHERE id = ? AND processing_status = 'processing' AND lease_token = ? AND lease_generation = ?",
                (status, error, completed, now, item.message_id, item.lease_token, item.lease_generation),
            )
            if cursor.rowcount != 1: raise MailError("処理中のメールが見つかりません")
            connection.commit()

    @staticmethod
    def _timestamp_seconds(value: str) -> float:
        return datetime.fromisoformat(value).timestamp()

    @staticmethod
    def _work_item(row: sqlite3.Row) -> WorkItem:
        kind = row["kind"] if "kind" in row.keys() else ("received" if row["recipient_read"] == 0 else "reply")
        event_at = row["event_at"] if "event_at" in row.keys() else row["created_at"]
        return WorkItem(kind, row["id"], row["sender_id"], row["recipient_id"], row["body"], row["reply"], row["created_at"], event_at, row["processing_status"], row["attempt_count"], row["last_error"], row["conversation_id"], row["parent_message_id"], row["hop_count"], row["lease_token"], row["lease_generation"])

    @staticmethod
    def _require_text(value: str, name: str) -> None:
        if not value or not value.strip(): raise MailError(f"{name}を空にはできません")

    @staticmethod
    def _ensure_parent_chain(connection: sqlite3.Connection, parent_id: int) -> None:
        seen: set[int] = set()
        current: int | None = parent_id
        while current is not None:
            if current in seen:
                raise MailError("メール系列に循環参照があります")
            seen.add(current)
            row = connection.execute("SELECT parent_message_id FROM messages WHERE id = ?", (current,)).fetchone()
            if row is None:
                raise MailError(f"親メールが存在しません: {current}")
            current = row["parent_message_id"]

    def _secure_database_files(self) -> None:
        for path in (self.database, Path(str(self.database) + "-wal"), Path(str(self.database) + "-shm")):
            try:
                if path.exists():
                    path.chmod(0o600)
            except OSError:
                continue

    @staticmethod
    def _ensure_agents_exist(connection: sqlite3.Connection, *agent_ids: str) -> None:
        for agent_id in agent_ids:
            if connection.execute("SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)).fetchone() is None:
                raise MailError(f"AIエージェントIDが登録されていません: {agent_id}")


class HandlerContext:
    def __init__(self, mailbox: AgentMail, item: WorkItem, agent_id: str):
        self.mailbox, self.item, self.agent_id = mailbox, item, agent_id

    def send(self, recipient_id: str, body: str) -> int:
        return self.mailbox.send(self.agent_id, recipient_id, body, conversation_id=self.item.conversation_id, parent_message_id=self.item.message_id, hop_count=self.item.hop_count + 1)


Handler = Callable[[WorkItem, HandlerContext], object]


class HandlerRegistry:
    def __init__(self): self._handlers: dict[str, Handler] = {}
    def register(self, name: str, handler: Handler) -> None: self._handlers[name] = handler
    def get(self, name: str = "default") -> Handler:
        if name not in self._handlers: raise MailError(f"ハンドラーが登録されていません: {name}")
        return self._handlers[name]


class SubprocessHandler:
    def __init__(self, command: Sequence[str], timeout: float | None = None):
        if not command: raise ValueError("command must not be empty")
        self.command, self.timeout = tuple(command), timeout
    def __call__(self, item: WorkItem, context: HandlerContext) -> object:
        completed = subprocess.run(self.command, input=item.body, text=True, capture_output=True, timeout=self.timeout, shell=False, check=True)
        return completed.stdout


class Worker:
    def __init__(self, mailbox: AgentMail, agent_id: str, handlers: HandlerRegistry, config: WorkerConfig | None = None):
        self.mailbox, self.agent_id, self.handlers = mailbox, agent_id, handlers
        self.config = config or WorkerConfig()
        self.stop_event = threading.Event()

    def stop(self) -> None: self.stop_event.set()

    def run_once(self) -> bool:
        item = self.mailbox.claim_next(self.agent_id)
        if item is None: return False
        if item.hop_count > self.config.max_hops:
            error = f"最大ホップ数を超過しました: {item.hop_count} > {self.config.max_hops}"
            self.mailbox.fail(item, error, retry=False)
            if self.config.escalation_agent_id:
                self.mailbox.send(self.agent_id, self.config.escalation_agent_id, error, conversation_id=item.conversation_id, parent_message_id=item.message_id, hop_count=item.hop_count + 1)
            return True
        context = HandlerContext(self.mailbox, item, self.agent_id)
        error: str | None = None
        thread_result: list[object] = []
        thread_error: list[BaseException] = []
        def invoke() -> None:
            try: thread_result.append(self.handlers.get()(item, context))
            except BaseException as exc: thread_error.append(exc)
        thread = threading.Thread(target=invoke, daemon=True)
        thread.start(); thread.join(self.config.timeout)
        if thread.is_alive(): error = f"処理がタイムアウトしました: {self.config.timeout}秒"
        elif thread_error: error = f"{type(thread_error[0]).__name__}: {thread_error[0]}"
        if error:
            self.mailbox.fail(item, error, retry=item.attempt_count < self.config.max_attempts)
        else:
            self.mailbox.complete(item)
        return True

    def run(self, *, max_messages: int | None = None) -> int:
        processed = 0
        while not self.stop_event.is_set() and (max_messages is None or processed < max_messages):
            self.mailbox.recover_stale(self.config.stale_after, self.agent_id)
            if self.run_once(): processed += 1; continue
            self.stop_event.wait(self.config.poll_interval)
        return processed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIエージェント用SQLiteメールシステム")
    parser.add_argument("--db", default=str(DEFAULT_DATABASE))
    parser.add_argument("--max-body-length", type=int, default=DEFAULT_MAX_BODY_LENGTH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    register = sub.add_parser("register"); register.add_argument("agent_id"); register.add_argument("--name")
    send = sub.add_parser("send"); send.add_argument("--from", dest="sender_id", required=True); send.add_argument("--to", dest="recipient_id", required=True); send.add_argument("--body", required=True); send.add_argument("--conversation"); send.add_argument("--parent", type=int); send.add_argument("--hop", type=int, default=None)
    unread = sub.add_parser("unread"); unread.add_argument("--agent", required=True)
    check = sub.add_parser("check"); check.add_argument("--agent", required=True)
    reply = sub.add_parser("reply"); reply.add_argument("--agent", required=True); reply.add_argument("--message", type=int, required=True); reply.add_argument("--body", required=True)
    read = sub.add_parser("mark-reply-read"); read.add_argument("--agent", required=True); read.add_argument("--message", type=int, required=True)
    for name in ("worker-once", "worker-loop"):
        worker = sub.add_parser(name); worker.add_argument("--agent", required=True); worker.add_argument("--timeout", type=float, default=300); worker.add_argument("--interval", type=float, default=1); worker.add_argument("--max-attempts", type=int); worker.add_argument("--max-retries", type=int); worker.add_argument("--stale-after", type=float, default=900); worker.add_argument("--max-hops", type=int, default=10); worker.add_argument("--escalation-agent"); worker.add_argument("--command", dest="handler_command", nargs=argparse.REMAINDER, required=True)
        if name == "worker-loop": worker.add_argument("--max-messages", type=int)
    recover = sub.add_parser("recover"); recover.add_argument("--stale-after", type=float, required=True); recover.add_argument("--agent")
    status = sub.add_parser("worker-status"); status.add_argument("--agent")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv); mailbox = AgentMail(args.db, max_body_length=args.max_body_length)
    try:
        if args.command == "init": mailbox.initialize(); result = {"database": str(mailbox.database), "initialized": True}
        elif args.command == "register": mailbox.register_agent(args.agent_id, args.name); result = {"agent_id": args.agent_id, "registered": True}
        elif args.command == "send": result = {"message_id": mailbox.send(args.sender_id, args.recipient_id, args.body, conversation_id=args.conversation, parent_message_id=args.parent, hop_count=args.hop)}
        elif args.command == "unread": result = mailbox.unread_count(args.agent)
        elif args.command == "check": result = [asdict(item) for item in mailbox.check(args.agent)]
        elif args.command == "reply": mailbox.reply(args.agent, args.message, args.body); result = {"message_id": args.message, "replied": True}
        elif args.command == "mark-reply-read": mailbox.mark_reply_read(args.agent, args.message); result = {"message_id": args.message, "sender_read": True}
        elif args.command == "recover": result = {"recovered": mailbox.recover_stale(args.stale_after, args.agent)}
        elif args.command == "worker-status": result = mailbox.worker_status(args.agent)
        else:
            mailbox.initialize(); mailbox.ensure_agent(args.agent, args.agent)
            if args.escalation_agent: mailbox.ensure_agent(args.escalation_agent, args.escalation_agent)
            registry = HandlerRegistry(); registry.register("default", SubprocessHandler(args.handler_command, args.timeout))
            config = WorkerConfig(args.max_attempts, args.timeout, args.interval, args.stale_after, args.max_hops, args.escalation_agent, max_retries=args.max_retries)
            worker = Worker(mailbox, args.agent, registry, config)
            try: count = worker.run_once() if args.command == "worker-once" else worker.run(max_messages=args.max_messages)
            except KeyboardInterrupt: worker.stop(); count = 0
            result = {"processed": int(bool(count)) if args.command == "worker-once" else count}
    except (MailError, sqlite3.Error, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False)); return 1
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
