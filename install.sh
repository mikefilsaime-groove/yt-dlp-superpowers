#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skills_dir="$HOME/.claude/skills"

echo "Installing yt-dlp Superpowers..."

require_cmd() {
  local cmd="$1"
  local hint="${2:-}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$cmd is required but was not found." >&2
    if [[ -n "$hint" ]]; then
      echo "$hint" >&2
    fi
    exit 127
  fi
}

find_whisperx() {
  if [[ -n "${WHISPERX_BIN:-}" && -x "$WHISPERX_BIN" ]]; then
    printf '%s\n' "$WHISPERX_BIN"
    return 0
  fi

  local candidates=(
    "$HOME/.local/bin/whisperx"
    "$HOME/.buttercut/venv/bin/whisperx"
    "$HOME/.perfect-cuts/venv/bin/whisperx"
    "$HOME/GitHub/Marketing Assets and Skills/.venv-whisperx/bin/whisperx"
    "$HOME/Library/Python/3.9/bin/whisperx"
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  command -v whisperx 2>/dev/null
}

pip_install_user() {
  local log_file
  log_file="$(mktemp)"

  if python3 -m pip install --user "$@" 2>"$log_file"; then
    rm -f "$log_file"
    return 0
  fi

  if grep -q "externally-managed-environment" "$log_file"; then
    rm -f "$log_file"
    echo "Homebrew Python blocked pip --user; retrying with pip's user-site override."
    python3 -m pip install --user --break-system-packages "$@"
    return $?
  fi

  cat "$log_file" >&2
  rm -f "$log_file"
  return 1
}

require_cmd python3 "Install Python 3, then re-run ./install.sh."
require_cmd ffmpeg "macOS: brew install ffmpeg"

pip_install_user --upgrade yt-dlp
pip_install_user -r "$repo_dir/requirements.txt"

mkdir -p "$skills_dir"
rm -rf "$skills_dir/yt-dlp" "$skills_dir/yt-dlp-superpowers" "$skills_dir/watch-video" "$skills_dir/perfect-cuts"
cp -R "$repo_dir/skills/yt-dlp-superpowers" "$skills_dir/yt-dlp-superpowers"
cp -R "$repo_dir/skills/watch-video" "$skills_dir/watch-video"
cp -R "$repo_dir/skills/perfect-cuts" "$skills_dir/perfect-cuts"

chmod +x "$skills_dir/yt-dlp-superpowers/scripts/ytdlp_job.sh"
chmod +x "$skills_dir/watch-video/scripts/watch_video.py"
chmod +x "$skills_dir/perfect-cuts/scripts/open-in-remotion-mac.command" 2>/dev/null || true

echo
echo "Installed skills:"
echo "  $skills_dir/yt-dlp-superpowers"
echo "  $skills_dir/watch-video"
echo "  $skills_dir/perfect-cuts"
echo
echo "Verifying commands..."
python_ytdlp_version="$(python3 -m yt_dlp --version)"
ffmpeg -version >/dev/null

echo "  yt-dlp Python package: $python_ytdlp_version"
if command -v yt-dlp >/dev/null 2>&1; then
  cli_ytdlp_path="$(command -v yt-dlp)"
  cli_ytdlp_version="$(yt-dlp --version 2>/dev/null || true)"
  echo "  yt-dlp executable: $cli_ytdlp_path ${cli_ytdlp_version:+($cli_ytdlp_version)}"
  if [[ -n "$cli_ytdlp_version" && "$cli_ytdlp_version" != "$python_ytdlp_version" ]]; then
    echo "  note: skill scripts prefer python3 -m yt_dlp unless YTDLP_BIN is set."
  fi
else
  echo "  yt-dlp Python package found, but the yt-dlp executable is not on PATH."
  echo "  skill scripts will use python3 -m yt_dlp unless YTDLP_BIN is set."
fi

whisperx_path="$(find_whisperx || true)"
if [[ -n "$whisperx_path" ]]; then
  echo "  whisperx found: $whisperx_path"
else
  echo "  whisperx not found. Transcript fallback and Perfect Cuts need a local WhisperX install."
  echo "  Set WHISPERX_BIN if WhisperX lives in a custom location."
fi

if command -v node >/dev/null 2>&1; then
  echo "  node found: $(command -v node)"
else
  echo "  node not found. Only the optional Perfect Cuts Remotion launcher needs Node.js."
fi

echo "Done."
