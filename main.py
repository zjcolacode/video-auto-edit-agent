"""CLI entrypoint for the Video Highlight Agent."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agents.orchestrator import Orchestrator
from config import settings

app = typer.Typer(
    add_completion=False,
    help="Video Highlight Agent - 多智能体视频精华提炼工具",
)
console = Console()


@app.command()
def run(
    video: Path = typer.Argument(..., exists=True, readable=True, help="输入视频路径"),
    target: int = typer.Option(
        settings.target_duration, "--target", "-t", help="目标精华视频时长（秒）"
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="输出视频路径；默认 workspace/output/<name>_highlight.mp4",
    ),
    model: str = typer.Option(
        "plus", "--model", "-m", help="视觉模型：plus(qwen3.6-plus) | pro(qwen3.5-plus) | kimi(kimi-k2.5)"
    ),
    min_score: float = typer.Option(
        settings.min_score, "--min-score", help="片段最低保留分数 0-1"
    ),
    num_frames: int = typer.Option(
        settings.num_frames, "--num-frames", help="抽帧数量（1帧约所需 token有限）"
    ),
) -> None:
    """端到端：抽帧 -> LLM 分析 -> 选片 -> 拼接为精华短视频。"""
    output_path = output or (
        settings.workspace / "output" / f"{video.stem}_highlight.mp4"
    )
    orchestrator = Orchestrator(
        min_score=min_score, preferred_model=model, num_frames=num_frames
    )
    result = orchestrator.run(video, target, output_path)
    _print_picks(result.curation.picks)


@app.command()
def analyze(
    video: Path = typer.Argument(..., exists=True, readable=True, help="输入视频路径"),
    model: str = typer.Option(
        "plus", "--model", "-m", help="视觉模型：plus(qwen3.6-plus) | pro(qwen3.5-plus) | kimi(kimi-k2.5)"
    ),
    num_frames: int = typer.Option(
        settings.num_frames, "--num-frames", help="抽帧数量"
    ),
) -> None:
    """只跑分析，输出结构化 JSON，便于调试 prompt。"""
    orchestrator = Orchestrator(preferred_model=model, num_frames=num_frames)
    analysis, out = orchestrator.analyze_only(video)
    console.print(
        f"\n[bold]video_type[/bold] = {analysis.video_type}    "
        f"[bold]duration[/bold] = {analysis.duration:.1f}s    "
        f"[bold]segments[/bold] = {len(analysis.segments)}"
    )
    console.print(f"[bold]overall[/bold]: {analysis.overall_summary}\n")
    _print_picks(analysis.segments)
    console.print(f"\nFull JSON: [green]{out}[/green]")


def _print_picks(segments) -> None:
    if not segments:
        console.print("[yellow]No segments.[/yellow]")
        return
    table = Table(title="Segments", show_lines=False)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("start", justify="right")
    table.add_column("end", justify="right")
    table.add_column("dur", justify="right")
    table.add_column("score", justify="right", style="magenta")
    table.add_column("topic", style="bold")
    table.add_column("tags")
    for i, seg in enumerate(segments):
        table.add_row(
            str(i + 1),
            f"{seg.start:.1f}",
            f"{seg.end:.1f}",
            f"{seg.duration:.1f}",
            f"{seg.score:.2f}",
            seg.topic,
            ",".join(seg.tags),
        )
    console.print(table)


if __name__ == "__main__":
    app()
