---
name: yt-dlp
description: Use this skill when the user wants to download, inspect, archive, or extract media from YouTube or other yt-dlp-supported sites using the local yt-dlp command. Includes workflows for video, audio, subtitles, metadata, playlists, and format selection.
metadata:
  short-description: Download and inspect media with yt-dlp
---

# yt-dlp

Use the local `yt-dlp` CLI for downloading or inspecting media from supported sites.

## First checks

1. Confirm `yt-dlp` is available:
   ```bash
   command -v yt-dlp && yt-dlp --version
   ```
2. If the user asks for downloads, save outputs in a task-specific folder unless they provide a destination. In Codex projectless threads, prefer `outputs/` for user-facing files.
3. Avoid downloading private, paywalled, or copyrighted media unless the user indicates they own it, have permission, or are only doing an allowed personal/archive use.

## Common Commands

Inspect a URL without downloading:

```bash
yt-dlp --dump-json --no-playlist "URL"
yt-dlp -F "URL"
```

Download a single best-quality video:

```bash
yt-dlp -P "DEST_DIR" -o "%(title).200B [%(id)s].%(ext)s" "URL"
```

Download audio as MP3:

```bash
yt-dlp -x --audio-format mp3 --audio-quality 0 -P "DEST_DIR" -o "%(title).200B [%(id)s].%(ext)s" "URL"
```

Download subtitles with metadata:

```bash
yt-dlp --write-subs --write-auto-subs --sub-langs "en.*" --write-info-json --write-thumbnail -P "DEST_DIR" "URL"
```

Download a playlist safely:

```bash
yt-dlp --yes-playlist --download-archive "DEST_DIR/archive.txt" -P "DEST_DIR" -o "%(playlist_index)03d - %(title).180B [%(id)s].%(ext)s" "URL"
```

## Helper Script

Use `scripts/ytdlp_job.sh` for predictable task folders:

```bash
scripts/ytdlp_job.sh inspect "URL"
scripts/ytdlp_job.sh video "URL" "DEST_DIR"
scripts/ytdlp_job.sh audio "URL" "DEST_DIR"
scripts/ytdlp_job.sh subs "URL" "DEST_DIR"
scripts/ytdlp_job.sh transcript "URL" "DEST_DIR"
scripts/ytdlp_job.sh playlist "URL" "DEST_DIR"
```

The script creates the destination folder and uses stable filenames with the media ID included.

Use `transcript` when the user wants a transcript and wants local transcription as a backup:

1. It first tries to download English subtitles/auto-subs with `yt-dlp`.
2. If no subtitle file is saved, it downloads audio.
3. It then transcribes locally with `whisperx` if available, falling back to `whisper`.

The helper detects `whisperx`/`whisper` on `PATH`. If installed through this repo's `install.sh`, they should be available automatically.

It also checks a few common local install paths:

```text
$HOME/.local/bin/whisperx
$HOME/.local/bin/whisper
$HOME/Library/Python/3.9/bin/whisperx
$HOME/Library/Python/3.9/bin/whisper
```

Optional environment overrides:

```bash
WHISPERX_BIN=/path/to/whisperx
WHISPER_BIN=/path/to/whisper
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

## Practical Workflow

- For a new URL, inspect first when format, playlist behavior, or site support is unclear.
- If a command fails, rerun with `--verbose` and read the site-specific error before changing flags.
- For YouTube playlists, ask or infer whether the user wants the whole playlist; otherwise add `--no-playlist`.
- For transcript/subtitle jobs, prefer `scripts/ytdlp_job.sh transcript` so subtitles are tried first and local WhisperX/Whisper is used only if subtitles are unavailable.
- When returning results, include the exact saved path and note whether subtitles, thumbnails, or metadata were included.

## Upstream Reference

For yt-dlp source inspection, supported-site docs, or examples, use the upstream project:

```text
https://github.com/yt-dlp/yt-dlp
```
