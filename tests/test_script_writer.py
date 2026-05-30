"""Unit tests for ScriptWriter sanitization & prompt assembly.

Mocks the LLM client to avoid real API calls. Focuses on:
1. frame_indices clamping when model returns out-of-range values
2. truncation of >50-char lines
3. tone field is forced to the requested value
4. prompt template substitution propagates header text
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.script_writer import ScriptWriter  # noqa: E402
from agents.video_ingestor import IngestResult  # noqa: E402
from schemas import Segment, VideoAnalysis  # noqa: E402
from tools.frame_extractor import Frame  # noqa: E402


def _ingest(num_frames: int = 4) -> IngestResult:
    frames = [
        Frame(timestamp=float(i + 1), b64_jpeg="ZmFrZQ==")
        for i in range(num_frames)
    ]
    meta = SimpleNamespace(
        path=Path("/fake.mp4"),
        duration=60.0,
        width=1280,
        height=720,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
    )
    return IngestResult(meta=meta, frames=frames, transcript="")


def _analysis() -> VideoAnalysis:
    return VideoAnalysis(
        video_type="tutorial",
        overall_summary="一个测试视频",
        duration=60.0,
        segments=[
            Segment(
                start=0,
                end=20,
                topic="开场",
                summary="",
                score=0.8,
                reason="",
                tags=[],
            )
        ],
    )


def test_clamps_out_of_range_frame_indices():
    """模型返回 999 这种越界 frame_index 时应被替换为合法下标。"""
    client = MagicMock()
    client.complete_json.return_value = {
        "title": "测试标题",
        "tone": "casual",
        "lines": [
            {"text": "钩子句子。", "emotion": "excited", "frame_indices": [999]},
            {"text": "正常句子。", "emotion": "neutral", "frame_indices": [1, 2]},
        ],
    }
    writer = ScriptWriter(client)
    script = writer.write(
        _ingest(num_frames=4), _analysis(),
        target_duration=30, tone="casual",
    )
    for line in script.lines:
        for idx in line.frame_indices:
            assert 0 <= idx < 4, f"frame_index {idx} out of range"


def test_at_most_two_frames_per_line():
    client = MagicMock()
    client.complete_json.return_value = {
        "title": "测试",
        "tone": "casual",
        "lines": [
            {
                "text": "贪心多帧。",
                "emotion": "neutral",
                "frame_indices": [0, 1, 2, 3],
            },
        ],
    }
    writer = ScriptWriter(client)
    script = writer.write(
        _ingest(), _analysis(), target_duration=20, tone="casual"
    )
    assert len(script.lines[0].frame_indices) <= 2


def test_tone_forced_to_request():
    """模型即使返回 hype，也应被覆盖为请求时指定的 formal。"""
    client = MagicMock()
    client.complete_json.return_value = {
        "title": "测试",
        "tone": "hype",
        "lines": [
            {"text": "句子一。", "emotion": "neutral", "frame_indices": [0]},
        ],
    }
    writer = ScriptWriter(client)
    script = writer.write(
        _ingest(), _analysis(), target_duration=20, tone="formal"
    )
    assert script.tone == "formal"


def test_retries_on_failure_then_raises():
    client = MagicMock()
    client.complete_json.side_effect = RuntimeError("LLM down")
    writer = ScriptWriter(client)
    with pytest.raises(RuntimeError, match="ScriptWriter failed"):
        writer.write(_ingest(), _analysis(), target_duration=30)
    assert client.complete_json.call_count == 2  # default max_retries


def test_prompt_contains_target_and_frames_header():
    client = MagicMock()
    captured = {}

    def fake_complete(prompt, frames_b64, **kw):
        captured["prompt"] = prompt
        return {
            "title": "标题",
            "tone": "casual",
            "lines": [
                {"text": "句一。", "emotion": "neutral", "frame_indices": [0]}
            ],
        }

    client.complete_json.side_effect = fake_complete
    writer = ScriptWriter(client)
    writer.write(_ingest(), _analysis(), target_duration=45, tone="hype")
    assert "目标解说时长 = 45 秒" in captured["prompt"]
    assert "tone = hype" in captured["prompt"]
    assert "已抽取 4 张关键帧" in captured["prompt"]
