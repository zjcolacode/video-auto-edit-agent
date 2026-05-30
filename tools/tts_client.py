"""CosyVoice-v2 TTS client over the DashScope SDK.

NOTE: 走原生 dashscope SDK，需要 "普通百炼 Key"（区别于 Coding Plan Key），
入口：阿里云百炼控制台 - API-KEY 管理。Coding Plan Key 不能调用 TTS。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class TTSError(RuntimeError):
    pass


# CosyVoice-v2 默认输出 mp3（22050Hz / 单声道），可直接被 ffmpeg 消费。
# 注意：v2 音色名带 _v2 后缀，与 v1 隔离；不带后缀会触发 InvalidParameter 418。
DEFAULT_VOICE = "longwan_v2"
DEFAULT_MODEL = "cosyvoice-v2"


def _probe_audio_duration(path: Path) -> float:
    """用 ffprobe 探测音频实际时长，作为 TTS 时长的权威来源。"""
    if shutil.which("ffprobe") is None:
        raise TTSError("ffprobe not found in PATH")
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise TTSError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    return float(data.get("format", {}).get("duration", 0.0))


class CosyVoiceClient:
    """同步逐句合成。封装 dashscope.audio.tts_v2.SpeechSynthesizer。"""

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = DEFAULT_VOICE,
        model: str = DEFAULT_MODEL,
    ) -> None:
        if not api_key:
            raise TTSError(
                "TTS_API_KEY is missing. Set it in .env (普通百炼 Key, "
                "Coding Plan Key 不可用)."
            )
        try:
            import dashscope  # noqa: F401
            from dashscope.audio.tts_v2 import SpeechSynthesizer  # noqa: F401
        except ImportError as exc:
            raise TTSError(
                "dashscope SDK not installed. Run: pip install dashscope>=1.20.0"
            ) from exc

        self._api_key = api_key
        self._voice = voice
        self._model = model

    def synthesize(self, text: str, out_path: str | Path) -> float:
        """合成一句解说音频，返回实际时长（秒）。

        Args:
            text: 单句解说文本
            out_path: 输出文件路径，建议 .mp3 后缀
        Returns:
            音频实际时长，由 ffprobe 探测得到
        """
        if not text or not text.strip():
            raise TTSError("text must not be empty")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 延迟导入，避免在未安装 SDK 的环境中 import-time 报错
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = self._api_key
        synthesizer = SpeechSynthesizer(model=self._model, voice=self._voice)
        try:
            audio_bytes = synthesizer.call(text)
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"CosyVoice call failed: {exc!r}") from exc

        if not audio_bytes:
            raise TTSError("CosyVoice returned empty audio bytes")
        if not isinstance(audio_bytes, (bytes, bytearray)):
            # SDK 偶尔会包一层 result 对象，做下兼容
            data = getattr(audio_bytes, "get_audio_data", None)
            if callable(data):
                audio_bytes = data()
            else:
                raise TTSError(
                    f"CosyVoice returned unexpected payload type: {type(audio_bytes)!r}"
                )

        out_path.write_bytes(audio_bytes)
        duration = _probe_audio_duration(out_path)
        if duration <= 0:
            raise TTSError(f"Synthesized audio has zero duration: {out_path}")
        return duration


def build_default_client(
    *, api_key: Optional[str] = None, voice: Optional[str] = None
) -> CosyVoiceClient:
    """工厂方法：从 settings 取默认参数。"""
    from config import settings

    return CosyVoiceClient(
        api_key=api_key or settings.tts_api_key,
        voice=voice or settings.tts_voice,
        model=settings.tts_model,
    )


__all__ = ["CosyVoiceClient", "TTSError", "build_default_client"]
