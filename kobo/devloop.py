from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .orchestrator import KoboError, now, safe_path

NAME=re.compile(r"^instruction-(\d{8})-(\d+)\.md$")

@dataclass(frozen=True)
class DevLoopConfig:
    root: Path; instructions: Path; database: Path; implement: list[str]; review: list[str]; tests: list[list[str]]; max_rounds: int=1; timeout: int=1800
    @classmethod
    def load(cls,path:Path):
        source=path.resolve(); data=json.loads(source.read_text(encoding="utf-8")); root=source.parent
        def p(value):return (root/value).resolve()
        return cls(root,p(data.get("instructions","instructions")),p(data.get("database",".kobo/devloop.db")),data.get("implement",[]),data.get("review",[]),data.get("tests",[["python","-m","unittest","discover","-v"]]),int(data.get("max_rounds",1)),int(data.get("timeout",1800)))

class DevLoop:
    def __init__(self,config:DevLoopConfig,runner=subprocess.run):self.config=config; self.runner=runner; self.initialize()
    def initialize(self):
        self.config.database.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.config.database)) as db:db.execute("CREATE TABLE IF NOT EXISTS dev_jobs(job_id TEXT PRIMARY KEY,instruction TEXT UNIQUE NOT NULL,result TEXT NOT NULL,status TEXT NOT NULL,round INTEGER NOT NULL,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"); db.commit()
    def discover(self):
        results={p.name for p in self.config.instructions.glob("result-*.md")}; jobs=[]
        with closing(sqlite3.connect(self.config.database)) as db:known={r[0] for r in db.execute("SELECT instruction FROM dev_jobs")}
        for path in sorted(self.config.instructions.glob("instruction-*.md")):
            match=NAME.fullmatch(path.name)
            if not match:continue
            result=f"result-{match.group(1)}-{match.group(2)}.md"
            if result not in results and path.name not in known:jobs.append({"instruction":str(path.resolve()),"result":str((self.config.instructions/result).resolve())})
        return jobs
    def status(self):
        with closing(sqlite3.connect(self.config.database)) as db:db.row_factory=sqlite3.Row; rows=[dict(r) for r in db.execute("SELECT * FROM dev_jobs ORDER BY created_at")]
        return {"pending":self.discover(),"jobs":rows}
    def _command(self,template,refs):
        allowed={"instruction_path","result_path","root"}; command=[]
        for part in template:
            fields=set(re.findall(r"\{([^{}]+)\}",part))
            if not fields<=allowed:raise KoboError(f"開発コマンドに未知の参照があります: {fields-allowed}")
            command.append(part.format(**refs))
        if not command:raise KoboError("開発AIコマンドが未設定です")
        return command
    def _run(self,command):
        completed=self.runner(command,cwd=self.config.root,text=True,capture_output=True,timeout=self.config.timeout,shell=False,check=False)
        if completed.returncode:raise KoboError(f"開発ループのコマンドが失敗しました: exit={completed.returncode}")
    def once(self,execute=False,publish=False):
        pending=self.discover()
        if not pending:return {"status":"idle"}
        item=pending[0]; instruction=safe_path(self.config.root,item["instruction"],must_exist=True); result=safe_path(self.config.root,item["result"]); job=f"dev-{uuid.uuid4().hex}"; timestamp=now()
        refs={"instruction_path":str(instruction),"result_path":str(result),"root":str(self.config.root)}
        if not execute:return {"job_id":job,"status":"planned","implement_command":self._command(self.config.implement,refs)}
        with closing(sqlite3.connect(self.config.database)) as db:db.execute("INSERT INTO dev_jobs VALUES(?,?,?,?,?,?,?,?)",(job,instruction.name,result.name,"running",1,None,timestamp,timestamp)); db.commit()
        try:
            self._run(["git","pull","--ff-only"]); self._run(self._command(self.config.implement,refs))
            for command in self.config.tests:self._run(command)
            if self.config.review:self._run(self._command(self.config.review,refs))
            if not result.is_file():raise KoboError("実装AIがresultを作成しませんでした")
            self._run(["git","diff","--check"])
            if publish:self._run(["git","add","-A"]); self._run(["git","commit","-m",f"devloop: {instruction.stem}"]); self._run(["git","push","origin","HEAD"])
            status="published" if publish else "passed"
        except Exception as error:
            with closing(sqlite3.connect(self.config.database)) as db:db.execute("UPDATE dev_jobs SET status='blocked',error=?,updated_at=? WHERE job_id=?",(str(error),now(),job)); db.commit()
            raise
        with closing(sqlite3.connect(self.config.database)) as db:db.execute("UPDATE dev_jobs SET status=?,updated_at=? WHERE job_id=?",(status,now(),job)); db.commit()
        return {"job_id":job,"status":status,"result":str(result)}
