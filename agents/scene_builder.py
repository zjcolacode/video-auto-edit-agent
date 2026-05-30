"""SceneBuilder agent: NarrationManifest + 抽帧 -> 最终解说短视频。

流程：
  1. 把所有 base64 关键帧 decode 成临时 jpg（按下标编号）
  2. 对每句解说：取其 frame_indices 对应的帧，按句子时长平均切分；
     每帧用 make_kenburns_clip 生成 Ken Burns 默片
  3. 多帧句子内部用 ffmpeg concat 拼接（同分辨率/帧率，可流拷贝）
  4. 全部句子的视频段 concat 成一条无音轨视频
  5. 全部句子音频用 concat_audio 合成单条 aac
  6. mux_video_audio 把视频和音频对齐，输出最终 mp4
"""
from __future__ import annotations

import base64
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from agents.video_ingestor import IngestResult
from config import settings
from schemas import NarrationManifest, VoicedLine
from tools.ffmpeg_tools import (
    FFmpegError,
    KEN_BURNS_STYLES,
    concat,
    concat_audio,
    make_kenburns_clip,
    mux_video_audio,
)


console = Console()


class SceneBuilder:
    def __init__(
        self,
        *,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        keep_temp: bool = False,
    ) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._keep_temp = keep_temp

    def build(
        self,
        ingest: IngestResult,
        manifest: NarrationManifest,
        output_path: str | Path,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # ---------------- 1. dump frames to disk ----------------
        tmp_root = Path(tempfile.mkdtemp(prefix="vae_scene_", dir=settings.workspace / "temp"))
        try:
            frames_dir = tmp_root / "frames"
            frames_dir.mkdir()
            frame_files: list[Path] = []
            for idx, frame in enumerate(ingest.frames):
                p = frames_dir / f"frame_{idx:03d}.jpg"
                p.write_bytes(base64.b64decode(frame.b64_jpeg))
                frame_files.append(p)

            # ---------------- 2. per-line video clips ----------------
            clips_dir = tmp_root / "clips"
            clips_dir.mkdir()
            line_clips: list[Path] = []
            for line_idx, voiced in enumerate(manifest.voiced):
                line_clip = self._build_line_clip(
                    voiced=voiced,
                    line_idx=line_idx,
                    frame_files=frame_files,
                    clips_dir=clips_dir,
                )
                line_clips.append(line_clip)

            # ---------------- 3. concat all line clips ----------------
            silent_video = tmp_root / "silent.mp4"
            if len(line_clips) == 1:
                shutil.copy(line_clips[0], silent_video)
            else:
                concat(line_clips, silent_video)

            # ---------------- 4. concat audio ----------------
            full_audio = tmp_root / "voice.m4a"
            audio_paths = [Path(v.audio_path) for v in manifest.voiced]
            concat_audio(audio_paths, full_audio)

            # ---------------- 5. mux ----------------
            mux_video_audio(silent_video, full_audio, output_path)
            console.log(f"[green]Narration video -> {output_path}[/green]")
            return output_path
        finally:
            if not self._keep_temp:
                shutil.rmtree(tmp_root, ignore_errors=True)

    # ---------------- helpers ----------------
    def _build_line_clip(
        self,
        *,
        voiced: VoicedLine,
        line_idx: int,
        frame_files: List[Path],
        clips_dir: Path,
    ) -> Path:
        """单句解说 -> 单段无声视频。多帧时内部 concat。

        动效轮转：同一句的多个帧用同一种运镜（避免句内跳动），
        句与句之间按 line_idx % 4 轮转 zoom_in / zoom_out / pan_left / pan_right。
        """
        indices = voiced.line.frame_indices
        n = len(indices)
        per_frame_dur = voiced.duration / n if n else voiced.duration
        style = KEN_BURNS_STYLES[line_idx % len(KEN_BURNS_STYLES)]
        sub_clips: list[Path] = []
        for k, idx in enumerate(indices):
            if not (0 <= idx < len(frame_files)):
                # ScriptWriter 已 sanitize，这里再兜一次
                idx = max(0, min(idx, len(frame_files) - 1))
            sub_path = clips_dir / f"line_{line_idx:03d}_part_{k}.mp4"
            make_kenburns_clip(
                frame_files[idx],
                per_frame_dur,
                sub_path,
                width=self._width,
                height=self._height,
                fps=self._fps,
                style=style,
            )
            sub_clips.append(sub_path)

        out = clips_dir / f"line_{line_idx:03d}.mp4"
        if len(sub_clips) == 1:
            shutil.copy(sub_clips[0], out)
        else:
            try:
                concat(sub_clips, out)
            except FFmpegError:
                # concat 失败时 fallback：只用第一帧覆盖整句时长
                make_kenburns_clip(
                    frame_files[indices[0]],
                    voiced.duration,
                    out,
                    width=self._width,
                    height=self._height,
                    fps=self._fps,
                    style=style,
                )
        return out


__all__ = ["SceneBuilder"]
