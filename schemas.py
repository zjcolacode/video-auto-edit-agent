"""Pydantic schemas used as the structured-output contract with the LLM."""
from __future__ import annotations

from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, Field, field_validator


VideoType = Literal["lecture", "vlog", "film", "interview", "tutorial", "mixed"]


class Segment(BaseModel):
    start: float = Field(..., ge=0, description="片段起始时间，单位秒")
    end: float = Field(..., gt=0, description="片段结束时间，单位秒")
    topic: str = Field(..., description="片段核心主题，短语")
    summary: str = Field(..., description="一句话摘要")
    score: float = Field(..., ge=0.0, le=1.0, description="0-1 综合价值分")
    reason: str = Field(..., description="保留或丢弃的理由")
    tags: List[str] = Field(default_factory=list, description="标签，如金句/高潮/转折")

    @field_validator("end")
    @classmethod
    def end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start")
        if start is not None and v <= start:
            raise ValueError(f"end ({v}) must be greater than start ({start})")
        return v

    @property
    def duration(self) -> float:
        return self.end - self.start


class VideoAnalysis(BaseModel):
    video_type: VideoType
    overall_summary: str
    duration: float = Field(..., gt=0)
    segments: List[Segment]


# ---------------- v0.3 narrate (AI 解说重剪) ----------------

NarrationTone = Literal["casual", "formal", "hype"]
LineEmotion = Literal["neutral", "excited", "serious", "warm"]


class ScriptLine(BaseModel):
    """一句解说台词。"""
    text: str = Field(..., min_length=1, max_length=50, description="单句解说文本，中文建议 12-25 字")
    emotion: LineEmotion = "neutral"
    frame_indices: List[int] = Field(
        default_factory=list,
        description="推荐配这句话的帧序号（1-2 个），取自 ingestor.frames 列表下标",
    )

    @field_validator("frame_indices")
    @classmethod
    def _at_least_one_frame(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("frame_indices 不得为空，每句必须至少指定 1 帧")
        return v


class NarrationScript(BaseModel):
    """一份完整的解说稿（供 TTS 逐句合成）。"""
    title: str = Field(..., max_length=20, description="短视频标题")
    tone: NarrationTone = "casual"
    lines: List[ScriptLine] = Field(..., min_length=1)

    @property
    def total_chars(self) -> int:
        return sum(len(line.text) for line in self.lines)


class VoicedLine(BaseModel):
    """单句 TTS 输出（不能走 BaseModel 默认的 schema，Path 需允许任意类型）。"""
    line: ScriptLine
    audio_path: Path
    duration: float = Field(..., gt=0)

    model_config = {"arbitrary_types_allowed": True}


class NarrationManifest(BaseModel):
    """AI 解说重剪的完整中间产物，被 SceneBuilder 消费。"""
    script: NarrationScript
    voiced: List[VoicedLine]
    total_duration: float = Field(..., gt=0)

    model_config = {"arbitrary_types_allowed": True}
