"""re-light skill — relight / re-scene a short clip via Fal.ai. Cross-platform (macOS/Windows/Linux).

Two model calls, orchestrated by Claude with a human approval gate between them:

  1. nano-banana-2/edit  — relight one frame into the target scene/lighting (cheap, ~$0.10)
  2. kling-video o3-pro   — push that look onto the whole clip, audio preserved (~$0.168/sec)

Self-bootstrapping: missing Python deps (fal-client, and a bundled ffmpeg via imageio-ffmpeg
when the system has none) are installed automatically on first use — no fuss to the user.

Reuses FAL_KEY or the local GenMedia fal.ai config, so if GenMedia is set up this needs no
separate credentials step. GenMedia CLI upload is also available as an upload fallback.

Subcommands:
  python relight.py setup [--output-dir <path>]
  python relight.py prep  --video <path> --title <slug> [--frame-time <sec>]
  python relight.py image --frame <path> --prompt "<text>" --folder <dir> [--reference <img>] [--aspect-ratio 16:9]
  python relight.py video --video <path> --still <path> --prompt "<text>" --folder <dir> [--model o3-pro|o1]

Each prints a JSON line on stdout. The script never prompts interactively (Claude asks the user).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ── Model registry (self-contained — these edit endpoints are not in media-gen's models.json) ──
IMAGE_MODEL = "fal-ai/nano-banana-2/edit"               # subject-consistent relight of the still
VIDEO_MODELS = {
    "o3-pro": "fal-ai/kling-video/o3/pro/video-to-video/edit",   # DEFAULT — best lip-sync / least morph
    "o1":     "fal-ai/kling-video/o1/video-to-video/edit",       # faster/cheaper draft, same per-sec price
}
DEFAULT_VIDEO_MODEL = "o3-pro"
MIN_DURATION = 3.0
MAX_DURATION = 10.0   # Kling edit hard cap — clips outside this are rejected

# Kling always returns 24fps and quantizes the clip to a whole number of frames in its
# processing window — dropping the final partial window (~0.1-0.2s). For talking-head
# dialogue that lost tail breaks lip-sync, so the video step tail-pads BEFORE the Kling
# pass and then conforms back to the source's EXACT fps + frame count + length afterward.
KLING_FPS = 24            # Kling's native output frame rate (every edit comes back at 24fps)
KLING_MAX_INPUT = 10.0    # Kling edit input-duration cap (seconds)
TAIL_PAD_SECONDS = 0.5    # freeze-pad appended before Kling so its tail-trim eats padding,
                          # not real frames; the pad is removed again during the conform

# Identity/geometry lock — ALWAYS applied (even with --no-cinematic). This is a VIDEO relight, so the
# still must stay pixel-aligned with the source frame or the Kling transfer breaks. Only light + bg change.
IDENTITY_LOCK = (
    "CRITICAL — this is a relight of an existing video frame, so the geometry must not change. Preserve "
    "the source EXACTLY: same face and facial features, same head pose and expression, same eye "
    "direction, same camera angle, same framing and crop, same distance and subject size, same body "
    "position. Do NOT move, zoom, rotate, re-pose, re-crop or re-compose the person — keep them aligned "
    "with the original. Change ONLY the lighting, the color grade, and the background behind the subject."
)

# Skin fidelity — ALWAYS applied (even with --no-cinematic). Image models over-render skin under
# dramatic/cool light: adding wrinkles, texture and grey/sickly tones that age the subject. This protects it.
SKIN_FIDELITY = (
    "PROTECT THE FACE — keep the subject looking like himself at his real age. Preserve his actual "
    "complexion from the source: smooth, clean, healthy skin. Do NOT add wrinkles, fine lines, age spots, "
    "blemishes, rough or leathery texture or exaggerated pores, and do NOT introduce grey, sallow, ashen "
    "or sickly tones or make him look older. Keep skin warm, even, healthy and flattering with natural "
    "color and a subtle healthy sheen — even under cool or neon lighting, let a warm key or warm bounce "
    "keep the face from going grey. Photoreal but clean, fresh and youthful, never aged, gaunt or sickly."
)

# Cinematic quality layer — appended (unless --no-cinematic) so every relight reaches a true film look
# through LIGHT and COLOR, never by re-framing. Natural-language (Nano-Banana-friendly).
CINEMATIC_IMAGE = (
    "Make this look like a beautiful cinematic film still through lighting and color only, without "
    "changing the framing or composition. Relight with motivated, directional light: a flattering shaped "
    "key — kept warm and healthy on the face — giving dimensional highlights and a soft-edged shadow on "
    "the opposite side for gentle chiaroscuro, a rim/kicker separating the subject from the background, "
    "and smooth realistic falloff — never flat or evenly lit. Throw the background into soft shallow-"
    "depth-of-field bokeh with subtle lens bloom on bright sources. Rich filmic color grade with cinematic "
    "contrast, clean highlights and a deliberate palette, while keeping skin tones warm and healthy. "
    "Subtle film grain in the shadows and background only — keep the skin clean and smooth. Volumetric "
    "haze and light wrap so the subject is genuinely lit by the scene. Photorealistic, high production "
    "value, flattering, evocative and beautiful."
)
CINEMATIC_VIDEO = (
    "Carry the full cinematic look of @Image1 across every frame — its color grade, contrast, directional "
    "lighting, lens character, shallow depth of field and atmosphere. Filmic, high production value, "
    "photorealistic, stable and consistent. Keep the subject's skin warm, healthy and his real age — no "
    "added wrinkles, texture or grey/sickly tones."
)

# Ambient background motion — baked into the video pass so the relit scene feels alive, not a static
# matte. The model animates ONLY environmental/atmospheric elements; the subject stays locked to the
# source footage. Claude names the specific movers per scene; this guarantees the behaviour. (--no-motion)
AMBIENT_MOTION = (
    "Bring the scene gently to life with subtle, slow, natural ambient motion in the background and "
    "atmosphere ONLY: drifting haze or smoke, softly swaying or slowly panning lights, gently shifting "
    "beams and glints, faint reflections and flicker — calm, understated and cinematic, never busy or "
    "distracting. Keep the person exactly as in the source video: same identity, same facial expression, "
    "same lip and mouth movements, same head and body motion and the same timing — add no motion to the "
    "subject. The background feels alive but relaxed while the subject stays locked to the original footage."
)


def cinematic_suffix(kind: str, on: bool) -> str:
    if not on:
        return ""
    return "\n\n" + (CINEMATIC_IMAGE if kind == "image" else CINEMATIC_VIDEO)

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"
DEFAULT_OUTPUT_DIR = "~/Documents/Relight"
GENMEDIA_CONFIG_PATH = Path(os.environ.get("GENMEDIA_CONFIG", "~/.genmedia/config.json")).expanduser()
GENMEDIA_CLI_CANDIDATES = (
    os.environ.get("GENMEDIA_CLI"),
    "/Users/mikefilsaime/.local/bin/genmedia",
    "~/.local/bin/genmedia",
    "~/.genmedia/bin/genmedia",
    shutil.which("genmedia"),
)


def log(msg: str) -> None:
    sys.stderr.write(f"[re-light] {msg}\n")
    sys.stderr.flush()


# ── Dependency bootstrap (auto-install, quietly) ───────────────────────────

def ensure_package(pip_name: str, import_name: str | None = None):
    """Import a package, auto-installing it from PyPI on first miss. Returns the module."""
    import_name = import_name or pip_name.replace("-", "_")
    try:
        return __import__(import_name)
    except ImportError:
        pass
    log(f"installing {pip_name} (one-time)…")
    # escalate: plain → user-site → user-site over PEP 668 (Homebrew/Debian managed Python)
    for extra in ([], ["--user"], ["--user", "--break-system-packages"]):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *extra, pip_name],
                           check=True)
            return __import__(import_name)
        except (subprocess.CalledProcessError, ImportError):
            continue
    sys.stderr.write(
        f"ERROR: couldn't auto-install '{pip_name}'.\n"
        f"  Install it manually: {sys.executable} -m pip install {pip_name}\n"
    )
    sys.exit(2)


def ensure_ffmpeg() -> tuple[str, str | None]:
    """Return (ffmpeg_path, ffprobe_path_or_None). Prefer system binaries; otherwise fall back
    to the pip-bundled ffmpeg from imageio-ffmpeg (cross-platform, no admin rights needed)."""
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    if ff:
        return ff, fp
    imageio_ffmpeg = ensure_package("imageio-ffmpeg", "imageio_ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe(), fp  # bundled build has no separate ffprobe


# ── Credentials (cross-platform; reuses FAL_KEY or GenMedia's fal.ai config) ──

def _genmedia_config_key() -> str | None:
    """Read the fal.ai key from GenMedia's config without printing it."""
    if not GENMEDIA_CONFIG_PATH.is_file():
        return None
    try:
        data = json.loads(GENMEDIA_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    for field in ("apiKey", "falKey", "fal_key", "FAL_KEY"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_fal_key(required: bool = True) -> str | None:
    """FAL_KEY from env/shell profile, or GenMedia's configured fal.ai key.

    The key is copied into os.environ for fal-client compatibility, but never logged.
    """
    key = os.environ.get("FAL_KEY")
    if key:
        return key
    if os.name != "nt":  # shell-profile recovery is a Unix convenience; Windows uses env/registry
        for profile in ("~/.zshrc", "~/.zprofile", "~/.bash_profile", "~/.bashrc", "~/.profile"):
            p = Path(os.path.expanduser(profile))
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r'^\s*export\s+FAL_KEY\s*=\s*"?([^"\s]+)"?\s*$', line)
                if m:
                    os.environ["FAL_KEY"] = m.group(1)
                    return m.group(1)
    key = _genmedia_config_key()
    if key:
        os.environ["FAL_KEY"] = key
        return key
    if required:
        setx = ('  Windows: setx FAL_KEY "<your-key>"  (then reopen the terminal)\n'
                if os.name == "nt" else
                '  Mac/Linux: add `export FAL_KEY="<your-key>"` to ~/.zshrc, then `source ~/.zshrc`\n')
        sys.stderr.write("ERROR: FAL_KEY not set.\n" + setx +
                         "  Or run `genmedia setup`; re-light can read ~/.genmedia/config.json.\n")
        sys.exit(3)
    return None


def fal_key_source() -> str:
    """Human-safe credential source for readiness output. Never returns the key."""
    if os.environ.get("FAL_KEY"):
        return "FAL_KEY"
    if _genmedia_config_key():
        return "GenMedia config"
    if os.name != "nt":
        for profile in ("~/.zshrc", "~/.zprofile", "~/.bash_profile", "~/.bashrc", "~/.profile"):
            p = Path(os.path.expanduser(profile))
            if p.is_file() and re.search(r"^\s*export\s+FAL_KEY\s*=", p.read_text(encoding="utf-8", errors="ignore"), re.M):
                return "shell profile"
    return "MISSING"


def genmedia_cli_path() -> str | None:
    for candidate in GENMEDIA_CLI_CANDIDATES:
        if not candidate:
            continue
        path = Path(os.path.expanduser(os.path.expandvars(candidate)))
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def genmedia_upload_file(path: Path) -> str | None:
    """Upload through the local GenMedia CLI. Returns a CDN URL, or None on failure."""
    cli = genmedia_cli_path()
    if not cli:
        return None
    r = subprocess.run([cli, "upload", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        log("GenMedia upload fallback failed.")
        return None
    out = r.stdout.strip()
    try:
        data = json.loads(out)
        for field in ("cdn_url", "url"):
            value = data.get(field)
            if isinstance(value, str) and value.startswith("https://"):
                return value
    except json.JSONDecodeError:
        pass
    m = re.search(r"https://\S+", out)
    return m.group(0).rstrip('",') if m else None


# ── Config / output dir ────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def resolve_output_dir() -> Path:
    out = load_config().get("output_dir") or DEFAULT_OUTPUT_DIR
    return Path(os.path.expanduser(os.path.expandvars(out)))


def save_output_dir(output_dir: str) -> None:
    CONFIG_PATH.write_text(json.dumps({"output_dir": output_dir}, indent=2) + "\n",
                           encoding="utf-8")


# ── Small utilities ────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s[:50] or "untitled"


def expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


def media_info(video: Path, ffmpeg: str, ffprobe: str | None) -> tuple[float, int, int]:
    """(duration_sec, width, height). Uses ffprobe if present, else parses ffmpeg -i stderr."""
    if ffprobe:
        dur = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                             capture_output=True, text=True)
        dims = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                               "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
                              capture_output=True, text=True)
        if dur.returncode == 0 and dims.returncode == 0 and dur.stdout.strip():
            w, h = (dims.stdout.strip().split(",") + ["0", "0"])[:2]
            return float(dur.stdout.strip()), int(w), int(h)
    # Fallback: parse `ffmpeg -i` stderr (works with the bundled binary, no ffprobe needed)
    out = subprocess.run([ffmpeg, "-i", str(video)], capture_output=True, text=True).stderr
    dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not dm:
        sys.stderr.write("ERROR: could not read video duration.\n"); sys.exit(5)
    dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
    sm = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    w, h = (int(sm.group(1)), int(sm.group(2))) if sm else (0, 0)
    return dur, w, h


def pick_aspect(w: int, h: int) -> str:
    if h == 0:
        return "16:9"
    r = w / h
    table = {"16:9": 16/9, "9:16": 9/16, "1:1": 1.0, "4:3": 4/3, "3:4": 3/4}
    return min(table, key=lambda k: abs(table[k] - r))


# ── Exact frame-rate / frame-count handling (so the relit clip matches the source 1:1) ──

def _rational_fps(s):
    """'60000/1001' -> (num, den, float). '30' -> (30, 1, 30.0). Robust to junk."""
    s = str(s).strip()
    try:
        if "/" in s:
            n, d = s.split("/"); n, d = int(n), int(d)
        else:
            n, d = int(round(float(s))), 1
    except (ValueError, ZeroDivisionError):
        return 30, 1, 30.0
    d = d or 1
    return n, d, n / d


def _approx_fps(f):
    """Map a measured float fps to an exact rational string (handles NTSC fractional rates)."""
    ntsc = {23.976: "24000/1001", 29.97: "30000/1001", 47.952: "48000/1001",
            59.94: "60000/1001", 119.88: "120000/1001"}
    for k, v in ntsc.items():
        if abs(f - k) < 0.06:
            return v, _rational_fps(v)[2]
    n = max(1, int(round(f)))
    return f"{n}/1", float(n)


def probe_exact(video, ffmpeg, ffprobe):
    """Exact source timing: {fps_str, fps, nb_frames, duration, width, height, samplerate}.
    Prefers ffprobe (counts frames for accuracy); falls back to ffmpeg -i + null-decode."""
    if ffprobe:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries",
             "stream=r_frame_rate,nb_read_frames,nb_frames,width,height:format=duration",
             "-of", "json", str(video)],
            capture_output=True, text=True)
        try:
            data = json.loads(r.stdout)
            st = data["streams"][0]
        except (json.JSONDecodeError, KeyError, IndexError):
            st, data = {}, {}
        fps_str = st.get("r_frame_rate", "30/1")
        _, _, fps = _rational_fps(fps_str)
        nb = int(st.get("nb_read_frames") or 0) or int(st.get("nb_frames") or 0)
        fmt_dur = float((data.get("format") or {}).get("duration") or 0)
        if nb <= 0 and fmt_dur and fps:
            nb = int(round(fmt_dur * fps))
        w = int(st.get("width") or 1920); h = int(st.get("height") or 1080)
        ar = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
                             "-show_entries", "stream=sample_rate", "-of",
                             "default=nokey=1:noprint_wrappers=1", str(video)],
                            capture_output=True, text=True)
        sr = int(ar.stdout.strip()) if ar.stdout.strip().isdigit() else 48000
    else:
        out = subprocess.run([ffmpeg, "-i", str(video)], capture_output=True, text=True).stderr
        m = re.search(r"(\d+(?:\.\d+)?)\s+tbr", out) or re.search(r"(\d+(?:\.\d+)?)\s+fps", out)
        fps_str, fps = _approx_fps(float(m.group(1)) if m else 30.0)
        dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
        fmt_dur = (int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))) if dm else 0
        sm = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
        w, h = (int(sm.group(1)), int(sm.group(2))) if sm else (1920, 1080)
        srm = re.search(r"(\d+) Hz", out); sr = int(srm.group(1)) if srm else 48000
        nd = subprocess.run([ffmpeg, "-nostdin", "-i", str(video), "-map", "0:v:0",
                             "-f", "null", "-"], capture_output=True, text=True).stderr
        fm = re.findall(r"frame=\s*(\d+)", nd)
        nb = int(fm[-1]) if fm else (int(round(fmt_dur * fps)) if fmt_dur else 0)
    duration = nb / fps if (fps and nb) else fmt_dur
    return {"fps_str": fps_str, "fps": fps, "nb_frames": int(nb),
            "duration": duration, "width": int(w), "height": int(h), "samplerate": int(sr)}


def pad_for_kling(ffmpeg, video, fps_str, pad, dest):
    """Freeze-hold the last frame (+ silence) for `pad` seconds, keeping the source fps."""
    r = subprocess.run(
        [ffmpeg, "-y", "-nostdin", "-i", str(video),
         "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
         "-af", f"apad=pad_dur={pad:.3f}",
         "-r", fps_str, "-c:v", "libx264", "-preset", "medium", "-crf", "16",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("ERROR: tail-pad step failed:\n" + r.stderr[-1500:] + "\n"); sys.exit(8)
    return dest


def conform_exact(ffmpeg, raw, original, src, dest):
    """Resample Kling's 24fps output back to the source's EXACT fps + frame count and remux the
    pristine original audio. A 1s clone-pad before the frame trim makes the length exact even if
    Kling came up short (safety net; the pre-pad normally keeps real frames through the true end).
    Result is frame-for-frame the same length as the source — required for dialogue sync."""
    F = src["fps_str"]; N = int(src["nb_frames"]); D = float(src["duration"])
    filt = (f"[0:v]fps={F},tpad=stop_mode=clone:stop_duration=1,"
            f"trim=end_frame={N},setpts=PTS-STARTPTS[v];"
            f"[1:a]atrim=0:{D:.6f},apad=whole_dur={D:.6f},asetpts=PTS-STARTPTS[a]")
    r = subprocess.run(
        [ffmpeg, "-y", "-nostdin", "-i", str(raw), "-i", str(original),
         "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
         "-r", F, "-c:v", "libx264", "-preset", "medium", "-crf", "16",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("ERROR: conform/trim step failed:\n" + r.stderr[-1500:] + "\n"); sys.exit(8)
    return dest


def next_index(folder: Path, kind: str, ext: str) -> int:
    pat = re.compile(rf"^{re.escape(kind)}-(\d+) \(C\)\.{ext}$")
    used = [int(m.group(1)) for f in folder.iterdir() if (m := pat.match(f.name))]
    return (max(used) + 1) if used else 1


def safe_download(url: str, dest: Path) -> None:
    if not url.lower().startswith("https://"):
        sys.stderr.write(f"ERROR: refusing to download non-HTTPS URL: {url[:60]}…\n")
        sys.exit(7)
    urllib.request.urlretrieve(url, dest)


def fal_subscribe(fal_id: str, call_args: dict):
    fal_client = ensure_package("fal-client", "fal_client")
    get_fal_key(required=True)  # ensures FAL_KEY is in env for the client
    return fal_client, fal_client.subscribe(fal_id, arguments=call_args, with_logs=False)


def upload_file(fal_client, path: Path) -> str:
    """Upload a local file to fal.ai. Prefer fal-client; fall back to GenMedia CLI."""
    try:
        return fal_client.upload_file(str(path))
    except Exception as exc:
        log(f"fal-client upload failed ({exc.__class__.__name__}); trying GenMedia CLI upload…")
        url = genmedia_upload_file(path)
        if url:
            return url
        raise


def write_meta(folder: Path, title: str, block: str) -> None:
    md = folder / "relight.md"
    if not md.exists():
        md.write_text(f"# {title}\n\n**Created:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                      encoding="utf-8")
    with md.open("a", encoding="utf-8") as f:
        f.write(block)


# ── setup: install deps, set output dir, report readiness ──────────────────

def cmd_setup(args) -> None:
    ensure_package("fal-client", "fal_client")
    ffmpeg, ffprobe = ensure_ffmpeg()
    out = args.output_dir or load_config().get("output_dir") or DEFAULT_OUTPUT_DIR
    save_output_dir(out)
    resolved = Path(os.path.expanduser(os.path.expandvars(out)))
    resolved.mkdir(parents=True, exist_ok=True)
    key_source = fal_key_source()
    key_found = bool(get_fal_key(required=False))
    print(json.dumps({
        "status": "ready",
        "output_dir": str(resolved),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe or "(bundled ffmpeg — duration read via ffmpeg)",
        "fal_key": "found" if key_found else "MISSING — set FAL_KEY or run genmedia setup",
        "fal_key_source": key_source if key_found else "MISSING",
        "genmedia_cli": genmedia_cli_path() or "not found",
        "default_video_model": DEFAULT_VIDEO_MODEL,
    }))


# ── frame selection helpers ────────────────────────────────────────────────

def try_optional(pip_name: str, import_name: str):
    """Import an optional package, attempting a one-time quiet install. Never exits."""
    try:
        return __import__(import_name)
    except ImportError:
        pass
    for extra in ([], ["--user"], ["--user", "--break-system-packages"]):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *extra, pip_name],
                           check=True, capture_output=True)
            return __import__(import_name)
        except Exception:
            continue
    return None


def _extract_frame(ffmpeg: str, video: Path, t: float, dest: Path) -> bool:
    r = subprocess.run([ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-q:v", "2", str(dest)],
                       capture_output=True, text=True)
    return r.returncode == 0 and dest.is_file()


def _sharpness(path: Path, pil) -> float | None:
    """Focus measure (variance of edges) over the centre/subject region. Higher = sharper;
    motion-blurred frames score low. Returns None if Pillow isn't available."""
    if pil is None:
        return None
    try:
        from PIL import Image, ImageFilter, ImageStat
        im = Image.open(path).convert("L")
        W, H = im.size
        cw, ch = int(W * 0.60), int(H * 0.72)          # subject sits centre, slightly high
        left, top = (W - cw) // 2, int(H * 0.10)
        im = im.crop((left, top, left + cw, min(top + ch, H)))
        im.thumbnail((512, 512))
        return float(ImageStat.Stat(im.filter(ImageFilter.FIND_EDGES)).var[0])
    except Exception:
        return None


# ── prep: validate duration, then auto-pick the SHARPEST frame (no blur, not random) ──

def cmd_prep(args) -> None:
    video = expand(args.video)
    if not video.is_file():
        sys.stderr.write(f"ERROR: video not found: {video}\n"); sys.exit(5)
    ffmpeg, ffprobe = ensure_ffmpeg()

    dur, w, h = media_info(video, ffmpeg, ffprobe)
    if dur < MIN_DURATION - 0.05:
        sys.stderr.write(f"ERROR: clip is {dur:.1f}s — too short. Kling edit needs ≥{MIN_DURATION:.0f}s.\n")
        sys.exit(6)
    if dur > MAX_DURATION + 0.05:
        sys.stderr.write(f"ERROR: clip is {dur:.1f}s — too long. Kling edit caps at {MAX_DURATION:.0f}s. "
                         f"Trim to a ≤{MAX_DURATION:.0f}s segment first, then re-run.\n")
        sys.exit(6)

    folder = resolve_output_dir() / f"{time.strftime('%Y-%m-%d')}-{slugify(args.title)}"
    folder.mkdir(parents=True, exist_ok=True)
    frame_path = folder / "source-frame (C).png"

    # Manual override: caller pinned an exact timestamp → use it, skip scoring.
    if args.frame_time is not None:
        t = max(0.0, min(args.frame_time, dur - 0.05))
        if not _extract_frame(ffmpeg, video, t, frame_path):
            sys.stderr.write("ERROR: frame extraction failed.\n"); sys.exit(5)
        print(json.dumps({"folder": str(folder), "frame_path": str(frame_path),
                          "duration": round(dur, 2), "width": w, "height": h,
                          "aspect_ratio": pick_aspect(w, h), "frame_time": round(t, 2),
                          "sharpness": None, "candidates": []}))
        return

    # Auto: sample several frames across the middle of the clip, score each for sharpness,
    # keep the sharpest (rejects motion blur). Candidates returned ranked for an eyes-open check.
    n = max(3, getattr(args, "candidates", 8))
    lo, hi = max(0.0, dur * 0.12), min(dur - 0.05, dur * 0.88)
    if hi <= lo:
        lo, hi = 0.0, max(0.0, dur - 0.05)
    times = [round(lo + i * (hi - lo) / (n - 1), 2) for i in range(n)]

    cand_dir = folder / ".candidates"; cand_dir.mkdir(exist_ok=True)
    pil = try_optional("pillow", "PIL")
    scored = []
    for i, t in enumerate(times):
        cp = cand_dir / f"cand-{i:02d}.png"
        if _extract_frame(ffmpeg, video, t, cp):
            scored.append({"time": t, "path": str(cp), "sharpness": _sharpness(cp, pil)})
    if not scored:
        sys.stderr.write("ERROR: could not extract any candidate frames.\n"); sys.exit(5)

    if any(c["sharpness"] is not None for c in scored):
        ranked = sorted(scored, key=lambda c: c["sharpness"] if c["sharpness"] is not None else -1.0,
                        reverse=True)
    else:
        log("Pillow unavailable — falling back to midpoint frame (install pillow for blur-aware picks).")
        ranked = sorted(scored, key=lambda c: abs(c["time"] - dur / 2))
    best = ranked[0]
    shutil.copyfile(best["path"], frame_path)

    print(json.dumps({"folder": str(folder), "frame_path": str(frame_path),
                      "duration": round(dur, 2), "width": w, "height": h,
                      "aspect_ratio": pick_aspect(w, h), "frame_time": best["time"],
                      "sharpness": best["sharpness"], "candidates": ranked[:4]}))


# ── image: relight the still via nano-banana-2 ─────────────────────────────

def cmd_image(args) -> None:
    frame = expand(args.frame)
    if not frame.is_file():
        sys.stderr.write(f"ERROR: frame not found: {frame}\n"); sys.exit(5)
    folder = expand(args.folder); folder.mkdir(parents=True, exist_ok=True)
    fal_client = ensure_package("fal-client", "fal_client")
    get_fal_key(required=True)

    log("uploading source frame…")
    image_urls = [upload_file(fal_client, frame)]
    if args.reference:
        ref = expand(args.reference)
        if not ref.is_file():
            sys.stderr.write(f"ERROR: reference image not found: {ref}\n"); sys.exit(5)
        log("uploading reference scene…")
        image_urls.append(upload_file(fal_client, ref))

    cine = getattr(args, "cinematic", True)
    prompt = (IDENTITY_LOCK + "\n\n" + SKIN_FIDELITY + "\n\n"
              + args.prompt.rstrip() + cinematic_suffix("image", cine))
    call_args = {"prompt": prompt, "image_urls": image_urls,
                 "aspect_ratio": args.aspect_ratio or "16:9", "resolution": "2K", "num_images": 1}
    log(f"relighting still with {IMAGE_MODEL}" + (" (cinematic)" if cine else "") + "…")
    result = fal_client.subscribe(IMAGE_MODEL, arguments=call_args, with_logs=False)
    url = result["images"][0]["url"]

    idx = next_index(folder, "still", "png")
    still_path = folder / f"still-{idx:02d} (C).png"
    safe_download(url, still_path)
    write_meta(folder, folder.name,
               f"\n## Still relight ({IMAGE_MODEL})\n- **File:** `{still_path.name}`\n"
               f"- **Reference image:** {'yes' if args.reference else 'no'}\n\n### Prompt\n\n{args.prompt}\n")
    print(json.dumps({"still_path": str(still_path), "folder": str(folder), "fal_url": url}))


# ── video: push the look onto the clip via Kling O3-Pro/O1 edit ────────────

def cmd_video(args) -> None:
    fal_id = VIDEO_MODELS.get(args.model)
    if not fal_id:
        sys.stderr.write(f"ERROR: unknown model '{args.model}'. Options: {list(VIDEO_MODELS)}\n"); sys.exit(4)
    video, still = expand(args.video), expand(args.still)
    for label, p in (("video", video), ("still", still)):
        if not p.is_file():
            sys.stderr.write(f"ERROR: {label} not found: {p}\n"); sys.exit(5)
    folder = expand(args.folder); folder.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = ensure_ffmpeg()
    fal_client = ensure_package("fal-client", "fal_client")
    get_fal_key(required=True)

    # Probe the source's EXACT timing. This is talking-head dialogue, so the final clip must match
    # the input frame-for-frame (fps + frame count + length) or the audio drifts out of sync.
    src = probe_exact(video, ffmpeg, ffprobe)
    have_exact = src["nb_frames"] > 0
    if have_exact:
        log(f"source: {src['nb_frames']} frames @ {src['fps_str']} = {src['duration']:.4f}s "
            f"({src['width']}x{src['height']})")
    else:
        log("WARNING: could not read an exact source frame count — keeping Kling's raw output "
            "(length may not match the source exactly).")

    # Tail-pad the source so Kling's 24fps quantization trims the padding, never real frames.
    pad = min(args.pad_seconds, max(0.0, KLING_MAX_INPUT - src["duration"])) if have_exact else 0.0
    kling_input, padded = video, folder / "_kling-input.mp4"
    if pad >= 0.05:
        if pad < args.pad_seconds - 0.01:
            log(f"clip is {src['duration']:.2f}s (near Kling's {KLING_MAX_INPUT:.0f}s cap) — "
                f"padding trimmed to {pad:.2f}s; keep clips <= "
                f"{KLING_MAX_INPUT - args.pad_seconds:.1f}s for full tail safety.")
        log(f"tail-padding source by {pad:.2f}s of freeze before the Kling pass…")
        kling_input = pad_for_kling(ffmpeg, video, src["fps_str"], pad, padded)
    elif have_exact:
        log(f"WARNING: clip is {src['duration']:.2f}s — no room to pad under the "
            f"{KLING_MAX_INPUT:.0f}s cap, so Kling may trim the tail. Trim to <= "
            f"{KLING_MAX_INPUT - args.pad_seconds:.1f}s for a guaranteed exact length.")

    log("uploading source video…")
    video_url = upload_file(fal_client, kling_input)
    log("uploading reference still…")
    image_url = upload_file(fal_client, still)

    prompt = args.prompt.rstrip()
    if getattr(args, "cinematic", True):
        prompt += "\n\n" + CINEMATIC_VIDEO
    if getattr(args, "motion", True):
        prompt += "\n\n" + AMBIENT_MOTION
    call_args = {"video_url": video_url, "prompt": prompt,
                 "image_urls": [image_url], "keep_audio": True}  # audio always kept — Vic's rule
    log(f"running {fal_id} (1-3 min)…")
    result = fal_client.subscribe(fal_id, arguments=call_args, with_logs=False)
    url = result["video"]["url"]

    idx = next_index(folder, "relit", "mp4")
    out_path = folder / f"relit-{idx:02d} (C).mp4"
    raw = folder / "_kling-raw.mp4"
    safe_download(url, raw)

    exact = False
    if have_exact:
        log(f"conforming {KLING_FPS}fps Kling output back to {src['fps_str']} / "
            f"{src['nb_frames']} frames and remuxing original audio…")
        conform_exact(ffmpeg, raw, video, src, out_path)
        chk = probe_exact(out_path, ffmpeg, ffprobe)
        exact = (chk["nb_frames"] == src["nb_frames"])
        if not exact:
            log(f"WARNING: output is {chk['nb_frames']} frames vs source {src['nb_frames']} — "
                f"expected an exact match; inspect {out_path.name}.")
        for tmp in (padded, raw):           # drop intermediates only after a clean conform
            try:
                if tmp.exists(): tmp.unlink()
            except OSError:
                pass
    else:
        raw.replace(out_path)               # no reliable frame count → ship Kling's raw output
        chk = probe_exact(out_path, ffmpeg, ffprobe)

    write_meta(folder, folder.name,
               f"\n## Video relight ({fal_id})\n- **File:** `{out_path.name}`\n- **keep_audio:** true\n"
               f"- **Source:** {src['nb_frames']} frames @ {src['fps_str']} ({src['duration']:.4f}s)\n"
               f"- **Tail pad:** {pad:.2f}s  |  **Kling fps:** {KLING_FPS}  |  "
               f"**Exact-length match:** {'yes' if exact else 'see warning'}\n"
               f"\n### Prompt\n\n{args.prompt}\n")
    print(json.dumps({"video_path": str(out_path), "folder": str(folder), "model": fal_id,
                      "fal_url": url, "src_fps": src["fps_str"], "frames": chk["nb_frames"],
                      "target_frames": src["nb_frames"], "duration": round(chk["duration"], 4),
                      "exact_length_match": exact}))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="re-light")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("setup", help="Install deps + set output folder")
    ps.add_argument("--output-dir", dest="output_dir", default=None,
                    help=f"Where to save outputs (default {DEFAULT_OUTPUT_DIR})")
    ps.set_defaults(func=cmd_setup)

    pp = sub.add_parser("prep", help="Validate duration + extract a frame")
    pp.add_argument("--video", required=True)
    pp.add_argument("--title", required=True)
    pp.add_argument("--frame-time", dest="frame_time", type=float, default=None,
                    help="Pin an exact timestamp (skips auto blur-aware selection)")
    pp.add_argument("--candidates", type=int, default=8,
                    help="How many frames to sample and score for sharpness (default 8)")
    pp.set_defaults(func=cmd_prep)

    pi = sub.add_parser("image", help="Relight the still via nano-banana-2")
    pi.add_argument("--frame", required=True)
    pi.add_argument("--prompt", required=True)
    pi.add_argument("--folder", required=True)
    pi.add_argument("--reference", default=None, help="Optional target-scene reference image")
    pi.add_argument("--aspect-ratio", dest="aspect_ratio", default=None)
    pi.add_argument("--no-cinematic", dest="cinematic", action="store_false",
                    help="Skip the baked-in cinematic look layer")
    pi.set_defaults(func=cmd_image, cinematic=True)

    pv = sub.add_parser("video", help="Push the look onto the clip via Kling edit")
    pv.add_argument("--video", required=True)
    pv.add_argument("--still", required=True)
    pv.add_argument("--prompt", required=True)
    pv.add_argument("--folder", required=True)
    pv.add_argument("--model", default=DEFAULT_VIDEO_MODEL, choices=list(VIDEO_MODELS))
    pv.add_argument("--no-cinematic", dest="cinematic", action="store_false",
                    help="Skip the baked-in cinematic look layer")
    pv.add_argument("--no-motion", dest="motion", action="store_false",
                    help="Keep the background static (no ambient motion)")
    pv.add_argument("--pad-seconds", dest="pad_seconds", type=float, default=TAIL_PAD_SECONDS,
                    help=f"Freeze-pad added before Kling so its tail-trim never eats real frames "
                         f"(default {TAIL_PAD_SECONDS}s; conformed back off afterward)")
    pv.set_defaults(func=cmd_video, cinematic=True, motion=True)

    return p


if __name__ == "__main__":
    a = build_parser().parse_args()
    a.func(a)
