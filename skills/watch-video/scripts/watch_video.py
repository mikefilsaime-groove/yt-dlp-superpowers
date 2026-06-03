#!/usr/bin/env python3
"""
Create a "watch video" evidence bundle from a video URL or local media file.

Outputs:
  - source video/audio and metadata
  - subtitles or local WhisperX/Whisper transcript
  - frames sampled by ffmpeg
  - watch_context.md mapping each frame timestamp to nearby transcript text
  - manifest.json with machine-readable paths/settings
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FRAME_PRESETS = {
    "4fps": ("fps=4", 0.25),
    "2fps": ("fps=2", 0.5),
    "1fps": ("fps=1", 1.0),
    "1s": ("fps=1", 1.0),
    "3s": ("fps=1/3", 3.0),
    "5s": ("fps=1/5", 5.0),
    "10s": ("fps=1/10", 10.0),
}

MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".ogg"}
SUB_EXTS = {".vtt", ".srt", ".ttml"}


@dataclass
class Segment:
    start: float
    end: float
    text: str


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def require(cmd: str) -> str:
    path = shutil.which(cmd)
    if not path:
        print(f"Missing required command: {cmd}", file=sys.stderr)
        sys.exit(127)
    return path


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\s.-]", "", value, flags=re.UNICODE).strip()
    value = re.sub(r"\s+", "-", value)
    return value[:120] or "video"


def parse_rate(rate: str) -> tuple[str, float]:
    if rate in FRAME_PRESETS:
        return FRAME_PRESETS[rate]
    if rate.endswith("fps"):
        num = float(rate[:-3])
        if num <= 0:
            raise ValueError("fps rate must be positive")
        return f"fps={num:g}", 1 / num
    if rate.endswith("s"):
        seconds = float(rate[:-1])
        if seconds <= 0:
            raise ValueError("seconds rate must be positive")
        return f"fps=1/{seconds:g}", seconds
    seconds = float(rate)
    if seconds <= 0:
        raise ValueError("seconds rate must be positive")
    return f"fps=1/{seconds:g}", seconds


def timestamp(seconds: float) -> str:
    total = int(seconds)
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    if hrs:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def parse_time(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = "0"
        m, s = parts
    else:
        return float(value)
    return int(h) * 3600 + int(m) * 60 + float(s)


def clean_vtt_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_vtt(path: Path) -> list[Segment]:
    segments: list[Segment] = []
    block: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() + [""]:
        line = line.strip("\ufeff")
        if line.strip():
            block.append(line)
            continue
        if not block:
            continue
        timing = next((x for x in block if "-->" in x), "")
        if timing:
            start_s, end_s = [x.strip().split()[0] for x in timing.split("-->", 1)]
            text = clean_vtt_text(" ".join(x for x in block if x != timing and not x.startswith(("WEBVTT", "Kind:", "Language:"))))
            if text:
                segments.append(Segment(parse_time(start_s), parse_time(end_s), text))
        block = []
    return segments


def parse_srt(path: Path) -> list[Segment]:
    segments: list[Segment] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="replace")):
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        timing = next((x for x in lines if "-->" in x), "")
        if not timing:
            continue
        start_s, end_s = [x.strip().split()[0] for x in timing.split("-->", 1)]
        text = clean_vtt_text(" ".join(x for x in lines if x != timing and not x.isdigit()))
        if text:
            segments.append(Segment(parse_time(start_s), parse_time(end_s), text))
    return segments


def parse_json_transcript(path: Path) -> list[Segment]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    segments = []
    for seg in data.get("segments", []):
        text = str(seg.get("text", "")).strip()
        if text:
            segments.append(Segment(float(seg.get("start", 0)), float(seg.get("end", 0)), text))
    return segments


def parse_transcript(path: Path | None) -> list[Segment]:
    if not path:
        return []
    if path.suffix == ".vtt":
        return parse_vtt(path)
    if path.suffix == ".srt":
        return parse_srt(path)
    if path.suffix == ".json":
        return parse_json_transcript(path)
    if path.suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return [Segment(0, 999999, text)] if text else []
    return []


def nearby_text(segments: Iterable[Segment], at: float, window: float) -> str:
    texts = []
    start = max(0, at - window)
    end = at + window
    for seg in segments:
        if seg.end >= start and seg.start <= end:
            texts.append(seg.text)
    return clean_vtt_text(" ".join(texts))


def find_newest(directory: Path, exts: set[str]) -> Path | None:
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def download_source(source: str, work_dir: Path) -> Path:
    if Path(source).exists():
        original = Path(source)
        target = work_dir / original.name
        if original.resolve() != target.resolve():
            shutil.copy2(original, target)
        return target

    require("yt-dlp")
    template = "%(title).200B [%(id)s].%(ext)s"
    run([
        "yt-dlp",
        "--no-playlist",
        "--write-info-json",
        "--write-thumbnail",
        "-P",
        str(work_dir),
        "-o",
        template,
        source,
    ])
    media = find_newest(work_dir, MEDIA_EXTS)
    if not media:
        raise RuntimeError("yt-dlp finished but no media file was found")
    return media


def try_download_subtitles(source: str, work_dir: Path) -> Path | None:
    if Path(source).exists():
        return None
    require("yt-dlp")
    before = {p.name for p in work_dir.iterdir() if p.is_file()}
    run([
        "yt-dlp",
        "--no-playlist",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en.*",
        "-P",
        str(work_dir),
        "-o",
        "%(title).200B [%(id)s].%(ext)s",
        source,
    ], check=False)
    new_subs = [p for p in work_dir.iterdir() if p.is_file() and p.name not in before and p.suffix.lower() in SUB_EXTS]
    return max(new_subs, key=lambda p: p.stat().st_mtime) if new_subs else find_newest(work_dir, SUB_EXTS)


def find_whisperx() -> str | None:
    candidates = [
        os.environ.get("WHISPERX_BIN"),
        shutil.which("whisperx"),
        str(Path.home() / ".local/bin/whisperx"),
        str(Path.home() / "Library/Python/3.9/bin/whisperx"),
    ]
    return next((x for x in candidates if x and Path(x).exists()), None)


def find_whisper() -> str | None:
    candidates = [
        os.environ.get("WHISPER_BIN"),
        shutil.which("whisper"),
        str(Path.home() / ".local/bin/whisper"),
        str(Path.home() / "Library/Python/3.9/bin/whisper"),
    ]
    return next((x for x in candidates if x and Path(x).exists()), None)


def transcribe(media: Path, work_dir: Path) -> Path | None:
    whisperx = find_whisperx()
    whisper = find_whisper()
    model = os.environ.get("WHISPER_MODEL", "base")
    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
    if whisperx:
        run([
            whisperx,
            str(media),
            "--model",
            model,
            "--device",
            device,
            "--compute_type",
            compute_type,
            "--output_dir",
            str(work_dir),
            "--output_format",
            "all",
        ])
        return find_newest(work_dir, {".json", ".vtt", ".srt", ".txt"})
    if whisper:
        run([
            whisper,
            str(media),
            "--model",
            model,
            "--device",
            device,
            "--output_dir",
            str(work_dir),
            "--output_format",
            "all",
        ])
        return find_newest(work_dir, {".json", ".vtt", ".srt", ".txt"})
    return None


def extract_frames(media: Path, frames_dir: Path, ffmpeg_filter: str) -> None:
    require("ffmpeg")
    frames_dir.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media),
        "-vf",
        ffmpeg_filter,
        "-vsync",
        "vfr",
        "-q:v",
        "3",
        str(frames_dir / "frame_%06d.jpg"),
    ])


def write_context(out_dir: Path, frames_dir: Path, segments: list[Segment], interval: float, transcript_path: Path | None, media: Path, source: str, rate: str) -> None:
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    lines = [
        f"# Watch Video Context: {media.stem}",
        "",
        f"- Source: {source}",
        f"- Media: {media}",
        f"- Transcript: {transcript_path or 'none'}",
        f"- Frame rate preset: {rate}",
        f"- Frames extracted: {len(frames)}",
        "",
        "## Timeline",
        "",
    ]
    manifest_frames = []
    for idx, frame in enumerate(frames):
        at = idx * interval
        text = nearby_text(segments, at, max(3.0, interval * 2))
        rel = frame.relative_to(out_dir)
        lines.extend([
            f"### {timestamp(at)}",
            "",
            f"![frame]({rel})",
            "",
            f"Transcript context: {text or '[no nearby transcript text]'}",
            "",
        ])
        manifest_frames.append({"time": at, "timestamp": timestamp(at), "frame": str(frame), "transcript_context": text})

    (out_dir / "watch_context.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps({
        "source": source,
        "media": str(media),
        "transcript": str(transcript_path) if transcript_path else None,
        "rate": rate,
        "interval_seconds": interval,
        "frames_dir": str(frames_dir),
        "frames": manifest_frames,
    }, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build transcript + frames context so an AI can actually watch a video.")
    parser.add_argument("source", help="Video URL or local media file")
    parser.add_argument("--output-dir", "-o", default="outputs/watch-video", help="Destination folder")
    parser.add_argument("--rate", default="1s", help="Frame sampling: 4fps, 2fps, 1fps/1s, 3s, 5s, 10s, or numeric seconds")
    args = parser.parse_args()

    ffmpeg_filter, interval = parse_rate(args.rate)
    out_root = Path(args.output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    name = slugify(Path(args.source).stem if Path(args.source).exists() else "video")
    work_dir = out_root / name
    work_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = try_download_subtitles(args.source, work_dir)
    media = download_source(args.source, work_dir)
    if not transcript_path:
        transcript_path = transcribe(media, work_dir)

    frames_dir = work_dir / "frames"
    extract_frames(media, frames_dir, ffmpeg_filter)
    segments = parse_transcript(transcript_path)
    write_context(work_dir, frames_dir, segments, interval, transcript_path, media, args.source, args.rate)

    print(f"Watch bundle: {work_dir}")
    print(f"Context: {work_dir / 'watch_context.md'}")
    print(f"Manifest: {work_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
