from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now, safe_path

KINDS = ("canon", "character_ledger", "timeline", "resource_ledger", "foreshadowing_ledger")
LABELS = dict(zip(KINDS, ("CANON", "CHARACTER_LEDGER", "TIMELINE", "RESOURCE_LEDGER", "FORESHADOWING_LEDGER")))
AUDIT_AXES = ("人物", "関係", "知識", "時系列", "資源", "能力", "伏線", "世界ルール")


class CanonManager:
    def __init__(self, orchestrator, *, dummy: bool = False):
        self.orchestrator = orchestrator
        self.dummy = dummy
        self.initialize()

    def initialize(self) -> None:
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS canon_sessions(
                session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), chapter_number INTEGER NOT NULL,
                chapter_title TEXT NOT NULL, manuscript_path TEXT NOT NULL, manuscript_version INTEGER NOT NULL,
                bible_path TEXT NOT NULL, bible_version INTEGER NOT NULL, plot_path TEXT NOT NULL, plot_version INTEGER NOT NULL,
                prior_canon_path TEXT, prior_canon_version INTEGER, status TEXT NOT NULL, adapter TEXT NOT NULL,
                source_mail_id INTEGER, latest_mail_id INTEGER, generation_run_id TEXT, audit_run_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT, UNIQUE(work_id, chapter_number));
            CREATE TABLE IF NOT EXISTS canon_artifacts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES canon_sessions(session_id), kind TEXT NOT NULL,
                revision INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL UNIQUE, agent_id TEXT NOT NULL,
                status TEXT NOT NULL, source_path TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id, kind, revision));
            CREATE TABLE IF NOT EXISTS canon_actions(
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES canon_sessions(session_id), action TEXT NOT NULL,
                reason TEXT, instruction_path TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS canon_documents(
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES canon_sessions(session_id), kind TEXT NOT NULL,
                version INTEGER NOT NULL, chapter_number INTEGER NOT NULL, manuscript_version INTEGER NOT NULL,
                prior_canon_version INTEGER, path TEXT NOT NULL UNIQUE, source_path TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(session_id, kind));
            """)

    def _work(self, work_id=None):
        return self.orchestrator.get_work(work_id)

    def _session(self, work_id=None, session_id=None):
        work = self._work(work_id)
        with self.orchestrator.connection() as db:
            if session_id:
                row = db.execute("SELECT * FROM canon_sessions WHERE session_id=? AND work_id=?", (session_id, work["work_id"])).fetchone()
            else:
                row = db.execute("SELECT * FROM canon_sessions WHERE work_id=? ORDER BY chapter_number DESC LIMIT 1", (work["work_id"],)).fetchone()
        if not row:
            raise KoboError("正史更新セッションが見つかりません")
        return row

    def _sources(self, work_id: str, chapter: int):
        with self.orchestrator.connection() as db:
            manuscript = db.execute("""SELECT d.path,d.version,s.chapter_title,s.latest_mail_id FROM manuscript_documents d
                JOIN manuscript_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND s.chapter_number=? AND s.status='completed'
                ORDER BY d.version DESC LIMIT 1""", (work_id, chapter)).fetchone()
            plot = db.execute("""SELECT d.path,d.version,s.session_id FROM story_design_documents d
                JOIN story_design_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND d.kind='plot' AND s.status='completed'
                ORDER BY d.version DESC LIMIT 1""", (work_id,)).fetchone()
            bible = db.execute("SELECT path,version FROM story_design_documents WHERE session_id=? AND kind='bible'", (plot["session_id"],)).fetchone() if plot else None
            prior = db.execute("""SELECT c.path,c.version FROM canon_documents c JOIN canon_sessions s ON s.session_id=c.session_id
                WHERE s.work_id=? AND s.chapter_number<? AND c.kind='canon' ORDER BY s.chapter_number DESC,c.version DESC LIMIT 1""", (work_id, chapter)).fetchone()
        if not manuscript or not plot or not bible:
            raise KoboError("確定本文・確定プロット・確定バイブルが必要です")
        for row in (manuscript, plot, bible, prior):
            if row:
                safe_path(self.orchestrator.config.root, row["path"], must_exist=True)
        return manuscript, bible, plot, prior

    def start(self, chapter_number: int, work_id=None):
        if chapter_number < 1:
            raise KoboError("章番号は1以上です")
        work = self._work(work_id); manuscript,bible,plot,prior = self._sources(work["work_id"], chapter_number)
        timestamp = now(); session_id = f"canon-{uuid.uuid4().hex}"; source_mail = manuscript["latest_mail_id"]
        values = (session_id,work["work_id"],chapter_number,manuscript["chapter_title"],manuscript["path"],manuscript["version"],bible["path"],bible["version"],plot["path"],plot["version"],prior["path"] if prior else None,prior["version"] if prior else None,"drafting","dummy" if self.dummy else "gemini",source_mail,source_mail,None,None,timestamp,timestamp,None)
        with self.orchestrator.connection() as db:
            try:
                db.execute("INSERT INTO canon_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            except sqlite3.IntegrityError as error:
                raise KoboError("この章の正史更新セッションは既に存在します") from error
        return self.resume(work["work_id"], session_id)

    def _dir(self, session):
        return self.orchestrator.config.store / "works" / session["work_id"] / "canon" / session["session_id"]

    def _artifact(self, session_id, kind, revision=None):
        query = "SELECT * FROM canon_artifacts WHERE session_id=? AND kind=?"; args = [session_id, kind]
        if revision is not None:
            query += " AND revision=?"; args.append(revision)
        query += " ORDER BY revision DESC LIMIT 1"
        with self.orchestrator.connection() as db:
            return db.execute(query, args).fetchone()

    def _next_revision(self, session_id):
        with self.orchestrator.connection() as db:
            return db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM canon_artifacts WHERE session_id=? AND kind='audit'", (session_id,)).fetchone()[0]

    def _mail(self, session, sender, recipient, body):
        if not session["latest_mail_id"]:
            return None
        mail_id = self.orchestrator.mail.send(sender, recipient, body, parent_message_id=session["latest_mail_id"])
        with self.orchestrator.connection() as db:
            db.execute("UPDATE canon_sessions SET latest_mail_id=?,updated_at=? WHERE session_id=?", (mail_id,now(),session["session_id"]))
        return mail_id

    def _generate(self, session, revision):
        directory=self._dir(session); directory.mkdir(parents=True,exist_ok=True)
        for kind in KINDS:
            if self._artifact(session["session_id"],kind,revision): continue
            path=directory/f"{kind}.r{revision:03d}.md"; run_id=self.orchestrator._new_run_id(); agent_id="canon-updater"
            if self.dummy:
                prior=session["prior_canon_path"] or "なし（第1章の空台帳）"
                text=f"# {LABELS[kind]} 草案\n\n- 生成アダプター: `dummy`（実Gemini生成物ではない）\n- 対象章: {session['chapter_number']}\n- 確定本文: `{session['manuscript_path']}` v{session['manuscript_version']}\n- 確定バイブル: `{session['bible_path']}` v{session['bible_version']}\n- 確定プロット: `{session['plot_path']}` v{session['plot_version']}\n- 直前の確定台帳: `{prior}`\n\n確定本文に明示された情報だけを抽出する。推測で補完しない。\n"
                atomic_write(path,text)
            else:
                task=directory/f"task-{kind}.r{revision:03d}.md"; atomic_write(task,f"# {LABELS[kind]}抽出\n\n本文生成は禁止。固定パスを読み、確定情報だけを抽出・構造化する。\n本文: {session['manuscript_path']}\nバイブル: {session['bible_path']}\nプロット: {session['plot_path']}\n直前台帳: {session['prior_canon_path'] or 'なし（空台帳）'}\n対象: {LABELS[kind]}")
                agent=self.orchestrator.agents[agent_id]; refs=self.orchestrator._refs(agent,run_id,directory,task,path,session["latest_mail_id"] or 0); self.orchestrator._adapter(agent).execute(agent,refs,path)
            if not path.is_file() or not path.read_text(encoding="utf-8").strip(): raise KoboError(f"{kind}の生成結果が空です")
            with self.orchestrator.connection() as db: db.execute("INSERT INTO canon_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],kind,revision,str(path),run_id,agent_id,"generated",session["manuscript_path"],now()))

    def _audit(self, session, revision):
        existing=self._artifact(session["session_id"],"audit",revision)
        if existing:return existing
        directory=self._dir(session); directory.mkdir(parents=True,exist_ok=True); path=directory/f"audit.r{revision:03d}.md"; run_id=self.orchestrator._new_run_id(); agent_id="canon-auditor"; drafts=[self._artifact(session["session_id"],k,revision) for k in KINDS]
        if self.dummy:
            body="# 正史・台帳独立監査\n\n- 監査アダプター: `dummy`（実Gemini監査ではない）\n\n"+"\n\n".join(f"## {axis}\n\n対象箇所: {drafts[0]['path']}\n根拠: 固定参照資料との照合。\n判定: 問題なし。\n深刻度: low" for axis in AXES)
        else:
            task=directory/f"task-audit.r{revision:03d}.md"; paths="\n".join(f"{LABELS[k]}: {self._artifact(session['session_id'],k,revision)['path']}" for k in KINDS); atomic_write(task,f"# 正史・台帳独立監査\n\n草案を直接書き換えず、確定資料へ独立照合する。各軸に対象箇所、根拠、判定、深刻度、`## 結論`を記録する。\n草案:\n{paths}\n本文: {session['manuscript_path']}\nバイブル: {session['bible_path']}\nプロット: {session['plot_path']}\n直前台帳: {session['prior_canon_path'] or 'なし（空台帳）'}\n監査軸: {', '.join(AXES)}"); agent=self.orchestrator.agents[agent_id]; refs=self.orchestrator._refs(agent,run_id,directory,task,path,session["latest_mail_id"] or 0); self.orchestrator._adapter(agent).execute(agent,refs,path); body=path.read_text(encoding="utf-8")
        if "## 結論" not in body: body += "\n\n## 結論\n\n利用者承認待ち。監査だけでは確定しない。\n"
        atomic_write(path,body)
        with self.orchestrator.connection() as db: db.execute("INSERT INTO canon_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],"audit",revision,str(path),run_id,agent_id,"completed",drafts[0]["path"],now())); db.execute("UPDATE canon_sessions SET generation_run_id=?,audit_run_id=?,updated_at=? WHERE session_id=?",(drafts[0]["run_id"],run_id,now(),session["session_id"]))
        return self._artifact(session["session_id"],"audit",revision)

    def resume(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        if session["status"] in ("drafting","revising","failed"):
            revision=self._next_revision(session["session_id"]) if session["status"]=="revising" else 1
            try:
                self._generate(session,revision); self._audit(session,revision); self._mail(session,"canon-updater","canon-auditor",f"正史・台帳草案生成完了 session_id={session['session_id']} 状態=監査完了")
                with self.orchestrator.connection() as db: db.execute("UPDATE canon_sessions SET status='awaiting_approval',error=NULL,updated_at=? WHERE session_id=?",(now(),session["session_id"]))
            except Exception as error:
                with self.orchestrator.connection() as db: db.execute("UPDATE canon_sessions SET status='failed',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
                raise
        return self.status(work_id,session_id)

    def status(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: artifacts=[dict(x) for x in db.execute("SELECT * FROM canon_artifacts WHERE session_id=? ORDER BY id",(session["session_id"],))]; actions=[dict(x) for x in db.execute("SELECT * FROM canon_actions WHERE session_id=? ORDER BY id",(session["session_id"],))]; documents=[dict(x) for x in db.execute("SELECT * FROM canon_documents WHERE session_id=? ORDER BY id",(session["session_id"],))]
        return {**dict(session),"artifacts":artifacts,"actions":actions,"documents":documents}

    def show(self, kind, work_id=None, session_id=None):
        if kind in ("draft","revision"): kind="canon"
        if kind not in KINDS+("audit",): raise KoboError("成果物種別が不正です")
        row=self._artifact(self._session(work_id,session_id)["session_id"],kind)
        if not row: raise KoboError("成果物がまだありません")
        result=dict(row); result["content"]=Path(row["path"]).read_text(encoding="utf-8"); return result

    def approve(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="awaiting_approval": raise KoboError("現在の状態では承認できません")
        with self.orchestrator.connection() as db: db.execute("INSERT INTO canon_actions(session_id,action,created_at) VALUES(?,?,?)",(session["session_id"],"approve",now())); db.execute("UPDATE canon_sessions SET status='approved',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
        self._mail(session,"canon-auditor","manager",f"正史・台帳監査完了 session_id={session['session_id']} 状態=承認済み"); return self.status(work_id,session_id)

    def reject(self, reason, instructions=None, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="awaiting_approval": raise KoboError("現在の状態では却下できません")
        instruction_path=str(safe_path(self.orchestrator.config.root,instructions,must_exist=True)) if instructions is not None else None
        with self.orchestrator.connection() as db: db.execute("INSERT INTO canon_actions(session_id,action,reason,instruction_path,created_at) VALUES(?,?,?,?,?)",(session["session_id"],"reject",reason,instruction_path,now())); db.execute("UPDATE canon_sessions SET status='revising',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
        return self.status(work_id,session_id)

    def finalize(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="approved": raise KoboError("利用者承認後にだけ確定できます")
        with self.orchestrator.connection() as db:
            if db.execute("SELECT 1 FROM canon_documents WHERE session_id=?",(session["session_id"],)).fetchone(): raise KoboError("同じセッションの二重確定を拒否しました")
            version=db.execute("SELECT COALESCE(MAX(c.version),0)+1 FROM canon_documents c JOIN canon_sessions s ON s.session_id=c.session_id WHERE s.work_id=?",(session["work_id"],)).fetchone()[0]
        paths=[]
        for kind in KINDS:
            source=self._artifact(session["session_id"],kind)
            if not source: raise KoboError("5種の台帳草案が揃っていません")
            path=self.o.config.store/"works"/session["work_id"]/"canon"/f"{LABELS[kind]}.v{version:03d}.md"
            if path.exists(): raise KoboError("確定成果物の上書きを拒否しました")
            prior=session["prior_canon_path"] or "なし（第1章の空台帳）"; header=f"# {LABELS[kind]}\n\n- 版: {version}\n- 状態: 確定\n- work_id: `{session['work_id']}`\n- 章: {session['chapter_number']}\n- 参照本文: `{session['manuscript_path']}`（v{session['manuscript_version']:03d}、固定）\n- 参照バイブル: `{session['bible_path']}`（v{session['bible_version']:03d}、固定）\n- 参照プロット: `{session['plot_path']}`（v{session['plot_version']:03d}、固定）\n- 参照直前台帳: `{prior}`（v{session['prior_canon_version'] or 'なし'}、固定）\n\n"; atomic_write(path,header+Path(source["path"]).read_text(encoding="utf-8")); paths.append(str(path))
        timestamp=now()
        with self.o.connection() as db:
            for kind,path in zip(KINDS,paths): db.execute("INSERT INTO canon_documents(session_id,kind,version,chapter_number,manuscript_version,prior_canon_version,path,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],kind,version,session["chapter_number"],session["manuscript_version"],session["prior_canon_version"],path,self._artifact(session["session_id"],kind)["path"],timestamp))
            db.execute("UPDATE canon_sessions SET status='completed',updated_at=? WHERE session_id=?",(timestamp,session["session_id"])); db.execute("UPDATE works SET current_agent='canon-updater',next_agent='scene-planner',status='pending',updated_at=? WHERE work_id=?",(timestamp,session["work_id"]))
        handoff=self._mail(session,"canon-updater","scene-planner",f"正史・台帳確定 work_id={session['work_id']} chapter={session['chapter_number']} 次工程=scene-planner plot_path={session['plot_path']} bible_path={session['bible_path']} canon_path={paths[0]} character_ledger_path={paths[1]} timeline_path={paths[2]} resource_ledger_path={paths[3]} foreshadowing_ledger_path={paths[4]}")
        return {"status":"completed","version":version,"paths":paths,"mail_id":handoff,"next_agent":"scene-planner","next_stage_implemented":True}
