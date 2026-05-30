"""Integration test for SceneBuilder.

Requires ``ffmpeg`` / ``ffprobe`` available on PATH. Uses lavfi to synthesize
a tiny color image and a 0.5s sine-wave audio as fixtures, then runs the full
build() pipeline and asserts the output mp4 exists and contains both video
and audio streams.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.scene_builder import SceneBuilder  # noqa: E402
from agents.video_ingestor import IngestResult  # noqa: E402
from schemas import (  # noqa: E402
    NarrationManifest,
    NarrationScript,
    ScriptLine,
    VoicedLine,
)
from tools.ffmpeg_tools import make_kenburns_clip  # noqa: E402
from tools.frame_extractor import Frame  # noqa: E402


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="requires ffmpeg/ffprobe in PATH",
)


def _make_fixture_jpeg(out_path: Path, color: str = "red") -> str:
    """生成一张 320x180 单色 jpg，返回 base64 字符串。"""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d=1",
        "-frames:v", "1", "-q:v", "5",
        "-loglevel", "error", str(out_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return base64.b64encode(out_path.read_bytes()).decode("ascii")


def _make_fixture_audio(out_path: Path, duration: float = 0.5) -> None:
    """生成一段 sine wave mp3，作为假 TTS 输出。"""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:a", "libmp3lame", "-b:a", "64k",
        "-loglevel", "error", str(out_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def _probe_streams(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def test_scene_builder_end_to_end(tmp_path: Path):
    # ---- 1. fixtures ----
    img1 = tmp_path / "f0.jpg"
    img2 = tmp_path / "f1.jpg"
    b64_1 = _make_fixture_jpeg(img1, color="red")
    b64_2 = _make_fixture_jpeg(img2, color="blue")

    audio0 = tmp_path / "audio0.mp3"
    audio1 = tmp_path / "audio1.mp3"
    _make_fixture_audio(audio0, duration=0.5)
    _make_fixture_audio(audio1, duration=0.5)

    frames = [
        Frame(timestamp=1.0, b64_jpeg=b64_1),
        Frame(timestamp=2.0, b64_jpeg=b64_2),
    ]
    meta = SimpleNamespace(
        path=Path("/fake.mp4"), duration=10.0,
        width=1280, height=720, fps=30.0,
        video_codec="h264", audio_codec="aac",
    )
    ingest = IngestResult(meta=meta, frames=frames, transcript="")

    script = NarrationScript(
        title="测试标题",
        tone="casual",
        lines=[
            ScriptLine(text="句子一。", emotion="excited", frame_indices=[0]),
            ScriptLine(text="句子二。", emotion="warm", frame_indices=[1, 0]),
        ],
    )
    manifest = NarrationManifest(
        script=script,
        voiced=[
            VoicedLine(line=script.lines[0], audio_path=audio0, duration=0.5),
            VoicedLine(line=script.lines[1], audio_path=audio1, duration=0.5),
        ],
        total_duration=1.0,
    )

    # ---- 2. run ----
    builder = SceneBuilder(width=320, height=180, fps=15)  # 小尺寸跑得快
    out = tmp_path / "narration.mp4"
    result = builder.build(ingest, manifest, out)

    # ---- 3. assertions ----
    assert result.exists() and result.stat().st_size > 0
    streams = _probe_streams(result).get("streams", [])
    kinds = {s.get("codec_type") for s in streams}
    assert "video" in kinds, "missing video stream"
    assert "audio" in kinds, "missing audio stream"

    fmt = _probe_streams(result).get("format", {})
    duration = float(fmt.get("duration", 0.0))
    # 总解说时长 1.0s，允许 ±0.3s 的容差（编码器开闭头）
    assert 0.6 <= duration <= 1.5, f"unexpected mux duration: {duration}"


def test_kenburns_clip_duration_matches_request(tmp_path: Path):
    """回归护栏：make_kenburns_clip 输出时长必须 ≈ duration。

    历史 bug：-loop 1 + zoompan 未限制输出帧数 → 输出时长膨胀 25-32 倍，
    导致成片 silent.mp4 比语音轨长 ×30，mux -shortest 后画面全部卡在第一句。
    """
    img = tmp_path / "src.jpg"
    _make_fixture_jpeg(img, color="green")

    out = tmp_path / "kb.mp4"
    target_duration = 1.5
    make_kenburns_clip(img, target_duration, out, width=320, height=180, fps=30)

    fmt = _probe_streams(out).get("format", {})
    actual = float(fmt.get("duration", 0.0))
    assert abs(actual - target_duration) < 0.15, (
        f"ken-burns duration {actual:.3f}s drifted from requested {target_duration}s"
    )


@pytest.mark.parametrize("style", ["zoom_in", "zoom_out", "pan_left", "pan_right"])
def test_kenburns_clip_each_style_renders(tmp_path: Path, style: str):
    """4 种运镜 preset 都能跳通、输出非空、时长准确。

    不断言画面不同（fixture 是纯色图，平移 / 缩放下色块仍是同一颜色），
    只保证滤镜表达式语法正确 + 输出可被 ffprobe 识别。
    """
    img = tmp_path / "src.jpg"
    _make_fixture_jpeg(img, color="orange")
    out = tmp_path / f"kb_{style}.mp4"
    target = 1.0
    make_kenburns_clip(img, target, out, width=320, height=180, fps=30, style=style)
    assert out.exists() and out.stat().st_size > 0
    fmt = _probe_streams(out).get("format", {})
    actual = float(fmt.get("duration", 0.0))
    assert abs(actual - target) < 0.15, (
        f"style={style} duration {actual:.3f}s drifted from {target}s"
    )


def test_kenburns_clip_int_style_wraps_modulo(tmp_path: Path):
    """int style 可被接受且以 % 4 轮转（SceneBuilder 依赖该行为）。"""
    img = tmp_path / "src.jpg"
    _make_fixture_jpeg(img, color="yellow")
    out = tmp_path / "kb_int.mp4"
    # 7 % 4 = 3 → pan_right，能跳通即可
    make_kenburns_clip(img, 0.5, out, width=320, height=180, fps=30, style=7)
    assert out.exists() and out.stat().st_size > 0
