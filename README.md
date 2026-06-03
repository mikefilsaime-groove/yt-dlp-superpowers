# yt-dlp Superpowers

Claude/Codex skills built downstream of the excellent [yt-dlp](https://github.com/yt-dlp/yt-dlp) project.

`yt-dlp` handles the media extraction layer. This repo adds AI-agent workflows around it:

- transcript/caption extraction with local WhisperX/Whisper fallback
- video frame extraction with `ffmpeg`
- timestamped "watch video" context that pairs screenshots with nearby transcript text
- installable Claude/Codex skills for repeatable workflows

This is not an official yt-dlp project and does not replace yt-dlp.

## What You Get

### `yt-dlp` Skill

Adds a predictable helper for:

- inspecting media URLs
- downloading video/audio
- downloading subtitles
- creating transcripts by trying captions first, then falling back to local WhisperX/Whisper

### `watch-video` Skill

Use only when you explicitly say **Watch Video**.

It creates a multimodal evidence bundle:

- source media
- captions or local transcript
- screenshots sampled by `ffmpeg`
- `watch_context.md` pairing each frame timestamp with nearby transcript text
- `manifest.json` for structured downstream use

Frame sampling presets:

```text
4fps
2fps
1fps
1s default
3s
5s
10s
```

## Install

Clone this repo:

```bash
git clone https://github.com/mikefilsaime-groove/yt-dlp-superpowers.git
cd yt-dlp-superpowers
```

Run the installer:

```bash
./install.sh
```

The installer:

- checks for `ffmpeg`
- installs/upgrades `yt-dlp`
- installs Python packages from `requirements.txt`
- copies the skills into `~/.claude/skills/`

If `ffmpeg` is missing on macOS:

```bash
brew install ffmpeg
```

## Manual Skill Install

If you do not want to run the installer, copy the skill folders manually:

```bash
mkdir -p "$HOME/.claude/skills"
cp -R skills/yt-dlp "$HOME/.claude/skills/"
cp -R skills/watch-video "$HOME/.claude/skills/"
```

Then install dependencies:

```bash
python3 -m pip install --user -r requirements.txt
```

## Usage

Transcript with caption-first fallback:

```bash
$HOME/.claude/skills/yt-dlp/scripts/ytdlp_job.sh transcript "VIDEO_URL" "outputs/transcript"
```

Watch Video bundle:

```bash
python3 "$HOME/.claude/skills/watch-video/scripts/watch_video.py" "VIDEO_URL" --output-dir "outputs/watch-video" --rate 1s
```

Use a lower frame density for long videos:

```bash
python3 "$HOME/.claude/skills/watch-video/scripts/watch_video.py" "VIDEO_URL" --output-dir "outputs/watch-video" --rate 10s
```

## Prompt To Give Claude/Codex

After cloning this repo, you can ask Claude/Codex:

```text
Install the Claude/Codex skills from this repo. Run ./install.sh, verify yt-dlp, ffmpeg, and WhisperX are available, then test the skill help commands. Do not download any videos unless I provide a URL.
```

To use the visual workflow:

```text
Watch Video: use the watch-video skill on this URL at 1 frame per second and summarize what is visible on screen compared with what is said in the transcript.
```

## Attribution

This project is built downstream of [yt-dlp](https://github.com/yt-dlp/yt-dlp).

All media extraction credit belongs to the yt-dlp project and its contributors. This repo adds Claude/Codex skills and workflow scripts around yt-dlp, ffmpeg, WhisperX, and Whisper.

