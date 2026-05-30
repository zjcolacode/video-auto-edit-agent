"""Orchestrator wiring all four agents into a single pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console

from agents.content_curator import ContentCurator, CurationResult
from agents.scene_builder import SceneBuilder
from agents.script_writer import ScriptWriter
from agents.video_composer import VideoComposer
from agents.video_ingestor import VideoIngestor
from agents.vision_analyst import VisionAnalyst
from agents.voice_caster import VoiceCaster
from config import settings
from schemas import NarrationManifest, NarrationTone, VideoAnalysis
from tools.asr import get_asr_provider
from tools.llm_client import LLMClient
from tools.tts_client import build_default_client as build_tts_client


console = Console()


@dataclass
class PipelineResult:
    analysis: VideoAnalysis
    curation: CurationResult
    output_video: Path
    analysis_json: Path
    highlight_json: Path


@dataclass
class NarrateResult:
    analysis: VideoAnalysis
    manifest: NarrationManifest
    output_video: Path
    analysis_json: Path
    manifest_json: Path


class Orchestrator:
    def __init__(
        self,
        *,
        min_score: Optional[float] = None,
        preferred_model: Optional[str] = None,
        num_frames: Optional[int] = None,
    ) -> None:
        self._client = LLMClient()
        self._ingestor = VideoIngestor(
            num_frames=num_frames or settings.num_frames,
            asr=get_asr_provider(),
        )
        self._analyst = VisionAnalyst(self._client)
        self._curator = ContentCurator(
            min_score=min_score if min_score is not None else settings.min_score
        )
        self._composer = VideoComposer()
        self._preferred_model = preferred_model

    # ---------------- public API ----------------
    def analyze_only(self, video_path: str | Path) -> tuple[VideoAnalysis, Path]:
        ingest = self._ingest_with_log(video_path)
        try:
            console.log("[cyan]LLM structured analysis...[/cyan]")
            analysis = self._analyst.analyze(
                ingest, preferred_model=self._preferred_model
            )
        finally:
            self._ingestor.cleanup(ingest)

        out = settings.workspace / "output" / f"{Path(video_path).stem}.analysis.json"
        out.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
        console.log(f"[green]Analysis saved -> {out}[/green]")
        return analysis, out

    def run(
        self,
        video_path: str | Path,
        target_duration: int,
        output_path: str | Path,
    ) -> PipelineResult:
        video_path = Path(video_path)
        output_path = Path(output_path)

        ingest = self._ingest_with_log(video_path)
        try:
            console.log("[cyan]LLM structured analysis...[/cyan]")
            analysis = self._analyst.analyze(
                ingest, preferred_model=self._preferred_model
            )
        finally:
            self._ingestor.cleanup(ingest)

        console.log(
            f"[cyan]Curating top segments (target={target_duration}s) ...[/cyan]"
        )
        curation = self._curator.curate(analysis, float(target_duration))
        console.log(
            f"  picked {len(curation.picks)} segments, "
            f"total {curation.total_duration:.1f}s"
        )

        console.log("[cyan]Composing highlight via ffmpeg ...[/cyan]")
        output_video = self._composer.compose(video_path, curation, output_path)

        analysis_json = output_path.with_suffix(".analysis.json")
        highlight_json = output_path.with_suffix(".highlight.json")
        analysis_json.write_text(
            analysis.model_dump_json(indent=2), encoding="utf-8"
        )
        highlight_json.write_text(
            json.dumps(
                {
                    "source": str(video_path),
                    "target_duration": target_duration,
                    "total_duration": curation.total_duration,
                    "picks": [seg.model_dump() for seg in curation.picks],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        console.log(f"[green]Highlight video -> {output_video}[/green]")
        console.log(f"[green]Analysis JSON   -> {analysis_json}[/green]")
        console.log(f"[green]Picks JSON      -> {highlight_json}[/green]")

        return PipelineResult(
            analysis=analysis,
            curation=curation,
            output_video=output_video,
            analysis_json=analysis_json,
            highlight_json=highlight_json,
        )

    # ---------------- v0.3 narrate ----------------
    def narrate(
        self,
        video_path: str | Path,
        target_duration: int,
        output_path: str | Path,
        *,
        tone: NarrationTone = "casual",
        voice: Optional[str] = None,
    ) -> NarrateResult:
        """AI 解说重剪：脚本主导、Ken Burns 画面、CosyVoice 配音。"""
        video_path = Path(video_path)
        output_path = Path(output_path)

        # 前置检查：TTS Key 必须在发请求之前验证，避免跑完 LLM 才报错。
        tts_client = build_tts_client(voice=voice)  # 内部会 check api_key

        ingest = self._ingest_with_log(video_path)
        try:
            console.log("[cyan]LLM structured analysis...[/cyan]")
            analysis = self._analyst.analyze(
                ingest, preferred_model=self._preferred_model
            )

            console.log(
                f"[cyan]Writing narration script (tone={tone}, target={target_duration}s) ...[/cyan]"
            )
            writer = ScriptWriter(self._client)
            script = writer.write(
                ingest,
                analysis,
                target_duration=target_duration,
                tone=tone,
                preferred_model=self._preferred_model,
            )
            console.log(
                f"  script: {len(script.lines)} lines, {script.total_chars} chars  "
                f"title=\"{script.title}\""
            )

            console.log("[cyan]Synthesizing voice with CosyVoice-v2 ...[/cyan]")
            caster = VoiceCaster(client=tts_client)
            manifest = caster.cast(script)
            console.log(
                f"  voiced {len(manifest.voiced)} lines, total {manifest.total_duration:.1f}s"
            )

            console.log("[cyan]Building scenes (Ken Burns + mux) ...[/cyan]")
            builder = SceneBuilder()
            output_video = builder.build(ingest, manifest, output_path)
        finally:
            self._ingestor.cleanup(ingest)

        analysis_json = output_path.with_suffix(".analysis.json")
        manifest_json = output_path.with_suffix(".narration.json")
        analysis_json.write_text(
            analysis.model_dump_json(indent=2), encoding="utf-8"
        )
        manifest_json.write_text(
            json.dumps(
                {
                    "source": str(video_path),
                    "target_duration": target_duration,
                    "total_duration": manifest.total_duration,
                    "script": manifest.script.model_dump(),
                    "voiced": [
                        {
                            "text": v.line.text,
                            "emotion": v.line.emotion,
                            "frame_indices": v.line.frame_indices,
                            "duration": v.duration,
                            "audio_path": str(v.audio_path),
                        }
                        for v in manifest.voiced
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        console.log(f"[green]Analysis JSON  -> {analysis_json}[/green]")
        console.log(f"[green]Narration JSON -> {manifest_json}[/green]")

        return NarrateResult(
            analysis=analysis,
            manifest=manifest,
            output_video=output_video,
            analysis_json=analysis_json,
            manifest_json=manifest_json,
        )

    # ---------------- internal ----------------
    def _ingest_with_log(self, video_path: str | Path):
        console.log(f"[cyan]Probing & extracting frames[/cyan] {video_path}")
        ingest = self._ingestor.prepare(video_path)
        console.log(
            f"  duration={ingest.meta.duration:.1f}s "
            f"{ingest.meta.width}x{ingest.meta.height}@{ingest.meta.fps:.1f}fps "
            f"frames={len(ingest.frames)} "
            f"transcript={'yes' if ingest.transcript else 'no'}"
        )
        return ingest


def run_pipeline(
    video_path: str | Path,
    target_duration: int,
    output_path: str | Path,
    *,
    min_score: Optional[float] = None,
    preferred_model: Optional[str] = None,
) -> PipelineResult:
    orchestrator = Orchestrator(
        min_score=min_score, preferred_model=preferred_model
    )
    return orchestrator.run(video_path, target_duration, output_path)


__all__ = ["Orchestrator", "PipelineResult", "NarrateResult", "run_pipeline"]
