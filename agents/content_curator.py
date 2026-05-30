"""Content curator: pick top segments under a target duration budget."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from schemas import Segment, VideoAnalysis


@dataclass
class CurationResult:
    picks: List[Segment]
    total_duration: float
    dropped: List[Segment]


class ContentCurator:
    """Greedy + topic-dedup selector that preserves narrative order."""

    def __init__(
        self,
        *,
        min_score: float = 0.5,
        max_per_topic: int = 2,
    ) -> None:
        self._min_score = min_score
        self._max_per_topic = max_per_topic

    def curate(
        self,
        analysis: VideoAnalysis,
        target_duration: float,
    ) -> CurationResult:
        if target_duration <= 0:
            raise ValueError("target_duration must be > 0")

        # Step 1: filter by minimum score.
        candidates = [s for s in analysis.segments if s.score >= self._min_score]
        dropped = [s for s in analysis.segments if s.score < self._min_score]

        if not candidates:
            # Loosen the bar: when nothing qualifies, fall back to top-scored segments.
            candidates = sorted(
                analysis.segments, key=lambda s: s.score, reverse=True
            )[:3]
            dropped = [s for s in analysis.segments if s not in candidates]

        # Step 2: greedy pick by score, respecting per-topic cap and budget.
        sorted_by_score = sorted(candidates, key=lambda s: s.score, reverse=True)
        picks: List[Segment] = []
        topic_count: dict[str, int] = {}
        total = 0.0

        for seg in sorted_by_score:
            key = seg.topic.strip().lower()
            if topic_count.get(key, 0) >= self._max_per_topic:
                dropped.append(seg)
                continue
            if total + seg.duration > target_duration and picks:
                # Budget exceeded -- only stop if we already have at least one pick.
                dropped.append(seg)
                continue
            picks.append(seg)
            topic_count[key] = topic_count.get(key, 0) + 1
            total += seg.duration
            if total >= target_duration:
                break

        # Step 3: restore chronological order for natural narrative flow.
        picks.sort(key=lambda s: s.start)

        return CurationResult(
            picks=picks,
            total_duration=round(total, 2),
            dropped=dropped,
        )
