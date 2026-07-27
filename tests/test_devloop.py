import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kobo.devloop import DevLoop,DevLoopConfig,default_runner,resolve_command
from kobo.orchestrator import KoboError

class DevLoopTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); (self.root/"instructions").mkdir(); self.cfg=self.root/"devloop.json"; self.cfg.write_text(json.dumps({"instructions":"instructions","database":"state.db","implement":["implement","{instruction_path}","{result_path}","{review_path}"],"review":["review","{review_path}"],"generate_next":["next","{next_instruction_path}"],"tests":[],"max_cycles":3}),encoding="utf-8")
    def tearDown(self):self.temp.cleanup()
    def test_detects_unpaired_instruction_and_dry_run_is_safe(self):
        (self.root/"instructions/instruction-20260727-1.md").write_text("task",encoding="utf-8"); loop=DevLoop(DevLoopConfig.load(self.cfg)); result=loop.once(); self.assertEqual(result["status"],"planned"); self.assertIn("instruction-20260727-1.md",result["implement_command"][1])
    def test_result_pair_is_not_repeated(self):
        directory=self.root/"instructions"; (directory/"instruction-20260727-1.md").write_text("task",encoding="utf-8"); (directory/"result-20260727-1.md").write_text("done",encoding="utf-8"); self.assertEqual(DevLoop(DevLoopConfig.load(self.cfg)).once()["status"],"idle")
    def test_unknown_placeholder_is_rejected(self):
        data=json.loads(self.cfg.read_text()); data["implement"]=["agent","{secret}"]; self.cfg.write_text(json.dumps(data)); (self.root/"instructions/instruction-20260727-2.md").write_text("task");
        with self.assertRaises(Exception):DevLoop(DevLoopConfig.load(self.cfg)).once()

    def test_execute_repairs_then_passes_and_generates_next(self):
        instruction=self.root/"instructions/instruction-20260727-1.md"; instruction.write_text("task",encoding="utf-8"); reviews=iter(("revise","pass"))
        class Result:
            returncode=0; stdout="ok"; stderr=""
        def runner(command,**kwargs):
            if command[0]=="implement":Path(command[2]).write_text("result",encoding="utf-8")
            elif command[0]=="review":Path(command[1]).write_text(json.dumps({"verdict":next(reviews),"reason":"check"}),encoding="utf-8")
            elif command[0]=="next":Path(command[1]).write_text("next task",encoding="utf-8")
            return Result()
        result=DevLoop(DevLoopConfig.load(self.cfg),runner).run(True,False,1); self.assertEqual(result["results"][0]["attempts"],2); self.assertTrue((self.root/"instructions/instruction-20260727-2.md").is_file())

    def test_cycle_limit_is_enforced(self):
        with self.assertRaises(Exception):DevLoop(DevLoopConfig.load(self.cfg)).run(False,False,4)

class ResolveCommandTest(unittest.TestCase):
    def test_windows_cmd_shim_is_resolved_to_full_path(self):
        with patch("kobo.devloop.shutil.which",return_value=r"C:\Users\garyo\AppData\Roaming\npm\codex.cmd") as which:
            resolved=resolve_command(["codex","exec","--sandbox","workspace-write","task.md"])
        which.assert_called_once_with("codex")
        self.assertEqual(resolved,[r"C:\Users\garyo\AppData\Roaming\npm\codex.cmd","exec","--sandbox","workspace-write","task.md"])
    def test_plain_executable_is_resolved(self):
        with patch("kobo.devloop.shutil.which",return_value="/usr/bin/git"):
            self.assertEqual(resolve_command(["git","status"]),["/usr/bin/git","status"])
    def test_absolute_path_command_is_passed_through_which(self):
        with patch("kobo.devloop.shutil.which",return_value="/opt/tools/claude") as which:
            resolved=resolve_command(["/opt/tools/claude","exec"])
        which.assert_called_once_with("/opt/tools/claude"); self.assertEqual(resolved,["/opt/tools/claude","exec"])
    def test_missing_executable_raises_clear_error_without_leaking_winerror(self):
        with patch("kobo.devloop.shutil.which",return_value=None):
            with self.assertRaises(KoboError) as ctx:resolve_command(["gemini","exec"])
        message=str(ctx.exception)
        self.assertIn("gemini",message); self.assertIn("platform=",message); self.assertNotIn("WinError",message)
    def test_empty_command_is_returned_unchanged(self):
        self.assertEqual(resolve_command([]),[])
    def test_default_runner_preserves_existing_arguments_and_calls_subprocess_with_resolved_path(self):
        class Result:
            returncode=0; stdout="ok"; stderr=""
        captured={}
        def fake_run(command,**kwargs):
            captured["command"]=command; captured["kwargs"]=kwargs; return Result()
        with patch("kobo.devloop.shutil.which",return_value="/usr/local/bin/codex"), patch("kobo.devloop.subprocess.run",side_effect=fake_run):
            result=default_runner(["codex","exec","task.md"],cwd="/repo",text=True,capture_output=True,timeout=1800,shell=False,check=False,input="hello",env={"A":"1"})
        self.assertIs(result.__class__,Result)
        self.assertEqual(captured["command"],["/usr/local/bin/codex","exec","task.md"])
        self.assertEqual(captured["kwargs"],{"cwd":"/repo","text":True,"capture_output":True,"timeout":1800,"shell":False,"check":False,"input":"hello","env":{"A":"1"}})

class RetryReuseTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); (self.root/"instructions").mkdir(); self.cfg=self.root/"devloop.json"
        self.cfg.write_text(json.dumps({"instructions":"instructions","database":"state.db","implement":["implement","{instruction_path}","{result_path}","{review_path}"],"review":["review","{review_path}"],"generate_next":["next","{next_instruction_path}"],"tests":[],"max_cycles":3}),encoding="utf-8")
        self.instruction=self.root/"instructions/instruction-20260727-1.md"; self.instruction.write_text("task",encoding="utf-8")
        self.item={"instruction":str(self.instruction),"result":str(self.root/"instructions/result-20260727-1.md")}
    def tearDown(self):self.temp.cleanup()
    def _insert_job(self,loop,job_id,status,error=None):
        with loop._db() as db:db.execute("INSERT INTO dev_jobs VALUES(?,?,?,?,?,?,?,?)",(job_id,"instruction-20260727-1.md","result-20260727-1.md",status,1,error,"2026-01-01T00:00:00+00:00","2026-01-01T00:00:00+00:00"));db.commit()
    def _passing_runner(self):
        class Result:
            returncode=0; stdout="ok"; stderr=""
        def runner(command,**kwargs):
            if command[0]=="implement":Path(command[2]).write_text("result",encoding="utf-8")
            elif command[0]=="review":Path(command[1]).write_text(json.dumps({"verdict":"pass","reason":"ok"}),encoding="utf-8")
            elif command[0]=="next":Path(command[1]).write_text("next",encoding="utf-8")
            return Result()
        return runner
    def test_retrying_blocked_job_does_not_violate_unique_constraint(self):
        loop=DevLoop(DevLoopConfig.load(self.cfg),self._passing_runner()); self._insert_job(loop,"dev-existing","blocked","前回のエラー")
        result=loop.once(True,False,True); self.assertEqual(result["status"],"passed")
    def test_existing_job_id_is_preserved_on_retry(self):
        loop=DevLoop(DevLoopConfig.load(self.cfg),self._passing_runner()); self._insert_job(loop,"dev-existing","blocked","前回のエラー")
        result=loop.once(True,False,True); self.assertEqual(result["job_id"],"dev-existing")
        with loop._db() as db:rows=db.execute("SELECT job_id FROM dev_jobs").fetchall()
        self.assertEqual(rows,[("dev-existing",)])
    def test_blocked_job_is_reset_to_running_state_before_retry(self):
        loop=DevLoop(DevLoopConfig.load(self.cfg),self._passing_runner()); self._insert_job(loop,"dev-existing","blocked","前回のエラー")
        job=loop._claim_job(self.item); self.assertEqual(job,"dev-existing")
        with loop._db() as db:row=db.execute("SELECT status,error,round FROM dev_jobs WHERE job_id=?",(job,)).fetchone()
        self.assertEqual(row,("running",None,1))
    def test_new_instruction_creates_new_job_row(self):
        loop=DevLoop(DevLoopConfig.load(self.cfg),self._passing_runner())
        job=loop._claim_job(self.item)
        with loop._db() as db:rows=db.execute("SELECT job_id,status FROM dev_jobs").fetchall()
        self.assertEqual(rows,[(job,"running")])
    def test_non_terminal_existing_job_is_not_duplicated(self):
        loop=DevLoop(DevLoopConfig.load(self.cfg),self._passing_runner()); self._insert_job(loop,"dev-running","running")
        with self.assertRaises(Exception):loop._claim_job(self.item)
        with loop._db() as db:rows=db.execute("SELECT job_id FROM dev_jobs").fetchall()
        self.assertEqual(rows,[("dev-running",)])
    def test_stopped_job_is_not_auto_retried(self):
        loop=DevLoop(DevLoopConfig.load(self.cfg),self._passing_runner()); self._insert_job(loop,"dev-stopped","stopped","仕様判断が必要")
        with self.assertRaises(Exception):loop._claim_job(self.item)
        with loop._db() as db:row=db.execute("SELECT status FROM dev_jobs WHERE job_id=?",("dev-stopped",)).fetchone()
        self.assertEqual(row,("stopped",))
    def test_retry_success_finalizes_result_and_status(self):
        loop=DevLoop(DevLoopConfig.load(self.cfg),self._passing_runner()); self._insert_job(loop,"dev-existing","blocked","前回のエラー")
        result=loop.once(True,False,True); self.assertEqual(result["status"],"passed"); self.assertTrue(Path(result["result"]).is_file())
        with loop._db() as db:row=db.execute("SELECT status,error FROM dev_jobs WHERE job_id=?",(result["job_id"],)).fetchone()
        self.assertEqual(row,("passed",None))

if __name__=="__main__":unittest.main()
