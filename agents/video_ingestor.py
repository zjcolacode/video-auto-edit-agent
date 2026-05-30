"""Video ingestion: probe metadata + extract frames + (optional) ASR.

For the Coding Plan endpoint we cannot stream video directly to the model;
instead we sample a small set of representative JPEG frames here and let
``VisionAnalyst`` send them via the OpenAI-compatible chat completion API.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from tools.asr import ASRProvider, NoopASR
from tools.ffmpeg_tools import VideoMeta, probe
from tools.frame_extractor import Frame, extract_frames


@dataclass
class IngestResult:
    meta: VideoMeta
    frames: List[Frame]
    transcript: str  # "" when ASR is disabled / not available


class VideoIngestor:
    def __init__(
        self,
        *,
        num_frames: int = 16,
        max_side: int = 768,
        asr: ASRProvider | None = None,
    ) -> None:
        self._num_frames = num_frames
        self._max_side = max_side
        self._asr = asr or NoopASR()

    def prepare(self, video_path: str | Path) -> IngestResult:
        path = Path(video_path)
        meta = probe(path)
        frames = extract_frames(
            path, num_frames=self._num_frames, max_side=self._max_side
        )
        transcript = ""
        try:
            transcript = self._asr.transcribe(path) or ""
        except NotImplementedError:
            # Skeleton ASR provider; pipeline runs vision-only.
            transcript = ""
        return IngestResult(meta=meta, frames=frames, transcript=transcript)

    def cleanup(self, ingest: IngestResult) -> None:  # noqa: D401
        """No remote state to clean up; frames live in memory only."""
        return None
