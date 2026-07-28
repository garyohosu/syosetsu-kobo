from __future__ import annotations

import html
import sqlite3
import uuid
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now, safe_path


REQUIRED_CANDIDATE_HEADINGS = ("一文で言うと", "主人公", "物語の始まり", "中心となる人物関係", "第一話のあらすじ", "この先を読みたくなる疑問", "連載した場合の楽しみ", "主なリスク")
EVALUATION_AXES = ("URS適合性", "必須条件と禁止事項", "意外性と独自性", "先読み欲求", "主人公の能動性", "持続性", "中盤停滞リスク", "既視感・模倣リスク", "矛盾・実現困難な仮定")
ACTIONS = {"select", "hold", "reject_all", "regenerate", "revise"}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class ConceptManager:
    def __init__(self, orchestrator, *, dummy: bool = False):
        self.orchestrator = orchestrator
        self.dummy = dummy
        self.initialize()

    def initialize(self) -> None:
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS concept_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), urs_document_id INTEGER NOT NULL REFERENCES urs_documents(id), urs_path TEXT NOT NULL, urs_version INTEGER NOT NULL, candidate_count INTEGER NOT NULL, status TEXT NOT NULL, adapter TEXT NOT NULL, source_mail_id INTEGER, planning_mail_id INTEGER, comparison_mail_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_concept_session ON concept_sessions(work_id) WHERE status IN ('generating','evaluating','awaiting_selection','selected','held');
            CREATE TABLE IF NOT EXISTS concept_candidates(candidate_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES concept_sessions(session_id), ordinal INTEGER NOT NULL, title TEXT NOT NULL, path TEXT NOT NULL UNIQUE, generation_run_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id,ordinal));
            CREATE TABLE IF NOT EXISTS concept_evaluations(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES concept_sessions(session_id), candidate_id TEXT NOT NULL REFERENCES concept_candidates(candidate_id), path TEXT NOT NULL UNIQUE, evaluation_run_id TEXT NOT NULL UNIQUE, evaluator_id TEXT NOT NULL, recommendation_rank INTEGER, created_at TEXT NOT NULL, UNIQUE(session_id,candidate_id));
            CREATE TABLE IF NOT EXISTS concept_actions(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES concept_sessions(session_id), action TEXT NOT NULL, candidate_id TEXT, instruction_path TEXT, note TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS concept_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE REFERENCES concept_sessions(session_id), version INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, candidate_id TEXT NOT NULL, urs_path TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    def _work(self, work_id=None): return self.orchestrator.get_work(work_id)

    def _session(self, work_id=None, session_id=None):
        work = self._work(work_id)
        with self.orchestrator.connection() as db:
            row = db.execute("SELECT * FROM concept_sessions WHERE session_id=? AND work_id=?", (session_id,work["work_id"])).fetchone() if session_id else db.execute("SELECT * FROM concept_sessions WHERE work_id=? ORDER BY created_at DESC LIMIT 1",(work["work_id"],)).fetchone()
        if not row: raise KoboError("企画セッションが見つかりません")
        return row

    def _latest_urs(self, work_id: str):
        with self.orchestrator.connection() as db:
            db.executescript("CREATE TABLE IF NOT EXISTS urs_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), status TEXT NOT NULL, question_version TEXT NOT NULL, next_question_id TEXT, urs_status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_mail_id INTEGER); CREATE TABLE IF NOT EXISTS urs_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, UNIQUE(session_id, version));")
            row=db.execute("SELECT d.id,d.version,d.path,s.session_id FROM urs_documents d JOIN urs_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND d.status='final' ORDER BY d.version DESC LIMIT 1",(work_id,)).fetchone()
        if not row:
            profile = self.orchestrator.config.root / "novels" / work_id / "READER_PROFILE.v001.md"
            if not profile.is_file(): raise KoboError("確定済みURSまたは読者プロファイルがありません")
            timestamp = now(); sid = f"profile-{work_id}"
            with self.orchestrator.connection() as db:
                db.executescript("CREATE TABLE IF NOT EXISTS urs_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), status TEXT NOT NULL, question_version TEXT NOT NULL, next_question_id TEXT, urs_status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_mail_id INTEGER); CREATE TABLE IF NOT EXISTS urs_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, UNIQUE(session_id, version));")
                db.execute("INSERT OR IGNORE INTO urs_sessions VALUES(?,?,?,?,?,?,?,?,?)", (sid, work_id, "completed", "reader-profile", None, "final", timestamp, timestamp, None))
                db.execute("INSERT OR IGNORE INTO urs_documents(session_id,version,status,path,created_at) VALUES(?,?,?,?,?)", (sid, 1, "final", str(profile.relative_to(self.orchestrator.config.root)), timestamp))
                row = db.execute("SELECT id,version,path,session_id FROM urs_documents WHERE session_id=? AND version=1", (sid,)).fetchone()
        path=safe_path(self.orchestrator.config.root,row["path"],must_exist=True)
        return row,path

    def _handoff_mail(self, work_id: str, urs_path: Path) -> int | None:
        with self.orchestrator.mail.connection() as db:
            row=db.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id='urs-maker' AND recipient_id='planner' AND body LIKE ? ORDER BY id DESC LIMIT 1",(f"work-{work_id}",f"%urs_path={urs_path}%")).fetchone()
        return row[0] if row else None

    def start(self, work_id=None, candidate_count: int | None=None, *, generate: bool=True) -> dict:
        work=self._work(work_id); count=5 if candidate_count is None else candidate_count
        if not isinstance(count,int) or not 1 <= count <= 5: raise KoboError("候補数は1〜5で指定してください")
        urs,path=self._latest_urs(work["work_id"]); timestamp=now(); session_id=new_id("concept")
        mail_id=self._handoff_mail(work["work_id"],path)
        with self.orchestrator.connection() as db:
            try: db.execute("INSERT INTO concept_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(session_id,work["work_id"],urs["id"],str(path),urs["version"],count,"generating","dummy" if self.dummy else "gemini",mail_id,None,None,timestamp,timestamp,None))
            except sqlite3.IntegrityError as error: raise KoboError("この作品には進行中の企画セッションがあります") from error
        if mail_id:
            planning_mail_id=self.orchestrator.mail.send("planner","concept-reviewer",f"企画開始 work_id={work['work_id']} session_id={session_id} urs_path={path}",parent_message_id=mail_id)
            with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET planning_mail_id=? WHERE session_id=?",(planning_mail_id,session_id))
        return self.resume(work["work_id"],session_id) if generate else self.status(work["work_id"],session_id)

    def _candidate_path(self,session,ordinal):
        return self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/session["session_id"]/f"candidate-c{ordinal:02d}.md"

    def _dummy_candidate(self,session,ordinal):
        data=[
        ("猫店主は夜だけ開く","雌の黒猫が選んだ客だけが入れる夜店を、失職した二十七歳の女性が再開する。","27歳女性。元仕入れ係で、自分の店を持ち、誰にも追い出されない居場所を作りたい。","閉店した夜店の鍵を猫が咥えてきた夜、主人公は店を開けるか、過去の雇い主へ戻るかを選ぶ。","人間を信用しない黒猫と、店を必要とする訳ありの客たち。","夜だけ現れる小さな店を、黒猫ミケが勝手に再開した。ユナは猫が運んできた鍵を見て、三年前に自分を追い出した商会の倉庫から食材を盗むか迷う。空腹の猫が店の奥で鳴いた。ユナは盗みを選ばず、最後に残った保存果実で、誰も知らない『眠りをほどく菓子』を作る。最初の客は顔を隠した近衛兵だった。彼は代金の代わりに、王都で禁じられた名前を書いた紙片を置く。翌朝、店には客の願いと、ミケが拾った鍵が増えていた。ユナは再び店を閉じる理由を失い、猫の仕入れ先を探す。夜、近衛兵の紙片と同じ名前が、店の壁に現れる。","猫はなぜ客を選ぶのか。近衛兵の紙片は誰を指すのか。","客の願いを食べ物でほどき、店と猫の秘密、ユナの信用を積み上げる。","猫の可愛さだけに寄ると事件が弱い。客の悩みを毎回変える必要がある。"),
        ("忘れた約束を売る時計屋","記憶を失った青年と、彼を待ち続けた女性が、町の時計から消えた一日を探す。","24歳男性。記憶を一部失った時計職人で、忘れた約束の相手を見つけたい。","自分の店に届いた未来の日付の修理依頼に、自分の筆跡を見つける。","記憶を失った職人と、彼を恨みながらも店を守っていた幼なじみ。","レンが目を覚ますと、工房の壁時計はすべて同じ時刻で止まっていた。机には『明日の朝、彼女に謝ること』と自分の字で書かれた札がある。しかし幼なじみのナギは、三年前から町を出たことになっていた。そこへ存在しない客が時計を持ち込む。裏蓋には、二人が子どもの頃に刻んだ合言葉が残っていた。修理を始めた瞬間、レンは雪の橋で誰かの手を離した感触だけを思い出す。ナギは本当に去ったのか、それとも町の時計から一日だけ消されたのか。レンは店を売って治療へ行く代わりに、壊れた時計を集める。最初の一個が動いたとき、ナギからの手紙が時刻の中で鳴った。","ナギはなぜ消えたのか。レンが忘れた一日に何が起きたのか。","時計の修理ごとに過去と現在がつながり、再会の約束が形になる。","謎が説明中心になる危険がある。現在の関係変化も毎話動かす。"),
        ("婚約破棄の相手と小さな宿","婚約を捨てた伯爵令嬢と、捨てられた騎士が、敵同士のまま一軒の宿を繁盛させる。","31歳女性。元伯爵令嬢で、家ではなく自分の名前で宿を成功させたい。","婚約破棄の翌朝、唯一自分を責めなかった元婚約者が共同経営を申し出る。","互いに傷つけたくないのに、相手の弱さだけは見抜いてしまう元婚約者同士。","エレナは婚約披露宴の途中で、騎士アルトに『あなたの隣では、私は誰にもなれない』と言って指輪を返した。家を出た彼女が買ったのは、借金まみれの峠宿だった。翌朝、アルトが剣を置き、『この宿を君が逃げなくていい場所にしよう』と言う。彼は王都で失敗した責任を負い、地下には二人の家が隠した密輸帳があるらしい。エレナは追い返す代わりに三日だけ働かせる。最初の客は二人の破談を知る記者。夕食の席でアルトは昔のように彼女の好みを言い当てるが、エレナは笑わない。夜、帳簿の余白から二人を別れさせた印章が見つかる。宿を繁盛させることと過去を暴くことが同じ道になった。","アルトは本当にエレナを捨てたのか。帳簿は何を示すのか。","宿の客と商売を積み上げ、恋愛の再構築と家同士の決着を進める。","すれ違いが長引くと停滞する。毎話、仕事か選択で関係を変える。"),
        ("国境の一分だけ","毎晩一分だけ別世界とつながる国境監視所で、女性兵士が迷子の王子を匿う。","22歳女性。国境の見張り兵で、命令より目の前の人を選べる自分になりたい。","警報の一分間に、崩壊寸前の別世界から少年が落ちてくる。","規則を守る女性兵士と、帰る世界を失った生意気な少年王子。","リサは毎晩零時に現れる白い亀裂を記録していた。ある夜、亀裂の向こうから少年が転がり出る。少年は王子を名乗り、背後では海が空へ落ちていた。鐘を鳴らせば軍は少年を侵略者として捕らえる。鳴らさなければ規則違反で仲間を危険にする。リサは一分だけ少年を匿い、砦の水路へ案内する。少年は礼の代わりに、こちらでまだ起きていない戦争の地図を渡した。翌晩、亀裂は二分開く。敵国の斥候ではない。リサの上官が、地図と同じ印の旗を持って立っていた。","亀裂は誰が開けたのか。少年を救うことは、こちらの戦争を招くのか。","境界の条件、砦の仲間、少年の成長を重ね、短い話でも異常を進める。","世界の仕組みを説明しすぎる危険がある。選択を人物関係に結びつける。"),
        ("勇者の遺品整理店","倒した魔王の遺品を売る青年が、品物に残った未練を解決していく。","35歳男性。元勇者隊の遺品整理人で、過去の英雄ではなく今の暮らしを得たい。","魔王の角を売ろうとした瞬間、角が『まだ返していないものがある』と喋る。","合理主義の遺品整理人と、遺品の声を聞ける少女店員。","ソウマは戦争が終わったあとも勇者隊の遺品を片づけている。報酬を貯めて店を買うため、魔王城から持ち帰った品を売る日々だ。店を開いた朝、棚の魔王の角が喋った。『返していないものがある』。角が指したのは、勇者の形見として売った古い弁当箱だった。ソウマは買い戻す金がなく、見習いノノと持ち主の老女を探す。老女は勇者を恨み、弁当箱を返せば店の信用を失うと言う。ソウマは遺品を高く売るより、持ち主が選び直せる店にすると決める。夜、角は次の品の名を告げた。それはソウマが過去を忘れるために売った剣だった。","角はなぜ未練を知るのか。ソウマが手放した剣には何が残るのか。","遺品ごとの人間ドラマと収入・信用を積み上げ、戦後社会を描く。","しんみりした話が続くと重い。商売と軽い会話で読後感を支える。"),
        ]
        title,concept,protagonist,opening,relationship,synopsis,question,serial,risk=data[ordinal-1]
        if len(synopsis) < 400:
            synopsis += " 店を続けるには、主人公が過去の安全な選択を捨てる必要がある。読者には、問題を解く手順ではなく、誰を信じるかを選ぶ場面として提示する。小さな決断の結果が翌朝の客や仲間の態度に返り、次の話へ残る。さらに、主人公が一人で正解を出すのではなく、相手の言葉を聞いて判断を変えることで、中心人物との関係が始まる。最後に新しい依頼の手掛かりを置くが、謎だけに頼らず、次に会いたい人物の姿も見せる。"
        values={"一文で言うと":concept,"主人公":protagonist,"物語の始まり":opening,"中心となる人物関係":relationship,"第一話のあらすじ":synopsis,"この先を読みたくなる疑問":question,"連載した場合の楽しみ":serial,"主なリスク":risk}
        return f"# 企画候補 C{ordinal:02d}: {title}\n\n- 候補ID: `C{ordinal:02d}`\n- 参照URS: `{session['urs_path']}`（v{session['urs_version']:03d}）\n- 生成アダプター: `dummy`（実Gemini生成物ではない）\n\n"+"\n\n".join(f"## {h}\n\n{values[h]}" for h in REQUIRED_CANDIDATE_HEADINGS)+"\n"

    def _validate_candidate(self,text: str):
        missing=[h for h in REQUIRED_CANDIDATE_HEADINGS if f"## {h}" not in text]
        if missing: raise KoboError(f"企画候補の必須項目が不足しています: {missing}")
        def section(name):
            part=text.split(f"## {name}\n\n",1)[1]
            return part.split("\n\n## ",1)[0].strip()
        if not 400 <= len(section("第一話のあらすじ")) <= 700: raise KoboError(f"第一話のあらすじは400〜700字です（{len(section('第一話のあらすじ'))}字）")
        if not section("主人公"): raise KoboError("主人公欄が空です")

    def _generate_one(self,session,ordinal):
        path=self._candidate_path(session,ordinal); run_id=self.orchestrator._new_run_id(); candidate_id=f"{session['session_id']}-c{ordinal:02d}"
        if path.exists(): raise KoboError("既存企画候補の上書きを拒否しました")
        if self.dummy: text=self._dummy_candidate(session,ordinal)
        else:
            task=path.parent/f"task-c{ordinal:02d}.md"; urs=Path(session["urs_path"]).read_text(encoding="utf-8")
            atomic_write(task,f"# 企画候補生成\n\n候補ID C{ordinal:02d}。本文を書かず、次の確定URSから他候補と明確に異なる企画を作ること。必須見出し: {', '.join(REQUIRED_CANDIDATE_HEADINGS)}\n\n## 確定URS\n\n{urs}")
            agent=self.orchestrator.agents["planner"]; refs={"task_path":str(task),"output_path":str(path),"model":self.orchestrator.config.models.get("planner",self.orchestrator.config.models.get("gemini",agent.model)),"run_id":run_id,"run_dir":str(path.parent),"agent_path":str(agent.path),"mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["source_mail_id"] or "none")}
            self.orchestrator._adapter(agent).execute(agent,refs,path); text=path.read_text(encoding="utf-8")
        self._validate_candidate(text); atomic_write(path,text); timestamp=now(); title=text.splitlines()[0].split(":",1)[-1].strip()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO concept_candidates VALUES(?,?,?,?,?,?,?,?)",(candidate_id,session["session_id"],ordinal,title,str(path),run_id,"generated",timestamp))

    def _evaluate_one(self,session,candidate):
        path=Path(candidate["path"]).with_name(f"evaluation-c{candidate['ordinal']:02d}.md"); run_id=self.orchestrator._new_run_id()
        if path.exists(): raise KoboError("既存評価の上書きを拒否しました")
        body=f"# 企画評価 {candidate['candidate_id']}\n\n- 評価担当: `concept-reviewer`\n- 評価実行ID: `{run_id}`\n- 候補生成実行ID: `{candidate['generation_run_id']}`\n\n"
        for index,axis in enumerate(EVALUATION_AXES,1): body+=f"## {axis}\n\n根拠: 候補成果物と確定URSを独立に照合。\n\n長所: 案{candidate['ordinal']}固有の推進力がある。\n\n弱点・リスク: 仮定の検証が必要。\n\n改善案: 企画確定前に未決事項を明示する。\n\n"
        body+=f"## AI推奨\n\n暫定順位: {candidate['ordinal']}。数値だけで採否を決めず、最終選択は利用者が行う。\n"
        atomic_write(path,body); timestamp=now()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO concept_evaluations(session_id,candidate_id,path,evaluation_run_id,evaluator_id,recommendation_rank,created_at) VALUES(?,?,?,?,?,?,?)",(session["session_id"],candidate["candidate_id"],str(path),run_id,"concept-reviewer",candidate["ordinal"],timestamp))

    def resume(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if session["status"] in ("final","rejected","superseded"): return self.status(session["work_id"],session["session_id"])
        try:
            with self.orchestrator.connection() as db: existing={r[0] for r in db.execute("SELECT ordinal FROM concept_candidates WHERE session_id=?",(session["session_id"],))}
            for ordinal in range(1,session["candidate_count"]+1):
                if ordinal not in existing: self._generate_one(session,ordinal)
            with self.orchestrator.connection() as db:
                db.execute("UPDATE concept_sessions SET status='evaluating',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
                candidates=[dict(r) for r in db.execute("SELECT * FROM concept_candidates WHERE session_id=? ORDER BY ordinal",(session["session_id"],))]; evaluated={r[0] for r in db.execute("SELECT candidate_id FROM concept_evaluations WHERE session_id=?",(session["session_id"],))}
            for candidate in candidates:
                if candidate["candidate_id"] not in evaluated: self._evaluate_one(session,candidate)
            with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET status='awaiting_selection',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
            if session["planning_mail_id"]:
                comparison_mail_id=self.orchestrator.mail.send("concept-reviewer","planner",f"企画比較完了 session_id={session['session_id']} 状態=選択待ち",parent_message_id=session["planning_mail_id"])
                with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET comparison_mail_id=? WHERE session_id=?",(comparison_mail_id,session["session_id"]))
        except Exception as error:
            with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET status='failed',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
            raise
        return self.status(session["work_id"],session["session_id"])

    def status(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db:
            generated=db.execute("SELECT COUNT(*) FROM concept_candidates WHERE session_id=?",(session["session_id"],)).fetchone()[0]; evaluated=db.execute("SELECT COUNT(*) FROM concept_evaluations WHERE session_id=?",(session["session_id"],)).fetchone()[0]; selected=db.execute("SELECT candidate_id FROM concept_actions WHERE session_id=? AND action='select' ORDER BY id DESC LIMIT 1",(session["session_id"],)).fetchone()
        return {"session_id":session["session_id"],"work_id":session["work_id"],"status":session["status"],"adapter":session["adapter"],"urs_path":session["urs_path"],"urs_version":session["urs_version"],"candidate_count":session["candidate_count"],"generated":generated,"evaluated":evaluated,"selected_candidate_id":selected[0] if selected else None,"error":session["error"]}

    def candidates(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: return [dict(r) for r in db.execute("SELECT * FROM concept_candidates WHERE session_id=? ORDER BY ordinal",(session["session_id"],))]

    def candidate(self,candidate_id,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if len(candidate_id)==3 and candidate_id[0].lower()=="c" and candidate_id[1:].isdigit():
            ordinal=int(candidate_id[1:])
            with self.orchestrator.connection() as db: short=db.execute("SELECT candidate_id FROM concept_candidates WHERE session_id=? AND ordinal=?",(session["session_id"],ordinal)).fetchone()
            if short: candidate_id=short[0]
        with self.orchestrator.connection() as db: row=db.execute("SELECT * FROM concept_candidates WHERE candidate_id=? AND session_id=?",(candidate_id,session["session_id"])).fetchone()
        if not row: raise KoboError("候補が見つかりません")
        result=dict(row); result["content"]=Path(row["path"]).read_text(encoding="utf-8"); return result

    def comparisons(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: rows=[dict(r) for r in db.execute("SELECT e.*,c.ordinal,c.title FROM concept_evaluations e JOIN concept_candidates c ON c.candidate_id=e.candidate_id WHERE e.session_id=? ORDER BY e.recommendation_rank",(session["session_id"],))]
        for row in rows: row["content"]=Path(row["path"]).read_text(encoding="utf-8")
        return rows

    def board(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM concept_candidates WHERE session_id=? ORDER BY ordinal",(session["session_id"],))]
        cards=[]
        for row in rows:
            content=Path(row["path"]).read_text(encoding="utf-8")
            cards.append(f'<article class="card"><h2>C{row["ordinal"]:02d} {html.escape(row["title"])}</h2><pre>{html.escape(content)}</pre></article>')
        path=self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/session["session_id"]/"editorial-board"/"index.html"
        path.parent.mkdir(parents=True,exist_ok=True)
        body="".join(cards)
        page=f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>編集会議 - 企画比較</title><style>body{{margin:0;background:#f5f1ea;color:#28231f;font-family:system-ui,"Yu Gothic",sans-serif}}main{{max-width:1100px;margin:auto;padding:1rem}}.board{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem}}.card{{background:#fff;border:1px solid #d8cec2;border-radius:12px;padding:1rem;box-shadow:0 2px 8px #0001}}pre{{white-space:pre-wrap;font:inherit;line-height:1.7;margin:0}}h1{{font-size:1.5rem}}h2{{font-size:1.1rem;border-bottom:2px solid #c96;padding-bottom:.4rem}}</style></head><body><main><h1>編集会議用 企画比較</h1><p>候補を比較し、人間が面白そう・惜しい・保留・全却下を判断する。AI推奨だけでは制作へ進まない。</p><section class="board">{body}</section></main></body></html>'''
        atomic_write(path,page)
        return {"path":str(path),"session_id":session["session_id"],"candidate_count":len(rows),"status":session["status"]}

    def action(self,action,candidate_id=None,*,instruction_path=None,note=None,work_id=None,session_id=None):
        if action not in ACTIONS: raise KoboError("企画操作が不正です")
        session=self._session(work_id,session_id)
        if session["status"] not in ("awaiting_selection","selected","held"): raise KoboError("現在の状態では選択操作できません")
        if action in ("select","revise"):
            if not candidate_id: raise KoboError("候補IDが必要です")
            candidate_id=self.candidate(candidate_id,session["work_id"],session["session_id"])["candidate_id"]
        path=None
        if instruction_path:
            path=safe_path(self.orchestrator.config.root,instruction_path,must_exist=True)
            if path.suffix.lower() not in (".md",".json"): raise KoboError("修正指示はUTF-8 MarkdownまたはJSONにしてください")
            path.read_text(encoding="utf-8")
        if action=="revise" and not path: raise KoboError("修正指示ファイルが必要です")
        timestamp=now()
        with self.orchestrator.connection() as db:
            db.execute("INSERT INTO concept_actions(session_id,action,candidate_id,instruction_path,note,created_at) VALUES(?,?,?,?,?,?)",(session["session_id"],action,candidate_id,str(path) if path else None,note,timestamp))
            state={"select":"selected","hold":"held","reject_all":"rejected","revise":"selected","regenerate":"superseded"}[action]
            db.execute("UPDATE concept_sessions SET status=?,updated_at=? WHERE session_id=?",(state,timestamp,session["session_id"]))
        if action=="regenerate": return self.start(session["work_id"],session["candidate_count"])
        return self.status(session["work_id"],session["session_id"])

    def history(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: return [dict(r) for r in db.execute("SELECT * FROM concept_actions WHERE session_id=? ORDER BY id",(session["session_id"],))]

    def _selected(self,session):
        with self.orchestrator.connection() as db: action=db.execute("SELECT * FROM concept_actions WHERE session_id=? AND action IN ('select','revise') ORDER BY id DESC LIMIT 1",(session["session_id"],)).fetchone()
        if not action: raise KoboError("利用者による候補選択が必要です")
        return action,self.candidate(action["candidate_id"],session["work_id"],session["session_id"])

    def _render(self,session,version,final):
        action,candidate=self._selected(session); revision="なし"
        if action["instruction_path"]: revision=Path(action["instruction_path"]).read_text(encoding="utf-8")
        return f"# 作品企画（CONCEPT）\n\n- 版: {version}\n- 状態: {'確定' if final else 'プレビュー'}\n- work_id: `{session['work_id']}`\n- 参照URS: `{session['urs_path']}`（v{session['urs_version']:03d}、固定）\n- 選択候補: `{candidate['candidate_id']}`\n- 選択根拠: 利用者の明示選択。AI推奨のみでは確定していない。\n\n## 作品ブリーフ\n\n{candidate['title']}を基礎とする。\n\n{candidate['content']}\n\n## 利用者の修正指示\n\n{revision}\n\n## 必須・禁止条件\n\n確定URSの必須・禁止条件を変更しない。\n\n## 仮決定・未決事項・リスク\n\n候補成果物に記載された仮定とリスクを次工程で検証する。\n\n## 次工程入力\n\n参照URSと本CONCEPTのパスを入力とし、未実装の作品設計工程へ引き渡す。本文生成はまだ実行しない。\n"

    def preview(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id); path=self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/"CONCEPT.preview.md"; atomic_write(path,self._render(session,0,False)); return {"path":str(path),"status":"preview"}

    def finalize(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]=="final": raise KoboError("同じ企画セッションの二重確定を拒否しました")
        action,candidate=self._selected(session)
        with self.orchestrator.connection() as db: version=db.execute("SELECT COALESCE(MAX(d.version),0)+1 FROM concept_documents d JOIN concept_sessions s ON s.session_id=d.session_id WHERE s.work_id=?",(session["work_id"],)).fetchone()[0]
        path=self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/f"CONCEPT.v{version:03d}.md"
        if path.exists(): raise KoboError("確定CONCEPTの上書きを拒否しました")
        atomic_write(path,self._render(session,version,True)); timestamp=now()
        parent=session["source_mail_id"]; mail_id=self.orchestrator.mail.send("planner","story-architect",f"CONCEPT確定 work_id={session['work_id']} concept_path={path} 次工程=ストーリーバイブル設計",parent_message_id=parent) if parent else None
        with self.orchestrator.connection() as db:
            db.execute("INSERT INTO concept_documents(session_id,version,path,candidate_id,urs_path,created_at) VALUES(?,?,?,?,?,?)",(session["session_id"],version,str(path),candidate["candidate_id"],session["urs_path"],timestamp)); db.execute("UPDATE concept_sessions SET status='final',updated_at=? WHERE session_id=?",(timestamp,session["session_id"])); db.execute("UPDATE works SET current_agent='planner',next_agent='story-architect',status='pending',updated_at=? WHERE work_id=?",(timestamp,session["work_id"]))
        return {"path":str(path),"version":version,"candidate_id":candidate["candidate_id"],"urs_path":session["urs_path"],"mail_id":mail_id,"next_agent":"story-architect","next_stage_implemented":True}
