"""Pydantic schemas used as the structured-output contract with Gemini."""
from __future__ import annotations

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
