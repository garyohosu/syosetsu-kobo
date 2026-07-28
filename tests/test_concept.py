import json
import re
import tempfile
import unittest
from pathlib import Path

from kobo.concept import EVALUATION_AXES, POSITION_MARKERS, REQUIRED_CANDIDATE_HEADINGS, ConceptManager
from kobo.orchestrator import Config, KoboError, Orchestrator
from kobo.urs import UrsManager
from tests.test_orchestrator import definition

OLD_FORMAT_CANDIDATE = """# 企画候補 C01: 旧仕様の候補

- 候補ID: `C01`
- 参照URS: `dummy.md`（v001）
- 生成アダプター: `dummy`

## 一文で言うと

旧フォーマットの一文コンセプト。

## 主人公

24歳女性。旧フォーマットの主人公欄。

## 物語の始まり

旧フォーマットの始まり。

## 中心となる人物関係

旧フォーマットの人物関係。

## 第一話のあらすじ

""" + ("旧フォーマットのあらすじ。" * 40) + """

## この先を読みたくなる疑問

旧フォーマットの疑問。

## 連載した場合の楽しみ

旧フォーマットの楽しみ。

## 主なリスク

旧フォーマットのリスク。
"""


class ConceptManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); agents=self.root/"agents"; agents.mkdir()
        (agents/"urs-maker.md").write_text(definition("urs-maker",next_agent="planner"),encoding="utf-8")
        (agents/"planner.md").write_text(definition("planner","gemini","story-architect",["concept-planning"]),encoding="utf-8")
        (agents/"concept-reviewer.md").write_text(definition("concept-reviewer",next_agent="planner"),encoding="utf-8")
        (agents/"story-architect.md").write_text(definition("story-architect","gemini","continuity-reviewer",["story-bible-design"]),encoding="utf-8")
        (agents/"continuity-reviewer.md").write_text(definition("continuity-reviewer",next_agent="story-architect"),encoding="utf-8")
        (agents/"writer.md").write_text(definition("writer","gemini",None,["prose-writing"]),encoding="utf-8")
        self.config_path=self.root/"kobo.json"; self.config_path.write_text(json.dumps({"store":".state","state_db":".state/state.db","mail_db":".state/mail.db","agents_dir":"agents","first_agent":"urs-maker","commands":{"dummy":["dummy"],"gemini":["gemini"]}}),encoding="utf-8")
        self.orch=Orchestrator(Config.load(self.config_path)); self.counter=0
        self.work_id=self.make_work(); self.manager=ConceptManager(self.orch,dummy=True); self.result=self.manager.start(self.work_id)

    def tearDown(self): self.temp.cleanup()

    def make_work(self):
        self.counter+=1; work_id=f"concept-story-{self.counter}"; self.orch.create_work(work_id,"架空企画",first_agent="urs-maker")
        urs=UrsManager(self.orch); session=urs.start(work_id); urs.answer("work-name","架空作品",work_id=work_id); urs.finalize(work_id,session["session_id"])
        return work_id

    def test_default_five_candidates_from_versioned_urs(self):
        self.assertEqual((self.result["generated"],self.result["evaluated"]),(5,5)); self.assertEqual(self.result["urs_version"],1)
        candidates=self.manager.candidates(self.work_id); self.assertEqual(len(candidates),5); self.assertEqual(len({c["candidate_id"] for c in candidates}),5)

    def test_candidate_artifacts_have_contract_and_fixed_urs(self):
        for candidate in self.manager.candidates(self.work_id):
            text=Path(candidate["path"]).read_text(encoding="utf-8")
            self.assertIn(self.result["urs_path"],text)
            for heading in REQUIRED_CANDIDATE_HEADINGS: self.assertIn(f"## {heading}",text)

    def test_generation_and_evaluation_runs_are_separate(self):
        candidates=self.manager.candidates(self.work_id); evaluations=self.manager.comparisons(self.work_id)
        self.assertEqual(len(evaluations),5); self.assertTrue({c["generation_run_id"] for c in candidates}.isdisjoint({e["evaluation_run_id"] for e in evaluations}))
        self.assertTrue(all(e["evaluator_id"]=="concept-reviewer" for e in evaluations))

    def test_all_comparison_axes_and_reasons_are_saved(self):
        for evaluation in self.manager.comparisons(self.work_id):
            for axis in EVALUATION_AXES: self.assertIn(f"## {axis}",evaluation["content"])
            self.assertIn("根拠:",evaluation["content"]); self.assertIn("改善案:",evaluation["content"])

    def test_ai_recommendation_does_not_select(self):
        self.assertIsNone(self.result["selected_candidate_id"])
        with self.assertRaisesRegex(KoboError,"利用者"): self.manager.finalize(self.work_id)

    def test_short_candidate_selection_and_finalize(self):
        selected=self.manager.action("select","C02",work_id=self.work_id); self.assertIsNotNone(selected["selected_candidate_id"])
        final=self.manager.finalize(self.work_id); self.assertEqual(final["version"],1); self.assertIn("c02",final["candidate_id"]); self.assertTrue(Path(final["path"]).is_file())
        text=Path(final["path"]).read_text(encoding="utf-8"); self.assertIn(self.result["urs_path"],text); self.assertIn("利用者の明示選択",text)

    def test_hold_and_reject_are_distinct(self):
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=True); manager.start(work); self.assertEqual(manager.action("hold",work_id=work)["status"],"held")
        self.assertEqual(manager.action("reject_all",work_id=work)["status"],"rejected")

    def test_regeneration_preserves_old_candidates(self):
        old={c["path"]:Path(c["path"]).read_text(encoding="utf-8") for c in self.manager.candidates(self.work_id)}
        regenerated=self.manager.action("regenerate",work_id=self.work_id)
        self.assertNotEqual(regenerated["session_id"],self.result["session_id"]); self.assertEqual(regenerated["generated"],5)
        for path,text in old.items():
            self.assertTrue(Path(path).is_file()); self.assertEqual(Path(path).read_text(encoding="utf-8"),text)
        new_paths={c["path"] for c in self.manager.candidates(self.work_id,regenerated["session_id"])}
        self.assertTrue(new_paths.isdisjoint(old))
        with self.orch.connection() as db:
            rows={r[0]:r[1] for r in db.execute("SELECT session_id,status FROM concept_sessions WHERE work_id=?",(self.work_id,))}
        self.assertEqual(rows[self.result["session_id"]],"superseded"); self.assertEqual(rows[regenerated["session_id"]],"awaiting_selection")

    def test_revision_uses_file_and_history_is_append_only(self):
        instructions=self.root/"revision.md"; instructions.write_text("中心関係を強める。"*1000,encoding="utf-8")
        self.manager.action("select","C01",work_id=self.work_id); self.manager.action("revise","C01",instruction_path=instructions,work_id=self.work_id)
        history=self.manager.history(self.work_id); self.assertEqual([x["action"] for x in history],["select","revise"])
        preview=self.manager.preview(self.work_id); self.assertIn("中心関係を強める",Path(preview["path"]).read_text(encoding="utf-8"))

    def test_finalize_is_non_overwriting_and_versioned_by_work(self):
        self.manager.action("select","C01",work_id=self.work_id); first=self.manager.finalize(self.work_id)
        with self.assertRaises(KoboError): self.manager.finalize(self.work_id)
        self.assertTrue(Path(first["path"]).is_file())

    def test_resume_skips_completed_generation_and_evaluation(self):
        before=[(c["candidate_id"],c["generation_run_id"]) for c in self.manager.candidates(self.work_id)]
        self.manager.resume(self.work_id); after=[(c["candidate_id"],c["generation_run_id"]) for c in self.manager.candidates(self.work_id)]
        self.assertEqual(before,after); self.assertEqual(len(self.manager.comparisons(self.work_id)),5)

    def test_editorial_board_contains_all_candidates_without_images_or_network(self):
        board=self.manager.board(self.work_id)
        text=Path(board["path"]).read_text(encoding="utf-8")
        self.assertEqual(board["candidate_count"],5)
        self.assertEqual(text.count('<article class="card">'),5)
        self.assertEqual(text.count("## 第一話のあらすじ"),5)
        self.assertEqual(text.count("面白そう度: 1 2 3 4 5"),5)
        self.assertEqual(text.count("続きを読みたい"),5)
        self.assertEqual(text.count("判定: 選ぶ / 修正候補 / 保留 / 却下"),5)
        self.assertNotIn("<img",text.lower()); self.assertNotIn("<script",text.lower()); self.assertNotIn("http://",text); self.assertNotIn("https://",text)

    def test_nine_required_headings(self):
        self.assertEqual(len(REQUIRED_CANDIDATE_HEADINGS),9)

    def test_logline_and_synopsis_length_bounds(self):
        for candidate in self.manager.candidates(self.work_id):
            text=Path(candidate["path"]).read_text(encoding="utf-8")
            logline=text.split("## ログライン\n\n",1)[1].split("\n\n## ",1)[0].strip()
            synopsis=text.split("## 第一話のあらすじ\n\n",1)[1].split("\n\n## ",1)[0].strip()
            self.assertLessEqual(len(logline),80)
            self.assertTrue(500<=len(synopsis)<=800)

    def test_protagonist_has_gender_age_position_and_desire(self):
        for candidate in self.manager.candidates(self.work_id):
            text=Path(candidate["path"]).read_text(encoding="utf-8")
            protagonist=text.split("## 主人公\n\n",1)[1].split("\n\n## ",1)[0].strip()
            self.assertRegex(protagonist,r"\d+歳|\d+代")
            self.assertTrue(any(token in protagonist for token in ("男性","女性")))
            self.assertTrue(any(token in protagonist for token in POSITION_MARKERS),f"立場がありません: {protagonist}")
            self.assertTrue(any(token in protagonist for token in ("望","願")))

    def test_protagonist_without_position_is_rejected(self):
        base=Path(self.manager.candidates(self.work_id)[0]["path"]).read_text(encoding="utf-8")
        head,_,tail=base.partition("## 主人公\n\n")
        _,_,rest=tail.partition("\n\n## ")
        no_position=head+"## 主人公\n\n32歳女性。祖母の家を整理している最中で、過去に区切りをつけたいと願っている。\n\n## "+rest
        with self.assertRaisesRegex(KoboError,"立場"): self.manager._validate_candidate(no_position)

    def test_reader_profile_section_present(self):
        for candidate in self.manager.candidates(self.work_id):
            text=Path(candidate["path"]).read_text(encoding="utf-8")
            reader=text.split("## 想定読者と読後感\n\n",1)[1].split("\n\n## ",1)[0].strip()
            self.assertTrue(reader)

    def test_central_characters_limited_to_three(self):
        for candidate in self.manager.candidates(self.work_id):
            text=Path(candidate["path"]).read_text(encoding="utf-8")
            central=text.split("## 中心人物\n\n",1)[1].split("\n\n## ",1)[0].strip()
            people=[line for line in central.splitlines() if line.startswith("- ")]
            self.assertTrue(1<=len(people)<=3)
        base=Path(self.manager.candidates(self.work_id)[0]["path"]).read_text(encoding="utf-8")
        head,_,tail=base.partition("## 中心人物\n\n")
        _,_,rest=tail.partition("\n\n## ")
        four_people=head+"## 中心人物\n\n- 一人目: 説明。\n- 二人目: 説明。\n- 三人目: 説明。\n- 四人目: 説明。\n\n## "+rest
        with self.assertRaisesRegex(KoboError,"3人以内"): self.manager._validate_candidate(four_people)

    def test_old_format_candidate_is_rejected_as_new_generation(self):
        with self.assertRaisesRegex(KoboError,"必須項目"): self.manager._validate_candidate(OLD_FORMAT_CANDIDATE)

    def test_mail_lineage_tracks_handoff_start_compare_and_final(self):
        self.manager.action("select","C01",work_id=self.work_id); final=self.manager.finalize(self.work_id)
        with self.orch.mail.connection() as db:
            rows=db.execute("SELECT sender_id,recipient_id,parent_message_id,conversation_id FROM messages WHERE conversation_id=? ORDER BY id",(f"work-{self.work_id}",)).fetchall()
        self.assertGreaterEqual(len(rows),5); self.assertTrue(all(row["conversation_id"]==f"work-{self.work_id}" for row in rows)); self.assertIsNotNone(final["mail_id"]); self.assertTrue(final["next_stage_implemented"])
        self.assertEqual(rows[-1]["recipient_id"],"story-architect")

    def test_invalid_count_unknown_candidate_and_path_traversal(self):
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=True)
        with self.assertRaises(KoboError): manager.start(work,0)
        with self.assertRaises(KoboError): manager.start(work,6)
        manager.start(work)
        with self.assertRaises(KoboError): manager.candidate("C99",work)
        with self.assertRaises(KoboError): manager.action("revise","C01",instruction_path="../outside.md",work_id=work)

    def test_large_urs_and_revision_are_not_candidate_ids_or_argv(self):
        Path(self.result["urs_path"]).write_text("あ"*100001,encoding="utf-8")
        instructions=self.root/"large.md"; instructions.write_text("い"*100001,encoding="utf-8")
        self.manager.action("revise","C01",instruction_path=instructions,work_id=self.work_id)
        history=self.manager.history(self.work_id); self.assertLess(len(history[-1]["instruction_path"]),len(instructions.read_text(encoding="utf-8")))

    def test_gemini_failure_is_recorded_without_dummy_fallback(self):
        work=self.make_work(); bad_path=self.root/"bad.json"; data=json.loads(self.config_path.read_text(encoding="utf-8")); data["commands"]["gemini"]=["definitely-missing-gemini"] ; bad_path.write_text(json.dumps(data),encoding="utf-8")
        orch=Orchestrator(Config.load(bad_path)); manager=ConceptManager(orch,dummy=False)
        with self.assertRaises(Exception): manager.start(work)
        self.assertEqual(manager.status(work)["status"],"failed"); self.assertEqual(manager.status(work)["adapter"],"gemini")


if __name__ == "__main__": unittest.main()
