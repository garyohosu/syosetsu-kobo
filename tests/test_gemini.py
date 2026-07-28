import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kobo.gemini import (GeminiAdapter, GeminiAuthenticationError, GeminiEmptyOutputError,
                         GeminiInvalidOutputError, GeminiNonZeroError, GeminiNotInstalled,
                         GeminiTimeoutError)


class GeminiAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.task = self.root / "task.md"; self.task.write_text("あ" * 100001, encoding="utf-8")
        self.output = self.root / "result.md"
        self.agent = SimpleNamespace(timeout=2)
        self.refs = {"task_path":str(self.task),"model":"configured-model"}

    def tearDown(self): self.temp.cleanup()

    def adapter(self, result=None, error=None):
        def runner(command, **kwargs):
            self.command = command; self.kwargs = kwargs
            if error: raise error
            return result or subprocess.CompletedProcess(command, 0, "# OK\n", "")
        return GeminiAdapter(sys.executable, runner=runner)

    def test_command_matches_real_cli_contract_and_excludes_body(self):
        adapter = self.adapter(); command = adapter.command(self.agent,self.refs)
        self.assertIn("--prompt",command); self.assertIn("--model",command); self.assertIn("--output-format",command)
        self.assertNotIn("あ"*100," ".join(command)); self.assertLess(sum(map(len,command)),1000)

    def test_long_body_is_sent_on_stdin_and_saved(self):
        adapter=self.adapter(); adapter.execute(self.agent,self.refs,self.output)
        self.assertEqual(len(self.kwargs["input"]),100001); self.assertFalse(self.kwargs["shell"])
        self.assertEqual(self.kwargs["encoding"],"utf-8"); self.assertEqual(self.kwargs["errors"],"strict")
        self.assertEqual(self.output.read_text(encoding="utf-8"),"# OK\n")

    def test_invalid_utf8_from_gemini_is_diagnostic(self):
        adapter=self.adapter(error=UnicodeDecodeError("utf-8",b"\xff",0,1,"invalid"))
        with self.assertRaises(GeminiInvalidOutputError): adapter.execute(self.agent,self.refs,self.output)

    def test_not_installed(self):
        with self.assertRaises(GeminiNotInstalled): GeminiAdapter("definitely-missing-gemini-binary").execute(self.agent,self.refs,self.output)

    def test_authentication_error(self):
        adapter=self.adapter(subprocess.CompletedProcess([],1,"","Authentication required"))
        with self.assertRaises(GeminiAuthenticationError): adapter.execute(self.agent,self.refs,self.output)

    def test_nonzero_error(self):
        adapter=self.adapter(subprocess.CompletedProcess([],7,"","ordinary failure"))
        with self.assertRaisesRegex(GeminiNonZeroError,"exit=7"): adapter.execute(self.agent,self.refs,self.output)

    def test_timeout_error(self):
        adapter=self.adapter(error=subprocess.TimeoutExpired([],2))
        with self.assertRaises(GeminiTimeoutError): adapter.execute(self.agent,self.refs,self.output)

    def test_empty_output(self):
        adapter=self.adapter(subprocess.CompletedProcess([],0,"  ",""))
        with self.assertRaises(GeminiEmptyOutputError): adapter.execute(self.agent,self.refs,self.output)

    def test_invalid_output(self):
        adapter=self.adapter(subprocess.CompletedProcess([],0,"bad\x00value",""))
        with self.assertRaises(GeminiInvalidOutputError): adapter.execute(self.agent,self.refs,self.output)

    def test_doctor_does_not_disclose_environment_or_prompt(self):
        responses=[subprocess.CompletedProcess([],0,"0.45.2\n",""),subprocess.CompletedProcess([],0,"--prompt stdin --model text json stream-json","")]
        def runner(command,**kwargs): return responses.pop(0)
        result=GeminiAdapter(sys.executable,runner=runner).doctor()
        text=str(result); self.assertTrue(result["installed"]); self.assertNotIn("あ",text); self.assertNotIn("input",result)

    def test_doctor_requests_strict_utf8(self):
        calls=[]
        responses=[subprocess.CompletedProcess([],0,"0.45.2\n",""),subprocess.CompletedProcess([],0,"--prompt stdin --model text"," ")]
        def runner(command,**kwargs): calls.append(kwargs); return responses.pop(0)
        result=GeminiAdapter(sys.executable,runner=runner).doctor()
        self.assertTrue(result["installed"])
        self.assertEqual([call["encoding"] for call in calls],["utf-8","utf-8"])
        self.assertEqual([call["errors"] for call in calls],["strict","strict"])


if __name__ == "__main__": unittest.main()
