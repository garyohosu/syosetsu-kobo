import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kobo.agy import (AgyAdapter, AgyCommandNotFound, AgyEmptyOutput,
                      AgyInvalidOutput, AgyNonZero, AgyPromptTooLarge,
                      AgyTimeout)


class AgyAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.output = root / "result.md"
        self.refs = {"model": "", "task_path": str(root / "task.md")}
        Path(self.refs["task_path"]).write_text("固定参照", encoding="utf-8")
        self.agent = SimpleNamespace(timeout=2)

    def tearDown(self):
        self.temp.cleanup()

    def adapter(self, result=None, error=None):
        def runner(command, **kwargs):
            self.command, self.kwargs = command, kwargs
            if error:
                raise error
            return result or subprocess.CompletedProcess(command, 0, "# 第一話\n本文", "")
        return AgyAdapter("agy", runner=runner)

    def test_command_is_print_prompt_and_permissions(self):
        adapter = self.adapter(); adapter.execute(SimpleNamespace(timeout=2), {"prompt": "短い依頼", "model": ""}, self.output)
        self.assertEqual(self.command[:2], ["agy", "--print"])
        self.assertIn("--dangerously-skip-permissions", self.command)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "# 第一話\n本文")

    def test_model_is_optional(self):
        adapter = self.adapter(); adapter.execute(self.agent, {"prompt": "依頼", "model": "agy-default"}, self.output)
        self.assertIn(["--model", "agy-default"], [self.command[i:i+2] for i in range(len(self.command)-1)])

    def test_utf8_timeout_nonzero_empty_and_invalid(self):
        adapter = self.adapter(); adapter.execute(self.agent, {"prompt": "日本語", "model": ""}, self.output)
        self.assertEqual(self.kwargs["encoding"], "utf-8")
        with self.assertRaises(AgyTimeout): self.adapter(error=subprocess.TimeoutExpired([], 2)).execute(self.agent, {"prompt":"x"}, self.output)
        with self.assertRaises(AgyNonZero): self.adapter(subprocess.CompletedProcess([], 3, "", "failed")).execute(self.agent, {"prompt":"x"}, self.output)
        with self.assertRaises(AgyEmptyOutput): self.adapter(subprocess.CompletedProcess([], 0, "  ", "")).execute(self.agent, {"prompt":"x"}, self.output)
        with self.assertRaises(AgyInvalidOutput): self.adapter(subprocess.CompletedProcess([], 0, "bad\x00", "")).execute(self.agent, {"prompt":"x"}, self.output)

    def test_command_not_found_and_prompt_limit_before_launch(self):
        with self.assertRaises(AgyCommandNotFound): self.adapter(error=FileNotFoundError()).execute(self.agent, {"prompt":"x"}, self.output)
        adapter = self.adapter()
        with patch("kobo.agy.os.name", "nt"):
            with self.assertRaises(AgyPromptTooLarge): adapter.execute(self.agent, {"prompt":"あ" * 40000}, self.output)

    def test_doctor_and_smoke_are_safe(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            if command[-1] == "--version": return subprocess.CompletedProcess(command, 0, "agy 1.2.3", "")
            return subprocess.CompletedProcess(command, 0, "--print --model --dangerously-skip-permissions", "")
        result = AgyAdapter("agy", runner=runner).doctor()
        self.assertEqual(result["version"], "agy 1.2.3")
        self.assertTrue(result["print"] and result["model"] and result["dangerously_skip_permissions"])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
