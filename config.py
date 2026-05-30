"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Coding Plan / OpenAI-compatible endpoint
    coding_api_key: str
    coding_base_url: str

    # Models
    default_model: str
    fallback_model: str

    # Pipeline knobs
    target_duration: int
    min_score: float
    num_frames: int
    workspace: Path

    # ASR
    asr_provider: str
    asr_api_key: str

    # TTS (v0.3 narrate)
    tts_api_key: str
    tts_model: str
    tts_voice: str

    @classmethod
    def load(cls) -> "Settings":
        workspace = Path(os.getenv("WORKSPACE", "./workspace")).resolve()
        (workspace / "segments").mkdir(parents=True, exist_ok=True)
        (workspace / "output").mkdir(parents=True, exist_ok=True)
        (workspace / "temp").mkdir(parents=True, exist_ok=True)
        # TTS 可复用 ASR 那把“普通百炼 Key”，后者为默认后备。
        tts_key = os.getenv("TTS_API_KEY", "") or os.getenv("ASR_API_KEY", "")
        return cls(
            coding_api_key=os.getenv("CODING_API_KEY", ""),
            coding_base_url=os.getenv(
                "CODING_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1"
            ),
            default_model=os.getenv("DEFAULT_MODEL", "qwen3.6-plus"),
            fallback_model=os.getenv("FALLBACK_MODEL", "qwen3.5-plus"),
            target_duration=int(os.getenv("TARGET_DURATION", "60")),
            min_score=float(os.getenv("MIN_SCORE", "0.5")),
            num_frames=int(os.getenv("NUM_FRAMES", "16")),
            workspace=workspace,
            asr_provider=os.getenv("ASR_PROVIDER", "noop"),
            asr_api_key=os.getenv("ASR_API_KEY", ""),
            tts_api_key=tts_key,
            tts_model=os.getenv("TTS_MODEL", "cosyvoice-v2"),
            tts_voice=os.getenv("TTS_VOICE", "longwan_v2"),
        )


settings = Settings.load()
