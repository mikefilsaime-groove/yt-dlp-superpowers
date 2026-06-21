# Example Commands

Transcript with caption-first fallback:

```bash
$HOME/.claude/skills/yt-dlp-superpowers/scripts/ytdlp_job.sh transcript "VIDEO_URL" "outputs/transcript"
```

Exact Markdown transcript from already-downloaded captions or Whisper output:

```bash
$HOME/.claude/skills/yt-dlp-superpowers/scripts/ytdlp_job.sh transcript-md "VIDEO_URL" "outputs/transcript"
```

Watch a video with one frame per second:

```bash
python3 "$HOME/.claude/skills/watch-video/scripts/watch_video.py" "VIDEO_URL" --output-dir "outputs/watch-video" --rate 1s
```

Watch a long video with one frame every ten seconds:

```bash
python3 "$HOME/.claude/skills/watch-video/scripts/watch_video.py" "VIDEO_URL" --output-dir "outputs/watch-video" --rate 10s
```

Download a video and hand it to Perfect Cuts:

```text
Use $yt-dlp-superpowers to download VIDEO_URL into outputs/source, then use $perfect-cuts on the downloaded local file.
```
