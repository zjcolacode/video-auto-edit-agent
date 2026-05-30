"""VoiceCaster agent: 把 NarrationScript 逐句送入 CosyVoice-v2 合成。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from config import settings
from schemas import NarrationManifest, NarrationScript, VoicedLine
from tools.tts_client import CosyVoiceClient, build_default_client


console = Console()


class VoiceCaster:
    def __init__(
        self,
        *,
        client: Optional[CosyVoiceClient] = None,
        out_dir: Optional[Path] = None,
    ) -> None:
        self._client = client or build_default_client()
        self._out_dir = out_dir or (settings.workspace / "temp" / "voice")
        self._out_dir.mkdir(parents=True, exist_ok=True)

    def cast(self, script: NarrationScript) -> NarrationManifest:
        voiced: list[VoicedLine] = []
        total = 0.0
        for idx, line in enumerate(script.lines):
            out_path = self._out_dir / f"line_{idx:03d}.mp3"
            console.log(
                f"[cyan]TTS[/cyan] {idx + 1}/{len(script.lines)}  "
                f"({len(line.text)} 字) {line.text}"
            )
            duration = self._client.synthesize(line.text, out_path)
            voiced.append(
                VoicedLine(line=line, audio_path=out_path, duration=duration)
            )
            total += duration

        return NarrationManifest(
            script=script, voiced=voiced, total_duration=total
        )

    def cleanup(self, manifest: NarrationManifest) -> None:
        """可选：清理临时音频文件。SceneBuilder 之后调。"""
        for v in manifest.voiced:
            try:
                Path(v.audio_path).unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["VoiceCaster"]
