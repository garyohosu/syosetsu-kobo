import json
import shutil
import uuid
import unittest
from pathlib import Path

from kobo.canon import AUDIT_AXES, KINDS, CanonManager
from kobo.orchestrator import Config, KoboError, Orchestrator, now
from tests.test_orchestrator import definition


class CanonManagerTest(unittest.TestCase):
    def setUp(self):
        root = Path.cwd() / (".test-canon-" + uuid.uuid4().hex); root.mkdir(); self.temp = root
        agents = root / "agents"; agents.mkdir()
        for agent_id, adapter, next_agent in (("canon-updater", "gemini", "canon-auditor"), ("canon-auditor", "gemini", "scene-planner"), ("scene-planner", "gemini", None)):
            (agents / f"{agent_id}.md").write_text(definition(agent_id, adapter, next_agent, []), encoding="utf-8")
        config = root / "kobo.json"
        config.write_text(json.dumps({"store": ".state", "state_db": ".state/state.db", "mail_db": ".state/mail.db", "agents_dir": "agents", "commands": {"dummy": ["dummy"], "gemini": ["gemini"]}}), encoding="utf-8")
        self.orch = Orchestrator(Config.load(config)); self.work = "canon-story"; self.orch.create_work(self.work, "正史テスト", first_agent="canon-updater")
        files = {}
        for name in ("chapter", "bible", "plot"):
            path = self.orch.config.store / f"{name}.md"; path.write_text(f"# {name}\n確定資料\n", encoding="utf-8"); files[name] = str(path)
        with self.orch.connection() as db:
            db.executescript("""
                CREATE TABLE manuscript_sessions(session_id TEXT PRIMARY KEY, work_id TEXT, chapter_number INTEGER, chapter_title TEXT, status TEXT, latest_mail_id INTEGER);
                CREATE TABLE manuscript_documents(id INTEGER PRIMARY KEY, session_id TEXT, version INTEGER, path TEXT);
                CREATE TABLE story_design_sessions(session_id TEXT PRIMARY KEY, work_id TEXT, status TEXT);
                CREATE TABLE story_design_documents(id INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, version INTEGER, path TEXT);
            """)
            db.execute("INSERT INTO manuscript_sessions VALUES(?,?,?,?,?,NULL)", ("m1", self.work, 1, "第一章", "completed"))
            db.execute("INSERT INTO manuscript_documents VALUES(?,?,?,?)", (1, "m1", 1, files["chapter"]))
            db.execute("INSERT INTO story_design_sessions VALUES(?,?,?)", ("s1", self.work, "completed"))
            db.execute("INSERT INTO story_design_documents VALUES(?,?,?,?,?)", (1, "s1", "bible", 1, files["bible"]))
            db.execute("INSERT INTO story_design_documents VALUES(?,?,?,?,?)", (2, "s1", "plot", 1, files["plot"]))
        self.manager = CanonManager(self.orch, dummy=True)

    def tearDown(self): shutil.rmtree(self.temp, ignore_errors=True)

    def test_dummy_generate_approve_finalize_and_handoff(self):
        started = self.manager.start(1, self.work)
        self.assertEqual(started["status"], "awaiting_approval")
        self.assertEqual(sum(a["kind"] in KINDS for a in started["artifacts"]), 5)
        for axis in AUDIT_AXES: self.assertIn(f"## {axis}", self.manager.show("audit", self.work)["content"])
        with self.assertRaises(KoboError): self.manager.finalize(self.work)
        self.manager.approve(self.work); result = self.manager.finalize(self.work)
        self.assertEqual(result["next_agent"], "scene-planner"); self.assertEqual(len(result["paths"]), 5)
        self.assertTrue(all(Path(path).is_file() for path in result["paths"]))
        with self.assertRaises(KoboError): self.manager.finalize(self.work, started["session_id"])
        with self.orch.mail.connection() as db: row = db.execute("SELECT body FROM messages WHERE id=?", (result["mail_id"],)).fetchone()
        self.assertIn("foreshadowing_ledger_path", row["body"])

    def test_resume_and_reject_are_append_only(self):
        started = self.manager.start(1, self.work); ids = [a["run_id"] for a in started["artifacts"]]
        self.assertEqual(ids, [a["run_id"] for a in self.manager.resume(self.work)["artifacts"]])
        self.manager.reject("人物関係を再確認", work_id=self.work, session_id=started["session_id"])
        resumed = self.manager.resume(self.work, started["session_id"])
        self.assertEqual(resumed["status"], "awaiting_approval"); self.assertEqual(len(resumed["actions"]), 1); self.assertEqual(len(resumed["artifacts"]), 12)

if __name__ == "__main__": unittest.main()
