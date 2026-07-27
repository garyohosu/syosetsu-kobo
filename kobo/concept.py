from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now, safe_path


REQUIRED_CANDIDATE_HEADINGS = (
    "一文コンセプト", "中心的な読書体験", "主人公・目的・障害・賭けるもの", "中心的な人物関係",
    "舞台・世界の核", "物語を動かす仕掛け", "序盤の導入", "中盤を持続させる力", "終盤・結末の方向性",
    "次を読みたいと思わせる要因", "URS必須条件への対応", "意図的に採用しなかった要素", "未決事項・仮定・リスク",
)
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
            row=db.execute("SELECT d.id,d.version,d.path,s.session_id FROM urs_documents d JOIN urs_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND d.status='final' ORDER BY d.version DESC LIMIT 1",(work_id,)).fetchone()
        if not row: raise KoboError("確定済みURSがありません")
        path=safe_path(self.orchestrator.config.root,row["path"],must_exist=True)
        return row,path

    def _handoff_mail(self, work_id: str, urs_path: Path) -> int | None:
        with self.orchestrator.mail.connection() as db:
            row=db.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id='urs-maker' AND recipient_id='planner' AND body LIKE ? ORDER BY id DESC LIMIT 1",(f"work-{work_id}",f"%urs_path={urs_path}%")).fetchone()
        return row[0] if row else None

    def start(self, work_id=None, candidate_count: int | None=None, *, generate: bool=True) -> dict:
        work=self._work(work_id); count=3 if candidate_count is None else candidate_count
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
        styles=(("静かな開拓工房","廃れた町の工房を知恵で再生する"),("巡回する不思議商店","移動する店で各地の問題を解く"),("辺境制度設計官","小さな制度改善を共同体へ広げる"),("空中庭園の管理人","失われた生活基盤を育て直す"),("夜明けの交易路","分断された町を商いで結ぶ"))
        title,concept=styles[ordinal-1]
        values={
            "一文コンセプト":concept,"中心的な読書体験":f"異なる案{ordinal}として、改善の蓄積と人間関係の変化を楽しむ。",
            "主人公・目的・障害・賭けるもの":"実務家の主人公が居場所を築く。資源不足と旧慣習を越え、仲間の生活を守る。",
            "中心的な人物関係":"主人公と価値観の異なる協力者が、仕事を通じて信頼を育てる。","舞台・世界の核":f"候補{ordinal}固有の生活圏と循環する資源。",
            "物語を動かす仕掛け":"各話の依頼が長期課題の一部を解き、次の選択を生む。","序盤の導入":"小さな依頼の成功が、より大きな問題への入口になる。",
            "中盤を持続させる力":"成果が新しい利害関係者と課題を呼び、共同体の規模が広がる。","終盤・結末の方向性":"主人公が築いた仕組みを共同体が自走させる。",
            "次を読みたいと思わせる要因":"成功の直後に、次の改善で解けそうな具体的問題を示す。","URS必須条件への対応":"確定URSを変更せず、未決事項は仮定として分離する。",
            "意図的に採用しなかった要素":"参考作品の固有名詞・設定・場面・特徴的表現。","未決事項・仮定・リスク":"未回答項目は確定せず、中盤の反復感を評価で監視する。",
        }
        return f"# 企画候補 C{ordinal:02d}: {title}\n\n- 候補ID: `C{ordinal:02d}`\n- 参照URS: `{session['urs_path']}`（v{session['urs_version']:03d}）\n- 生成アダプター: `dummy`（実Gemini生成物ではない）\n\n"+"\n\n".join(f"## {h}\n\n{values[h]}" for h in REQUIRED_CANDIDATE_HEADINGS)+"\n"

    def _validate_candidate(self,text: str):
        missing=[h for h in REQUIRED_CANDIDATE_HEADINGS if f"## {h}" not in text]
        if missing: raise KoboError(f"企画候補の必須項目が不足しています: {missing}")

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
