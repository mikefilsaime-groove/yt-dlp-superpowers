#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skills_dir="$HOME/.claude/skills"

echo "Installing yt-dlp Superpowers..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 127
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required but was not found." >&2
  echo "macOS: brew install ffmpeg" >&2
  exit 127
fi

python3 -m pip install --user --upgrade yt-dlp
python3 -m pip install --user -r "$repo_dir/requirements.txt"

mkdir -p "$skills_dir"
rm -rf "$skills_dir/yt-dlp" "$skills_dir/watch-video"
cp -R "$repo_dir/skills/yt-dlp" "$skills_dir/yt-dlp"
cp -R "$repo_dir/skills/watch-video" "$skills_dir/watch-video"

chmod +x "$skills_dir/yt-dlp/scripts/ytdlp_job.sh"
chmod +x "$skills_dir/watch-video/scripts/watch_video.py"

echo
echo "Installed skills:"
echo "  $skills_dir/yt-dlp"
echo "  $skills_dir/watch-video"
echo
echo "Verifying commands..."
python3 -m yt_dlp --version >/dev/null
ffmpeg -version >/dev/null

if command -v whisperx >/dev/null 2>&1 || [[ -x "$HOME/.local/bin/whisperx" ]]; then
  echo "  whisperx found"
else
  echo "  whisperx was installed as a Python package, but the executable was not found on PATH."
  echo "  If needed, set WHISPERX_BIN or add your Python user bin directory to PATH."
fi

echo "Done."

