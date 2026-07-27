from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from mail.agent_mail import AgentMail, MailError


WORK_ID = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
RUN_ID = re.compile(r"^run-\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{12}$")
META_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
STATES = {"pending", "running", "completed", "failed", "interrupted"}
REQUIRED = {"agent_id", "display_name", "role", "adapter", "model", "inputs", "output", "next_agent", "allowed_operations", "forbidden", "timeout", "max_attempts"}


class KoboError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def safe_path(root: Path, value: str | Path, *, must_exist: bool = False) -> Path:
    root = root.resolve()
    candidate = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise KoboError(f"参照先が許可範囲外です: {value}") from error
    if must_exist and not candidate.exists():
        raise KoboError(f"参照先が存在しません: {candidate}")
    return candidate


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    display_name: str
    role: str
    adapter: str
    model: str
    inputs: list[str]
    output: str
    next_agent: str | None
    allowed_operations: list[str]
    forbidden: list[str]
    timeout: float
    max_attempts: int
    path: Path

    @property
    def is_prose_writer(self) -> bool:
        return "prose-writing" in self.allowed_operations


def load_agent(path: Path) -> AgentDefinition:
    text = path.read_text(encoding="utf-8")
    match = META_BLOCK.search(text)
    if not match:
        raise KoboError(f"JSONメタデータブロックがありません: {path}")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise KoboError(f"JSONメタデータが不正です: {path}: {error}") from error
    missing = REQUIRED - data.keys()
    if missing:
        raise KoboError(f"必須項目がありません: {path}: {', '.join(sorted(missing))}")
    if not AGENT_ID.fullmatch(str(data["agent_id"])):
        raise KoboError(f"agent_idが不正です: {data['agent_id']}")
    for key in ("inputs", "allowed_operations", "forbidden"):
        if not isinstance(data[key], list) or not all(isinstance(item, str) for item in data[key]):
            raise KoboError(f"{key}は文字列配列でなければなりません: {path}")
    if not isinstance(data["timeout"], (int, float)) or data["timeout"] <= 0:
        raise KoboError(f"timeoutが不正です: {path}")
    if not isinstance(data["max_attempts"], int) or data["max_attempts"] < 1:
        raise KoboError(f"max_attemptsが不正です: {path}")
    next_agent = data["next_agent"] or None
    if next_agent is not None and not AGENT_ID.fullmatch(str(next_agent)):
        raise KoboError(f"next_agentが不正です: {path}")
    return AgentDefinition(path=path.resolve(), next_agent=next_agent, **{key: data[key] for key in REQUIRED - {"next_agent"}})


def load_agents(directory: Path) -> dict[str, AgentDefinition]:
    if not directory.is_dir():
        raise KoboError(f"エージェント定義ディレクトリが存在しません: {directory}")
    agents: dict[str, AgentDefinition] = {}
    for path in sorted(directory.glob("*.md")):
        agent = load_agent(path)
        if agent.agent_id in agents:
            raise KoboError(f"agent_idが重複しています: {agent.agent_id}")
        agents[agent.agent_id] = agent
    if not agents:
        raise KoboError("エージェント定義がありません")
    for agent in agents.values():
        if agent.next_agent and agent.next_agent not in agents:
            raise KoboError(f"未定義のnext_agentです: {agent.agent_id} -> {agent.next_agent}")
        if agent.is_prose_writer and agent.adapter != "gemini":
            raise KoboError(f"文章作成工程はGeminiアダプター必須です: {agent.agent_id}")
    return agents


@dataclass(frozen=True)
class Config:
    root: Path
    store: Path
    state_db: Path
    mail_db: Path
    agents_dir: Path
    commands: dict[str, list[str]]
    models: dict[str, str]
    default_timeout: float = 300
    max_attempts: int = 3
    max_hops: int = 10
    escalation_agent: str = "manager"
    first_agent: str | None = None

    @classmethod
    def load(cls, path: Path | None = None, environ: dict[str, str] | None = None) -> "Config":
        env = os.environ if environ is None else environ
        source = (path or Path(env.get("KOBO_CONFIG", "kobo.json"))).resolve()
        base = source.parent
        data = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        def resolved(name: str, default: str) -> Path:
            raw = env.get(f"KOBO_{name.upper()}", data.get(name, default))
            return (base / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        commands = data.get("commands", {"dummy": ["dummy"], "gemini": ["gemini", "--model", "{model}", "--input", "{task_path}", "--output", "{output_path}"]})
        if not isinstance(commands, dict) or any(not isinstance(v, list) or not all(isinstance(x, str) for x in v) for v in commands.values()):
            raise KoboError("commandsは文字列配列の辞書でなければなりません")
        return cls(root=base, store=resolved("store", ".kobo"), state_db=resolved("state_db", ".kobo/state.db"), mail_db=resolved("mail_db", ".kobo/mail.db"), agents_dir=resolved("agents_dir", "agents"), commands=commands, models=data.get("models", {}), default_timeout=float(data.get("default_timeout", 300)), max_attempts=int(data.get("max_attempts", 3)), max_hops=int(data.get("max_hops", 10)), escalation_agent=str(data.get("escalation_agent", "manager")), first_agent=data.get("first_agent"))


class Adapter:
    def command(self, agent: AgentDefinition, refs: dict[str, str]) -> list[str]:
        raise NotImplementedError

    def execute(self, agent: AgentDefinition, refs: dict[str, str], output_path: Path) -> None:
        raise NotImplementedError


class DummyAdapter(Adapter):
    def command(self, agent: AgentDefinition, refs: dict[str, str]) -> list[str]:
        return ["dummy", "--agent-definition", refs["agent_path"], "--task", refs["task_path"], "--mail-db", refs["mail_db"], "--mail-id", refs["mail_id"], "--run-id", refs["run_id"], "--output", refs["output_path"]]

    def execute(self, agent: AgentDefinition, refs: dict[str, str], output_path: Path) -> None:
        atomic_write(output_path, f"# {agent.display_name}のダミー結果\n\nrun_id: `{refs['run_id']}`\nmail_id: `{refs['mail_id']}`\n")


class CliAdapter(Adapter):
    ALLOWED = {"agent_path", "task_path", "mail_db", "mail_id", "run_id", "run_dir", "output_path", "model"}

    def __init__(self, template: list[str]):
        self.template = template

    def command(self, agent: AgentDefinition, refs: dict[str, str]) -> list[str]:
        command = []
        for part in self.template:
            fields = set(re.findall(r"\{([^{}]+)\}", part))
            if not fields <= self.ALLOWED:
                raise KoboError(f"コマンドテンプレートに未知の参照があります: {fields - self.ALLOWED}")
            command.append(part.format(**refs))
        if not command or not command[0].strip():
            raise KoboError("空のCLIコマンドです")
        return command

    def execute(self, agent: AgentDefinition, refs: dict[str, str], output_path: Path) -> None:
        command = self.command(agent, refs)
        try:
            subprocess.run(command, check=True, timeout=agent.timeout, shell=False)
        except subprocess.TimeoutExpired as error:
            raise KoboError(f"CLIがタイムアウトしました: {agent.adapter}") from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise KoboError(f"CLIの実行に失敗しました: {agent.adapter}: {error}") from error
        if not output_path.is_file():
            raise KoboError(f"CLIが出力ファイルを作成しませんでした: {output_path}")


class Orchestrator:
    def __init__(self, config: Config, *, adapters: dict[str, Adapter] | None = None, id_factory: Callable[[], str] | None = None):
        self.config = config
        self.agents = load_agents(config.agents_dir)
        self.adapters = {"dummy": DummyAdapter(), **(adapters or {})}
        self.id_factory = id_factory or self._new_run_id
        self.stop_event = threading.Event()
        config.store.mkdir(parents=True, exist_ok=True)
        config.state_db.parent.mkdir(parents=True, exist_ok=True)
        self.mail = AgentMail(config.mail_db)
        self.initialize()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.config.state_db)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS works(work_id TEXT PRIMARY KEY, title TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, current_agent TEXT, next_agent TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_work ON works(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), agent_id TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL, input_path TEXT, output_path TEXT, error TEXT, started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL, mail_id INTEGER, conversation_id TEXT, parent_mail_id INTEGER, command_json TEXT NOT NULL);
            """)
        self.mail.initialize()
        self.mail.ensure_agent("manager", "工房長")
        self.mail.ensure_agent(self.config.escalation_agent, self.config.escalation_agent)
        for agent in self.agents.values():
            self.mail.ensure_agent(agent.agent_id, agent.display_name)

    @staticmethod
    def _new_run_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"run-{stamp}-{uuid.uuid4().hex[:12]}"

    def create_work(self, work_id: str, title: str, *, activate: bool = True, first_agent: str | None = None) -> dict:
        if not WORK_ID.fullmatch(work_id):
            raise KoboError("work_idは英小文字で始まる3〜63文字の英小文字・数字・ハイフンです")
        first = first_agent or self.config.first_agent or next(iter(self.agents))
        if first not in self.agents:
            raise KoboError(f"未知のエージェントです: {first}")
        timestamp = now()
        with self.connection() as db:
            if activate:
                db.execute("UPDATE works SET active=0, updated_at=? WHERE active=1", (timestamp,))
            db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?)", (work_id, title, int(activate), first, first, "pending", timestamp, timestamp))
        (self.config.store / "works" / work_id / "runs").mkdir(parents=True, exist_ok=False)
        return self.get_work(work_id)

    def get_work(self, work_id: str | None = None) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone() if work_id else db.execute("SELECT * FROM works WHERE active=1").fetchone()
        if not row:
            raise KoboError("作品が見つかりません")
        return dict(row)

    def list_works(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM works ORDER BY created_at, work_id")]

    def history(self, work_id: str | None = None) -> list[dict]:
        target = self.get_work(work_id)["work_id"]
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM runs WHERE work_id=? ORDER BY updated_at, run_id", (target,))]

    def _adapter(self, agent: AgentDefinition) -> Adapter:
        if agent.adapter in self.adapters:
            return self.adapters[agent.adapter]
        template = self.config.commands.get(agent.adapter)
        if not template:
            raise KoboError(f"アダプターが登録されていません: {agent.adapter}")
        return CliAdapter(template)

    def _refs(self, agent: AgentDefinition, run_id: str, run_dir: Path, task_path: Path, output_path: Path, mail_id: int) -> dict[str, str]:
        for path in (agent.path, task_path, run_dir, output_path.parent, self.config.mail_db.parent):
            safe_path(self.config.root, path)
        return {"agent_path": str(agent.path), "task_path": str(task_path), "mail_db": str(self.config.mail_db), "mail_id": str(mail_id), "run_id": run_id, "run_dir": str(run_dir), "output_path": str(output_path), "model": self.config.models.get(agent.agent_id, self.config.models.get(agent.adapter, agent.model))}

    def dry_run(self, work_id: str | None = None) -> dict:
        work = self.get_work(work_id); agent = self.agents[work["next_agent"]]
        run_id = self.id_factory(); run_dir = self.config.store / "works" / work["work_id"] / "runs" / run_id
        refs = {"agent_path": str(agent.path), "task_path": str(run_dir / "task.md"), "mail_db": str(self.config.mail_db), "mail_id": "<mail-id>", "run_id": run_id, "run_dir": str(run_dir), "output_path": str(run_dir / "result.md"), "model": self.config.models.get(agent.agent_id, self.config.models.get(agent.adapter, agent.model))}
        return {"agent_id": agent.agent_id, "adapter": agent.adapter, "command": self._adapter(agent).command(agent, refs), "references": refs}

    def run_step(self, work_id: str | None = None) -> dict:
        work = self.get_work(work_id)
        if work["status"] == "completed" or not work["next_agent"]:
            raise KoboError("完了済み作品に未実行工程はありません")
        if self.stop_event.is_set():
            raise KoboError("停止が要求されています")
        agent = self.agents[work["next_agent"]]
        run_id = self.id_factory()
        if not RUN_ID.fullmatch(run_id):
            raise KoboError(f"run_idが不正です: {run_id}")
        run_dir = self.config.store / "works" / work["work_id"] / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        task_path, output_path = run_dir / "task.md", run_dir / "result.md"
        atomic_write(task_path, f"# 作業依頼\n\n- work_id: `{work['work_id']}`\n- run_id: `{run_id}`\n- agent_id: `{agent.agent_id}`\n- role: {agent.role}\n- output: `{output_path}`\n\n長文資料はこのファイルと、エージェント定義に記載された参照先から読み込むこと。\n")
        mail_id = self.mail.send("manager", agent.agent_id, f"作業依頼 run_id={run_id} task={task_path}", conversation_id=f"work-{work['work_id']}")
        refs = self._refs(agent, run_id, run_dir, task_path, output_path, mail_id)
        adapter = self._adapter(agent); command = adapter.command(agent, refs)
        timestamp = now()
        with self.connection() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, work["work_id"], agent.agent_id, "running", 1, str(task_path), str(output_path), None, timestamp, None, timestamp, mail_id, f"work-{work['work_id']}", None, json.dumps(command, ensure_ascii=False)))
            db.execute("UPDATE works SET status='running', current_agent=?, updated_at=? WHERE work_id=?", (agent.agent_id, timestamp, work["work_id"]))
        error: Exception | None = None
        attempts = min(agent.max_attempts, self.config.max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                adapter.execute(agent, refs, output_path)
                error = None
                break
            except (KoboError, OSError) as caught:
                error = caught
                with self.connection() as db:
                    db.execute("UPDATE runs SET attempt=?, error=?, updated_at=? WHERE run_id=?", (attempt, str(caught), now(), run_id))
                if self.stop_event.is_set():
                    break
        if error is not None:
            timestamp = now()
            with self.connection() as db:
                db.execute("UPDATE runs SET status='failed', attempt=?, error=?, finished_at=?, updated_at=? WHERE run_id=?", (attempts, str(error), timestamp, timestamp, run_id))
                db.execute("UPDATE works SET status='failed', updated_at=? WHERE work_id=?", (timestamp, work["work_id"]))
            raise error
        timestamp = now(); next_agent = agent.next_agent; state = "pending" if next_agent else "completed"
        with self.connection() as db:
            db.execute("UPDATE runs SET status='completed', finished_at=?, updated_at=? WHERE run_id=?", (timestamp, timestamp, run_id))
            db.execute("UPDATE works SET status=?, current_agent=?, next_agent=?, updated_at=? WHERE work_id=?", (state, agent.agent_id, next_agent, timestamp, work["work_id"]))
        return self.history(work["work_id"])[-1]

    def run_until_done(self, work_id: str | None = None) -> list[dict]:
        completed = []
        while True:
            work = self.get_work(work_id)
            if work["status"] == "completed" or not work["next_agent"] or self.stop_event.is_set():
                break
            completed.append(self.run_step(work["work_id"]))
        return completed

    def continue_work(self, work_id: str | None = None) -> list[dict]:
        work = self.get_work(work_id)
        if work["status"] == "running":
            timestamp = now()
            with self.connection() as db:
                db.execute("UPDATE runs SET status='interrupted', error='前回プロセス中断', finished_at=?, updated_at=? WHERE work_id=? AND status='running'", (timestamp, timestamp, work["work_id"]))
                db.execute("UPDATE works SET status='pending', updated_at=? WHERE work_id=?", (timestamp, work["work_id"]))
        self.stop_event.clear()
        return self.run_until_done(work["work_id"])

    def retry(self, run_id: str) -> dict:
        if not RUN_ID.fullmatch(run_id):
            raise KoboError("run_idが不正です")
        with self.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row or row["status"] != "failed":
                raise KoboError("再試行可能な失敗実行がありません")
            db.execute("UPDATE works SET next_agent=?, status='pending', updated_at=? WHERE work_id=?", (row["agent_id"], now(), row["work_id"]))
        return self.run_step(row["work_id"])

    def stop(self) -> None:
        self.stop_event.set()
