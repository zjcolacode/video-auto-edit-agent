"""Video composer: cut segments and concatenate into a highlight reel."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from agents.content_curator import CurationResult
from config import settings
from schemas import Segment
from tools.ffmpeg_tools import concat, cut


class VideoComposer:
    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace or settings.workspace
        self._segments_dir = self._workspace / "segments"
        self._segments_dir.mkdir(parents=True, exist_ok=True)

    def compose(
        self,
        source_video: str | Path,
        curation: CurationResult,
        output_path: str | Path,
    ) -> Path:
        source_video = Path(source_video)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not curation.picks:
            raise ValueError("No picks to compose. Curation returned empty list.")

        clip_paths = self._cut_clips(source_video, curation.picks)
        return concat(clip_paths, output_path)

    # ---------------- internal ----------------
    def _cut_clips(self, source: Path, segments: List[Segment]) -> List[Path]:
        # Clear stale segment files for this run.
        for f in self._segments_dir.glob("clip_*.mp4"):
            try:
                f.unlink()
            except OSError:
                pass

        clip_paths: List[Path] = []
        for idx, seg in enumerate(segments):
            out = self._segments_dir / f"clip_{idx:03d}.mp4"
            cut(source, seg.start, seg.end, out)
            clip_paths.append(out)
        return clip_paths

    def cleanup(self) -> None:
        if self._segments_dir.exists():
            shutil.rmtree(self._segments_dir, ignore_errors=True)
