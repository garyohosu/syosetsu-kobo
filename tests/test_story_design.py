import json
import tempfile
import unittest
from pathlib import Path

from kobo.concept import ConceptManager
from kobo.orchestrator import Config, KoboError, Orchestrator
from kobo.story_design import BIBLE_AUDIT, BIBLE_HEADINGS, PLOT_AUDIT, PLOT_HEADINGS, StoryDesignManager
from kobo.urs import UrsManager
from tests.test_orchestrator import definition


class StoryDesignManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); agents=self.root/"agents"; agents.mkdir()
        specs=(("urs-maker","dummy","planner"),("planner","gemini","story-architect"),("concept-reviewer","dummy","planner"),("story-architect","gemini","continuity-reviewer"),("continuity-reviewer","dummy","story-architect"),("plotter","gemini","plot-reviewer"),("plot-reviewer","dummy","plotter"),("scene-planner","gemini","writer"),("writer","gemini","critic"),("critic","dummy",None))
        for agent_id,adapter,next_agent in specs: (agents/f"{agent_id}.md").write_text(definition(agent_id,adapter,next_agent,["test"]),encoding="utf-8")
        config=self.root/"kobo.json"; config.write_text(json.dumps({"store":".state","state_db":".state/state.db","mail_db":".state/mail.db","agents_dir":"agents","first_agent":"urs-maker","commands":{"dummy":["dummy"],"gemini":["gemini"]}}),encoding="utf-8")
        self.orch=Orchestrator(Config.load(config)); self.work="story-design"; self.orch.create_work(self.work,"架空作品",first_agent="urs-maker")
        urs=UrsManager(self.orch); u=urs.start(self.work); urs.answer("work-name","架空作品",work_id=self.work); urs.finalize(self.work,u["session_id"])
        concept=ConceptManager(self.orch,dummy=True); concept.start(self.work); concept.action("select","C01",work_id=self.work); concept.finalize(self.work)
        self.manager=StoryDesignManager(self.orch,dummy=True); self.started=self.manager.start(self.work)

    def tearDown(self): self.temp.cleanup()

    def test_bible_generation_audit_and_explicit_approval(self):
        self.assertEqual(self.started["status"],"bible_awaiting_approval")
        for heading in BIBLE_HEADINGS: self.assertIn(f"## {heading}",self.manager.show("bible_draft",self.work)["content"])
        for axis in BIBLE_AUDIT: self.assertIn(f"## {axis}",self.manager.show("bible_audit",self.work)["content"])
        with self.assertRaises(KoboError): self.manager.finalize_bible(self.work)
        self.manager.approve("bible",self.work); final=self.manager.finalize_bible(self.work)
        self.assertTrue(Path(final["path"]).is_file()); self.assertEqual(final["version"],1)

    def test_full_plot_flow_and_writer_boundary(self):
        self.manager.approve("bible",self.work); self.manager.finalize_bible(self.work); status=self.manager.start_plot(self.work)
        self.assertEqual(status["status"],"plot_awaiting_approval")
        for heading in PLOT_HEADINGS: self.assertIn(f"## {heading}",self.manager.show("plot_draft",self.work)["content"])
        for axis in PLOT_AUDIT: self.assertIn(f"## {axis}",self.manager.show("plot_audit",self.work)["content"])
        self.manager.approve("plot",self.work); final=self.manager.finalize_plot(self.work)
        self.assertTrue(Path(final["path"]).is_file()); self.assertEqual(final["next_agent"],"scene-planner"); self.assertTrue(final["next_stage_implemented"])

    def test_resume_is_idempotent_and_fixed_sources_are_recorded(self):
        before=[a["run_id"] for a in self.started["artifacts"]]; after=self.manager.resume(self.work)
        self.assertEqual(before,[a["run_id"] for a in after["artifacts"]])
        draft=self.manager.show("bible_draft",self.work)["content"]; self.assertIn(self.started["concept_path"],draft)

    def test_plot_requires_final_bible_and_double_finalization_is_rejected(self):
        with self.assertRaises(KoboError): self.manager.start_plot(self.work)
        self.manager.approve("bible",self.work); self.manager.finalize_bible(self.work)
        with self.assertRaises(KoboError): self.manager.finalize_bible(self.work)

    def test_mail_lineage_reaches_story_architect_plotter_and_writer(self):
        self.manager.approve("bible",self.work); self.manager.finalize_bible(self.work); self.manager.start_plot(self.work); self.manager.approve("plot",self.work); self.manager.finalize_plot(self.work)
        with self.orch.mail.connection() as db: rows=db.execute("SELECT recipient_id,parent_message_id FROM messages WHERE conversation_id=? ORDER BY id",(f"work-{self.work}",)).fetchall()
        recipients=[r["recipient_id"] for r in rows]; self.assertIn("story-architect",recipients); self.assertIn("continuity-reviewer",recipients); self.assertIn("plotter",recipients); self.assertIn("plot-reviewer",recipients); self.assertEqual(recipients[-1],"scene-planner"); self.assertIsNotNone(rows[-1]["parent_message_id"])


if __name__ == "__main__": unittest.main()
