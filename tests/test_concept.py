import json
import re
import tempfile
import unittest
from pathlib import Path

from kobo.concept import (CANDIDATE_DIRECTIONS, EVALUATION_AXES, POSITION_MARKERS, QUALITY_ERROR,
                          REQUIRED_CANDIDATE_HEADINGS, ConceptManager, latest_reader_profile, text_quality_problems)
from kobo.orchestrator import Adapter, Config, KoboError, Orchestrator, atomic_write
from kobo.urs import UrsManager
from tests.test_orchestrator import definition

SYNOPSIS_UNIT = ("主人公は自分の望みを口にできないまま、相手との距離を測っている。しかし予期しない出来事が起き、"
                 "主人公は逃げるか向き合うかの選択を迫られる。中盤で相手の本当の事情が明らかになり、関係の意味が変わる。"
                 "主人公は自分から一歩踏み出し、その場の問題には小さな決着がつく。最後に、まだ解けていない問いが一つ残る。")


def candidate_markdown(ordinal, title=None):
    """検証を通過する最小限の企画候補。実AI応答のスタブとして使う。"""
    values = {
        "ログライン": f"会社員が相手との距離に悩み、自分から一歩踏み出すことを選ぶ話。案{ordinal}。",
        "一行コンセプト": f"言えなかった一言が関係を変える、案{ordinal}の物語。",
        "想定読者と読後感": "20〜30代向け。読後に小さな前向きさが残る。",
        "主人公": f"2{ordinal}歳女性。会社員。相手との関係を進めたいと望んでいる。臆病で決定的な言葉を避ける。『まだ言えない』とつぶやく。",
        "中心人物": "- 主人公: 本音を言えない会社員。\n- 相手: 事情を隠している同僚。\n- 友人: 主人公の背中を押す。",
        "物語の始まり": "帰り際、相手から思いがけない相談を持ちかけられる。",
        "第一話のあらすじ": SYNOPSIS_UNIT * 4,
        "連載の推進力": "相手の隠している事情と、主人公が本音を言えるようになる変化が次話を動かす。",
        "この企画の弱点": "すれ違いが長引くと中盤が停滞しやすい。",
    }
    body = "\n\n".join(f"## {heading}\n\n{values[heading]}" for heading in REQUIRED_CANDIDATE_HEADINGS)
    return f"# 企画候補 C{ordinal:02d}: {title or f'案{ordinal}の仮題'}\n\n{body}\n"


def evaluation_markdown(score=3):
    parts = []
    for axis in EVALUATION_AXES:
        parts.append(f"## {axis}\n\n根拠: 候補の記述を確認した。\n長所: 明確である。\n弱点: 検証が要る。\n改善案: 具体化する。\n5段階評価: {score}")
    return "\n\n".join(parts) + "\n\n## 総評\n\n強みと不安を要約した。\n"


def bible_markdown(revised=False):
    from kobo.story_design import BIBLE_HEADINGS
    mark = "改訂済み。ギルベルトは治療だと理解した上で処分を保留する。使い魔の名はミルラ。" if revised else "初版。使い魔の名はルナ。"
    return "# ストーリーバイブル草案\n\n" + "\n\n".join(
        f"## {h}\n\n{h}を確定CONCEPTへ整合する形で定義する。{mark}" for h in BIBLE_HEADINGS) + "\n"


def bible_audit_markdown(verdict="ok"):
    from kobo.story_design import BIBLE_AUDIT
    return "\n\n".join(
        f"## {a}\n\n根拠: 草案の記述を確認した。\n長所: 整合している。\n弱点: 追跡が要る。\n改善案: 明示する。\n判定: {verdict}"
        for a in BIBLE_AUDIT) + "\n\n## 監査結論\n\n確定可否は利用者承認に委ねる。\n"


class RecordingAgyAdapter(Adapter):
    """agyの代わりに決定論的な応答を返し、渡されたプロンプトを記録する。"""

    def __init__(self):
        self.prompts = []
        self.candidate_failures = 0
        self.always_fail_candidates = False
        self.contaminate_candidates = 0
        self.always_contaminate = False
        self.always_fail_bible = False

    def command(self, agent, refs): return ["agy", "--print", "<prompt>"]

    def execute(self, agent, refs, output_path):
        prompt = refs["prompt"]
        self.prompts.append((agent.agent_id, prompt))
        if agent.agent_id == "story-architect":
            if self.always_fail_bible:
                atomic_write(output_path, "# 見出しの足りないバイブル\n\n## 作品の核\n\n不完全。\n"); return
            atomic_write(output_path, bible_markdown(revised="改訂指示" in prompt)); return
        if agent.agent_id == "continuity-reviewer":
            atomic_write(output_path, bible_audit_markdown()); return
        if agent.agent_id == "concept-reviewer":
            if "比較総括" in prompt:
                atomic_write(output_path, "# 比較総括\n\n## 各案の違い\n\n案ごとに読書体験が異なる。\n\n## 補助順位\n\n根拠付きの順位。\n\n## 利用者へ確認したい点\n\n判断を仰ぐ。\n"); return
            # 候補番号が大きいほど高得点にし、順位が候補番号順にならないことを検証できるようにする。
            atomic_write(output_path, evaluation_markdown(int(re.search(r"企画候補 C(\d+)", prompt).group(1)))); return
        if "修復対象" in prompt:  # 文字混入の修復要求。内容を変えずに直した体で返す。
            ordinal = int(re.search(r"企画候補 C(\d+)", prompt).group(1))
            fixed = candidate_markdown(ordinal)
            if self.always_contaminate: fixed = fixed.replace("その場の問題には", "その場 of 問題には", 1)
            atomic_write(output_path, fixed); return
        if "改訂対象の既存案" in prompt:  # 実AIによる候補改訂
            ordinal = int(re.search(r"企画候補 C(\d+)", prompt).group(1))
            atomic_write(output_path, candidate_markdown(ordinal, title="改訂後の仮題")); return
        ordinal = int(re.search(r"候補C(\d+)", prompt).group(1))
        if self.always_fail_candidates or self.candidate_failures > 0:
            if not self.always_fail_candidates: self.candidate_failures -= 1
            atomic_write(output_path, "# 企画候補 C01: 書式不正\n\n## ログライン\n\n見出しが足りない。\n"); return
        if self.always_contaminate or self.contaminate_candidates > 0:
            if not self.always_contaminate: self.contaminate_candidates -= 1
            atomic_write(output_path, candidate_markdown(ordinal).replace("その場の問題には", "その場 of 問題には", 1)); return
        atomic_write(output_path, candidate_markdown(ordinal))

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
        (agents/"planner.md").write_text(definition("planner","agy","story-architect",["concept-planning"],max_attempts=3),encoding="utf-8")
        (agents/"concept-reviewer.md").write_text(definition("concept-reviewer","agy",next_agent="planner",max_attempts=3),encoding="utf-8")
        (agents/"story-architect.md").write_text(definition("story-architect","gemini","continuity-reviewer",["story-bible-design"]),encoding="utf-8")
        (agents/"continuity-reviewer.md").write_text(definition("continuity-reviewer",next_agent="story-architect"),encoding="utf-8")
        (agents/"writer.md").write_text(definition("writer","gemini",None,["prose-writing"]),encoding="utf-8")
        self.config_path=self.root/"kobo.json"; self.config_path.write_text(json.dumps({"store":".state","state_db":".state/state.db","mail_db":".state/mail.db","agents_dir":"agents","first_agent":"urs-maker","commands":{"dummy":["dummy"],"gemini":["gemini"]}}),encoding="utf-8")
        self.agy=RecordingAgyAdapter()
        self.orch=Orchestrator(Config.load(self.config_path),adapters={"agy":self.agy}); self.counter=0
        self.work_id=self.make_work(); self.manager=ConceptManager(self.orch,dummy=True); self.result=self.manager.start(self.work_id)

    def tearDown(self): self.temp.cleanup()

    def make_work(self):
        self.counter+=1; work_id=f"concept-story-{self.counter}"; self.orch.create_work(work_id,"架空企画",first_agent="urs-maker")
        urs=UrsManager(self.orch); session=urs.start(work_id); urs.answer("work-name","架空作品",work_id=work_id); urs.finalize(work_id,session["session_id"])
        return work_id

    def real_session(self):
        """adapter=agyの実AI相当セッション。選択・確定を伴う検証に使う。"""
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=False)
        return work,manager,manager.start(work)

    def test_default_five_candidates_from_versioned_urs(self):
        self.assertEqual((self.result["generated"],self.result["evaluated"]),(5,5)); self.assertEqual(self.result["urs_version"],1)
        candidates=self.manager.candidates(self.work_id); self.assertEqual(len(candidates),5); self.assertEqual(len({c["candidate_id"] for c in candidates}),5)

    def test_candidate_artifacts_have_contract_and_fixed_urs(self):
        relative=Path(self.result["urs_path"]).name
        for candidate in self.manager.candidates(self.work_id):
            text=Path(candidate["path"]).read_text(encoding="utf-8")
            self.assertIn(relative,text); self.assertNotIn(str(self.root),text)
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
        work,manager,result=self.real_session()
        self.assertIsNone(result["selected_candidate_id"])
        with self.assertRaisesRegex(KoboError,"利用者"): manager.finalize(work)

    def test_short_candidate_selection_and_finalize(self):
        work,manager,result=self.real_session()
        selected=manager.action("select","C02",work_id=work); self.assertIsNotNone(selected["selected_candidate_id"])
        final=manager.finalize(work); self.assertEqual(final["version"],1); self.assertIn("c02",final["candidate_id"]); self.assertTrue(Path(final["path"]).is_file())
        text=Path(final["path"]).read_text(encoding="utf-8")
        self.assertIn(Path(result["urs_path"]).name,text); self.assertNotIn(str(self.root),text)
        self.assertIn("利用者の明示選択",text)

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
        work,manager,_=self.real_session()
        instructions=self.root/"revision.md"; instructions.write_text("中心関係を強める。"*1000,encoding="utf-8")
        manager.action("select","C01",work_id=work); manager.action("revise","C01",instruction_path=instructions,work_id=work)
        history=manager.history(work); self.assertEqual([x["action"] for x in history],["select","revise"])
        preview=manager.preview(work); self.assertIn("中心関係を強める",Path(preview["path"]).read_text(encoding="utf-8"))

    def test_finalize_is_non_overwriting_and_versioned_by_work(self):
        work,manager,_=self.real_session()
        manager.action("select","C01",work_id=work); first=manager.finalize(work)
        with self.assertRaises(KoboError): manager.finalize(work)
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

    def _with_protagonist(self,text):
        base=Path(self.manager.candidates(self.work_id)[0]["path"]).read_text(encoding="utf-8")
        head,_,tail=base.partition("## 主人公\n\n")
        _,_,rest=tail.partition("\n\n## ")
        return head+"## 主人公\n\n"+text+"\n\n## "+rest

    def test_protagonist_without_position_is_rejected(self):
        no_position=self._with_protagonist("32歳女性。祖母の家を整理している最中で、過去に区切りをつけたいと願っている。")
        with self.assertRaisesRegex(KoboError,"立場"): self.manager._validate_candidate(no_position)

    def test_explicitly_labelled_position_is_accepted(self):
        """職業語尾に当たらない立場でも、`立場:`と明示されていれば受理する。"""
        labelled=self._with_protagonist("- 性別: 女性\n- 年齢: 27歳\n- 立場: 街のフリーランス遺品整理人\n- 願望: 遺品を正しく分類することを望む。")
        self.manager._validate_candidate(labelled)

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
        work,manager,_=self.real_session()
        manager.action("select","C01",work_id=work); final=manager.finalize(work)
        with self.orch.mail.connection() as db:
            rows=db.execute("SELECT sender_id,recipient_id,parent_message_id,conversation_id FROM messages WHERE conversation_id=? ORDER BY id",(f"work-{work}",)).fetchall()
        self.assertGreaterEqual(len(rows),3); self.assertTrue(all(row["conversation_id"]==f"work-{work}" for row in rows)); self.assertIsNotNone(final["mail_id"]); self.assertTrue(final["next_stage_implemented"])
        self.assertEqual(rows[-1]["recipient_id"],"story-architect")

    def test_invalid_count_unknown_candidate_and_path_traversal(self):
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=False)
        with self.assertRaises(KoboError): manager.start(work,0)
        with self.assertRaises(KoboError): manager.start(work,6)
        manager.start(work)
        with self.assertRaises(KoboError): manager.candidate("C99",work)
        with self.assertRaises(KoboError): manager.action("revise","C01",instruction_path="../outside.md",work_id=work)

    def test_large_urs_and_revision_are_not_candidate_ids_or_argv(self):
        work,manager,result=self.real_session()
        Path(result["urs_path"]).write_text("あ"*100001,encoding="utf-8")
        instructions=self.root/"large.md"; instructions.write_text("い"*100001,encoding="utf-8")
        manager.action("revise","C01",instruction_path=instructions,work_id=work)
        history=manager.history(work); self.assertLess(len(history[-1]["instruction_path"]),len(instructions.read_text(encoding="utf-8")))

    def test_non_dummy_session_records_agy_adapter(self):
        _,_,result=self.real_session()
        self.assertEqual(result["adapter"],"agy")
        self.assertEqual(self.manager.status(self.work_id)["adapter"],"dummy")

    def test_dummy_session_rejects_select_revise_preview_finalize(self):
        instructions=self.root/"revision.md"; instructions.write_text("修正指示。",encoding="utf-8")
        for call in (lambda: self.manager.action("select","C01",work_id=self.work_id),
                     lambda: self.manager.action("revise","C01",instruction_path=instructions,work_id=self.work_id),
                     lambda: self.manager.preview(self.work_id),
                     lambda: self.manager.finalize(self.work_id),
                     lambda: self.manager.publish(self.work_id)):
            with self.assertRaisesRegex(KoboError,"ダミー"): call()

    def test_dummy_session_allows_inspection_hold_reject_and_regenerate(self):
        self.assertEqual(self.manager.status(self.work_id)["status"],"awaiting_selection")
        self.assertEqual(len(self.manager.candidates(self.work_id)),5)
        self.assertEqual(len(self.manager.comparisons(self.work_id)),5)
        self.assertEqual(self.manager.board(self.work_id)["candidate_count"],5)
        self.assertEqual(self.manager.action("hold",work_id=self.work_id)["status"],"held")
        self.assertEqual(self.manager.action("reject_all",work_id=self.work_id)["status"],"rejected")
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=True); manager.start(work)
        self.assertEqual(manager.action("regenerate",work_id=work)["generated"],5)

    def test_real_generation_calls_planner_adapter_with_direction_and_prior_summaries(self):
        self.real_session()
        planner=[prompt for agent_id,prompt in self.agy.prompts if agent_id=="planner"]
        self.assertEqual(len(planner),5)
        for ordinal,prompt in enumerate(planner,1):
            self.assertIn(CANDIDATE_DIRECTIONS[ordinal-1],prompt)
        self.assertNotIn("既に採用済みの候補",planner[0])
        for prompt in planner[1:]:
            self.assertIn("既に採用済みの候補",prompt); self.assertIn("C01 ログライン:",prompt)

    def test_real_evaluation_calls_reviewer_adapter_and_is_not_boilerplate(self):
        work,manager,_=self.real_session()
        reviewer=[prompt for agent_id,prompt in self.agy.prompts if agent_id=="concept-reviewer"]
        self.assertEqual(len(reviewer),6)
        self.assertEqual(len([p for p in reviewer if "比較総括" in p]),1)
        for evaluation in manager.comparisons(work):
            self.assertNotIn("案1固有の推進力",evaluation["content"]); self.assertIn("5段階評価:",evaluation["content"])
            self.assertIn("実Antigravity",evaluation["content"])
        ranks=[e["recommendation_rank"] for e in manager.comparisons(work)]
        self.assertEqual(sorted(ranks),[1,2,3,4,5])
        self.assertNotEqual([e["ordinal"] for e in manager.comparisons(work)],[1,2,3,4,5])

    def test_limited_retry_uses_validation_feedback(self):
        self.agy.candidate_failures=1
        work,manager,result=self.real_session()
        self.assertEqual(result["generated"],5)
        planner=[prompt for agent_id,prompt in self.agy.prompts if agent_id=="planner"]
        self.assertEqual(len(planner),6)
        self.assertIn("前回出力の書式エラー",planner[1])
        status=[c["status"] for c in manager.candidates(work)]
        self.assertEqual(status[0],"generated:attempt=2")

    def test_contaminated_candidate_triggers_repair_retry_and_is_not_saved(self):
        self.agy.contaminate_candidates=1
        work,manager,result=self.real_session()
        self.assertEqual(result["generated"],5)
        planner=[p for agent_id,p in self.agy.prompts if agent_id=="planner"]
        self.assertEqual(len(planner),6)
        repair=planner[1]
        self.assertIn("修復対象",repair); self.assertIn("その場 of 問題には",repair)
        self.assertIn("一切変更しない",repair)
        saved=Path(manager.candidates(work)[0]["path"]).read_text(encoding="utf-8")
        self.assertEqual(text_quality_problems(saved),[])
        attempts=Path(manager.candidates(work)[0]["path"]).parent/"attempts"
        self.assertTrue((attempts/"c01-attempt-1.error.txt").is_file())
        self.assertIn(QUALITY_ERROR,(attempts/"c01-attempt-1.error.txt").read_text(encoding="utf-8"))

    def test_persistent_contamination_fails_without_dummy_fallback(self):
        self.agy.always_contaminate=True
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=False)
        with self.assertRaisesRegex(KoboError,"ダミーへ代替しません"): manager.start(work)
        self.assertEqual(manager.status(work)["status"],"failed"); self.assertEqual(manager.candidates(work),[])

    def test_real_revision_regenerates_candidate_without_overwriting_original(self):
        work,manager,_=self.real_session()
        instructions=self.root/"revision.md"; instructions.write_text("仮題を変える。",encoding="utf-8")
        before=manager.candidate("C01",work)
        original_text=Path(before["path"]).read_text(encoding="utf-8")
        result=manager.action("select","C01",work_id=work)
        result=manager.action("revise","C01",instruction_path=instructions,work_id=work)
        self.assertEqual(result["revision"]["revision"],1); self.assertEqual(result["revision"]["attempts"],1)
        # 原本は一字も変わらない
        self.assertEqual(Path(before["path"]).read_text(encoding="utf-8"),original_text)
        after=manager.candidate("C01",work)
        self.assertEqual(after["revision"],1); self.assertNotEqual(after["path"],after["original_path"])
        self.assertIn("改訂後の仮題",after["content"]); self.assertNotIn("改訂後の仮題",original_text)
        self.assertNotEqual(after["revision_run_id"],before["generation_run_id"])
        prompts=[p for agent_id,p in self.agy.prompts if agent_id=="planner"]
        self.assertIn("改訂対象の既存案",prompts[-1]); self.assertIn("仮題を変える。",prompts[-1])
        rows=manager.revisions("C01",work)
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]["source_run_id"],before["generation_run_id"])

    def test_finalized_concept_uses_revised_candidate_and_records_lineage(self):
        work,manager,_=self.real_session()
        instructions=self.root/"revision.md"; instructions.write_text("仮題を変える。",encoding="utf-8")
        manager.action("select","C01",work_id=work)
        manager.action("revise","C01",instruction_path=instructions,work_id=work)
        final=manager.finalize(work)
        text=Path(final["path"]).read_text(encoding="utf-8")
        self.assertIn("改訂後の仮題",text)
        self.assertIn("候補改訂: 第1版",text)
        self.assertIn("仮題を変える。",text)
        self.assertNotIn(str(self.root),text)

    def test_dummy_revision_still_records_only(self):
        """ダミーセッションでは実AI改訂を行わない（従来どおり指示の記録のみ）。"""
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=True); manager.start(work)
        instructions=self.root/"revision.md"; instructions.write_text("記録のみ。",encoding="utf-8")
        with self.assertRaisesRegex(KoboError,"ダミー"):
            manager.action("revise","C01",instruction_path=instructions,work_id=work)
        self.assertEqual(manager.revisions(work_id=work),[])

    def test_stale_profile_registration_does_not_beat_newer_file(self):
        """一度v001で企画を作った後にv002を置いたら、次のセッションはv002を使う。"""
        work=self.make_work()
        novels=self.root/"novels"/work; novels.mkdir(parents=True)
        (novels/"READER_PROFILE.v001.md").write_text("旧プロファイル",encoding="utf-8")
        with self.orch.connection() as db:
            db.execute("UPDATE urs_documents SET status='draft' WHERE session_id IN (SELECT session_id FROM urs_sessions WHERE work_id=?)",(work,))
        manager=ConceptManager(self.orch,dummy=False)
        first=manager.start(work)
        self.assertTrue(first["urs_path"].endswith("READER_PROFILE.v001.md"))
        (novels/"READER_PROFILE.v002.md").write_text("最新プロファイル",encoding="utf-8")
        second=manager.action("regenerate",work_id=work)
        self.assertTrue(second["urs_path"].endswith("READER_PROFILE.v002.md"),second["urs_path"])
        self.assertEqual(second["urs_version"],2)

    def test_session_uses_latest_reader_profile(self):
        work=self.make_work()
        novels=self.root/"novels"/work; novels.mkdir(parents=True)
        (novels/"READER_PROFILE.v001.md").write_text("旧プロファイル",encoding="utf-8")
        (novels/"READER_PROFILE.v002.md").write_text("最新プロファイル",encoding="utf-8")
        with self.orch.connection() as db:
            db.execute("UPDATE urs_documents SET status='draft' WHERE session_id IN (SELECT session_id FROM urs_sessions WHERE work_id=?)",(work,))
        manager=ConceptManager(self.orch,dummy=False); result=manager.start(work)
        self.assertNotIn("v001",Path(result["urs_path"]).name)
        self.assertTrue(result["urs_path"].endswith("READER_PROFILE.v002.md"))
        self.assertEqual(result["urs_version"],2)
        published=manager.publish(work)
        used=(self.root/published["path"]/"READER_PROFILE_USED.md").read_text(encoding="utf-8")
        self.assertIn("最新プロファイル",used); self.assertIn("READER_PROFILE.v002.md",used)

    def test_exhausted_retries_fail_without_dummy_fallback(self):
        self.agy.always_fail_candidates=True
        work=self.make_work(); manager=ConceptManager(self.orch,dummy=False)
        with self.assertRaisesRegex(KoboError,"ダミーへ代替しません"): manager.start(work)
        self.assertEqual(manager.status(work)["status"],"failed"); self.assertEqual(manager.status(work)["adapter"],"agy")
        self.assertEqual(manager.candidates(work),[])

    def test_board_shows_generation_kind(self):
        work,manager,_=self.real_session()
        real=Path(manager.board(work)["path"]).read_text(encoding="utf-8")
        self.assertIn("実Antigravity",real); self.assertIn("AI比較総括",real)
        dummy=Path(self.manager.board(self.work_id)["path"]).read_text(encoding="utf-8")
        self.assertIn("企画選定禁止",dummy)

    def test_publish_writes_tracked_artifacts_without_absolute_paths(self):
        work,manager,result=self.real_session()
        published=manager.publish(work,selection_note="前版は選定対象外")
        target=self.root/published["path"]
        self.assertEqual(published["version"],1); self.assertEqual(published["status"],"awaiting_selection")
        names=["index.html","comparison.md","PROVENANCE.json","READER_PROFILE_USED.md"]
        names+=[f"candidates/candidate-c{i:02d}.md" for i in range(1,6)]
        names+=[f"evaluations/evaluation-c{i:02d}.md" for i in range(1,6)]
        for name in names: self.assertTrue((target/name).is_file(),name)
        provenance=json.loads((target/"PROVENANCE.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["adapter"],"agy"); self.assertFalse(provenance["dummy"])
        self.assertEqual(len(provenance["candidate_run_ids"]),5); self.assertEqual(len(provenance["evaluation_run_ids"]),5)
        for path in target.rglob("*"):
            if path.is_file(): self.assertNotIn(str(self.root),path.read_text(encoding="utf-8"),path.name)
        index=(target/"index.html").read_text(encoding="utf-8")
        self.assertIn("参照読者プロファイル",index); self.assertIn(Path(result["urs_path"]).name,index)
        self.assertIn("前版は選定対象外",index); self.assertIn(published["session_id"],index)
        second=manager.publish(work); self.assertEqual(second["version"],2)
        self.assertTrue((self.root/published["path"]/"index.html").is_file(),"v001が残っていない")

    def test_publish_records_revision_lineage_predecessor_and_digests(self):
        work,manager,result=self.real_session()
        instructions=self.root/"revision.md"; instructions.write_text("仮題を変える。",encoding="utf-8")
        manager.action("select","C01",work_id=work)
        manager.action("revise","C01",instruction_path=instructions,work_id=work)
        published=manager.publish(work,predecessor_session_id="concept-previous-environment")
        target=self.root/published["path"]
        provenance=json.loads((target/"PROVENANCE.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["predecessor_session_id"],"concept-previous-environment")
        self.assertIn("後継", provenance["predecessor_note"])
        revision=provenance["revisions"][0]
        self.assertEqual(revision["candidate"],"C01"); self.assertEqual(revision["revision"],1)
        self.assertNotEqual(revision["revision_run_id"],revision["source_run_id"])
        self.assertTrue(revision["instruction"].endswith("revision.md"))
        digests=provenance["sha256"]
        self.assertIn(Path(result["urs_path"]).name,"".join(digests["inputs"]))
        for name in ("index.html","comparison.md","candidates/candidate-c01.md"):
            self.assertEqual(len(digests["outputs"][name]),64,name)
            self.assertEqual(digests["outputs"][name],
                             __import__("hashlib").sha256((target/name).read_bytes()).hexdigest())
        self.assertNotIn("PROVENANCE.json",digests["outputs"])


class ReaderProfileSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.dir=Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def write(self,*names):
        for name in names: (self.dir/name).write_text("x",encoding="utf-8")

    def test_missing_directory_and_no_profile(self):
        self.assertEqual(latest_reader_profile(self.dir/"absent"),(None,0))
        self.assertEqual(latest_reader_profile(self.dir),(None,0))

    def test_v001_only_stays_compatible(self):
        self.write("READER_PROFILE.v001.md")
        path,version=latest_reader_profile(self.dir)
        self.assertEqual((path.name,version),("READER_PROFILE.v001.md",1))

    def test_v002_wins_over_v001(self):
        self.write("READER_PROFILE.v001.md","READER_PROFILE.v002.md")
        path,version=latest_reader_profile(self.dir)
        self.assertEqual((path.name,version),("READER_PROFILE.v002.md",2))

    def test_numeric_comparison_not_lexicographic(self):
        self.write("READER_PROFILE.v002.md","READER_PROFILE.v010.md")
        path,version=latest_reader_profile(self.dir)
        self.assertEqual((path.name,version),("READER_PROFILE.v010.md",10))

    def test_irregular_names_are_ignored(self):
        self.write("READER_PROFILE.v001.md","READER_PROFILE.md","READER_PROFILE.vXX.md",
                   "READER_PROFILE.v999.md.bak","READER_FEEDBACK.v009.md")
        path,version=latest_reader_profile(self.dir)
        self.assertEqual((path.name,version),("READER_PROFILE.v001.md",1))


class TextQualityTest(unittest.TestCase):
    def test_replacement_character_is_rejected(self):
        self.assertTrue(any("U+FFFD" in p for p in text_quality_problems("木箱�の底")))

    def test_hangul_is_rejected(self):
        self.assertTrue(any("ハングル" in p for p in text_quality_problems("彼女의負担が軽減される")))

    def test_isolated_english_between_japanese_is_rejected(self):
        for sample in ("木箱 of 底から","街道 of 測量士","最小 of 最小の労力","彼女 the 負担"):
            self.assertTrue(text_quality_problems(sample),sample)

    def test_control_characters_are_rejected(self):
        self.assertTrue(any("制御文字" in p for p in text_quality_problems("本文\x00です")))

    def test_legitimate_ascii_and_code_are_not_rejected(self):
        legit=("- adapter: `agy`\n- モデル: `Antigravity既定（--model未指定）`\n"
               "- 実行ID: `run-20260728T114021.051331Z-f1b1d9eb06f9`\n"
               "主人公はWebデザイナーで、CLIのAPIを設計している。\n"
               "```python\nfor item in rows: print('of the and')\n```\n"
               "英語のタイトルはRain of Stonesという。\n")
        self.assertEqual(text_quality_problems(legit),[])

    def test_published_v001_contamination_is_detected(self):
        """instruction-21が報告した実際の混入を検出できることを固定する。"""
        samples={"C02ハングル":"穏やかに微笑む姿へと変わり、彼女의負担が軽減される",
                 "C02英語":"最小 of 最小の労力で解決する",
                 "C03英語":"木箱 of 底から現れたのは",
                 "C04英語":"街道 of 測量士として働く",
                 "C05ハングル":"魔石に刻まれた刻印의謎を追う"}
        for label,sample in samples.items():
            self.assertTrue(text_quality_problems(sample),label)


if __name__ == "__main__": unittest.main()
