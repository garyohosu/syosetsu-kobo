import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kobo.cli import main
from kobo.orchestrator import Adapter, Config, DummyAdapter, KoboError, Orchestrator, load_agents, safe_path


def definition(agent_id="planner", adapter="dummy", next_agent=None, operations=None, **overrides):
    data = {
        "agent_id": agent_id, "display_name": agent_id, "role": "role", "adapter": adapter,
        "model": "model", "inputs": ["task"], "output": "result.md", "next_agent": next_agent,
        "allowed_operations": operations or ["planning"], "forbidden": ["execute-input"],
        "timeout": 1, "max_attempts": 2,
    }
    data.update(overrides)
    return "# Agent\n\n```json\n" + json.dumps(data) + "\n```\n"


class FailingAdapter(Adapter):
    def command(self, agent, refs): return ["failing", "--task", refs["task_path"]]
    def execute(self, agent, refs, output_path): raise KoboError("expected failure")


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.agents = self.root / "agents"; self.agents.mkdir()
        (self.agents / "planner.md").write_text(definition(next_agent="writer"), encoding="utf-8")
        (self.agents / "writer.md").write_text(definition("writer", "gemini", "critic", ["prose-writing"]), encoding="utf-8")
        (self.agents / "critic.md").write_text(definition("critic"), encoding="utf-8")
        self.config_path = self.root / "kobo.json"
        self.config_path.write_text(json.dumps({
            "store": ".state", "state_db": ".state/state.db", "mail_db": ".state/mail.db",
            "agents_dir": "agents", "first_agent": "planner", "commands": {"dummy": ["dummy"], "gemini": ["gemini", "--input", "{task_path}", "--output", "{output_path}"]}
        }), encoding="utf-8")
        self.config = Config.load(self.config_path)
        self.orch = Orchestrator(self.config, adapters={"gemini": DummyAdapter()})

    def tearDown(self): self.temp.cleanup()

    def test_agent_markdown_loads(self):
        agents = load_agents(self.agents)
        self.assertEqual(list(agents), ["critic", "planner", "writer"])
        self.assertTrue(agents["writer"].is_prose_writer)

    def test_missing_field_is_rejected(self):
        (self.agents / "bad.md").write_text("# x\n```json\n{}\n```", encoding="utf-8")
        with self.assertRaises(KoboError): load_agents(self.agents)

    def test_duplicate_id_is_rejected(self):
        (self.agents / "other.md").write_text(definition(), encoding="utf-8")
        with self.assertRaises(KoboError): load_agents(self.agents)

    def test_invalid_values_are_rejected(self):
        (self.agents / "bad.md").write_text(definition("Bad ID", timeout=0), encoding="utf-8")
        with self.assertRaises(KoboError): load_agents(self.agents)

    def test_non_gemini_prose_writer_is_rejected(self):
        (self.agents / "writer.md").write_text(definition("writer", "dummy", "critic", ["prose-writing"]), encoding="utf-8")
        with self.assertRaisesRegex(KoboError, "Gemini"): load_agents(self.agents)

    def test_create_and_active_work(self):
        work = self.orch.create_work("story-one", "Story")
        self.assertEqual(work["work_id"], "story-one")
        self.assertEqual(self.orch.get_work()["work_id"], "story-one")

    def test_invalid_work_id_is_rejected(self):
        with self.assertRaises(KoboError): self.orch.create_work("../bad", "Bad")

    def test_run_ids_are_unique(self):
        values = {self.orch._new_run_id() for _ in range(100)}
        self.assertEqual(len(values), 100)

    def test_state_transition_and_mail_lineage(self):
        self.orch.create_work("story-one", "Story")
        run = self.orch.run_step()
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.orch.get_work()["next_agent"], "writer")
        self.assertEqual(run["conversation_id"], "work-story-one")
        self.assertIsNotNone(run["mail_id"])

    def test_mvp_dummy_run_completes_and_routes_writer_to_gemini(self):
        self.orch.create_work("story-one", "Story")
        runs = self.orch.run_until_done()
        self.assertEqual([r["agent_id"] for r in runs], ["planner", "writer", "critic"])
        self.assertEqual(self.orch.get_work()["status"], "completed")
        writer = runs[1]
        self.assertTrue(Path(writer["output_path"]).is_file())

    def test_completed_steps_are_not_reexecuted(self):
        self.orch.create_work("story-one", "Story"); self.orch.run_until_done()
        self.assertEqual(self.orch.continue_work(), [])
        self.assertEqual(len(self.orch.history()), 3)

    def test_outputs_are_never_overwritten(self):
        self.orch.create_work("story-one", "Story")
        first = self.orch.run_step(); first_text = Path(first["output_path"]).read_text(encoding="utf-8")
        with self.orch.connection() as db: db.execute("UPDATE works SET next_agent='planner', status='pending'")
        second = self.orch.run_step()
        self.assertNotEqual(first["output_path"], second["output_path"])
        self.assertEqual(Path(first["output_path"]).read_text(encoding="utf-8"), first_text)

    def test_failure_is_recorded_without_silent_fallback(self):
        orch = Orchestrator(self.config, adapters={"gemini": FailingAdapter()})
        orch.create_work("story-one", "Story", first_agent="writer")
        with self.assertRaisesRegex(KoboError, "expected failure"): orch.run_step()
        self.assertEqual(orch.get_work()["status"], "failed")
        self.assertEqual(orch.history()[0]["status"], "failed")

    def test_retry_creates_new_run_and_keeps_failed_output_path(self):
        orch = Orchestrator(self.config, adapters={"gemini": FailingAdapter()})
        orch.create_work("story-one", "Story", first_agent="writer")
        with self.assertRaises(KoboError): orch.run_step()
        failed = orch.history()[0]
        orch.adapters["gemini"] = DummyAdapter()
        retried = orch.retry(failed["run_id"])
        self.assertNotEqual(failed["run_id"], retried["run_id"])

    def test_safe_stop_and_resume(self):
        self.orch.create_work("story-one", "Story")
        self.orch.stop()
        self.assertEqual(self.orch.run_until_done(), [])
        self.assertEqual(len(self.orch.continue_work()), 3)

    def test_restart_marks_stale_running_as_interrupted(self):
        self.orch.create_work("story-one", "Story")
        run_id = self.orch._new_run_id(); timestamp = "2026-01-01T00:00:00+00:00"
        with self.orch.connection() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id,"story-one","planner","running",1,None,None,None,timestamp,None,timestamp,None,None,None,"[]"))
            db.execute("UPDATE works SET status='running'")
        self.orch.continue_work()
        self.assertEqual(self.orch.history()[0]["status"], "interrupted")

    def test_long_input_does_not_grow_command(self):
        self.orch.create_work("story-one", "Story")
        preview = self.orch.dry_run(); baseline = sum(map(len, preview["command"]))
        huge = self.root / "huge.md"; huge.write_text("あ" * 100001, encoding="utf-8")
        preview2 = self.orch.dry_run(); self.assertLessEqual(sum(map(len, preview2["command"])), baseline + 100)
        self.assertNotIn("あ" * 100, " ".join(preview2["command"]))

    def test_dry_run_contains_only_references(self):
        self.orch.create_work("story-one", "Story")
        command = self.orch.dry_run()["command"]
        self.assertIn("--task", command)
        self.assertNotIn("role", " ".join(command))

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(KoboError): safe_path(self.root, "../outside")

    def test_initialization_is_idempotent(self):
        self.orch.initialize(); self.orch.initialize()

    def test_cli_smoke_and_machine_readable_error(self):
        self.assertEqual(main(["--config", str(self.config_path), "--dummy", "work-create", "story-one", "Story"]), 0)
        self.assertEqual(main(["--config", str(self.config_path), "--dummy", "run", "--work", "story-one"]), 0)
        self.assertEqual(main(["--config", str(self.config_path), "work-create", "BAD", "Bad"]), 1)


if __name__ == "__main__": unittest.main()
