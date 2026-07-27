import json
import tempfile
import unittest
from pathlib import Path

from kobo.orchestrator import Config, DummyAdapter, KoboError, Orchestrator
from kobo.urs import QUESTIONS, UrsManager
from tests.test_orchestrator import definition


class UrsManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); agents=self.root/"agents"; agents.mkdir()
        (agents/"urs-maker.md").write_text(definition("urs-maker",next_agent="planner"),encoding="utf-8")
        (agents/"planner.md").write_text(definition("planner"),encoding="utf-8")
        config_path=self.root/"kobo.json"; config_path.write_text(json.dumps({"store":".state","state_db":".state/state.db","mail_db":".state/mail.db","agents_dir":"agents","first_agent":"urs-maker","commands":{"dummy":["dummy"]}}),encoding="utf-8")
        self.orch=Orchestrator(Config.load(config_path)); self.orch.create_work("urs-story","URS story")
        self.manager=UrsManager(self.orch)

    def tearDown(self): self.temp.cleanup()

    def start(self,known=None): return self.manager.start("urs-story",known)

    def test_start_returns_only_first_question(self):
        result=self.start(); self.assertEqual(result["answered"],0); self.assertEqual(result["next_question"]["question_id"],QUESTIONS[0].question_id)
        self.assertGreaterEqual(len(result["next_question"]["choices"]),2); self.assertTrue(result["next_question"]["allows_free_text"])

    def test_known_information_is_not_reasked_and_remains_provisional(self):
        result=self.start({QUESTIONS[0].question_id:"既知の仮題"})
        self.assertEqual(result["next_question"]["question_id"],QUESTIONS[1].question_id)
        self.assertEqual(result["answers"][0]["status"],"provisional"); self.assertEqual(result["answers"][0]["evidence"],"known")

    def test_one_question_at_a_time_and_free_text(self):
        result=self.start(); q=result["next_question"]["question_id"]
        result=self.manager.answer(q,"自由な回答",work_id="urs-story")
        self.assertEqual(result["answered"],1); self.assertNotEqual(result["next_question"]["question_id"],q)

    def test_defer(self):
        result=self.start(); q=result["next_question"]["question_id"]
        result=self.manager.answer(q,None,status="deferred",work_id="urs-story")
        self.assertEqual(result["answers"][0]["status"],"deferred")

    def test_duplicate_and_wrong_question_are_rejected(self):
        result=self.start(); q=result["next_question"]["question_id"]
        with self.assertRaises(KoboError): self.manager.answer(QUESTIONS[1].question_id,"bad",work_id="urs-story")
        self.manager.answer(q,"ok",work_id="urs-story")
        with self.assertRaises(KoboError): self.manager.answer(q,"again",work_id="urs-story")

    def test_revision_keeps_history(self):
        result=self.start(); q=result["next_question"]["question_id"]
        self.manager.answer(q,"first",work_id="urs-story")
        self.manager.answer(q,"second",work_id="urs-story",revise=True)
        history=self.manager.history(q,"urs-story"); self.assertEqual(len(history),2); self.assertEqual(history[-1]["old_answer"],"first")

    def test_invalid_question_and_cross_work_session_are_rejected(self):
        result=self.start(); session=result["session_id"]
        with self.assertRaises(KoboError): self.manager.answer("unknown","x",work_id="urs-story")
        self.orch.create_work("other-story","Other")
        with self.assertRaises(KoboError): self.manager.status("other-story",session)

    def test_restart_preserves_answers_and_resumes_next(self):
        result=self.start(); q=result["next_question"]["question_id"]; self.manager.answer(q,"saved",work_id="urs-story")
        restarted=UrsManager(Orchestrator(self.orch.config)); status=restarted.status("urs-story")
        self.assertEqual(status["answered"],1); self.assertNotEqual(status["next_question"]["question_id"],q)

    def test_progress(self):
        result=self.start(); self.manager.answer(result["next_question"]["question_id"],"x",work_id="urs-story")
        self.assertEqual(self.manager.status("urs-story")["progress"],round(1/len(QUESTIONS),3))

    def test_preview_is_stable_and_does_not_promote_unanswered(self):
        self.start(); first=self.manager.preview("urs-story"); text1=Path(first["path"]).read_text(encoding="utf-8")
        second=self.manager.preview("urs-story"); text2=Path(second["path"]).read_text(encoding="utf-8")
        self.assertEqual(text1,text2); self.assertIn("状態: `unanswered`",text1); self.assertIn("## 未決事項",text1)

    def test_finalize_versions_without_overwrite_and_hands_to_planner(self):
        result=self.start(); first=self.manager.finalize("urs-story",result["session_id"])
        self.assertEqual(first["version"],1); self.assertTrue(Path(first["path"]).is_file()); self.assertEqual(first["next_agent"],"planner")
        self.assertEqual(self.orch.get_work("urs-story")["next_agent"],"planner")
        with self.assertRaises(KoboError): self.manager.answer(QUESTIONS[0].question_id,"late",work_id="urs-story",session_id=result["session_id"])

    def test_second_session_creates_new_versioned_file(self):
        first=self.start(); path1=self.manager.finalize("urs-story",first["session_id"])["path"]
        second=self.manager.start("urs-story"); path2=self.manager.finalize("urs-story",second["session_id"])["path"]
        self.assertNotEqual(path1,path2)

    def test_mail_and_next_stage_linkage(self):
        result=self.start(); final=self.manager.finalize("urs-story",result["session_id"])
        self.assertIsInstance(final["mail_id"],int)
        with self.orch.mail.connection() as db:
            row=db.execute("SELECT sender_id,recipient_id,conversation_id,parent_message_id FROM messages WHERE id=?",(final["mail_id"],)).fetchone()
        self.assertEqual((row["sender_id"],row["recipient_id"]),("urs-maker","planner")); self.assertIsNotNone(row["parent_message_id"])


if __name__ == "__main__": unittest.main()
