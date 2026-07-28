from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from .agy import command_line_utf16_units, executable_available, launch_name, WINDOWS_SAFE_COMMAND_LINE_LIMIT

Runner = Callable[..., subprocess.CompletedProcess[str]]


class AgyImageError(RuntimeError): pass
class AgyImageCommandNotFound(AgyImageError): pass
class AgyImageLimit(AgyImageError): pass
class AgyImageTimeout(AgyImageError): pass
class AgyImageNonZero(AgyImageError): pass
class AgyImageMissing(AgyImageError): pass
class AgyImageInvalid(AgyImageError): pass
class AgyImagePromptTooLarge(AgyImageError): pass


class AgyImageAdapter:
    """agyへ画像生成を依頼し、指定パスへ生成された画像を検証する。"""

    def __init__(self, executable: str = "agy", *, runner: Runner = subprocess.run, timeout: float = 300):
        self.executable = executable
        self.runner = runner
        self.timeout = timeout

    def command(self, prompt: str) -> list[str]:
        return [launch_name(self.executable), "--print", prompt, "--dangerously-skip-permissions"]

    @staticmethod
    def _validate(path: Path) -> tuple[str, int, int]:
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise AgyImageInvalid(f"画像拡張子が許可されていません: {path.suffix}")
        if not path.is_file():
            raise AgyImageMissing(f"画像が生成されませんでした: {path}")
        size = path.stat().st_size
        if size < 32 or size > 20_000_000:
            raise AgyImageInvalid(f"画像ファイルサイズが不正です: {size}")
        data = path.read_bytes()
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(data) < 24 or data[12:16] != b"IHDR": raise AgyImageInvalid("PNGヘッダーが不正です")
            width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
            return "png", width, height
        if data.startswith(b"\xff\xd8\xff"): return "jpeg", 0, 0
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP": return "webp", 0, 0
        raise AgyImageInvalid("画像マジックナンバーが不正です")

    def generate(self, prompt: str, output_path: Path) -> dict:
        if not executable_available(self.executable): raise AgyImageCommandNotFound(f"agyが見つかりません: {self.executable}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = self.command(prompt)
        if os.name == "nt" and command_line_utf16_units(command) > WINDOWS_SAFE_COMMAND_LINE_LIMIT:
            raise AgyImagePromptTooLarge("画像生成プロンプトがWindowsのargv安全上限を超えました")
        try:
            result = self.runner(command, capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=self.timeout, shell=False, check=False)
        except FileNotFoundError as error: raise AgyImageCommandNotFound("agyを起動できません") from error
        except subprocess.TimeoutExpired as error: raise AgyImageTimeout(f"agy画像生成が{self.timeout}秒でタイムアウトしました") from error
        diagnostic = ((result.stderr or "") + "\n" + (result.stdout or "")).lower()
        if any(x in diagnostic for x in ("quota", "rate limit", "session limit", "利用枠", "利用上限")): raise AgyImageLimit("agy画像生成の利用枠またはセッション上限です")
        if result.returncode: raise AgyImageNonZero(f"agy画像生成が非ゼロ終了しました: exit={result.returncode}")
        kind, width, height = self._validate(output_path)
        return {"path": str(output_path), "format": kind, "width": width, "height": height, "size": output_path.stat().st_size}
