from __future__ import annotations

import json, os, platform, re, shutil, sqlite3, subprocess, tempfile, time, uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now, safe_path

NAME=re.compile(r"^instruction-(\d{8})-(\d+)\.md$")
ALLOWED={"instruction_path","result_path","review_path","test_report_path","diff_path","next_instruction_path","root","job_id","attempt"}

def resolve_command(command:list[str])->list[str]:
    """コマンド先頭要素をPATH（Windowsでは PATHEXT を含む）上の実行可能フルパスへ解決する。絶対パス・相対パス指定はshutil.whichがそのまま検証する。codex/claude/gemini/grok等どの外部CLIにも同様に適用される共通処理。"""
    if not command:return command
    name=command[0]; resolved=shutil.which(name)
    if not resolved:raise KoboError(f"実行ファイルを解決できませんでした: command={name} platform={platform.system()} reason=PATH上に実行可能ファイルが見つかりません")
    return [resolved,*command[1:]]

def default_runner(command,**kwargs):
    """外部コマンドを実行する。出力の復号は必ずUTF-8で行う。

    text=Trueだけを指定するとPythonはロケール既定エンコーディングで復号する。日本語Windowsではcp932になり、
    git diffやAI CLIが出力する日本語（UTF-8）を復号できずreader threadがUnicodeDecodeErrorで落ちる。
    その場合returncodeは0のまま、stdoutだけが空文字列として返るため、差分やテスト結果が無言で失われる。
    encoding/errorsを明示して復号を固定し、未知のバイトは例外にせず置換する。呼び出し側の明示指定は尊重する。"""
    kwargs.setdefault("encoding","utf-8"); kwargs.setdefault("errors","replace")
    return subprocess.run(resolve_command(command),**kwargs)

@dataclass(frozen=True)
class DevLoopConfig:
    root:Path; instructions:Path; database:Path; implement:list[str]; review:list[str]; generate_next:list[str]; tests:list[list[str]]; max_repairs:int=2; max_cycles:int=3; max_ai_calls:int=12; max_cost_units:float=12; timeout:int=1800; max_elapsed:int=7200
    @classmethod
    def load(cls,path:Path):
        source=path.resolve(); data=json.loads(source.read_text(encoding="utf-8")); root=source.parent
        def p(v):return (root/v).resolve()
        return cls(root,p(data.get("instructions","instructions")),p(data.get("database",".kobo/devloop.db")),data.get("implement",[]),data.get("review",[]),data.get("generate_next",[]),data.get("tests",[["python","-m","unittest","discover","-v"]]),int(data.get("max_repairs",2)),int(data.get("max_cycles",3)),int(data.get("max_ai_calls",12)),float(data.get("max_cost_units",12)),int(data.get("timeout",1800)),int(data.get("max_elapsed",7200)))

class DevLoop:
    def __init__(self,config,runner=default_runner):self.config=config; self.runner=runner; self.initialize()
    def _db(self):return closing(sqlite3.connect(self.config.database))
    def initialize(self):
        self.config.database.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS dev_jobs(job_id TEXT PRIMARY KEY,instruction TEXT UNIQUE NOT NULL,result TEXT NOT NULL,status TEXT NOT NULL,round INTEGER NOT NULL,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS dev_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,attempt INTEGER NOT NULL,phase TEXT NOT NULL,status TEXT NOT NULL,detail TEXT,created_at TEXT NOT NULL);""");db.commit()
    def discover(self):
        results={p.name for p in self.config.instructions.glob("result-*.md")}
        with self._db() as db:known={r[0] for r in db.execute("SELECT instruction FROM dev_jobs WHERE status NOT IN ('blocked','stopped')")}
        found=[]
        for path in sorted(self.config.instructions.glob("instruction-*.md")):
            m=NAME.fullmatch(path.name)
            if m and f"result-{m.group(1)}-{m.group(2)}.md" not in results and path.name not in known:found.append({"instruction":str(path.resolve()),"result":str((self.config.instructions/f'result-{m.group(1)}-{m.group(2)}.md').resolve())})
        return found
    def status(self):
        with self._db() as db:db.row_factory=sqlite3.Row; jobs=[dict(r) for r in db.execute("SELECT * FROM dev_jobs ORDER BY created_at")]; attempts=[dict(r) for r in db.execute("SELECT * FROM dev_attempts ORDER BY id")]
        return {"pending":self.discover(),"jobs":jobs,"attempts":attempts}
    def _command(self,template,refs,label):
        if not template:raise KoboError(f"{label}コマンドが未設定です")
        command=[]
        for part in template:
            fields=set(re.findall(r"\{([^{}]+)\}",part))
            if not fields<=ALLOWED:raise KoboError(f"{label}コマンドに未知の参照があります: {fields-ALLOWED}")
            command.append(part.format(**refs))
        return command
    def _run(self,command,**runner_options):
        options={"cwd":self.config.root,"text":True,"capture_output":True,"timeout":self.config.timeout,"shell":False,"check":False}
        options.update(runner_options)
        done=self.runner(command,**options)
        if done.returncode:raise KoboError(f"コマンド失敗 exit={done.returncode}")
        return (done.stdout or "")+(done.stderr or "")
    def _build_review_patch(self):
        """HEAD対作業ツリーの完全なレビュー差分を実インデックスへ触れずに作る。"""
        status=self._run(["git","-c","core.quotePath=false","status","--porcelain"])
        index_dir=self.config.database.parent
        index_dir.mkdir(parents=True,exist_ok=True)
        fd,index_name=tempfile.mkstemp(prefix="review-index-",dir=index_dir)
        os.close(fd)
        index_path=Path(index_name)
        try:
            index_path.unlink()
        except FileNotFoundError:
            pass
        env=os.environ.copy()
        env["GIT_INDEX_FILE"]=str(index_path)
        try:
            self._run(["git","read-tree","HEAD"],env=env)
            self._run(["git","add","-A","-N","--","."],env=env)
            patch=self._run(["git","-c","core.quotePath=false","diff","--binary","--","."],env=env)
            if status.strip() and not patch:
                raise KoboError("作業ツリーに変更があるのにレビュー用パッチが空です")
            untracked=[line[3:] for line in status.splitlines() if line.startswith("?? ")]
            normalized=patch.replace("\\","/")
            for path in untracked:
                path=path.replace("\\","/")
                if f"diff --git a/{path} b/{path}" not in normalized:
                    raise KoboError(f"未追跡ファイルがレビュー用パッチに含まれていません: {path}")
            return patch
        finally:
            for path in (index_path, Path(str(index_path)+".lock")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
    def _record(self,job,attempt,phase,status,detail=None):
        with self._db() as db:db.execute("INSERT INTO dev_attempts(job_id,attempt,phase,status,detail,created_at) VALUES(?,?,?,?,?,?)",(job,attempt,phase,status,(detail or "")[:1000],now()));db.commit()
    def _claim_job(self,item):
        name=Path(item["instruction"]).name; result=Path(item["result"]).name; stamp=now()
        with self._db() as db:
            row=db.execute("SELECT job_id,status FROM dev_jobs WHERE instruction=?",(name,)).fetchone()
            if row is None:
                job=f"dev-{uuid.uuid4().hex}"
                db.execute("INSERT INTO dev_jobs VALUES(?,?,?,?,?,?,?,?)",(job,name,result,"running",1,None,stamp,stamp));db.commit();return job
            job,status=row
            if status=="blocked":
                db.execute("UPDATE dev_jobs SET status='running',round=1,error=NULL,updated_at=? WHERE job_id=?",(stamp,job));db.commit();return job
            if status=="stopped":raise KoboError(f"停止済みジョブのため自動再試行しません: instruction={name}")
            raise KoboError(f"既に処理中または完了済みのジョブが存在します: instruction={name} status={status}")
    def _refs(self,item,job,attempt):
        run=self.config.database.parent/"devloop"/job; run.mkdir(parents=True,exist_ok=True); instruction=Path(item["instruction"]); m=NAME.fullmatch(instruction.name); next_number=max([int(x.group(2)) for p in self.config.instructions.glob("instruction-*.md") if (x:=NAME.fullmatch(p.name))] or [0])+1
        return {"instruction_path":str(instruction),"result_path":item["result"],"review_path":str(run/f"review-{attempt}.json"),"test_report_path":str(run/f"tests-{attempt}.txt"),"diff_path":str(run/f"diff-{attempt}.patch"),"next_instruction_path":str(self.config.instructions/f"instruction-{m.group(1)}-{next_number}.md"),"root":str(self.config.root),"job_id":job,"attempt":str(attempt)}
    def once(self,execute=False,publish=False,generate_next=True,deadline=None,budget=None):
        pending=self.discover()
        if not pending:return {"status":"idle"}
        item=pending[0]; safe_path(self.config.root,item["instruction"],must_exist=True)
        if not execute:
            job=f"dev-{uuid.uuid4().hex}"; refs=self._refs(item,job,1)
            return {"job_id":job,"status":"planned","implement_command":self._command(self.config.implement,refs,"実装AI"),"review_command":self._command(self.config.review,refs,"レビューAI")}
        if self._run(["git","status","--porcelain"]).strip():raise KoboError("実行開始時の作業ツリーがクリーンではありません")
        job=self._claim_job(item)
        calls=0
        try:
            self._run(["git","pull","--ff-only"])
            for attempt in range(1,self.config.max_repairs+2):
                if deadline and time.monotonic()>deadline:raise KoboError("実行時間上限に達しました")
                refs=self._refs(item,job,attempt); calls+=1
                if budget is not None and calls>budget:raise KoboError("AI呼出上限に達しました")
                self._run(self._command(self.config.implement,refs,"実装AI"));self._record(job,attempt,"implement","passed")
                reports=[]
                for command in self.config.tests:reports.append(self._run(command))
                atomic_write(Path(refs["test_report_path"]),"\n".join(reports));atomic_write(Path(refs["diff_path"]),self._build_review_patch());self._run(["git","diff","--check"])
                calls+=1; self._run(self._command(self.config.review,refs,"レビューAI")); review_path=Path(refs["review_path"])
                if not review_path.is_file():raise KoboError("レビューAIが判定JSONを作成しませんでした")
                verdict=json.loads(review_path.read_text(encoding="utf-8")); decision=verdict.get("verdict");self._record(job,attempt,"review",decision,verdict.get("reason"))
                if decision=="pass":break
                if decision=="stop":raise KoboError("レビューが仕様判断を要求して停止しました")
                if decision!="revise" or attempt>self.config.max_repairs:raise KoboError("修正回数上限に達しました")
            result=Path(item["result"])
            if not result.is_file():raise KoboError("resultが作成されていません")
            if generate_next and self.config.generate_next:
                calls+=1; self._run(self._command(self.config.generate_next,refs,"次指示生成AI")); next_path=Path(refs["next_instruction_path"])
                if not next_path.is_file():raise KoboError("次のinstructionが生成されませんでした")
            if publish:self._run(["git","add","-A"]);self._run(["git","commit","-m",f"devloop: {Path(item['instruction']).stem}"]);self._run(["git","push","origin","HEAD"])
            status="published" if publish else "passed"
        except Exception as error:
            with self._db() as db:db.execute("UPDATE dev_jobs SET status='blocked',error=?,updated_at=? WHERE job_id=?",(str(error),now(),job));db.commit()
            raise
        with self._db() as db:db.execute("UPDATE dev_jobs SET status=?,round=?,updated_at=? WHERE job_id=?",(status,attempt,now(),job));db.commit()
        return {"job_id":job,"status":status,"attempts":attempt,"ai_calls":calls,"result":str(result),"next_instruction":refs["next_instruction_path"] if generate_next and self.config.generate_next else None}
    def run(self,execute=False,publish=False,max_cycles=None):
        cycles=max_cycles or self.config.max_cycles
        if cycles<1 or cycles>self.config.max_cycles:raise KoboError("max-cyclesが設定上限外です")
        if not execute:return {"status":"planned","cycles":cycles,"next":self.once(False,publish)}
        deadline=time.monotonic()+self.config.max_elapsed; remaining=min(self.config.max_ai_calls,int(self.config.max_cost_units)); results=[]
        for _ in range(cycles):
            result=self.once(True,publish,True,deadline,remaining);results.append(result);remaining-=result.get("ai_calls",0)
            if result["status"]=="idle":break
        return {"status":"completed","cycles":len([r for r in results if r['status']!='idle']),"results":results,"remaining_ai_calls":remaining}
