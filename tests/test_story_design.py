import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from kobo.concept import ConceptManager
from kobo.orchestrator import Config, KoboError, Orchestrator
from kobo.story_design import BIBLE_AUDIT, BIBLE_HEADINGS, PLOT_AUDIT, PLOT_HEADINGS, StoryDesignManager
from kobo.urs import UrsManager
from tests.test_concept import RecordingAgyAdapter
from tests.test_orchestrator import definition


class StoryDesignManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); agents=self.root/"agents"; agents.mkdir()
        specs=(("urs-maker","dummy","planner"),("planner","agy","story-architect"),("concept-reviewer","agy","planner"),("story-architect","agy","continuity-reviewer"),("continuity-reviewer","agy","story-architect"),("plotter","gemini","plot-reviewer"),("plot-reviewer","dummy","plotter"),("scene-planner","gemini","writer"),("writer","gemini","critic"),("critic","dummy",None))
        for agent_id,adapter,next_agent in specs: (agents/f"{agent_id}.md").write_text(definition(agent_id,adapter,next_agent,["test"]),encoding="utf-8")
        config=self.root/"kobo.json"; config.write_text(json.dumps({"store":".state","state_db":".state/state.db","mail_db":".state/mail.db","agents_dir":"agents","first_agent":"urs-maker","commands":{"dummy":["dummy"],"gemini":["gemini"]}}),encoding="utf-8")
        self.orch=Orchestrator(Config.load(config),adapters={"agy":RecordingAgyAdapter()}); self.work="story-design"; self.orch.create_work(self.work,"架空作品",first_agent="urs-maker")
        urs=UrsManager(self.orch); u=urs.start(self.work); urs.answer("work-name","架空作品",work_id=self.work); urs.finalize(self.work,u["session_id"])
        concept=ConceptManager(self.orch,dummy=False); concept.start(self.work); concept.action("select","C01",work_id=self.work); concept.finalize(self.work)
        self.manager=StoryDesignManager(self.orch,dummy=True); self.started=self.manager.start(self.work)

    def tearDown(self): self.temp.cleanup()

    def real_bible(self):
        """実AI相当（adapter=agy）のバイブルセッション。改訂経路の検証に使う。"""
        with self.orch.connection() as db:
            db.execute("UPDATE story_design_sessions SET status='superseded' WHERE work_id=?",(self.work,))
        manager=StoryDesignManager(self.orch,dummy=False)
        started=manager.start(self.work)
        instructions=self.root/"BIBLE_REVISION.v001.md"
        instructions.write_text("## 修正1\n\n改訂指示。黒猫の名前をミルラにする。",encoding="utf-8")
        return manager,started,instructions

    def artifacts(self,session_id,kind):
        with self.orch.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM story_design_artifacts WHERE session_id=? AND kind=? ORDER BY revision",(session_id,kind))]

    def test_bible_revision_only_from_awaiting_approval(self):
        manager,started,instructions=self.real_bible()
        self.assertEqual(started["status"],"bible_awaiting_approval")
        session=started["session_id"]
        manager.approve("bible",self.work,session); manager.finalize_bible(self.work,session)
        with self.assertRaisesRegex(KoboError,"承認待ち"):
            manager.revise_bible(self.work,session,instructions=instructions)

    def test_bible_revision_requires_instruction_file(self):
        manager,started,_=self.real_bible()
        with self.assertRaisesRegex(KoboError,"改訂指示ファイル"):
            manager.revise_bible(self.work,started["session_id"])
        with self.assertRaises(KoboError):
            manager.revise_bible(self.work,started["session_id"],instructions="../outside.md")

    def test_bible_revision_keeps_r001_and_writes_r002(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        first=self.artifacts(session,"bible_draft")[0]
        first_text=Path(first["path"]).read_text(encoding="utf-8")
        first_audit=self.artifacts(session,"bible_audit")[0]
        first_audit_text=Path(first_audit["path"]).read_text(encoding="utf-8")
        result=manager.revise_bible(self.work,session,instructions=instructions)
        drafts=self.artifacts(session,"bible_draft"); audits=self.artifacts(session,"bible_audit")
        self.assertEqual([d["revision"] for d in drafts],[1,2])
        self.assertEqual([a["revision"] for a in audits],[1,2])
        # r001は内容もパスも保持される
        self.assertEqual(Path(first["path"]).read_text(encoding="utf-8"),first_text)
        self.assertEqual(Path(first_audit["path"]).read_text(encoding="utf-8"),first_audit_text)
        self.assertTrue(drafts[1]["path"].endswith("bible_draft.r002.md"))
        self.assertTrue(audits[1]["path"].endswith("bible_audit.r002.md"))
        self.assertIn("改訂済み",Path(drafts[1]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"],"bible_awaiting_approval")

    def test_bible_revision_uses_separate_runs_and_agents(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        manager.revise_bible(self.work,session,instructions=instructions)
        drafts=self.artifacts(session,"bible_draft"); audits=self.artifacts(session,"bible_audit")
        run_ids={d["run_id"] for d in drafts}|{a["run_id"] for a in audits}
        self.assertEqual(len(run_ids),4,"4件すべて別run")
        self.assertEqual(drafts[1]["agent_id"],"story-architect")
        self.assertEqual(audits[1]["agent_id"],"continuity-reviewer")
        self.assertNotEqual(drafts[1]["agent_id"],audits[1]["agent_id"])

    def test_bible_revision_records_history_and_source_path(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        manager.revise_bible(self.work,session,instructions=instructions)
        drafts=self.artifacts(session,"bible_draft"); audits=self.artifacts(session,"bible_audit")
        self.assertEqual(drafts[1]["source_path"],drafts[0]["path"],"r002のsource_pathがr001")
        self.assertEqual(audits[1]["source_path"],drafts[1]["path"],"再監査のsource_pathがr002")
        self.assertEqual(drafts[1]["status"],"revised")
        with self.orch.connection() as db:
            actions=[dict(r) for r in db.execute("SELECT * FROM story_design_actions WHERE session_id=? AND action='revise'",(session,))]
        self.assertEqual(len(actions),1)
        self.assertTrue(actions[0]["instruction_path"].endswith("BIBLE_REVISION.v001.md"))
        self.assertEqual(actions[0]["stage"],"bible")

    def test_bible_revision_passes_instruction_by_path_not_argv(self):
        manager,started,instructions=self.real_bible()
        instructions.write_text("## 修正1\n\n"+"長い改訂指示。"*500,encoding="utf-8")
        manager.revise_bible(self.work,started["session_id"],instructions=instructions)
        with self.orch.connection() as db:
            row=db.execute("SELECT instruction_path FROM story_design_actions WHERE session_id=? AND action='revise'",(started["session_id"],)).fetchone()
        # DBへ残るのは本文ではなくパス
        self.assertLess(len(row[0]),len(instructions.read_text(encoding="utf-8")))
        self.assertTrue(row[0].endswith("BIBLE_REVISION.v001.md"))

    def test_bible_revision_does_not_fall_back_to_dummy(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        self.orch.adapters["agy"].always_fail_bible=True
        with self.assertRaisesRegex(KoboError,"ダミーへ代替しません"):
            manager.revise_bible(self.work,session,instructions=instructions)
        drafts=self.artifacts(session,"bible_draft")
        self.assertEqual([d["revision"] for d in drafts],[1],"不合格のr002は保存されない")
        self.assertFalse((Path(drafts[0]["path"]).parent/"bible_draft.r002.md").exists())
        # 何も確定していないので、承認待ちへ戻り再試行できる
        self.assertEqual(manager.status(self.work,session)["status"],"bible_awaiting_approval")
        self.orch.adapters["agy"].always_fail_bible=False
        retried=manager.revise_bible(self.work,session,instructions=instructions)
        self.assertEqual(retried["status"],"bible_awaiting_approval")
        self.assertEqual([d["revision"] for d in self.artifacts(session,"bible_draft")],[1,2])

    def rebase_inputs(self):
        concept=self.root/"novels"/self.work/"CONCEPT.v002.md"
        concept.parent.mkdir(parents=True,exist_ok=True)
        concept.write_text("# 作品企画（CONCEPT）\n\n- 版: 2\n\n黒猫の名はミルラ。妨害は流通と評判へ分散する。\n",encoding="utf-8")
        rebase=self.root/"BIBLE_REBASE.v001.md"; rebase.write_text("## 再接続\n\n黒猫をミルラへ統一する。",encoding="utf-8")
        return concept,rebase

    def test_bible_rebase_records_both_concepts_and_writes_r003(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        manager.revise_bible(self.work,session,instructions=instructions)
        concept,rebase=self.rebase_inputs()
        before=[dict(r) for r in self.artifacts(session,"bible_draft")]
        before_texts={a["path"]:Path(a["path"]).read_text(encoding="utf-8") for a in before}
        result=manager.rebase_bible(self.work,session,concept=concept,instructions=rebase)
        drafts=self.artifacts(session,"bible_draft"); audits=self.artifacts(session,"bible_audit")
        self.assertEqual([d["revision"] for d in drafts],[1,2,3])
        self.assertEqual([a["revision"] for a in audits],[1,2,3])
        for path,text in before_texts.items():
            self.assertEqual(Path(path).read_text(encoding="utf-8"),text,"r001/r002が変更された")
        self.assertTrue(drafts[2]["path"].endswith("bible_draft.r003.md"))
        self.assertEqual(drafts[2]["status"],"rebased")
        self.assertEqual(drafts[2]["source_path"],drafts[1]["path"],"r003のsourceがr002")
        self.assertEqual(result["status"],"bible_awaiting_approval")
        # セッションの正本CONCEPTがv002へ差し替わる
        self.assertTrue(result["concept_path"].endswith("CONCEPT.v002.md"))
        self.assertEqual(result["concept_version"],2)

    def test_bible_rebase_logs_concept_rebase_with_versions_and_digests(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        manager.revise_bible(self.work,session,instructions=instructions)
        concept,rebase=self.rebase_inputs()
        old_path=Path(manager.status(self.work,session)["concept_path"])
        manager.rebase_bible(self.work,session,concept=concept,instructions=rebase)
        with self.orch.connection() as db:
            rows={r["action"]:r["instruction_path"] for r in db.execute("SELECT action,instruction_path FROM story_design_actions WHERE session_id=?",(session,))}
        self.assertIn("concept_rebase",rows); self.assertIn("rebase",rows)
        note=rows["concept_rebase"]
        self.assertIn(old_path.name,note); self.assertIn("CONCEPT.v002.md",note)
        self.assertIn(hashlib.sha256(old_path.read_bytes()).hexdigest(),note)
        self.assertIn(hashlib.sha256(concept.read_bytes()).hexdigest(),note)
        self.assertTrue(rows["rebase"].endswith("BIBLE_REBASE.v001.md"))

    def test_bible_rebase_requires_awaiting_approval_and_new_concept(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        concept,rebase=self.rebase_inputs()
        current=manager.status(self.work,session)["concept_path"]
        with self.assertRaisesRegex(KoboError,"同じCONCEPT"):
            manager.rebase_bible(self.work,session,concept=current,instructions=rebase)
        with self.assertRaisesRegex(KoboError,"再接続指示ファイル"):
            manager.rebase_bible(self.work,session,concept=concept)
        manager.approve("bible",self.work,session); manager.finalize_bible(self.work,session)
        with self.assertRaisesRegex(KoboError,"承認待ち"):
            manager.rebase_bible(self.work,session,concept=concept,instructions=rebase)

    def test_bible_rebase_uses_separate_runs_and_does_not_approve(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        manager.revise_bible(self.work,session,instructions=instructions)
        concept,rebase=self.rebase_inputs()
        manager.rebase_bible(self.work,session,concept=concept,instructions=rebase)
        drafts=self.artifacts(session,"bible_draft"); audits=self.artifacts(session,"bible_audit")
        runs={a["run_id"] for a in drafts}|{a["run_id"] for a in audits}
        self.assertEqual(len(runs),6,"6件すべて別run")
        self.assertEqual(drafts[2]["agent_id"],"story-architect")
        self.assertEqual(audits[2]["agent_id"],"continuity-reviewer")
        with self.orch.connection() as db:
            docs=db.execute("SELECT COUNT(*) FROM story_design_documents WHERE session_id=?",(session,)).fetchone()[0]
            approvals=db.execute("SELECT COUNT(*) FROM story_design_actions WHERE session_id=? AND action='approve'",(session,)).fetchone()[0]
        self.assertEqual((docs,approvals),(0,0))

    def test_bible_revision_does_not_auto_approve_or_finalize(self):
        manager,started,instructions=self.real_bible()
        session=started["session_id"]
        manager.revise_bible(self.work,session,instructions=instructions)
        self.assertEqual(manager.status(self.work,session)["status"],"bible_awaiting_approval")
        with self.orch.connection() as db:
            docs=db.execute("SELECT COUNT(*) FROM story_design_documents WHERE session_id=?",(session,)).fetchone()[0]
            approvals=db.execute("SELECT COUNT(*) FROM story_design_actions WHERE session_id=? AND action='approve'",(session,)).fetchone()[0]
        self.assertEqual(docs,0,"確定バイブルが作られていない")
        self.assertEqual(approvals,0,"自動承認されていない")

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
