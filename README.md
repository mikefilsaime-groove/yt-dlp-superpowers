# yt-dlp Superpowers

Claude/Codex skills built downstream of the excellent [yt-dlp](https://github.com/yt-dlp/yt-dlp) project.

`yt-dlp` handles the media extraction layer. This repo adds AI-agent workflows around it:

- caption-first transcript extraction with local WhisperX/Whisper fallback
- exact Markdown transcript artifacts for downstream research
- timestamped "watch video" context that pairs screenshots with nearby transcript text
- perfect-cut handoff for talking-head clips using local WhisperX plus waveform analysis
- re-light handoff for short cinematic relighting / re-scene workflows using Fal.ai
- installable Claude/Codex skills for repeatable workflows

This is not an official yt-dlp project and does not replace yt-dlp.

## What You Get

### `yt-dlp-superpowers` Skill

Adds a predictable helper for:

- inspecting media URLs
- downloading video/audio
- downloading subtitles
- creating transcripts by trying captions first, then falling back to local WhisperX/Whisper
- handing downloaded media to `watch-video`, `perfect-cuts`, or `re-light`

### `watch-video` Skill

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

### `perfect-cuts` Skill

Turns a raw talking-head recording into an edited package:

- Premiere/Resolve XML
- MP4 render when requested
- EDL, SRT, cut log, and Remotion launcher files
- retake and false-start detection from transcript plus waveform timing

No API keys are required for the default bundle. `perfect-cuts` uses local `ffmpeg`/`ffprobe` and local WhisperX. It does not use FAL or the Gen Media connector.

### `re-light` Skill

Relights or re-scenes a 3-10 second talking-head / B-roll clip:

- extracts a sharp source frame
- generates a relit reference still with Fal.ai
- waits for user approval before spending on the video pass
- transfers the approved look to the clip while preserving audio and source timing

`re-light` requires Fal.ai credentials. It can use `FAL_KEY` directly, or it can read the existing GenMedia config at `~/.genmedia/config.json` without printing secrets. The local GenMedia CLI is also used as an upload fallback when available.

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
- installs/upgrades `yt-dlp` using the minimum upstream version floor in `requirements.txt`
- installs Python packages from `requirements.txt`
- copies `yt-dlp-superpowers`, `watch-video`, `perfect-cuts`, and `re-light` into `~/.claude/skills/`
- removes the old installed `~/.claude/skills/yt-dlp` folder if present, so the renamed skill is the one agents see
- checks for local WhisperX in common install locations and prints a note if it is missing
- checks for GenMedia / Fal.ai credential readiness for `re-light`
- preserves an existing `~/.claude/skills/re-light/config.json` across reinstalls

On Homebrew-managed Python installs, `install.sh` retries user-site `pip` installs with pip's PEP 668 override when required.

If `ffmpeg` is missing on macOS:

```bash
brew install ffmpeg
```

WhisperX is intentionally not installed by default because it can pull a large local ML stack. If you already have it in a custom place, set:

```bash
export WHISPERX_BIN="/absolute/path/to/whisperx"
```

If an older `yt-dlp` executable is already on `PATH`, the bundled scripts prefer the freshly installed Python package via `python3 -m yt_dlp`. Set `YTDLP_BIN` only when you need to force a specific executable.

For `re-light`, configure one of these before generating:

```bash
export FAL_KEY="your-fal-key"
```

Or configure the GenMedia CLI so `~/.genmedia/config.json` exists. Do not commit or share that config file.

## Manual Skill Install

If you do not want to run the installer, copy the skill folders manually:

```bash
mkdir -p "$HOME/.claude/skills"
rm -rf "$HOME/.claude/skills/yt-dlp"
cp -R skills/yt-dlp-superpowers "$HOME/.claude/skills/"
cp -R skills/watch-video "$HOME/.claude/skills/"
cp -R skills/perfect-cuts "$HOME/.claude/skills/"
cp -R skills/re-light "$HOME/.claude/skills/"
```

Then install dependencies:

```bash
python3 -m pip install --user -r requirements.txt
```

## Usage

Transcript with caption-first fallback:

```bash
$HOME/.claude/skills/yt-dlp-superpowers/scripts/ytdlp_job.sh transcript "VIDEO_URL" "outputs/transcript"
```

Convert already-downloaded captions or Whisper output into an exact Markdown transcript:

```bash
$HOME/.claude/skills/yt-dlp-superpowers/scripts/ytdlp_job.sh transcript-md "VIDEO_URL" "outputs/transcript"
```

Watch Video bundle:

```bash
python3 "$HOME/.claude/skills/watch-video/scripts/watch_video.py" "VIDEO_URL" --output-dir "outputs/watch-video" --rate 1s
```

Use a lower frame density for long videos:

```bash
python3 "$HOME/.claude/skills/watch-video/scripts/watch_video.py" "VIDEO_URL" --output-dir "outputs/watch-video" --rate 10s
```

Perfect Cuts handoff:

```text
Use $yt-dlp-superpowers to download this video, then use $perfect-cuts on the downloaded file.
```

Re-light handoff:

```text
Use $yt-dlp-superpowers to download this short clip, then use $re-light to put it in a cinematic neon studio.
```

## Prompt To Give Claude/Codex

After cloning this repo, you can ask Claude/Codex:

```text
Install the Claude/Codex skills from this repo. Run ./install.sh, verify yt-dlp and ffmpeg, check whether WhisperX is already available, check whether GenMedia or FAL_KEY is configured for re-light, and test the skill help commands. Do not download any videos unless I provide a URL. Do not install WhisperX unless I explicitly ask you to.
```

To use the visual workflow:

```text
Watch Video: use the watch-video skill on this URL at 1 frame per second and summarize what is visible on screen compared with what is said in the transcript.
```

To clean up talking-head footage:

```text
Use $perfect-cuts on this local clip. Include an MP4 render and put the package in Downloads.
```

To relight a short clip:

```text
Use $re-light on this 7-second clip. Make it look like a warm cinematic office at night.
```

## Attribution

This project is built downstream of [yt-dlp](https://github.com/yt-dlp/yt-dlp).

All media extraction credit belongs to the yt-dlp project and its contributors. This repo adds Claude/Codex skills and workflow scripts around yt-dlp, ffmpeg, WhisperX, and Whisper.
