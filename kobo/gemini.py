from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable


class GeminiError(RuntimeError):
    """Gemini CLIの診断可能な失敗。本文を例外へ含めない。"""


class GeminiNotInstalled(GeminiError): pass
class GeminiAuthenticationError(GeminiError): pass
class GeminiTimeoutError(GeminiError): pass
class GeminiNonZeroError(GeminiError): pass
class GeminiEmptyOutputError(GeminiError): pass
class GeminiInvalidOutputError(GeminiError): pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


def executable_available(executable: str) -> str | None:
    candidate = Path(executable)
    if candidate.parent != Path("."):
        return str(candidate) if candidate.is_file() else None
    return shutil.which(executable)


def launch_name(executable: str) -> str:
    if os.name == "nt" and Path(executable).suffix == "" and shutil.which(executable + ".cmd"):
        return executable + ".cmd"
    return executable


class GeminiAdapter:
    """Gemini CLI 0.45系の標準入力を使う安全なアダプター。"""

    AUTH_MARKERS = ("authentication", "authenticat", "ineligibletier", "login", "oauth", "api key", "認証")

    def __init__(self, executable: str = "gemini", fixed_args: list[str] | None = None, runner: Runner = subprocess.run):
        self.executable = executable
        self.fixed_args = list(fixed_args or [])
        self.runner = runner

    def command(self, agent, refs: dict[str, str]) -> list[str]:
        command = [launch_name(self.executable), *self.fixed_args]
        if refs.get("model"):
            command.extend(["--model", refs["model"]])
        command.extend(["--output-format", "text", "--prompt", "Read the complete task from stdin and return only the requested Markdown result."])
        return command

    def execute(self, agent, refs: dict[str, str], output_path: Path) -> None:
        from .orchestrator import atomic_write

        if not executable_available(self.executable):
            raise GeminiNotInstalled(f"Gemini CLIが見つかりません: {self.executable}")
        task_path = Path(refs["task_path"])
        prompt = task_path.read_text(encoding="utf-8")
        try:
            completed = self.runner(self.command(agent, refs), input=prompt, text=True, capture_output=True, timeout=agent.timeout, shell=False, check=False)
        except subprocess.TimeoutExpired as error:
            raise GeminiTimeoutError(f"Gemini CLIが{agent.timeout}秒でタイムアウトしました") from error
        except OSError as error:
            raise GeminiNotInstalled(f"Gemini CLIを起動できません: {self.executable}") from error
        if completed.returncode:
            diagnostic = (completed.stderr or completed.stdout or "").strip().lower()
            if any(marker in diagnostic for marker in self.AUTH_MARKERS):
                raise GeminiAuthenticationError("Gemini CLIの認証が必要です")
            raise GeminiNonZeroError(f"Gemini CLIが非ゼロ終了しました: exit={completed.returncode}")
        result = completed.stdout
        if not result or not result.strip():
            raise GeminiEmptyOutputError("Gemini CLIの出力が空です")
        if "\x00" in result or len(result) > 10_000_000:
            raise GeminiInvalidOutputError("Gemini CLIの出力形式が不正です")
        atomic_write(output_path, result)

    def doctor(self, timeout: float = 10) -> dict:
        resolved = executable_available(self.executable)
        base = {"executable": self.executable, "resolved_name": Path(resolved).name if resolved else None, "installed": bool(resolved), "version": None, "headless": False, "stdin": False, "model_option": False, "output_formats": [], "authentication": "not_checked"}
        if not resolved:
            return base
        try:
            command_name = launch_name(self.executable)
            version = self.runner([command_name, "--version"], text=True, capture_output=True, timeout=timeout, shell=False, check=False)
            help_result = self.runner([command_name, "--help"], text=True, capture_output=True, timeout=timeout, shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {**base, "error": type(error).__name__}
        help_text = (help_result.stdout or "") + (help_result.stderr or "")
        return {**base, "version": (version.stdout or version.stderr or "").strip()[:100], "headless": "--prompt" in help_text, "stdin": "stdin" in help_text.lower(), "model_option": "--model" in help_text, "output_formats": [item for item in ("text", "json", "stream-json") if item in help_text], "version_exit_code": version.returncode, "help_exit_code": help_result.returncode}

    def smoke(self, agent, refs: dict[str, str], output_path: Path) -> dict:
        started = time.monotonic()
        self.execute(agent, refs, output_path)
        return {"exit_code": 0, "elapsed_seconds": round(time.monotonic() - started, 3), "output_exists": output_path.is_file(), "output_characters": len(output_path.read_text(encoding="utf-8"))}
