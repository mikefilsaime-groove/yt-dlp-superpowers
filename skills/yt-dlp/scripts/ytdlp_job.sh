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

if ! command -v yt-dlp >/dev/null 2>&1; then
  echo "yt-dlp is not installed or not on PATH." >&2
  exit 127
fi

mkdir -p "$dest"

case "$mode" in
  inspect)
    yt-dlp --dump-json --no-playlist "$url" > "$dest/metadata.json"
    printf 'Saved metadata to %s\n' "$dest/metadata.json"
    ;;
  formats)
    yt-dlp -F "$url"
    ;;
  video)
    yt-dlp --no-playlist --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"
    ;;
  audio)
    yt-dlp --no-playlist -x --audio-format mp3 --audio-quality 0 \
      --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"
    ;;
  subs)
    yt-dlp --no-playlist --skip-download --write-subs --write-auto-subs \
      --sub-langs "en.*" --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"
    ;;
  transcript)
    before_count="$(find "$dest" -maxdepth 1 -type f \( -name '*.vtt' -o -name '*.srt' -o -name '*.ttml' \) | wc -l | tr -d ' ')"

    yt-dlp --no-playlist --skip-download --write-subs --write-auto-subs \
      --sub-langs "en.*" --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url" || true

    after_count="$(find "$dest" -maxdepth 1 -type f \( -name '*.vtt' -o -name '*.srt' -o -name '*.ttml' \) | wc -l | tr -d ' ')"
    if [[ "$after_count" -gt "$before_count" ]]; then
      printf 'Saved existing subtitles to %s\n' "$dest"
      exit 0
    fi

    printf 'No subtitles found; downloading audio for local transcription...\n'
    yt-dlp --no-playlist -x --audio-format mp3 --audio-quality 0 \
      --write-info-json --write-thumbnail \
      -P "$dest" -o "$template" "$url"

    audio_file="$(find "$dest" -maxdepth 1 -type f \( -name '*.mp3' -o -name '*.m4a' -o -name '*.wav' -o -name '*.webm' -o -name '*.aac' -o -name '*.ogg' \) -print0 | xargs -0 ls -t | head -1)"
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
    ;;
  playlist)
    yt-dlp --yes-playlist --download-archive "$dest/archive.txt" \
      --write-info-json --write-thumbnail \
      -P "$dest" -o "%(playlist_index)03d - %(title).180B [%(id)s].%(ext)s" "$url"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
