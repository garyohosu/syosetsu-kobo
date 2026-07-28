from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

WINDOWS_SAFE_COMMAND_LINE_LIMIT = 30_000
Runner = Callable[..., subprocess.CompletedProcess[str]]


class AgyError(RuntimeError):
    """Antigravity CLIの診断可能な失敗。"""


class AgyCommandNotFound(AgyError): pass
class AgyAuthentication(AgyError): pass
class AgyLimit(AgyError): pass
class AgyTimeout(AgyError): pass
class AgyNonZero(AgyError): pass
class AgyEmptyOutput(AgyError): pass
class AgyInvalidOutput(AgyError): pass
class AgyPromptTooLarge(AgyError): pass


def executable_available(executable: str) -> str | None:
    candidate = Path(executable)
    if candidate.parent != Path("."):
        return str(candidate) if candidate.is_file() else None
    return shutil.which(executable)


def launch_name(executable: str) -> str:
    if os.name == "nt" and Path(executable).suffix == "" and shutil.which(executable + ".cmd"):
        return executable + ".cmd"
    return executable


def command_line_utf16_units(command: list[str]) -> int:
    return len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2


class AgyAdapter:
    """Antigravity CLIの単発Markdownアダプター。

    agyはプロンプトをstdinやファイルではなくargvで受け、stdoutへ生テキストを返す。
    """

    LIMIT_MARKERS = ("quota", "rate limit", "session limit", "usage limit", "利用枠", "利用上限")
    AUTH_MARKERS = ("authentication", "login", "oauth", "api key", "認証", "ログイン")

    def __init__(self, executable: str = "agy", *, runner: Runner = subprocess.run):
        self.executable = executable
        self.runner = runner

    def command(self, agent, refs: dict[str, str]) -> list[str]:
        prompt = refs.get("prompt")
        if prompt is None:
            prompt = Path(refs["task_path"]).read_text(encoding="utf-8")
        command = [launch_name(self.executable), "--print", prompt]
        model = refs.get("model")
        if model and model not in ("configured-by-settings", "default"):
            command.extend(["--model", model])
        if refs.get("dangerously_skip_permissions", True):
            command.append("--dangerously-skip-permissions")
        return command

    def execute(self, agent, refs: dict[str, str], output_path: Path) -> None:
        if not executable_available(self.executable):
            raise AgyCommandNotFound(f"agyが見つかりません: {self.executable}")
        command = self.command(agent, refs)
        if os.name == "nt":
            units = command_line_utf16_units(command)
            if units > WINDOWS_SAFE_COMMAND_LINE_LIMIT:
                raise AgyPromptTooLarge(f"agyのコマンドラインが長すぎます: {units} UTF-16 code units")
        try:
            result = self.runner(command, capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=agent.timeout, shell=False, check=False)
        except FileNotFoundError as error:
            raise AgyCommandNotFound(f"agyを起動できません: {self.executable}") from error
        except subprocess.TimeoutExpired as error:
            raise AgyTimeout(f"agyが{agent.timeout}秒でタイムアウトしました") from error
        except UnicodeDecodeError as error:
            raise AgyInvalidOutput("agyのUTF-8出力を復号できません") from error
        diagnostic = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        lowered = diagnostic.lower()
        if any(marker in lowered for marker in self.LIMIT_MARKERS):
            raise AgyLimit("agyの利用枠またはセッション上限に達しました")
        if any(marker in lowered for marker in self.AUTH_MARKERS):
            raise AgyAuthentication("agyの認証が必要です")
        if result.returncode:
            raise AgyNonZero(f"agyが非ゼロ終了しました: exit={result.returncode}")
        output = result.stdout or ""
        if not output.strip():
            raise AgyEmptyOutput("agyの出力が空です")
        if "\x00" in output or len(output) > 10_000_000:
            raise AgyInvalidOutput("agyの出力形式が不正です")
        from .orchestrator import atomic_write
        atomic_write(output_path, output)

    def doctor(self, timeout: float = 10) -> dict:
        resolved = executable_available(self.executable)
        result = {"executable": self.executable, "resolved_name": Path(resolved).name if resolved else None, "installed": bool(resolved), "version": None, "print": False, "model": False, "dangerously_skip_permissions": False}
        if not resolved:
            return result
        command = launch_name(self.executable)
        try:
            version = self.runner([command, "--version"], text=True, encoding="utf-8", errors="strict", capture_output=True, timeout=timeout, shell=False, check=False)
            help_result = self.runner([command, "--help"], text=True, encoding="utf-8", errors="strict", capture_output=True, timeout=timeout, shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
            return {**result, "error": type(error).__name__}
        help_text = (help_result.stdout or "") + (help_result.stderr or "")
        return {**result, "version": (version.stdout or version.stderr or "").strip()[:100], "print": "--print" in help_text, "model": "--model" in help_text, "dangerously_skip_permissions": "--dangerously-skip-permissions" in help_text, "version_exit_code": version.returncode, "help_exit_code": help_result.returncode}

    def smoke(self, agent, refs: dict[str, str], output_path: Path) -> dict:
        smoke_refs = {**refs, "prompt": "日本語で『接続確認成功』とだけ返してください。"}
        self.execute(agent, smoke_refs, output_path)
        text = output_path.read_text(encoding="utf-8")
        if "接続確認成功" not in text:
            raise AgyInvalidOutput("agy smokeの応答に接続確認成功がありません")
        return {"exit_code": 0, "output_exists": True, "output_characters": len(text)}
