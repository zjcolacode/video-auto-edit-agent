"""Vision analyst: ask the Coding Plan LLM for a structured VideoAnalysis."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from agents.video_ingestor import IngestResult
from config import settings
from schemas import Segment, VideoAnalysis
from tools.llm_client import LLMClient


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "analyze_video.md"


def _load_prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class VisionAnalyst:
    """Single-pass structured analysis with retry + baseline fallback."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._template = _load_prompt_template()

    def analyze(
        self,
        ingest: IngestResult,
        *,
        preferred_model: Optional[str] = None,
        max_retries: int = 2,
    ) -> VideoAnalysis:
        primary = preferred_model or settings.default_model
        fallback = (
            settings.fallback_model
            if settings.fallback_model and settings.fallback_model != primary
            else primary
        )

        frame_summary = self._frames_header(ingest)
        prompt = self._template.replace("{FRAMES_HEADER}", frame_summary)

        frames_b64 = [f.b64_jpeg for f in ingest.frames]
        transcript = ingest.transcript or None

        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            model = primary if attempt == 0 else fallback
            try:
                analysis = self._client.analyze(
                    prompt=prompt,
                    frames_b64=frames_b64,
                    transcript=transcript,
                    model=model,
                )
                return self._sanitize(analysis, ingest.meta.duration)
            except Exception as exc:  # noqa: BLE001
                last_err = exc

        # Final baseline: uniform slicing so the downstream pipeline keeps working.
        baseline = self._uniform_baseline(ingest.meta.duration)
        baseline.overall_summary = (
            f"Baseline used because LLM analysis failed: {last_err!r}"
        )
        return baseline

    # ---------------- helpers ----------------
    @staticmethod
    def _frames_header(ingest: IngestResult) -> str:
        """Build a small text block telling the model which timestamps the frames cover."""
        lines = [
            f"视频总时长 {ingest.meta.duration:.1f} 秒，"
            f"分辨率 {ingest.meta.width}x{ingest.meta.height}，"
            f"已为你均匀抽取 {len(ingest.frames)} 张关键帧，按顺序排列。",
            "每一帧的源时间戳（秒）依次为：",
            "  " + ", ".join(f"{f.timestamp:.2f}" for f in ingest.frames),
        ]
        return "\n".join(lines)

    @staticmethod
    def _sanitize(analysis: VideoAnalysis, duration: float) -> VideoAnalysis:
        """Clamp timestamps to the real duration and drop invalid segments."""
        clean: list[Segment] = []
        for seg in analysis.segments:
            start = max(0.0, min(seg.start, duration))
            end = max(0.0, min(seg.end, duration))
            if end - start < 1.0:
                continue
            clean.append(seg.model_copy(update={"start": start, "end": end}))
        clean.sort(key=lambda s: s.start)
        return analysis.model_copy(
            update={"segments": clean, "duration": duration}
        )

    @staticmethod
    def _uniform_baseline(duration: float) -> VideoAnalysis:
        segments: list[Segment] = []
        chunk = 15.0
        idx = 0
        t = 0.0
        while t < duration:
            end = min(t + chunk, duration)
            if end - t >= 3.0:
                segments.append(
                    Segment(
                        start=round(t, 2),
                        end=round(end, 2),
                        topic=f"片段 {idx + 1}",
                        summary="baseline uniform slice",
                        score=0.5,
                        reason="LLM fallback baseline",
                        tags=[],
                    )
                )
                idx += 1
            t = end
        return VideoAnalysis(
            video_type="mixed",
            overall_summary="baseline",
            duration=duration,
            segments=segments,
        )
