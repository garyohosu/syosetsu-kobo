import json
import tempfile
import unittest
from pathlib import Path

from kobo.concept import ConceptManager
from kobo.manuscript import AUDIT_AXES, CHAPTER_HEADINGS, SCENE_HEADINGS, ManuscriptManager
from kobo.orchestrator import Config, KoboError, Orchestrator
from kobo.story_design import StoryDesignManager
from kobo.urs import UrsManager
from tests.test_orchestrator import definition


class ManuscriptManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); agents=self.root/"agents"; agents.mkdir()
        specs=(("urs-maker","dummy","planner",["test"]),("planner","gemini","story-architect",["test"]),("concept-reviewer","dummy","planner",["test"]),("story-architect","gemini","continuity-reviewer",["test"]),("continuity-reviewer","dummy","story-architect",["test"]),("plotter","gemini","plot-reviewer",["test"]),("plot-reviewer","dummy","plotter",["test"]),("scene-planner","gemini","writer",["test"]),("writer","gemini","prose-reviewer",["prose-writing"]),("prose-reviewer","gemini","writer",["prose-review"]),("critic","dummy",None,["review"]))
        for agent_id,adapter,next_agent,ops in specs:(agents/f"{agent_id}.md").write_text(definition(agent_id,adapter,next_agent,ops),encoding="utf-8")
        config=self.root/"kobo.json"; config.write_text(json.dumps({"store":".state","state_db":".state/state.db","mail_db":".state/mail.db","agents_dir":"agents","first_agent":"urs-maker","commands":{"dummy":["dummy"],"gemini":["gemini"]}}),encoding="utf-8")
        self.orch=Orchestrator(Config.load(config)); self.work="manuscript-story"; self.orch.create_work(self.work,"架空作品",first_agent="urs-maker")
        urs=UrsManager(self.orch); u=urs.start(self.work); urs.answer("work-name","架空作品",work_id=self.work); urs.finalize(self.work,u["session_id"])
        concept=ConceptManager(self.orch,dummy=True); concept.start(self.work); concept.action("select","C01",work_id=self.work); concept.finalize(self.work)
        story=StoryDesignManager(self.orch,dummy=True); s=story.start(self.work); story.approve("bible",self.work,s["session_id"]); story.finalize_bible(self.work,s["session_id"]); story.start_plot(self.work,s["session_id"]); story.approve("plot",self.work,s["session_id"]); story.finalize_plot(self.work,s["session_id"])
        self.manager=ManuscriptManager(self.orch,dummy=True); self.started=self.manager.start(1,"旅立ち",self.work)

    def tearDown(self):self.temp.cleanup()

    def test_complete_pipeline_keeps_all_artifacts_separate(self):
        self.assertEqual(self.started["status"],"awaiting_approval"); kinds=[a["kind"] for a in self.started["artifacts"]]
        self.assertEqual(kinds,["chapter_design","scene_design","draft","audit","revision","reaudit"]); self.assertEqual(len({a["run_id"] for a in self.started["artifacts"]}),6)
        for heading in CHAPTER_HEADINGS:self.assertIn(f"## {heading}",self.manager.show("chapter_design",self.work)["content"])
        for heading in SCENE_HEADINGS:self.assertIn(f"## {heading}",self.manager.show("scene_design",self.work)["content"])
        for axis in AUDIT_AXES:self.assertIn(f"## {axis}",self.manager.show("audit",self.work)["content"])

    def test_writer_is_gemini_role_and_dummy_is_disclosed(self):
        self.assertEqual(self.orch.agents["writer"].adapter,"gemini"); self.assertIn("ダミー本文",self.manager.show("draft",self.work)["content"]); self.assertEqual(self.manager.show("audit",self.work)["agent_id"],"prose-reviewer")

    def test_approval_and_non_overwriting_final(self):
        with self.assertRaises(KoboError):self.manager.finalize(self.work)
        self.manager.approve(self.work); final=self.manager.finalize(self.work); self.assertTrue(Path(final["path"]).is_file()); self.assertEqual(final["chapter_number"],1)
        with self.assertRaises(KoboError):self.manager.finalize(self.work)

    def test_resume_is_idempotent(self):
        before=[a["run_id"] for a in self.started["artifacts"]]; after=self.manager.resume(self.work); self.assertEqual(before,[a["run_id"] for a in after["artifacts"]])

    def test_requires_final_plot_and_unique_chapter(self):
        with self.assertRaises(KoboError):self.manager.start(1,"重複",self.work)
        with self.assertRaises(KoboError):self.manager.start(0,"不正",self.work)

    def test_gemini_failure_is_recorded_without_dummy_fallback(self):
        manager=ManuscriptManager(self.orch,dummy=False)
        with self.assertRaises(Exception):manager.start(2,"失敗確認",self.work)
        self.assertEqual(manager.status(self.work)["status"],"failed")

    def test_mail_lineage_covers_design_write_audit_revision(self):
        with self.orch.mail.connection() as db:rows=db.execute("SELECT sender_id,recipient_id,parent_message_id FROM messages WHERE conversation_id=? ORDER BY id",(f"work-{self.work}",)).fetchall()
        tail=[(r["sender_id"],r["recipient_id"]) for r in rows[-5:]]; self.assertEqual(tail,[("scene-planner","writer"),("writer","prose-reviewer"),("prose-reviewer","writer"),("writer","prose-reviewer"),("prose-reviewer","writer")]); self.assertTrue(all(r["parent_message_id"] for r in rows[-5:]))


if __name__=="__main__":unittest.main()
