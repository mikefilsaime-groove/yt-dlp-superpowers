#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ytdlp_job.sh inspect URL [DEST_DIR]
  ytdlp_job.sh formats URL [DEST_DIR]
  ytdlp_job.sh video URL DEST_DIR
  ytdlp_job.sh audio URL DEST_DIR
  ytdlp_job.sh subs URL DEST_DIR
  ytdlp_job.sh transcript URL DEST_DIR
  ytdlp_job.sh transcript-md URL DEST_DIR
  ytdlp_job.sh playlist URL DEST_DIR

Modes:
  inspect   Save JSON metadata without downloading media.
  formats   List available formats.
  video     Download best video/audio with metadata and thumbnail.
  audio     Extract best audio as MP3.
  subs      Download English subtitles/auto-subs plus metadata.
  transcript
            Download English subtitles/auto-subs; if none are available,
            download audio and transcribe locally with WhisperX/Whisper.
            Also write an exact Markdown transcript from the captions or
            local transcription output.
  transcript-md
            Convert existing captions or transcription text in DEST_DIR into
            an exact Markdown transcript without downloading anything.
  playlist  Download playlist using an archive file.
USAGE
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 2
fi

mode="$1"
url="$2"
dest="${3:-outputs/yt-dlp}"
template="%(title).200B [%(id)s].%(ext)s"
ytdlp_cmd=()

if [[ -n "${YTDLP_BIN:-}" ]]; then
  if [[ ! -x "$YTDLP_BIN" ]]; then
    echo "YTDLP_BIN is set but is not executable: $YTDLP_BIN" >&2
    exit 127
  fi
  ytdlp_cmd=("$YTDLP_BIN")
elif python3 -m yt_dlp --version >/dev/null 2>&1; then
  ytdlp_cmd=(python3 -m yt_dlp)
elif command -v yt-dlp >/dev/null 2>&1; then
  ytdlp_cmd=(yt-dlp)
else
  echo "yt-dlp is not installed. Install the Python package or set YTDLP_BIN." >&2
  exit 127
fi

mkdir -p "$dest"

run_ytdlp() {
  "${ytdlp_cmd[@]}" "$@"
}

write_markdown_transcript() {
  local transcript_dest="$1"
  local transcript_url="$2"

  python3 - "$transcript_dest" "$transcript_url" <<'PY'
from pathlib import Path
import html
import json
import re
import sys

dest = Path(sys.argv[1])
url = sys.argv[2]

def newest(pattern):
    files = list(dest.glob(pattern))
    return max(files, key=lambda path: path.stat().st_mtime) if files else None

info_file = newest("*.info.json") or (dest / "metadata.json" if (dest / "metadata.json").exists() else None)
info = {}
if info_file:
    try:
        info = json.loads(info_file.read_text(encoding="utf-8"))
    except Exception:
        info = {}

def priority(path):
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name.endswith(".en.vtt"):
        rank = 0
    elif name.endswith(".en-orig.vtt"):
        rank = 1
    elif suffix == ".vtt":
        rank = 2
    elif suffix == ".srt":
        rank = 3
    elif suffix == ".txt":
        rank = 4
    else:
        rank = 5
    return (rank, -path.stat().st_mtime)

sources = [
    path for path in dest.iterdir()
    if path.is_file() and path.suffix.lower() in {".vtt", ".srt", ".txt"}
]

if not sources:
    raise SystemExit("No caption or transcription text file found to convert to Markdown.")

source = sorted(sources, key=priority)[0]

def clean_caption_line(line):
    line = re.sub(r"<\d\d:\d\d:\d\d[.,]\d+>", "", line)
    line = re.sub(r"</?c[^>]*>", "", line)
    line = re.sub(r"<[^>]+>", "", line)
    line = html.unescape(line)
    return re.sub(r"\s+", " ", line).strip()

def dedupe_lines(lines):
    result = []
    last = None
    for line in lines:
        line = clean_caption_line(line)
        if not line or line == last:
            continue
        result.append(line)
        last = line
    return result

def parse_vtt(text):
    lines = []
    in_cue = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped == "WEBVTT" or stripped.startswith(("Kind:", "Language:", "NOTE", "STYLE", "REGION")):
            continue
        if "-->" in stripped:
            in_cue = True
            continue
        if in_cue:
            lines.append(stripped)
    return dedupe_lines(lines)

def parse_srt(text):
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return dedupe_lines(lines)

def parse_txt(text):
    return [line.rstrip() for line in text.splitlines() if line.strip()]

text = source.read_text(encoding="utf-8", errors="replace")
suffix = source.suffix.lower()
if suffix == ".vtt":
    transcript_lines = parse_vtt(text)
elif suffix == ".srt":
    transcript_lines = parse_srt(text)
else:
    transcript_lines = parse_txt(text)

if not transcript_lines:
    raise SystemExit(f"No transcript text could be extracted from {source}.")

title = info.get("title") or source.stem
video_id = info.get("id")
channel = info.get("channel") or info.get("uploader")
duration = info.get("duration_string") or info.get("duration")
upload_date = info.get("upload_date")
webpage_url = info.get("webpage_url") or url

base = source.stem
base = re.sub(r"\.(en|en-orig|[a-z]{2}(?:-[A-Z]{2})?|[a-z]{2}-orig)$", "", base)
out_path = dest / f"{base}.transcript.md"

meta = [
    f"# {title} - Exact Transcript",
    "",
    f"- Source URL: {webpage_url}",
]
if video_id:
    meta.append(f"- YouTube ID: `{video_id}`")
if channel:
    meta.append(f"- Channel: {channel}")
if duration:
    meta.append(f"- Duration: {duration}")
if upload_date:
    meta.append(f"- Upload date: {upload_date}")
meta.extend([
    f"- Transcript source: `{source.name}`",
    "- Transcript handling: exact caption/transcription text with timing markup removed and repeated rolling-caption lines de-duplicated.",
    "",
    "## Transcript",
    "",
])

body = "\n".join(transcript_lines)
out_path.write_text("\n".join(meta) + body + "\n", encoding="utf-8")
print(out_path)
PY
}

case "$mode" in
  inspect)
    run_ytdlp --dump-json --no-playlist "$url" > "$dest/metadata.json"
    printf 'Saved metadata to %s\n' "$dest/metadata.json"
    ;;
  formats)
    run_ytdlp -F "$url"
    ;;
  video)
    run_ytdlp --no-playlist --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"
    ;;
  audio)
    run_ytdlp --no-playlist -x --audio-format mp3 --audio-quality 0 \
      --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"
    ;;
  subs)
    run_ytdlp --no-playlist --skip-download --write-subs --write-auto-subs \
      --sub-langs "en.*" --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"
    ;;
  transcript-md)
    md_path="$(write_markdown_transcript "$dest" "$url")"
    printf 'Saved exact Markdown transcript to %s\n' "$md_path"
    ;;
  transcript)
    run_ytdlp --no-playlist --skip-download --write-subs --write-auto-subs \
      --sub-langs "en.*" --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url" || true

    subtitle_count="$(find "$dest" -maxdepth 1 -type f \( -name '*.vtt' -o -name '*.srt' \) | wc -l | tr -d ' ')"
    if [[ "$subtitle_count" -gt 0 ]]; then
      md_path="$(write_markdown_transcript "$dest" "$url")"
      printf 'Saved existing subtitles to %s\n' "$dest"
      printf 'Saved exact Markdown transcript to %s\n' "$md_path"
      exit 0
    fi

    printf 'No subtitles found; downloading audio for local transcription...\n'
    run_ytdlp --no-playlist -x --audio-format mp3 --audio-quality 0 \
      --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"

    audio_file="$(
      python3 - "$dest" <<'PY'
from pathlib import Path
import sys

dest = Path(sys.argv[1])
extensions = {".mp3", ".m4a", ".wav", ".webm", ".aac", ".ogg"}
files = [
    path for path in dest.iterdir()
    if path.is_file() and path.suffix.lower() in extensions
]
if files:
    print(max(files, key=lambda path: path.stat().st_mtime))
PY
    )"
    if [[ -z "${audio_file:-}" ]]; then
      echo "Could not find downloaded audio file for transcription." >&2
      exit 1
    fi

    whisperx_bin="${WHISPERX_BIN:-}"
    whisper_bin="${WHISPER_BIN:-}"
    if [[ -z "$whisperx_bin" ]]; then
      if command -v whisperx >/dev/null 2>&1; then
        whisperx_bin="$(command -v whisperx)"
      elif [[ -x "$HOME/.local/bin/whisperx" ]]; then
        whisperx_bin="$HOME/.local/bin/whisperx"
      elif [[ -x "$HOME/.buttercut/venv/bin/whisperx" ]]; then
        whisperx_bin="$HOME/.buttercut/venv/bin/whisperx"
      elif [[ -x "$HOME/.perfect-cuts/venv/bin/whisperx" ]]; then
        whisperx_bin="$HOME/.perfect-cuts/venv/bin/whisperx"
      elif [[ -x "$HOME/GitHub/Marketing Assets and Skills/.venv-whisperx/bin/whisperx" ]]; then
        whisperx_bin="$HOME/GitHub/Marketing Assets and Skills/.venv-whisperx/bin/whisperx"
      elif [[ -x "$HOME/Library/Python/3.9/bin/whisperx" ]]; then
        whisperx_bin="$HOME/Library/Python/3.9/bin/whisperx"
      fi
    fi
    if [[ -z "$whisper_bin" ]]; then
      if command -v whisper >/dev/null 2>&1; then
        whisper_bin="$(command -v whisper)"
      elif [[ -x "$HOME/.local/bin/whisper" ]]; then
        whisper_bin="$HOME/.local/bin/whisper"
      elif [[ -x "$HOME/Library/Python/3.9/bin/whisper" ]]; then
        whisper_bin="$HOME/Library/Python/3.9/bin/whisper"
      fi
    fi

    if [[ -n "$whisperx_bin" ]]; then
      "$whisperx_bin" "$audio_file" \
        --model "${WHISPER_MODEL:-base}" \
        --device "${WHISPER_DEVICE:-cpu}" \
        --compute_type "${WHISPER_COMPUTE_TYPE:-int8}" \
        --output_dir "$dest" \
        --output_format all
    elif [[ -n "$whisper_bin" ]]; then
      "$whisper_bin" "$audio_file" \
        --model "${WHISPER_MODEL:-base}" \
        --device "${WHISPER_DEVICE:-cpu}" \
        --output_dir "$dest" \
        --output_format all
    else
      echo "No local whisperx or whisper executable found." >&2
      echo "Set WHISPERX_BIN or WHISPER_BIN, or install one of them." >&2
      exit 127
    fi

    md_path="$(write_markdown_transcript "$dest" "$url")"
    printf 'Saved exact Markdown transcript to %s\n' "$md_path"
    ;;
  playlist)
    run_ytdlp --yes-playlist --download-archive "$dest/archive.txt" \
      --write-info-json --write-thumbnail \
      -P "$dest" -o "%(playlist_index)03d - %(title).180B [%(id)s].%(ext)s" "$url"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
