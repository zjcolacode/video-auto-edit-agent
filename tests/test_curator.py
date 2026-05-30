"""Unit tests for ContentCurator selection logic."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make project root importable when running `pytest` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.content_curator import ContentCurator  # noqa: E402
from schemas import Segment, VideoAnalysis  # noqa: E402


def _seg(start, end, topic, score, tags=None):
    return Segment(
        start=start,
        end=end,
        topic=topic,
        summary="",
        score=score,
        reason="",
        tags=tags or [],
    )


def _analysis(segments):
    total = max((s.end for s in segments), default=1.0)
    return VideoAnalysis(
        video_type="mixed",
        overall_summary="",
        duration=total,
        segments=segments,
    )


def test_filters_below_min_score():
    curator = ContentCurator(min_score=0.5)
    analysis = _analysis(
        [
            _seg(0, 10, "A", 0.9),
            _seg(10, 20, "B", 0.3),  # dropped by min_score
            _seg(20, 30, "C", 0.7),
        ]
    )
    res = curator.curate(analysis, target_duration=60)
    topics = [p.topic for p in res.picks]
    assert topics == ["A", "C"]
    assert any(d.topic == "B" for d in res.dropped)


def test_respects_target_duration_budget():
    curator = ContentCurator(min_score=0.0)
    analysis = _analysis(
        [
            _seg(0, 20, "A", 0.9),  # 20s
            _seg(20, 40, "B", 0.8),  # 20s -> 40s total
            _seg(40, 60, "C", 0.7),  # would push to 60s
            _seg(60, 80, "D", 0.6),  # over budget, must be dropped
        ]
    )
    res = curator.curate(analysis, target_duration=45)
    # Greedy picks: A(20) -> B(20) total 40 <= 45; C(20) would push to 60 -> drop.
    topics = [p.topic for p in res.picks]
    assert topics == ["A", "B"]
    assert res.total_duration == pytest.approx(40.0)
    assert any(d.topic == "D" for d in res.dropped)


def test_preserves_chronological_order():
    curator = ContentCurator(min_score=0.0)
    analysis = _analysis(
        [
            _seg(0, 10, "A", 0.6),
            _seg(10, 20, "B", 0.95),  # highest score but later in time
            _seg(20, 30, "C", 0.8),
        ]
    )
    res = curator.curate(analysis, target_duration=60)
    # All three fit in budget. Final order must follow original timeline.
    assert [p.topic for p in res.picks] == ["A", "B", "C"]


def test_topic_deduplication():
    curator = ContentCurator(min_score=0.0, max_per_topic=2)
    analysis = _analysis(
        [
            _seg(0, 5, "intro", 0.9),
            _seg(5, 10, "intro", 0.8),
            _seg(10, 15, "intro", 0.7),  # third "intro" should be capped
            _seg(15, 20, "outro", 0.85),
        ]
    )
    res = curator.curate(analysis, target_duration=60)
    topics = [p.topic for p in res.picks]
    assert topics.count("intro") == 2
    assert "outro" in topics


def test_fallback_when_all_below_threshold():
    curator = ContentCurator(min_score=0.9)
    analysis = _analysis(
        [
            _seg(0, 5, "A", 0.4),
            _seg(5, 10, "B", 0.5),
            _seg(10, 15, "C", 0.6),
        ]
    )
    res = curator.curate(analysis, target_duration=30)
    # No segment >= 0.9 -> fallback picks top-3 by score.
    assert len(res.picks) == 3
    assert {p.topic for p in res.picks} == {"A", "B", "C"}


def test_invalid_target_duration():
    curator = ContentCurator()
    analysis = _analysis([_seg(0, 5, "A", 0.9)])
    with pytest.raises(ValueError):
        curator.curate(analysis, target_duration=0)
