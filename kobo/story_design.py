from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now, safe_path


BIBLE_HEADINGS = ("作品の核", "世界のルール", "主要舞台", "主要人物", "人物関係", "組織・勢力", "時系列", "資源・能力・制約", "謎・伏線候補", "テーマと読書体験", "禁止事項・模倣回避", "整合性ルール", "仮定・未決事項・リスク")
PLOT_HEADINGS = ("全体構造", "主要アーク", "序盤", "中盤", "終盤", "章・話のビート", "主人公の変化", "主要人物の変化", "伏線と回収", "葛藤・賭けるもの", "因果関係", "ペーシングと中だるみ対策", "結末", "未決事項・リスク")
BIBLE_AUDIT = ("CONCEPT・URS適合", "世界ルールの明確性", "人物と関係の整合性", "時系列・資源・能力の整合性", "テーマと禁止事項", "模倣リスク", "長編運用可能性")
PLOT_AUDIT = ("CONCEPT・バイブル適合", "因果関係", "主人公の能動性", "アークと人物変化", "先読み欲求", "伏線と回収", "中盤持続性", "結末の納得感", "設定矛盾・模倣リスク")


def uid(prefix: str) -> str: return f"{prefix}-{uuid.uuid4().hex}"


class StoryDesignManager:
    def __init__(self, orchestrator, *, dummy: bool = False):
        self.orchestrator=orchestrator; self.dummy=dummy; self.initialize()

    def initialize(self):
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS story_design_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), concept_document_id INTEGER NOT NULL REFERENCES concept_documents(id), concept_path TEXT NOT NULL, concept_version INTEGER NOT NULL, urs_path TEXT NOT NULL, status TEXT NOT NULL, adapter TEXT NOT NULL, source_mail_id INTEGER, bible_mail_id INTEGER, plot_mail_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_story_design ON story_design_sessions(work_id) WHERE status NOT IN ('completed','failed','superseded');
            CREATE TABLE IF NOT EXISTS story_design_artifacts(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES story_design_sessions(session_id), kind TEXT NOT NULL, revision INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL UNIQUE, agent_id TEXT NOT NULL, status TEXT NOT NULL, source_path TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id,kind,revision));
            CREATE TABLE IF NOT EXISTS story_design_actions(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES story_design_sessions(session_id), action TEXT NOT NULL, stage TEXT NOT NULL, instruction_path TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS story_design_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES story_design_sessions(session_id), kind TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, source_path TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id,kind), UNIQUE(path));
            """)

    def _work(self, work_id=None): return self.orchestrator.get_work(work_id)

    def _session(self, work_id=None, session_id=None):
        work=self._work(work_id)
        with self.orchestrator.connection() as db:
            row=db.execute("SELECT * FROM story_design_sessions WHERE session_id=? AND work_id=?",(session_id,work["work_id"])).fetchone() if session_id else db.execute("SELECT * FROM story_design_sessions WHERE work_id=? ORDER BY created_at DESC LIMIT 1",(work["work_id"],)).fetchone()
        if not row: raise KoboError("ストーリー設計セッションが見つかりません")
        return row

    def _latest_concept(self, work_id):
        with self.orchestrator.connection() as db:
            row=db.execute("SELECT d.id,d.version,d.path,d.urs_path FROM concept_documents d JOIN concept_sessions s ON s.session_id=d.session_id WHERE s.work_id=? ORDER BY d.version DESC LIMIT 1",(work_id,)).fetchone()
        if not row: raise KoboError("確定済みCONCEPTがありません")
        return row,safe_path(self.orchestrator.config.root,row["path"],must_exist=True)

    def _handoff_mail(self, work_id, concept_path):
        with self.orchestrator.mail.connection() as db:
            row=db.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id='planner' AND recipient_id='story-architect' AND body LIKE ? ORDER BY id DESC LIMIT 1",(f"work-{work_id}",f"%concept_path={concept_path}%")).fetchone()
        return row[0] if row else None

    def start(self, work_id=None):
        work=self._work(work_id); concept,path=self._latest_concept(work["work_id"]); timestamp=now(); session_id=uid("story") ; source=self._handoff_mail(work["work_id"],path)
        with self.orchestrator.connection() as db:
            try: db.execute("INSERT INTO story_design_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(session_id,work["work_id"],concept["id"],str(path),concept["version"],concept["urs_path"],"bible_generating","dummy" if self.dummy else "gemini",source,None,None,timestamp,timestamp,None))
            except sqlite3.IntegrityError as error: raise KoboError("この作品には進行中のストーリー設計があります") from error
        return self.resume(work["work_id"],session_id)

    def _dir(self, session): return self.orchestrator.config.store/"works"/session["work_id"]/"story-design"/session["session_id"]
    def _artifact(self, session_id, kind):
        with self.orchestrator.connection() as db: row=db.execute("SELECT * FROM story_design_artifacts WHERE session_id=? AND kind=? ORDER BY revision DESC LIMIT 1",(session_id,kind)).fetchone()
        return row

    def _dummy(self, session, stage):
        headings=BIBLE_HEADINGS if stage=="bible" else PLOT_HEADINGS
        values={heading:f"{heading}を、固定参照したCONCEPTと上流資料に整合する形で定義する。未決事項は確定せず、後工程の検証点として残す。" for heading in headings}
        if stage=="bible":
            values["主要人物"]="主人公、協力者、対立する立場の人物について、欲求・役割・制約を定義する。"; values["整合性ルール"]="時系列、資源、能力、関係変化は確定版を基準に追跡し、変更理由を残す。"
        else:
            values["全体構造"]="導入・拡大・転換・決着の四段階。各段階で状況と関係を不可逆に変化させる。"; values["ペーシングと中だるみ対策"]="各話に部分決着と次の具体的問題を置き、同型の障害を連続させない。"
        title="ストーリーバイブル草案" if stage=="bible" else "全体プロット草案"
        sources=f"- 参照CONCEPT: `{session['concept_path']}`（v{session['concept_version']:03d}、固定）\n- 参照URS: `{session['urs_path']}`\n- 生成アダプター: `dummy`（実Gemini生成物ではない）"
        return f"# {title}\n\n{sources}\n\n"+"\n\n".join(f"## {h}\n\n{values[h]}" for h in headings)+"\n"

    def _validate(self, text, headings, label):
        missing=[h for h in headings if f"## {h}" not in text]
        if missing: raise KoboError(f"{label}の必須項目が不足しています: {missing}")

    def _generate(self, session, stage):
        kind=f"{stage}_draft"; existing=self._artifact(session["session_id"],kind)
        if existing: return existing
        directory=self._dir(session); revision=1; path=directory/f"{kind}.r{revision:03d}.md"; run_id=self.orchestrator._new_run_id(); headings=BIBLE_HEADINGS if stage=="bible" else PLOT_HEADINGS
        source=Path(session["concept_path"]) if stage=="bible" else Path(self._document(session["session_id"],"bible")["path"])
        if self.dummy: text=self._dummy(session,stage)
        else:
            upstream=source.read_text(encoding="utf-8"); concept=Path(session["concept_path"]).read_text(encoding="utf-8")
            task=directory/f"task-{kind}.md"; atomic_write(task,f"# {kind}生成\n\n本文を書かず、必須見出し {', '.join(headings)} を使う。確定資料を変更・補完しない。\n\n## 固定CONCEPT\n{concept}\n\n## 直接入力\n{upstream}")
            agent_id="story-architect" if stage=="bible" else "plotter"; agent=self.orchestrator.agents[agent_id]; refs={"task_path":str(task),"output_path":str(path),"model":self.orchestrator.config.models.get(agent_id,self.orchestrator.config.models.get("gemini",agent.model)),"run_id":run_id,"run_dir":str(directory),"agent_path":str(agent.path),"mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["source_mail_id"] or "none")}; self.orchestrator._adapter(agent).execute(agent,refs,path); text=path.read_text(encoding="utf-8")
        self._validate(text,headings,stage); atomic_write(path,text); timestamp=now(); agent_id="story-architect" if stage=="bible" else "plotter"
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],kind,revision,str(path),run_id,agent_id,"generated",str(source),timestamp))
        return self._artifact(session["session_id"],kind)

    def _audit(self, session, stage):
        kind=f"{stage}_audit"; existing=self._artifact(session["session_id"],kind)
        if existing:return existing
        draft=self._artifact(session["session_id"],f"{stage}_draft"); directory=self._dir(session); path=directory/f"{kind}.r001.md"; run_id=self.orchestrator._new_run_id(); axes=BIBLE_AUDIT if stage=="bible" else PLOT_AUDIT; reviewer="continuity-reviewer" if stage=="bible" else "plot-reviewer"
        body=f"# {'ストーリーバイブル' if stage=='bible' else '全体プロット'}独立監査\n\n- 監査担当: `{reviewer}`\n- 生成実行ID: `{draft['run_id']}`\n- 監査実行ID: `{run_id}`\n\n"
        body+="\n\n".join(f"## {axis}\n\n根拠: 固定上流資料と草案を独立照合。\n\n長所: 構造化され、後工程から参照できる。\n\n弱点・リスク: 未決事項の追跡が必要。\n\n改善案: 確定前に矛盾と仮定を明示する。" for axis in axes)+"\n\n## 監査結論\n\n確定可否は利用者承認に委ねる。監査だけでは確定しない。\n"
        atomic_write(path,body); timestamp=now()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],kind,1,str(path),run_id,reviewer,"completed",draft["path"],timestamp))
        return self._artifact(session["session_id"],kind)

    def _document(self, session_id, kind):
        with self.orchestrator.connection() as db: row=db.execute("SELECT * FROM story_design_documents WHERE session_id=? AND kind=?",(session_id,kind)).fetchone()
        return row

    def resume(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        try:
            if session["status"] in ("bible_generating","bible_review"):
                self._generate(session,"bible"); self._audit(session,"bible")
                with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='bible_awaiting_approval',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
                if session["source_mail_id"]:
                    mail_id=self.orchestrator.mail.send("story-architect","continuity-reviewer",f"バイブル監査完了 session_id={session['session_id']} 状態=承認待ち",parent_message_id=session["source_mail_id"])
                    with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET bible_mail_id=? WHERE session_id=?",(mail_id,session["session_id"]))
            elif session["status"] in ("plot_generating","plot_review"):
                self._generate(session,"plot"); self._audit(session,"plot")
                with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='plot_awaiting_approval',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
                if session["bible_mail_id"]:
                    mail_id=self.orchestrator.mail.send("plotter","plot-reviewer",f"プロット監査完了 session_id={session['session_id']} 状態=承認待ち",parent_message_id=session["bible_mail_id"])
                    with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET plot_mail_id=? WHERE session_id=?",(mail_id,session["session_id"]))
        except Exception as error:
            with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='failed',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
            raise
        return self.status(session["work_id"],session["session_id"])

    def status(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: artifacts=[dict(r) for r in db.execute("SELECT * FROM story_design_artifacts WHERE session_id=? ORDER BY id",(session["session_id"],))]; actions=[dict(r) for r in db.execute("SELECT * FROM story_design_actions WHERE session_id=? ORDER BY id",(session["session_id"],))]; docs=[dict(r) for r in db.execute("SELECT * FROM story_design_documents WHERE session_id=? ORDER BY id",(session["session_id"],))]
        return {"session_id":session["session_id"],"work_id":session["work_id"],"status":session["status"],"adapter":session["adapter"],"concept_path":session["concept_path"],"concept_version":session["concept_version"],"artifacts":artifacts,"actions":actions,"documents":docs,"error":session["error"]}

    def show(self,kind,work_id=None,session_id=None):
        if kind not in ("bible_draft","bible_audit","plot_draft","plot_audit","bible","plot"): raise KoboError("成果物種別が不正です")
        session=self._session(work_id,session_id); row=self._document(session["session_id"],kind) if kind in ("bible","plot") else self._artifact(session["session_id"],kind)
        if not row: raise KoboError("成果物がまだありません")
        result=dict(row); result["content"]=Path(row["path"]).read_text(encoding="utf-8"); return result

    def approve(self,stage,work_id=None,session_id=None):
        if stage not in ("bible","plot"):raise KoboError("承認段階が不正です")
        session=self._session(work_id,session_id); expected=f"{stage}_awaiting_approval"
        if session["status"]!=expected:raise KoboError("現在の状態では承認できません")
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_actions(session_id,action,stage,created_at) VALUES(?,?,?,?)",(session["session_id"],"approve",stage,now())); db.execute("UPDATE story_design_sessions SET status=?,updated_at=? WHERE session_id=?",(f"{stage}_approved",now(),session["session_id"]))
        return self.status(session["work_id"],session["session_id"])

    def _finalize(self,stage,session):
        if session["status"]!=f"{stage}_approved":raise KoboError("利用者承認後にだけ確定できます")
        if self._document(session["session_id"],stage):raise KoboError("同じセッションの二重確定を拒否しました")
        draft=self._artifact(session["session_id"],f"{stage}_draft"); label="STORY_BIBLE" if stage=="bible" else "PLOT"
        with self.orchestrator.connection() as db: version=db.execute("SELECT COALESCE(MAX(d.version),0)+1 FROM story_design_documents d JOIN story_design_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND d.kind=?",(session["work_id"],stage)).fetchone()[0]
        path=self.orchestrator.config.store/"works"/session["work_id"]/"story-design"/f"{label}.v{version:03d}.md"
        if path.exists():raise KoboError("確定成果物の上書きを拒否しました")
        header=f"# {'ストーリーバイブル' if stage=='bible' else '全体プロット'}\n\n- 版: {version}\n- 状態: 確定\n- work_id: `{session['work_id']}`\n- 参照CONCEPT: `{session['concept_path']}`（v{session['concept_version']:03d}、固定）\n"
        if stage=="plot": header+=f"- 参照バイブル: `{self._document(session['session_id'],'bible')['path']}`（固定）\n"
        atomic_write(path,header+"\n"+Path(draft["path"]).read_text(encoding="utf-8")); timestamp=now()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_documents(session_id,kind,version,path,source_path,created_at) VALUES(?,?,?,?,?,?)",(session["session_id"],stage,version,str(path),draft["path"],timestamp)); db.execute("UPDATE story_design_sessions SET status=?,updated_at=? WHERE session_id=?",("bible_final" if stage=="bible" else "completed",timestamp,session["session_id"]))
        return {"path":str(path),"version":version,"stage":stage}

    def finalize_bible(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id); result=self._finalize("bible",session); session=self._session(session["work_id"],session["session_id"])
        parent=session["source_mail_id"]
        if parent:
            mail_id=self.orchestrator.mail.send("story-architect","plotter",f"バイブル確定 work_id={session['work_id']} bible_path={result['path']}",parent_message_id=parent); result["mail_id"]=mail_id
            with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET bible_mail_id=? WHERE session_id=?",(mail_id,session["session_id"]))
        return result

    def start_plot(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="bible_final" or not self._document(session["session_id"],"bible"):raise KoboError("確定バイブルが必要です")
        with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='plot_generating',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
        return self.resume(session["work_id"],session["session_id"])

    def finalize_plot(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id); result=self._finalize("plot",session); session=self._session(session["work_id"],session["session_id"]); parent=session["bible_mail_id"]
        if parent:
            mail_id=self.orchestrator.mail.send("plotter","writer",f"全体プロット確定 work_id={session['work_id']} plot_path={result['path']} 次工程=本文設計（未実装）",parent_message_id=parent); result["mail_id"]=mail_id
        with self.orchestrator.connection() as db: db.execute("UPDATE works SET current_agent='plotter',next_agent='writer',status='pending',updated_at=? WHERE work_id=?",(now(),session["work_id"]))
        result.update({"next_agent":"writer","next_stage_implemented":False}); return result
