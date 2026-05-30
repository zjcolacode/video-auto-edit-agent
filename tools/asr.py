"""ASR provider abstraction.

The Coding Plan endpoint is text/vision only -- it does NOT include ASR
models. To enrich the analysis with spoken-word information you need a
separate audio transcription service (e.g. Aliyun Bailian paraformer-v2,
Tencent / Volc Engine ASR, or local whisper.cpp).

This module defines a small ``ASRProvider`` interface and ships a
``NoopASR`` default. Concrete cloud-ASR providers are planned for the
next iteration; see ``DashScopeParaformerASR`` skeleton at the bottom of
this file for the integration outline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from config import settings


class ASRProvider(ABC):
    """Transcribe an audio (or video w/ audio) file to plain text."""

    @abstractmethod
    def transcribe(self, audio_or_video: Path) -> str:
        """Return the transcript. Empty string means 'no transcript'."""


class NoopASR(ASRProvider):
    """Returns an empty transcript. Pipeline runs vision-only."""

    def transcribe(self, audio_or_video: Path) -> str:  # noqa: D401
        return ""


class DashScopeParaformerASR(ASRProvider):
    """Skeleton for Aliyun Bailian paraformer-v2 integration (TODO v0.2).

    Implementation outline:
        1. Use ffmpeg to extract a mono 16k WAV from the source video.
        2. Either:
            a) Upload the WAV to OSS and call ``Transcription.async_call``
               with the public URL, then poll for the result; or
            b) Use ``paraformer-realtime-v2`` streaming SDK to feed audio
               chunks directly without OSS.
        3. Concatenate segment texts (optionally with timestamps) and
           return the merged transcript.

    Requires a SEPARATE Bailian API key (the Coding Plan key cannot call
    ASR services). Configure via ``ASR_API_KEY`` in ``.env``.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RuntimeError(
                "ASR_API_KEY is required for DashScopeParaformerASR."
            )
        self._api_key = api_key

    def transcribe(self, audio_or_video: Path) -> str:
        raise NotImplementedError(
            "DashScopeParaformerASR is not yet implemented. "
            "Set ASR_PROVIDER=noop for now, or contribute the implementation."
        )


def get_asr_provider(name: Optional[str] = None) -> ASRProvider:
    """Factory that resolves an ASRProvider from config / argument."""
    provider = (name or settings.asr_provider or "noop").lower()
    if provider == "noop":
        return NoopASR()
    if provider == "dashscope":
        return DashScopeParaformerASR(settings.asr_api_key)
    raise ValueError(f"Unknown ASR_PROVIDER: {provider!r}")
