"""ScriptWriter agent: 把 VideoAnalysis + 关键帧改写成一份解说稿。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from agents.video_ingestor import IngestResult
from config import settings
from schemas import NarrationScript, NarrationTone, ScriptLine, VideoAnalysis
from tools.llm_client import LLMClient


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "write_script.md"


def _load_prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class ScriptWriter:
    """温度 0.7 的多模态创作型调用。失败时抛异常，不做静默 baseline。"""

    def __init__(self, client: LLMClient, *, temperature: float = 0.7) -> None:
        self._client = client
        self._template = _load_prompt_template()
        self._temperature = temperature

    def write(
        self,
        ingest: IngestResult,
        analysis: VideoAnalysis,
        *,
        target_duration: int,
        tone: NarrationTone = "casual",
        preferred_model: Optional[str] = None,
        max_retries: int = 2,
    ) -> NarrationScript:
        primary = preferred_model or settings.default_model
        fallback = (
            settings.fallback_model
            if settings.fallback_model and settings.fallback_model != primary
            else primary
        )

        prompt = self._build_prompt(ingest, analysis, target_duration, tone)
        frames_b64 = [f.b64_jpeg for f in ingest.frames]
        num_frames = len(frames_b64)

        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            model = primary if attempt == 0 else fallback
            try:
                payload = self._client.complete_json(
                    prompt=prompt,
                    frames_b64=frames_b64,
                    model=model,
                    temperature=self._temperature,
                )
                script = NarrationScript.model_validate(payload)
                return self._sanitize(script, num_frames=num_frames, tone=tone)
            except Exception as exc:  # noqa: BLE001
                last_err = exc

        raise RuntimeError(
            f"ScriptWriter failed after {max_retries} attempts: {last_err!r}"
        )

    # ---------------- helpers ----------------
    def _build_prompt(
        self,
        ingest: IngestResult,
        analysis: VideoAnalysis,
        target_duration: int,
        tone: NarrationTone,
    ) -> str:
        frame_lines = ", ".join(
            f"[{i}]@{f.timestamp:.1f}s" for i, f in enumerate(ingest.frames)
        )
        seg_lines = "\n".join(
            f"  - [{seg.start:.1f}-{seg.end:.1f}s | score={seg.score:.2f}] "
            f"{seg.topic}: {seg.summary}"
            for seg in analysis.segments
        )
        header = "\n".join(
            [
                f"原视频总时长 {ingest.meta.duration:.1f} 秒，"
                f"分辨率 {ingest.meta.width}x{ingest.meta.height}。",
                f"video_type = {analysis.video_type}",
                f"overall_summary: {analysis.overall_summary}",
                "",
                f"目标解说时长 = {target_duration} 秒（按 4 字/秒，约 "
                f"{target_duration * 4} 字上下）",
                f"目标口吻 tone = {tone}",
                "",
                f"已抽取 {len(ingest.frames)} 张关键帧，下标@时间戳：",
                f"  {frame_lines}",
                "",
                "原视频片段列表（供你挑选哪些值得在解说稿里展开）：",
                seg_lines or "  (空)",
            ]
        )
        return self._template.replace("{INPUT_HEADER}", header)

    @staticmethod
    def _sanitize(
        script: NarrationScript, *, num_frames: int, tone: NarrationTone
    ) -> NarrationScript:
        """裁剪非法 frame_indices、保证 tone 与请求一致。"""
        clean: list[ScriptLine] = []
        for line in script.lines:
            valid = [i for i in line.frame_indices if 0 <= i < num_frames]
            if not valid:
                # 模型偶尔越界；强制至少给一帧（用句子在序列中的位置插值）
                valid = [min(num_frames - 1, max(0, len(clean)))]
            valid = valid[:2]  # 最多 2 帧
            clean.append(
                line.model_copy(
                    update={
                        "frame_indices": valid,
                        # text 长度交给 pydantic 校验；这里只裁切超过 50 字的兜底
                        "text": line.text[:50],
                    }
                )
            )
        return script.model_copy(update={"lines": clean, "tone": tone})


__all__ = ["ScriptWriter"]
