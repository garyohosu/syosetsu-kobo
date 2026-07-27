from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now, safe_path


CHAPTER_HEADINGS = ("章の役割", "開始状態", "終了状態", "主要な転換", "人物変化", "伏線", "章末フック", "上流整合性", "未決事項")
SCENE_HEADINGS = ("シーン一覧", "視点と時制", "シーン1", "シーン2", "シーン3", "連結とペーシング", "本文執筆制約", "未決事項")
AUDIT_AXES = ("上流設計との整合", "因果と場面目標", "人物・会話", "視点・時制", "先読み欲求", "文体・冗長", "設定・時系列", "模倣リスク")


def uid() -> str: return f"manuscript-{uuid.uuid4().hex}"


class ManuscriptManager:
    def __init__(self, orchestrator, *, dummy: bool = False):
        self.orchestrator=orchestrator; self.dummy=dummy; self.initialize()

    def initialize(self):
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS manuscript_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), story_session_id TEXT NOT NULL REFERENCES story_design_sessions(session_id), plot_document_id INTEGER NOT NULL REFERENCES story_design_documents(id), plot_path TEXT NOT NULL, bible_path TEXT NOT NULL, concept_path TEXT NOT NULL, chapter_number INTEGER NOT NULL, chapter_title TEXT NOT NULL, status TEXT NOT NULL, adapter TEXT NOT NULL, source_mail_id INTEGER, latest_mail_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT, UNIQUE(work_id,chapter_number));
            CREATE TABLE IF NOT EXISTS manuscript_artifacts(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES manuscript_sessions(session_id), kind TEXT NOT NULL, revision INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL UNIQUE, agent_id TEXT NOT NULL, source_path TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id,kind,revision));
            CREATE TABLE IF NOT EXISTS manuscript_actions(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES manuscript_sessions(session_id), action TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manuscript_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE REFERENCES manuscript_sessions(session_id), version INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, source_path TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    def _work(self, work_id=None): return self.orchestrator.get_work(work_id)

    def _session(self, work_id=None, session_id=None):
        work=self._work(work_id)
        with self.orchestrator.connection() as db:
            row=db.execute("SELECT * FROM manuscript_sessions WHERE session_id=? AND work_id=?",(session_id,work["work_id"])).fetchone() if session_id else db.execute("SELECT * FROM manuscript_sessions WHERE work_id=? ORDER BY chapter_number DESC LIMIT 1",(work["work_id"],)).fetchone()
        if not row: raise KoboError("本文制作セッションが見つかりません")
        return row

    def _latest_plot(self, work_id):
        with self.orchestrator.connection() as db:
            row=db.execute("SELECT d.id,d.session_id,d.path FROM story_design_documents d JOIN story_design_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND d.kind='plot' ORDER BY d.version DESC LIMIT 1",(work_id,)).fetchone()
            bible=db.execute("SELECT path FROM story_design_documents WHERE session_id=? AND kind='bible'",(row["session_id"],)).fetchone() if row else None
            story=db.execute("SELECT concept_path FROM story_design_sessions WHERE session_id=?",(row["session_id"],)).fetchone() if row else None
        if not row or not bible or not story: raise KoboError("確定プロットと確定バイブルが必要です")
        return row,safe_path(self.orchestrator.config.root,row["path"],must_exist=True),safe_path(self.orchestrator.config.root,bible["path"],must_exist=True),safe_path(self.orchestrator.config.root,story["concept_path"],must_exist=True)

    def _source_mail(self, work_id, plot_path):
        with self.orchestrator.mail.connection() as db:
            row=db.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id='plotter' AND recipient_id='scene-planner' AND body LIKE ? ORDER BY id DESC LIMIT 1",(f"work-{work_id}",f"%plot_path={plot_path}%")).fetchone()
        return row[0] if row else None

    def start(self, chapter_number: int, chapter_title: str | None = None, work_id=None):
        if chapter_number < 1: raise KoboError("章番号は1以上です")
        work=self._work(work_id); plot,path,bible,concept=self._latest_plot(work["work_id"]); timestamp=now(); session_id=uid(); title=(chapter_title or f"第{chapter_number}章").strip()
        if not title or len(title)>200: raise KoboError("章タイトルが不正です")
        source=self._source_mail(work["work_id"],path)
        with self.orchestrator.connection() as db:
            try: db.execute("INSERT INTO manuscript_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(session_id,work["work_id"],plot["session_id"],plot["id"],str(path),str(bible),str(concept),chapter_number,title,"designing","dummy" if self.dummy else "gemini",source,source,timestamp,timestamp,None))
            except sqlite3.IntegrityError as error: raise KoboError("この章の本文制作セッションは既に存在します") from error
        return self.resume(work["work_id"],session_id)

    def _dir(self, session): return self.orchestrator.config.store/"works"/session["work_id"]/"manuscript"/session["session_id"]

    def _artifact(self, session_id, kind):
        with self.orchestrator.connection() as db: return db.execute("SELECT * FROM manuscript_artifacts WHERE session_id=? AND kind=? ORDER BY revision DESC LIMIT 1",(session_id,kind)).fetchone()

    def _save(self, session, kind, text, agent_id, source_path, revision=1):
        existing=self._artifact(session["session_id"],kind)
        if existing:return existing
        directory=self._dir(session); path=directory/f"{kind}.r{revision:03d}.md"; run_id=self.orchestrator._new_run_id(); atomic_write(path,text)
        with self.orchestrator.connection() as db: db.execute("INSERT INTO manuscript_artifacts(session_id,kind,revision,path,run_id,agent_id,source_path,created_at) VALUES(?,?,?,?,?,?,?,?)",(session["session_id"],kind,revision,str(path),run_id,agent_id,str(source_path),now()))
        return self._artifact(session["session_id"],kind)

    def _dummy_design(self, session, kind):
        headings=CHAPTER_HEADINGS if kind=="chapter_design" else SCENE_HEADINGS; title="章設計" if kind=="chapter_design" else "シーン設計"
        return f"# {session['chapter_title']} {title}\n\n- 固定プロット: `{session['plot_path']}`\n- 固定バイブル: `{session['bible_path']}`\n- 生成アダプター: `dummy`（実生成物ではない）\n\n"+"\n\n".join(f"## {h}\n\n{h}を固定上流資料に整合する検証可能な形で定義する。" for h in headings)+"\n"

    def _execute_gemini(self, session, kind, agent_id, task_text, source_path):
        existing=self._artifact(session["session_id"],kind)
        if existing:return existing
        directory=self._dir(session); path=directory/f"{kind}.r001.md"; task=directory/f"task-{kind}.md"; run_id=self.orchestrator._new_run_id(); atomic_write(task,task_text); agent=self.orchestrator.agents[agent_id]
        refs={"task_path":str(task),"output_path":str(path),"model":self.orchestrator.config.models.get(agent_id,self.orchestrator.config.models.get("gemini",agent.model)),"run_id":run_id,"run_dir":str(directory),"agent_path":str(agent.path),"mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["latest_mail_id"] or "none")}
        self.orchestrator._adapter(agent).execute(agent,refs,path); text=path.read_text(encoding="utf-8")
        with self.orchestrator.connection() as db: db.execute("INSERT INTO manuscript_artifacts(session_id,kind,revision,path,run_id,agent_id,source_path,created_at) VALUES(?,?,?,?,?,?,?,?)",(session["session_id"],kind,1,str(path),run_id,agent_id,str(source_path),now()))
        return self._artifact(session["session_id"],kind)

    def _design(self, session, kind):
        if self._artifact(session["session_id"],kind): return
        headings=CHAPTER_HEADINGS if kind=="chapter_design" else SCENE_HEADINGS
        if self.dummy: text=self._dummy_design(session,kind); self._save(session,kind,text,"scene-planner",session["plot_path"]); return
        upstream=Path(session["plot_path"]).read_text(encoding="utf-8")+"\n\n"+Path(session["bible_path"]).read_text(encoding="utf-8")
        task=f"# {session['chapter_title']}の{kind}生成\n\n小説本文は書かない。必須見出し: {', '.join(headings)}。確定資料を変更せず、未決は未決のまま記す。\n\n## 固定上流資料\n{upstream}"
        result=self._execute_gemini(session,kind,"scene-planner",task,session["plot_path"]); text=Path(result["path"]).read_text(encoding="utf-8"); missing=[h for h in headings if f"## {h}" not in text]
        if missing: raise KoboError(f"{kind}の必須項目が不足しています: {missing}")

    def _prose(self, session, revision=False):
        kind="revision" if revision else "draft"
        if self._artifact(session["session_id"],kind):return
        design=Path(self._artifact(session["session_id"],"scene_design")["path"]).read_text(encoding="utf-8")
        if revision:
            prior=Path(self._artifact(session["session_id"],"draft")["path"]).read_text(encoding="utf-8"); audit=Path(self._artifact(session["session_id"],"audit")["path"]).read_text(encoding="utf-8")
            task=f"# 部分改稿\n\n監査で指定された箇所だけを直し、全文を完成稿候補として返す。未指摘の癖・勢い・曖昧さを均さない。Markdown見出し `# {session['chapter_title']}` と `## 本文` を必須とする。\n\n## シーン設計\n{design}\n\n## 初稿\n{prior}\n\n## 監査\n{audit}"
        else:
            task=f"# 場面単位の本文初稿\n\n固定設計に従い小説本文を書く。設定不足を黙って確定しない。Markdown見出し `# {session['chapter_title']}` と `## 本文`、末尾に `## 未解決事項` を必須とする。\n\n## シーン設計\n{design}"
        if self.dummy:
            label="改稿" if revision else "初稿"; text=f"# {session['chapter_title']}\n\n## 本文\n\nこれは{label}工程のダミー本文であり、実際のGemini生成小説ではない。場面目標、対立、転換、結果を順に確認できる。\n\n## 未解決事項\n\nなし（ダミー検証用）。\n"; self._save(session,kind,text,"writer",self._artifact(session["session_id"],"audit")["path"] if revision else self._artifact(session["session_id"],"scene_design")["path"]); return
        result=self._execute_gemini(session,kind,"writer",task,self._artifact(session["session_id"],"audit")["path"] if revision else self._artifact(session["session_id"],"scene_design")["path"]); text=Path(result["path"]).read_text(encoding="utf-8")
        if "## 本文" not in text: raise KoboError("本文出力契約を満たしていません")

    def _audit(self, session, recheck=False):
        kind="reaudit" if recheck else "audit"
        if self._artifact(session["session_id"],kind):return
        target=self._artifact(session["session_id"],"revision" if recheck else "draft"); run_id=self.orchestrator._new_run_id()
        if not self.dummy:
            prose=Path(target["path"]).read_text(encoding="utf-8"); design=Path(self._artifact(session["session_id"],"scene_design")["path"]).read_text(encoding="utf-8"); plot=Path(session["plot_path"]).read_text(encoding="utf-8")
            task=f"# {'差分再監査' if recheck else '本文独立監査'}\n\n本文を書き換えず、必須見出し {', '.join(AUDIT_AXES)} と `## 結論` を使う。各観点に対象箇所、根拠、判定、限定的な改稿指示を記す。\n\n## 固定プロット\n{plot}\n\n## シーン設計\n{design}\n\n## 対象本文\n{prose}"
            result=self._execute_gemini(session,kind,"prose-reviewer",task,target["path"]); text=Path(result["path"]).read_text(encoding="utf-8"); missing=[h for h in AUDIT_AXES if f"## {h}" not in text]
            if missing or "## 結論" not in text: raise KoboError(f"本文監査の必須項目が不足しています: {missing}")
            return
        body=f"# {'差分再監査' if recheck else '本文独立監査'}\n\n- 監査担当: `critic`\n- 対象実行ID: `{target['run_id']}`\n- 監査実行ID: `{run_id}`\n\n"+"\n\n".join(f"## {axis}\n\n対象箇所: 本文全体（ダミー検証）。\n\n根拠: 固定プロット・章設計・シーン設計と対象本文を独立照合。\n\n判定: 重大な矛盾なし。\n\n改稿指示: 読み味を損なわない範囲で対象箇所だけを明確化する。" for axis in AUDIT_AXES)+"\n\n## 結論\n\n利用者承認待ち。監査だけで確定しない。\n"
        directory=self._dir(session); path=directory/f"{kind}.r001.md"; atomic_write(path,body)
        with self.orchestrator.connection() as db: db.execute("INSERT INTO manuscript_artifacts(session_id,kind,revision,path,run_id,agent_id,source_path,created_at) VALUES(?,?,?,?,?,?,?,?)",(session["session_id"],kind,1,str(path),run_id,"prose-reviewer",target["path"],now()))

    def _mail(self, session, sender, recipient, body):
        parent=session["latest_mail_id"]
        if not parent:return
        mail_id=self.orchestrator.mail.send(sender,recipient,body,parent_message_id=parent)
        with self.orchestrator.connection() as db: db.execute("UPDATE manuscript_sessions SET latest_mail_id=?,updated_at=? WHERE session_id=?",(mail_id,now(),session["session_id"]))

    def resume(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        try:
            if session["status"]=="designing":
                self._design(session,"chapter_design"); self._design(session,"scene_design"); self._mail(session,"scene-planner","writer",f"章・シーン設計完了 session_id={session['session_id']}"); self._set(session,"drafting")
            session=self._session(session["work_id"],session["session_id"])
            if session["status"]=="drafting": self._prose(session); self._mail(session,"writer","prose-reviewer",f"本文初稿完了 session_id={session['session_id']}"); self._set(session,"auditing")
            session=self._session(session["work_id"],session["session_id"])
            if session["status"]=="auditing": self._audit(session); self._mail(session,"prose-reviewer","writer",f"独立監査完了 session_id={session['session_id']}"); self._set(session,"revising")
            session=self._session(session["work_id"],session["session_id"])
            if session["status"]=="revising": self._prose(session,True); self._mail(session,"writer","prose-reviewer",f"部分改稿完了 session_id={session['session_id']}"); self._set(session,"reauditing")
            session=self._session(session["work_id"],session["session_id"])
            if session["status"]=="reauditing": self._audit(session,True); self._mail(session,"prose-reviewer","writer",f"差分再監査完了 session_id={session['session_id']} 状態=承認待ち"); self._set(session,"awaiting_approval")
        except Exception as error:
            with self.orchestrator.connection() as db: db.execute("UPDATE manuscript_sessions SET status='failed',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
            raise
        return self.status(session["work_id"],session["session_id"])

    def _set(self, session, status):
        with self.orchestrator.connection() as db: db.execute("UPDATE manuscript_sessions SET status=?,updated_at=? WHERE session_id=?",(status,now(),session["session_id"]))

    def status(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: artifacts=[dict(r) for r in db.execute("SELECT * FROM manuscript_artifacts WHERE session_id=? ORDER BY id",(session["session_id"],))]; actions=[dict(r) for r in db.execute("SELECT * FROM manuscript_actions WHERE session_id=? ORDER BY id",(session["session_id"],))]; document=db.execute("SELECT * FROM manuscript_documents WHERE session_id=?",(session["session_id"],)).fetchone()
        return {"session_id":session["session_id"],"work_id":session["work_id"],"chapter_number":session["chapter_number"],"chapter_title":session["chapter_title"],"status":session["status"],"adapter":session["adapter"],"plot_path":session["plot_path"],"artifacts":artifacts,"actions":actions,"document":dict(document) if document else None,"error":session["error"]}

    def show(self, kind, work_id=None, session_id=None):
        allowed=("chapter_design","scene_design","draft","audit","revision","reaudit","final")
        if kind not in allowed: raise KoboError("成果物種別が不正です")
        session=self._session(work_id,session_id)
        if kind=="final":
            with self.orchestrator.connection() as db: row=db.execute("SELECT * FROM manuscript_documents WHERE session_id=?",(session["session_id"],)).fetchone()
        else: row=self._artifact(session["session_id"],kind)
        if not row: raise KoboError("成果物がまだありません")
        result=dict(row); result["content"]=Path(row["path"]).read_text(encoding="utf-8"); return result

    def approve(self, work_id=None, session_id=None, reason=None):
        session=self._session(work_id,session_id)
        if session["status"]!="awaiting_approval":raise KoboError("現在の状態では承認できません")
        with self.orchestrator.connection() as db: db.execute("INSERT INTO manuscript_actions(session_id,action,reason,created_at) VALUES(?,?,?,?)",(session["session_id"],"approve",reason,now())); db.execute("UPDATE manuscript_sessions SET status='approved',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
        return self.status(session["work_id"],session["session_id"])

    def finalize(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="approved":raise KoboError("利用者承認後にだけ確定できます")
        with self.orchestrator.connection() as db:
            if db.execute("SELECT 1 FROM manuscript_documents WHERE session_id=?",(session["session_id"],)).fetchone(): raise KoboError("同じセッションの二重確定を拒否しました")
            version=db.execute("SELECT COALESCE(MAX(d.version),0)+1 FROM manuscript_documents d JOIN manuscript_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND s.chapter_number=?",(session["work_id"],session["chapter_number"])).fetchone()[0]
        source=self._artifact(session["session_id"],"revision"); path=self.orchestrator.config.store/"works"/session["work_id"]/"manuscript"/f"CHAPTER-{session['chapter_number']:03d}.v{version:03d}.md"
        if path.exists():raise KoboError("確定本文の上書きを拒否しました")
        header=f"# {session['chapter_title']}\n\n- 版: {version}\n- 状態: 確定\n- 参照プロット: `{session['plot_path']}`（固定）\n- 参照バイブル: `{session['bible_path']}`（固定）\n- 承認単位: 章\n\n"
        atomic_write(path,header+Path(source["path"]).read_text(encoding="utf-8")); timestamp=now()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO manuscript_documents(session_id,version,path,source_path,created_at) VALUES(?,?,?,?,?)",(session["session_id"],version,str(path),source["path"],timestamp)); db.execute("UPDATE manuscript_sessions SET status='completed',updated_at=? WHERE session_id=?",(timestamp,session["session_id"])); db.execute("UPDATE works SET current_agent='writer',next_agent=NULL,status='completed',updated_at=? WHERE work_id=?",(timestamp,session["work_id"]))
        return {"path":str(path),"version":version,"chapter_number":session["chapter_number"],"status":"completed","next_stage_implemented":False}
