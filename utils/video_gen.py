"""
video_gen.py — AI Video Generator 1.0
========================================================
Fetches today's top news headlines, turns them into rich cinematic
video prompts using Z.AI GLM-4.7, then submits each prompt to an
AI video generation API.

Pipeline
  1. Fetch headlines  →  Google News RSS (region / topic / language)
  2. Write prompts    →  Z.AI GLM-4.7   (style / mood / duration)
  3. Generate videos  →  AI Video API   (aspect / duration / mode)
  4. Poll & download  →  save .mp4 files + manifest

Usage
  python utils/video_gen.py                          Interactive wizard
  python utils/video_gen.py --preset presets/x.json  Load a preset
  python utils/video_gen.py --resume manifest.json   Re-download failures
  python utils/video_gen.py --pipeline auto --topic technology --videos 3

Dependencies
  pip install requests openai colorama

Environment variables (optional — fall back to hardcoded defaults)
  VIDEO_API_KEY   AI video generation API bearer token
  ZAI_API_KEY     Z.AI / GLM-4.7 API key
"""

from __future__ import annotations

import os
import re
import sys
import time
import json
import base64
import pathlib
import datetime
import argparse
import threading
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import requests
from openai import OpenAI
from colorama import init as _colorama_init, Fore, Style

# Initialise colorama — handles Windows ANSI color codes automatically
_colorama_init(autoreset=True)

# ── API keys ──────────────────────────────────────────────────────────────────
# Both must be set as environment variables before running the script.
VIDEO_API_KEY = os.environ.get("VIDEO_API_KEY", "")
ZAI_API_KEY   = os.environ.get("ZAI_API_KEY", "")


# ── Tunable parameters ───────────────────────────────────────────────────────
# Adjust these constants to trade off quality, cost, and speed.
PROMPT_TEMPERATURE   = 0.95     # GLM creativity  (0.0 = deterministic … 1.0 = wild)
PROMPT_MAX_TOKENS    = 4096     # Token budget for GLM response (thinking + output)
SNIPPET_DISPLAY_LEN  = 100      # Max characters shown in console per article snippet
SNIPPET_PROMPT_LEN   = 250      # Max characters sent to GLM per article snippet
FETCH_MULTIPLIER     = 2        # Fetch N× requested headlines so GLM can cherry-pick
FETCH_CAP            = 30       # Hard ceiling on fetched articles
POLL_INTERVAL_SEC    = 15       # Seconds between video API status polls
POLL_TIMEOUT_SEC     = 600      # Max seconds to wait for a single video render
HTTP_TIMEOUT_SEC     = 15       # Timeout for all outbound HTTP requests
MAX_JSON_RETRIES     = 1        # Extra attempts if GLM returns unparseable JSON
RETRY_TEMP_BUMP      = -0.10    # Temperature adjustment per JSON retry (negative = safer)

# ── Project directories ───────────────────────────────────────────────────────
# Script lives in utils/ — all directories are relative to the project root.
_PROJECT_DIR = pathlib.Path(__file__).parent.parent
OUTPUT_DIR   = _PROJECT_DIR / "video_outputs"
PRESETS_DIR  = _PROJECT_DIR / "presets"
IMAGES_DIR   = _PROJECT_DIR / "images"

# Applied to every AI video — suppresses common generation artefacts
NEGATIVE_PROMPT = (
    "text, watermarks, logos, subtitles, UI elements, "
    "motion blur, lens distortion, overexposed highlights, "
    "low quality, blurry, grainy, flickering, cartoonish"
)

# ── Video API config ─────────────────────────────────────────────────────────
VIDEO_API_BASE_URL = "https://api.kie.ai/api/v1"
VIDEO_API_HEADERS  = {
    "Authorization": f"Bearer {VIDEO_API_KEY}",
    "Content-Type":  "application/json",
}

# ── Z.AI / GLM-4.7 OpenAI-compatible client ───────────────────────────────────
zai_client = OpenAI(
    api_key=ZAI_API_KEY,
    base_url="https://api.z.ai/api/coding/paas/v4/",
)

# ── Predefined Google News section topics ─────────────────────────────────────
# Any value NOT in this dict is treated as a free-text search keyword.
_TOPIC_SECTIONS: dict[str, str] = {
    "top":           "",              # default feed — no section path needed
    "world":         "WORLD",
    "nation":        "NATION",
    "business":      "BUSINESS",
    "technology":    "TECHNOLOGY",
    "entertainment": "ENTERTAINMENT",
    "sports":        "SPORTS",
    "science":       "SCIENCE",
    "health":        "HEALTH",
}

# ── Supported Google News regions (shown in the setup wizard) ─────────────────
# China (CN) is intentionally excluded — Google services are blocked there.
# For Chinese-language news use HK or TW with language set to "zh".
REGIONS: dict[str, list[tuple[str, str]]] = {
    "Americas": [
        ("US", "United States"), ("CA", "Canada"),   ("MX", "Mexico"),
        ("BR", "Brazil"),        ("AR", "Argentina"),
    ],
    "Europe": [
        ("GB", "United Kingdom"), ("DE", "Germany"),     ("FR", "France"),
        ("IT", "Italy"),          ("ES", "Spain"),        ("NL", "Netherlands"),
        ("PL", "Poland"),         ("RU", "Russia"),
    ],
    "Asia-Pacific": [
        ("AU", "Australia"),  ("IN", "India"),       ("JP", "Japan"),
        ("KR", "South Korea"),("SG", "Singapore"),   ("MY", "Malaysia"),
        ("PH", "Philippines"),("TW", "Taiwan"),       ("HK", "Hong Kong"),
        ("TH", "Thailand"),   ("ID", "Indonesia"),   ("VN", "Vietnam"),
    ],
    "Middle East & Africa": [
        ("SA", "Saudi Arabia"), ("AE", "UAE"),       ("IL", "Israel"),
        ("ZA", "South Africa"), ("EG", "Egypt"),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Startup validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_api_keys() -> None:
    """Fail fast with a clear message if either API key is obviously missing."""
    missing: list[str] = []
    if not VIDEO_API_KEY:
        missing.append("VIDEO_API_KEY")
    if not ZAI_API_KEY:
        missing.append("ZAI_API_KEY")
    if missing:
        for key in missing:
            print(f"  {Fore.RED}✖  {key} is not set.{Style.RESET_ALL}")
        print(
            f"\n  Set the environment variable(s) or edit the defaults at "
            f"the top of {Fore.CYAN}{__file__}{Style.RESET_ALL}."
        )
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# CLI argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.  No args → interactive wizard mode."""
    p = argparse.ArgumentParser(
        description="AI Video Generator 1.0 — Generate AI videos from "
                    "trending news or your own scene concepts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s                                  Interactive wizard
  %(prog)s --preset presets/sample.json      Load a saved preset
  %(prog)s --resume video_outputs/manifest.json  Re-download failed videos
  %(prog)s --pipeline auto --topic technology --style cinematic --videos 3
""",
    )
    p.add_argument("--preset",   help="Load settings from a preset JSON file")
    p.add_argument("--resume",   help="Resume downloads from a manifest JSON")
    p.add_argument("--pipeline", choices=["auto", "manual"])
    p.add_argument("--region",   default=None)
    p.add_argument("--language", default=None)
    p.add_argument("--topic",    default=None)
    p.add_argument("--trends-count", type=int, default=None)
    p.add_argument("--style",    default=None)
    p.add_argument("--mood",     default=None)
    p.add_argument("--videos",   type=int, default=None)
    p.add_argument("--duration", type=int, default=None)
    p.add_argument("--aspect",   default=None)
    p.add_argument("--mode",     choices=["std", "pro"], default=None)
    p.add_argument("--sound",    action="store_true", default=None)
    p.add_argument("--no-sound", dest="sound", action="store_false")
    p.add_argument("--image",    default=None,
                   help="Reference image URL or local file path")
    p.add_argument("--prompts",  nargs="+", default=None,
                   help="Scene prompts for manual mode (one per arg)")
    return p.parse_args()


def _cli_has_args(args: argparse.Namespace) -> bool:
    """Return True if the user supplied any meaningful CLI arguments."""
    return any([
        args.preset, args.resume, args.pipeline, args.topic, args.style,
        args.mood, args.videos, args.prompts, args.region, args.language,
    ])


def build_cfg_from_args(args: argparse.Namespace) -> dict:
    """
    Build a config dict from CLI args.

    Any argument not provided on the command line falls back to its default:
        --pipeline   auto        --region   US         --language  en
        --topic      top         --videos   3          --style     cinematic
        --mood       dynamic     --aspect   16:9       --duration  5
        --mode       std         --sound    on         --image     none
    """
    pipeline = args.pipeline or "auto"          # default: auto
    cfg: dict = {
        "pipeline":  pipeline,
        "style":     args.style    or "cinematic",  # default: cinematic
        "mood":      args.mood     or "dynamic",    # default: dynamic
        "aspect":    args.aspect   or "16:9",       # default: 16:9
        "duration":  args.duration or 5,            # default: 5 seconds
        "mode":      args.mode     or "std",        # default: std (720p)
        "sound":     args.sound if args.sound is not None else True,  # default: on
        "image_url": args.image,                    # default: none
    }
    if pipeline == "auto":
        cfg["region"]       = args.region       or "US"   # default: US
        cfg["language"]     = args.language     or "en"   # default: en
        cfg["topic"]        = args.topic        or "top"  # default: top headlines
        cfg["trends_count"] = args.trends_count or 5     # default: 5 headlines
        cfg["videos"]       = args.videos       or 3     # default: 3 videos
    else:
        cfg["user_prompts"] = args.prompts or []
        cfg["topic"]        = "custom"
        cfg["videos"]       = len(cfg["user_prompts"])
        if not cfg["user_prompts"]:
            print(f"  {Fore.RED}✖  --prompts required for manual mode.{Style.RESET_ALL}")
            sys.exit(1)
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Preset loading
# ─────────────────────────────────────────────────────────────────────────────

def load_preset(path: str) -> dict:
    """Load a preset JSON file and return it as a config dict."""
    p = pathlib.Path(path)
    if not p.exists():
        print(f"  {Fore.RED}✖  Preset not found: {p}{Style.RESET_ALL}")
        sys.exit(1)
    with p.open(encoding="utf-8") as f:
        raw = json.load(f)
    # Strip comment fields
    cfg = {k: v for k, v in raw.items() if not k.startswith("_")}
    # Ensure defaults
    cfg.setdefault("pipeline", "auto")
    cfg.setdefault("style", "cinematic")
    cfg.setdefault("mood", "dynamic")
    cfg.setdefault("aspect", "16:9")
    cfg.setdefault("duration", 5)
    cfg.setdefault("mode", "std")
    cfg.setdefault("sound", True)
    cfg.setdefault("image_url", None)
    if cfg["pipeline"] == "auto":
        cfg.setdefault("region", "US")
        cfg.setdefault("language", "en")
        cfg.setdefault("topic", "top")
        cfg.setdefault("trends_count", 5)
        cfg.setdefault("videos", 3)
    else:
        cfg.setdefault("user_prompts", [])
        cfg.setdefault("topic", "custom")
        cfg["videos"] = len(cfg.get("user_prompts", []))
    success(f"Preset loaded: {Fore.CYAN}{p.name}{Style.RESET_ALL}")
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Resume from manifest
# ─────────────────────────────────────────────────────────────────────────────

def resume_from_manifest(path: str) -> None:
    """
    Read a run manifest and re-download any videos that previously failed.
    This avoids re-running the entire pipeline.
    """
    p = pathlib.Path(path)
    if not p.exists():
        print(f"  {Fore.RED}✖  Manifest not found: {p}{Style.RESET_ALL}")
        sys.exit(1)
    with p.open(encoding="utf-8") as f:
        manifest = json.load(f)

    out_dir = p.parent
    cfg     = manifest.get("settings", {})
    videos  = manifest.get("videos", [])
    run_ts  = manifest.get("run_timestamp", "resume")

    to_retry = [v for v in videos if v.get("url") and not v.get("filename")]
    if not to_retry:
        success("Nothing to resume — all videos already downloaded.")
        return

    banner(f"RESUMING {len(to_retry)} DOWNLOAD(S)")
    fixed = 0
    for v in to_retry:
        fname = _make_video_filename(
            run_ts,
            cfg.get("topic", "resumed"),
            cfg.get("style", "unknown"),
            cfg.get("mood", "unknown"),
            v["index"],
        )
        dest = out_dir / fname
        if download_video(v["url"], dest):
            v["filename"] = fname
            success(f"Saved → {Fore.CYAN}{dest}{Style.RESET_ALL}")
            fixed += 1
        else:
            error(f"Still failed: {v['url'][:80]}")

    # Rewrite the manifest with updated filenames
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    success(f"Manifest updated.  {fixed}/{len(to_retry)} video(s) recovered.")


# ─────────────────────────────────────────────────────────────────────────────
# Image helpers — list, encode, select
# ─────────────────────────────────────────────────────────────────────────────

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _list_images() -> list[pathlib.Path]:
    """Return sorted list of image files in the images/ folder."""
    if not IMAGES_DIR.exists():
        return []
    return sorted(
        f for f in IMAGES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
    )


def _encode_image_base64(path: pathlib.Path) -> str:
    """Read a local image and return a base64 data URI."""
    ext = path.suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
    data = path.read_bytes()
    b64  = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def ask_image() -> str | None:
    """
    Interactive helper: let the user pick an image from images/ folder,
    enter a URL, or skip entirely.  Returns a URL string or None.
    """
    images = _list_images()
    print(f"\n  {Style.BRIGHT}Attach a reference image? (image-to-video){Style.RESET_ALL}")
    print(f"  {Style.DIM}The image becomes the first frame — the AI animates it.{Style.RESET_ALL}")
    if images:
        print(f"  {Style.DIM}Images found in {IMAGES_DIR.name}/:{Style.RESET_ALL}")
        for i, img in enumerate(images, 1):
            size_kb = img.stat().st_size // 1024
            print(f"    {Fore.CYAN}{i}){Style.RESET_ALL} {img.name}  "
                  f"{Style.DIM}({size_kb:,} KB){Style.RESET_ALL}")
        print(f"    {Fore.CYAN}0){Style.RESET_ALL} No image (text-to-video)")
        print(f"    {Style.DIM}Or paste a URL directly{Style.RESET_ALL}")
    else:
        print(f"  {Style.DIM}No images in {IMAGES_DIR.name}/ — enter a URL or press Enter to skip.{Style.RESET_ALL}")

    raw = input(f"  {Fore.YELLOW}Image{Style.RESET_ALL}: ").strip()

    if not raw or raw == "0":
        return None

    # Numeric selection from the list
    if raw.isdigit() and images:
        idx = int(raw) - 1
        if 0 <= idx < len(images):
            chosen = images[idx]
            success(f"Using {chosen.name}")
            return _encode_image_base64(chosen)

    # URL
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    # Could be a local file path
    p = pathlib.Path(raw)
    if p.exists() and p.suffix.lower() in _IMAGE_EXTS:
        success(f"Using {p.name}")
        return _encode_image_base64(p)

    warn(f"Not a valid image selection: {raw}. Proceeding without image.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# GLM spinner — shows activity during long-running GLM API calls
# ─────────────────────────────────────────────────────────────────────────────

class _GlmSpinner:
    """Context manager that spins in a background thread during GLM calls."""
    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "GLM-4.7 thinking"):
        self._msg = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _spin(self) -> None:
        tick = 0
        t0 = time.time()
        while not self._stop.is_set():
            elapsed = int(time.time() - t0)
            frame = self._FRAMES[tick % len(self._FRAMES)]
            print(
                f"\r  {Fore.CYAN}{frame}{Style.RESET_ALL}  "
                f"{Style.DIM}{self._msg} ({elapsed}s){Style.RESET_ALL}     ",
                end="", flush=True,
            )
            tick += 1
            self._stop.wait(0.15)
        print(f"\r{' ' * 60}\r", end="", flush=True)

    def __enter__(self):
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, headers: dict | None = None,
              retries: int = 2, backoff: float = 1.0) -> requests.Response:
    """GET with automatic retry on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(1 + retries):
        try:
            resp = requests.get(
                url, headers=headers or {}, timeout=HTTP_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def _http_post(url: str, payload: dict, headers: dict,
               retries: int = 2, backoff: float = 1.0) -> requests.Response:
    """POST with automatic retry on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(1 + retries):
        try:
            resp = requests.post(
                url, json=payload, headers=headers, timeout=HTTP_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers — colors, banners, prompts
# ─────────────────────────────────────────────────────────────────────────────

W  = 62   # total banner width


def banner(title: str) -> None:
    """Print a full-width cyan banner box."""
    inner = title.center(W - 2)
    print(f"\n{Fore.CYAN}{Style.BRIGHT}╔{'═' * (W - 2)}╗")
    print(f"║{inner}║")
    print(f"╚{'═' * (W - 2)}╝{Style.RESET_ALL}")


def section(title: str) -> None:
    """Print a yellow section divider."""
    pad = W - 4 - len(title)
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}  ┌─ {title} {'─' * max(pad, 0)}┐{Style.RESET_ALL}")


def info(label: str, value: str) -> None:
    print(f"  {Style.DIM}{label:<12}{Style.RESET_ALL} {Fore.WHITE}{value}{Style.RESET_ALL}")


def success(msg: str) -> None:
    print(f"  {Fore.GREEN}✔  {msg}{Style.RESET_ALL}")


def warn(msg: str) -> None:
    print(f"  {Fore.YELLOW}⚠  {msg}{Style.RESET_ALL}")


def error(msg: str) -> None:
    print(f"  {Fore.RED}✖  {msg}{Style.RESET_ALL}")


def step(n: int, total: int, msg: str) -> None:
    """Print a numbered pipeline step header."""
    tag = f"[{n}/{total}]"
    print(f"\n{Fore.CYAN}{Style.BRIGHT}{tag}{Style.RESET_ALL}  {msg}")


def print_region_table() -> None:
    """Print all supported regions grouped by geography."""
    print(f"\n  {Style.BRIGHT}Supported regions:{Style.RESET_ALL}")
    for group, entries in REGIONS.items():
        print(f"\n  {Fore.YELLOW}{group}{Style.RESET_ALL}")
        # Print in rows of 3
        for i in range(0, len(entries), 3):
            row = entries[i:i + 3]
            line = "    ".join(
                f"{Fore.CYAN}{Style.BRIGHT}{code}{Style.RESET_ALL}  {name:<16}"
                for code, name in row
            )
            print(f"    {line}")
    print(
        f"\n  {Fore.RED}⚠  China (CN) is not supported — Google is blocked there.{Style.RESET_ALL}"
        f"\n     For Chinese-language news use {Fore.CYAN}HK{Style.RESET_ALL} or "
        f"{Fore.CYAN}TW{Style.RESET_ALL} with language {Fore.CYAN}zh{Style.RESET_ALL}."
    )


def ask_choice(prompt: str, options: list[str], default: str,
               descriptions: dict[str, str] | None = None) -> str:
    """
    Show a numbered list of options and return the user's pick.
    Accepts either a number or the option value typed directly.
    Anything typed that isn't in the list is returned as-is (treated
    as a free-text keyword) and the user is notified.
    """
    print(f"\n  {Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    for i, opt in enumerate(options, 1):
        desc  = f"  — {descriptions[opt]}" if descriptions and opt in descriptions else ""
        dflt  = f"  {Fore.GREEN}(default){Style.RESET_ALL}" if opt == default else ""
        print(f"    {Fore.CYAN}{i:>2}){Style.RESET_ALL}  {opt:<16}{desc}{dflt}")

    while True:
        raw = input(
            f"\n  {Fore.YELLOW}Enter number or value{Style.RESET_ALL} "
            f"{Style.DIM}[{default}]{Style.RESET_ALL}: "
        ).strip()

        if not raw:
            return default
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
            error(f"Please pick a number between 1 and {len(options)}.")
            continue

        # Feedback when the user types a value not in the predefined list
        lower_options = [o.lower() for o in options]
        if raw.lower() not in lower_options:
            print(f"  {Style.DIM}→ Using '{raw}' as a keyword search{Style.RESET_ALL}")
        return raw


def ask_free(prompt: str, default: str, note: str = "") -> str:
    """Prompt for a free-text value; shows a note line if provided."""
    print(f"\n  {Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    if note:
        print(f"  {Style.DIM}{note}{Style.RESET_ALL}")
    raw = input(
        f"  {Fore.YELLOW}Enter value{Style.RESET_ALL} "
        f"{Style.DIM}[{default}]{Style.RESET_ALL}: "
    ).strip()
    return raw if raw else default


def ask_int(prompt: str, default: int, min_val: int, max_val: int) -> int:
    """Prompt for an integer within [min_val, max_val]."""
    print(f"\n  {Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    while True:
        raw = input(
            f"  {Fore.YELLOW}Enter number{Style.RESET_ALL} "
            f"{Style.DIM}[{default}]  range {min_val}–{max_val}{Style.RESET_ALL}: "
        ).strip()
        if not raw:
            return default
        try:
            val = int(raw)
        except ValueError:
            error(f"'{raw}' is not a valid number.")
            continue
        if min_val <= val <= max_val:
            return val
        error(f"Please enter a number between {min_val} and {max_val}.")


def ask_prompts() -> list[str]:
    """
    Let the user type one or more scene concepts.

    - One scene per entry → one video per scene (1:1, no cherry-picking).
    - End a line with ``\\`` to continue on the next line.
    - Press Enter on an empty line when done.
    """
    print(f"\n  {Style.BRIGHT}Enter your scene concept(s){Style.RESET_ALL}")
    print(f"  {Style.DIM}One scene per entry = one video.  Each scene is enhanced by GLM.{Style.RESET_ALL}")
    print(f"  {Style.DIM}End a line with \\ to continue on the next line.{Style.RESET_ALL}")
    print(f"  {Style.DIM}Press Enter on an empty line when done.{Style.RESET_ALL}")
    prompts: list[str] = []
    while True:
        # Collect lines for one scene (supports \ continuation)
        parts: list[str] = []
        first = True
        while True:
            label = (
                f"  {Fore.YELLOW}Scene {len(prompts) + 1}{Style.RESET_ALL}: "
                if first
                else f"  {Fore.YELLOW}     ...{Style.RESET_ALL}: "
            )
            raw = input(label).rstrip()
            first = False

            if raw.endswith("\\"):
                parts.append(raw[:-1].strip())
                continue      # keep reading lines for this scene
            else:
                parts.append(raw.strip())
                break         # scene is complete

        concept = " ".join(p for p in parts if p)

        if not concept:
            if not prompts:
                error("Please enter at least one scene concept.")
                continue
            break   # empty line on a fresh scene → done

        prompts.append(concept)
        success(f"Got it ({len(concept)} chars)")
    return prompts


def confirm_settings(cfg: dict) -> bool:
    """Show a summary of all chosen settings and ask for confirmation."""
    banner("YOUR SETTINGS — please confirm")

    if cfg["pipeline"] == "auto":
        section("News source")
        info("Region",     cfg["region"])
        info("Language",   cfg["language"])
        info("Topic",      cfg["topic"])
        info("Headlines",  str(cfg["trends_count"]))
    else:
        section("Your scene concepts")
        for i, p in enumerate(cfg["user_prompts"], 1):
            print(f"    {Fore.CYAN}{i}.{Style.RESET_ALL} {p[:80]}{'…' if len(p) > 80 else ''}")

    section("Prompt style")
    info("Style",  cfg["style"])
    info("Mood",   cfg["mood"])
    info("Videos", str(cfg["videos"]))

    section("Video output")
    info("Aspect",   cfg["aspect"])
    info("Duration", f"{cfg['duration']}s")
    info("Mode",     cfg["mode"])
    info("Sound",    "on" if cfg["sound"] else "off")
    info("Image",    "attached" if cfg.get("image_url") else "none (text-to-video)")

    print()
    raw = input(
        f"  {Fore.YELLOW}Start generating?{Style.RESET_ALL} "
        f"{Style.DIM}[Y/n]{Style.RESET_ALL}: "
    ).strip().lower()
    return raw in ("", "y", "yes")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive setup wizard
# ─────────────────────────────────────────────────────────────────────────────

def configure_interactively() -> dict:
    """
    Walk the user through every setting with numbered choices,
    descriptions, and sensible defaults. Returns a config dict.
    """
    banner("AI Video Generator 1.0")
    print(f"  {Style.DIM}Press Enter at any prompt to accept the default value.{Style.RESET_ALL}")
    print(f"  {Style.DIM}Press Ctrl+C at any time to quit.{Style.RESET_ALL}")

    # ── Pipeline mode ─────────────────────────────────────────────────────────
    pipeline = ask_choice(
        "Pipeline mode:",
        options=["auto", "manual"],
        default="auto",
        descriptions={
            "auto":   "Trending news → GLM writes prompts → AI generates video",
            "manual": "You describe scenes → GLM enhances → AI generates video",
        },
    )

    user_prompts: list[str] = []

    if pipeline == "auto":
        # ── Section 1: News source ────────────────────────────────────────────
        section("STEP 1 OF 3 — News source")

        print_region_table()
        region = ask_free(
            "Which region? (enter the 2-letter code)",
            default="US",
        ).upper()

        language = ask_free(
            "Language code?",
            default="en",
            note="en English │ es Spanish │ fr French │ de German │ ja Japanese\n"
                 "  zh Chinese │ ko Korean  │ pt Portuguese │ ar Arabic",
        ).lower()

        topic = ask_choice(
            "Which news topic?  (or type any keyword, e.g. 'AI chips')",
            options=list(_TOPIC_SECTIONS.keys()),
            default="top",
            descriptions={
                "top":           "Today's top headlines across all categories",
                "world":         "International news",
                "nation":        "Domestic / national news",
                "business":      "Finance, markets, economy",
                "technology":    "Tech, AI, gadgets",
                "entertainment": "Movies, music, culture",
                "sports":        "All sports",
                "science":       "Science & research",
                "health":        "Health, medicine, wellness",
            },
        )

        trends_count = ask_int(
            "How many headlines to pull in?",
            default=5, min_val=1, max_val=20,
        )
    else:
        # ── Section 1: Your scene concepts ────────────────────────────────────
        section("STEP 1 OF 3 — Your scene concepts")
        user_prompts = ask_prompts()

    # ── Section 2: Prompt style ───────────────────────────────────────────────
    section("STEP 2 OF 3 — Prompt style")

    style = ask_choice(
        "Visual style for the generated prompts:",
        options=["cinematic", "documentary", "commercial", "noir", "abstract",
                 "anime", "retro", "aerial", "cyberpunk", "minimalist"],
        default="cinematic",
        descriptions={
            "cinematic":    "Film-quality, lens effects, shallow depth of field",
            "documentary":  "Raw, handheld, observational, natural imperfections",
            "commercial":   "Polished, brand-ready, clean product-focused shots",
            "noir":         "High-contrast B&W, deep shadows, venetian-blind light",
            "abstract":     "Surreal, non-literal, conceptual visual metaphors",
            "anime":        "Cel-shaded, vibrant colors, stylised exaggerated motion",
            "retro":        "Film grain, VHS artifacts, 70s/80s vintage color science",
            "aerial":       "Drone / bird's-eye, sweeping wide-angle landscapes",
            "cyberpunk":    "Neon-drenched, rain-slicked, holographic, dystopian urban",
            "minimalist":   "Clean negative space, limited palette, geometric symmetry",
        },
    )

    mood = ask_choice(
        "Mood / tone:",
        options=["dynamic", "serene", "tense", "euphoric", "dark",
                 "inspirational", "mysterious", "nostalgic", "epic", "playful"],
        default="dynamic",
        descriptions={
            "dynamic":       "Fast motion, energy, excitement",
            "serene":        "Calm, peaceful, slow movement",
            "tense":         "Suspense, unease, tight framing",
            "euphoric":      "Joyful, vibrant, uplifting energy",
            "dark":          "Moody, shadowy, ominous atmosphere",
            "inspirational": "Hopeful, grand, motivating tone",
            "mysterious":    "Enigmatic, atmospheric, fog and haze",
            "nostalgic":     "Warm, wistful, memory-like softness",
            "epic":          "Grand scale, sweeping, monumental scope",
            "playful":       "Whimsical, lighthearted, bouncy motion",
        },
    )

    if pipeline == "auto":
        videos = ask_int(
            "How many videos to generate?",
            default=3, min_val=1, max_val=10,
        )
    else:
        videos = len(user_prompts)
        print(f"\n  {Style.DIM}Videos: {videos} (one per scene concept){Style.RESET_ALL}")

    # ── Section 3: Video output ───────────────────────────────────────────────
    section("STEP 3 OF 3 — Video output")

    aspect = ask_choice(
        "Aspect ratio:",
        options=["16:9", "9:16", "1:1"],
        default="16:9",
        descriptions={
            "16:9": "Landscape — YouTube, TV, desktop",
            "9:16": "Vertical  — TikTok, Instagram Reels, Shorts",
            "1:1":  "Square    — Instagram feed, general social",
        },
    )

    duration = ask_int(
        "Clip duration (seconds):",
        default=5, min_val=3, max_val=15,
    )

    mode = ask_choice(
        "Render mode  (controls resolution & cost):",
        options=["std", "pro"],
        default="std",
        descriptions={
            "std": "720P  — faster & cheaper     (20–30 credits/sec)",
            "pro": "1080P — higher quality        (27–40 credits/sec)",
        },
    )

    sound = ask_choice(
        "Auto-generated sound?  (adds ~10–13 credits/sec):",
        options=["yes", "no"],
        default="yes",
        descriptions={
            "yes": "AI generates matching ambient / music audio",
            "no":  "Silent video — lower cost",
        },
    )

    # Cost estimate — shown before the confirmation screen
    _cps   = {"std": {"yes": 30, "no": 20}, "pro": {"yes": 40, "no": 27}}
    cps    = _cps[mode][sound]
    total  = cps * duration * videos
    usd    = total * 0.005
    print(
        f"\n  {Style.DIM}Estimated cost: "
        f"{cps} credits/sec × {duration}s × {videos} video(s)"
        f" = {Style.RESET_ALL}{Fore.YELLOW}{Style.BRIGHT}{total} credits"
        f"{Style.RESET_ALL}  {Style.DIM}(≈ ${usd:.2f} USD){Style.RESET_ALL}"
    )

    # ── Build config dict ─────────────────────────────────────────────────────
    image_url = ask_image()

    cfg: dict = {
        "pipeline":     pipeline,
        "style":        style,
        "mood":         mood,
        "videos":       videos,
        "aspect":       aspect,
        "duration":     duration,
        "mode":         mode,
        "sound":        sound == "yes",
        "image_url":    image_url,
    }

    if pipeline == "auto":
        cfg.update({
            "region":       region,
            "language":     language,
            "topic":        topic,
            "trends_count": trends_count,
        })
    else:
        cfg["user_prompts"] = user_prompts
        cfg["topic"] = "custom"    # used in output filenames

    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline step 1 — Fetch trending headlines from Google News RSS
# ─────────────────────────────────────────────────────────────────────────────

def build_rss_url(region: str, language: str, topic: str) -> str:
    """
    Construct the Google News RSS URL for the chosen region/language/topic.
    - Predefined section topics use the /headlines/section/topic/ path.
    - Anything else is treated as a keyword search via /search?q=.
    """
    lang_region = f"{language}-{region}"  # e.g. en-US
    ceid        = f"{region}:{language}"  # e.g. US:en
    params      = f"hl={lang_region}&gl={region}&ceid={ceid}"

    topic_lower = topic.strip().lower()

    if topic_lower == "top":
        return f"https://news.google.com/rss?{params}"

    if topic_lower in _TOPIC_SECTIONS and _TOPIC_SECTIONS[topic_lower]:
        return (
            f"https://news.google.com/rss/headlines/section/topic"
            f"/{_TOPIC_SECTIONS[topic_lower]}?{params}"
        )

    # Free-text keyword search
    return f"https://news.google.com/rss/search?q={quote_plus(topic)}&{params}"


def _strip_html(raw: str) -> str:
    """Strip HTML tags, decode HTML entities, and collapse whitespace."""
    from html import unescape
    text = re.sub(r"<[^>]+>", " ", raw)   # strip tags
    text = unescape(text)                  # &nbsp; &amp; etc. → plain chars
    return re.sub(r"\s+", " ", text).strip()


def fetch_trending_topics(region: str, language: str, topic: str,
                          count: int) -> list[dict]:
    """
    Fetch headlines from Google News RSS and return a list of dicts:
      {"title": "...", "snippet": "..."}

    Fetches FETCH_MULTIPLIER× the requested count so GLM-4.7 has a wider
    pool to choose the most visually compelling stories from.
    """
    rss_url  = build_rss_url(region, language, topic)
    fetch_n  = min(count * FETCH_MULTIPLIER, FETCH_CAP)
    print(f"  {Style.DIM}RSS → {rss_url}{Style.RESET_ALL}")

    try:
        resp = _http_get(rss_url, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:
        error(f"Failed to fetch RSS feed: {exc}")
        sys.exit(1)

    root     = ET.fromstring(resp.content)
    articles: list[dict] = []
    for item in root.iter("item"):
        title_el   = item.find("title")
        desc_el    = item.find("description")

        if title_el is None or not title_el.text:
            continue

        # Strip the "  - Source Name" suffix Google appends to every title
        title   = title_el.text.strip().rsplit(" - ", 1)[0]
        snippet = _strip_html(desc_el.text or "") if desc_el is not None else ""

        articles.append({"title": title, "snippet": snippet})

        if len(articles) >= fetch_n:
            break

    if not articles:
        error("No articles found. Try a different region, language, or topic.")
        sys.exit(1)

    for i, a in enumerate(articles, 1):
        trunc = a["snippet"][:SNIPPET_DISPLAY_LEN]
        ellip = "…" if len(a["snippet"]) > SNIPPET_DISPLAY_LEN else ""
        print(f"  {Fore.CYAN}{i}.{Style.RESET_ALL} {a['title']}")
        if trunc:
            print(f"     {Style.DIM}{trunc}{ellip}{Style.RESET_ALL}")

    return articles


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline step 2 — Generate video prompts via Z.AI GLM-4.7
# ─────────────────────────────────────────────────────────────────────────────

def _wrap(text: str, width: int = 72, indent: str = "  ") -> str:
    """Word-wrap ``text`` at ``width`` characters, prefixing each line."""
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(indent + line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(indent + line)
    return "\n".join(lines)


def _strip_json_fences(raw: str) -> str:
    """Remove markdown code fences that models occasionally add."""
    return re.sub(
        r"^```(?:json)?\s*|\s*```\s*$", "", raw, flags=re.MULTILINE,
    ).strip()


def _prompt_length_guidance(duration: int) -> tuple[str, str]:
    """
    Return (sentence_range, arc_guidance) scaled to clip duration.
    Shorter clips need punchy single-beat prompts; longer clips need
    multi-phase temporal arcs.
    """
    if duration <= 5:
        return (
            "2–3 sentences",
            "one clear action or movement that completes within the clip",
        )
    if duration <= 10:
        return (
            "4–6 sentences",
            "two to three distinct beats with smooth transitions between them",
        )
    return (
        "5–8 sentences",
        "a multi-phase progression with visible transitions between beats",
    )


def _audio_element(sound: bool) -> str:
    """
    Return the AUDIO & SOUND DESIGN element block for the system prompt.
    Only included when sound generation is enabled.

    When sound is on, GLM is instructed to decide per-prompt whether the
    scene calls for spoken dialogue (e.g. speeches, press conferences,
    interviews) or pure ambient/music sound design (e.g. disasters, markets,
    landscapes).  The decision is embedded inline in the prompt string so
    Kling 3.0's native audio engine can generate the appropriate output.
    """
    if not sound:
        return ""
    return """

6. AUDIO & SOUND DESIGN (with optional DIALOGUE)
   First decide: does this story naturally involve people speaking on screen?

   DIALOGUE stories — press conferences, speeches, interviews, courtroom
   hearings, product launches, debates, announcements, negotiations:
     Describe who is speaking and write their words in quotes, then add
     the soundscape around them.
     Format: [Role] says: "[line]". Then describe ambient audio.
     e.g. "A CEO at a podium leans into the microphone: 'We are doubling
           our investment in clean energy this year.' Sparse applause
           ripples through the hall; low HVAC hum underneath."

   AMBIENT stories — natural disasters, space events, markets, landscapes,
   abstract data, technology processes, wildlife, architecture:
     Describe only the soundscape: ambient sounds, music/BGM style,
     mechanical or environmental effects. No spoken words.
     e.g. "Deep subsonic rumble building to a sharp crack as the ice shelf
           calves; no music — raw diegetic environmental audio with
           heavy low-frequency reverb." """


def _build_system_prompt(count: int, style: str, mood: str,
                         duration: int, sound: bool) -> str:
    """
    Build the GLM-4.7 system prompt.  Kept as a function so the prompt
    template is easy to iterate on without touching pipeline logic.

    One example per element keeps the prompt lean (~400 tokens) while
    still anchoring the model's output format.
    """
    sentences, arc_guide = _prompt_length_guidance(duration)
    n_elements = 6 if sound else 5
    audio_block = _audio_element(sound)
    return f"""\
You are a world-class AI video director and prompt engineer.

TASK
From the provided news articles, select the {count} stories with the strongest
visual potential. For each, write one production-ready AI video prompt.

STYLE BRIEF
- Visual style : {style}
- Mood / tone  : {mood}
- Clip length  : {duration} seconds
- Audio        : {'enabled — include sound design in each prompt' if sound else 'disabled'}

EACH PROMPT MUST CONTAIN ALL {n_elements} ELEMENTS ({sentences} total):

1. SHOT TYPE & LENS
   e.g. "Slow dolly-in on an 85mm f/1.4, shallow bokeh pulling focus from foreground to subject"

2. SUBJECT & TEMPORAL ARC
   Describe {arc_guide} that unfolds over {duration} seconds.
   e.g. "A lone scientist's gloved hands place a glowing vial into a rack; the camera
         slowly reveals an entire laboratory of identical vials stretching to the horizon."

3. ENVIRONMENT & SURFACE TEXTURES
   e.g. "Inside a brutalist concrete bunker, wet brushed-steel walls, cracked terracotta
         floor tiles, 4:47 PM amber light cutting through narrow slits."

4. LIGHTING
   e.g. "Single overhead sodium-vapour lamp casting a hard amber pool, deep indigo shadows
         bleeding outward"

5. COLOR PALETTE & FILM STYLE ANCHOR
   e.g. "Desaturated sage green and burnt umber tones, Roger Deakins natural-light aesthetic"
{audio_block}

SELECTION RULES
- Prefer stories with strong inherent visual metaphors over purely abstract news.
- Each prompt must come from a DIFFERENT article — no duplicates.
- Every prompt MUST be unique — never repeat the same visual scene.

QUALITY RULES
- Motion is mandatory: camera OR subject must move visibly within {duration} seconds.
- Hyper-specific language only. No "beautiful", "stunning", "amazing", "dramatic".
- No text, logos, watermarks, UI elements, or subtitles in the scene.
- Visuals must be metaphorical — do NOT reproduce the headline text as on-screen text.
- Dialogue (when used) must feel cinematic and natural, NOT like a news anchor reading a headline.
- Dialogue length should scale to the clip duration — longer clips can support more exchanges.
- If sound is disabled, do NOT include any spoken words or dialogue in the prompt.
- Return ONLY a raw JSON array of {count} strings. No markdown, no explanation."""


def generate_prompts(articles: list[dict], count: int,
                     style: str, mood: str, duration: int,
                     sound: bool) -> list[str]:
    """
    Ask GLM-4.7 to select the best articles and write one AI video prompt
    per selected story.  Includes automatic retry with lower temperature
    if the model returns unparseable JSON.  Deduplicates results.
    """
    system_msg = _build_system_prompt(count, style, mood, duration, sound)

    # Format articles — cap snippet length to reduce token usage
    articles_text = "\n".join(
        f"{i}. {a['title']}"
        + (f"\n   Context: {a['snippet'][:SNIPPET_PROMPT_LEN]}" if a["snippet"] else "")
        for i, a in enumerate(articles, 1)
    )
    user_msg = (
        f"News articles pool ({len(articles)} total):\n\n{articles_text}\n\n"
        f"Select the {count} most visually compelling stories and write one "
        f"AI video prompt for each. Return a JSON array of {count} strings only."
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": user_msg},
    ]

    prompts: list[str] | None = None
    last_error: str = ""

    for attempt in range(1 + MAX_JSON_RETRIES):
        temp = max(0.1, PROMPT_TEMPERATURE + attempt * RETRY_TEMP_BUMP)

        try:
            with _GlmSpinner("GLM-4.7 thinking"):
                response = zai_client.chat.completions.create(
                    model="glm-4.7",
                    messages=messages,
                    temperature=temp,
                    max_tokens=PROMPT_MAX_TOKENS,
                    extra_body={"thinking": {"type": "enabled"}},
                )
        except Exception as exc:
            error(f"GLM API call failed: {exc}")
            sys.exit(1)

        raw = (response.choices[0].message.content or "").strip()
        raw = _strip_json_fences(raw)

        try:
            prompts = json.loads(raw)
            if not isinstance(prompts, list) or not all(isinstance(p, str) for p in prompts):
                raise ValueError("Expected a JSON array of strings")
            break
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = str(exc)
            if attempt < MAX_JSON_RETRIES:
                warn(
                    f"GLM returned invalid JSON (attempt {attempt + 1}/"
                    f"{1 + MAX_JSON_RETRIES}), retrying with temp={temp + RETRY_TEMP_BUMP:.2f}…"
                )
            continue

    if prompts is None:
        error(f"GLM failed to return valid JSON after {1 + MAX_JSON_RETRIES} attempt(s).")
        error(f"Last parse error: {last_error}")
        error(f"Raw output (first 500 chars): {raw[:500]}")
        sys.exit(1)

    # Deduplicate — GLM occasionally returns identical prompts
    seen: set[str] = set()
    unique: list[str] = []
    for p in prompts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
        else:
            warn("Duplicate prompt detected and removed.")
    prompts = unique

    for i, p in enumerate(prompts, 1):
        print(f"\n  {Fore.CYAN}{Style.BRIGHT}Prompt {i}{Style.RESET_ALL}")
        print(_wrap(p))

    return prompts


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline step 2b — Enhance user prompts via Z.AI GLM-4.7  (manual mode)
# ─────────────────────────────────────────────────────────────────────────────

def _build_enhance_prompt(count: int, style: str, mood: str,
                          duration: int, sound: bool) -> str:
    """
    Build the system prompt for manual mode.  GLM enhances the user's raw
    scene concepts into production-ready AI video prompts while staying
    faithful to the user's original vision.
    """
    sentences, arc_guide = _prompt_length_guidance(duration)
    n_elements = 6 if sound else 5
    audio_block = _audio_element(sound)
    return f"""\
You are a world-class AI video director and prompt engineer.

TASK
The user has provided {count} scene concept(s). For each one, enhance it into
a production-ready AI video prompt. Stay faithful to the user's vision —
add technical detail where missing, but do not replace their core idea.

STYLE BRIEF
- Visual style : {style}
- Mood / tone  : {mood}
- Clip length  : {duration} seconds
- Audio        : {'enabled — include sound design in each prompt' if sound else 'disabled'}

EACH PROMPT MUST CONTAIN ALL {n_elements} ELEMENTS ({sentences} total):

1. SHOT TYPE & LENS
   e.g. "Slow dolly-in on an 85mm f/1.4, shallow bokeh pulling focus"

2. SUBJECT & TEMPORAL ARC
   Describe {arc_guide} that unfolds over {duration} seconds.

3. ENVIRONMENT & SURFACE TEXTURES
   Location, time of day, named materials and surface finishes.

4. LIGHTING
   Name the light source, quality, and direction.

5. COLOR PALETTE & FILM STYLE ANCHOR
   Name exact palette hues + one cinematographic reference.
{audio_block}

QUALITY RULES
- Keep the user's core concept intact — enhance, don't replace.
- If the user already specified some elements, refine rather than overwrite.
- Motion is mandatory: camera OR subject must move visibly within {duration} seconds.
- Hyper-specific language only. No "beautiful", "stunning", "amazing", "dramatic".
- No text, logos, watermarks, UI elements, or subtitles in the scene.
- Dialogue (when used) must feel cinematic and natural; scale the length to the clip duration.
- If sound is disabled, do NOT include any spoken words or dialogue in the prompt.
- Return ONLY a raw JSON array of {count} strings. No markdown, no explanation."""


def enhance_prompts(user_concepts: list[str], style: str, mood: str,
                    duration: int, sound: bool) -> list[str]:
    """
    Take the user's raw scene concepts and ask GLM-4.7 to enhance each into
    a production-ready 5-element AI video prompt.  Same retry logic as
    generate_prompts.
    """
    count = len(user_concepts)
    system_msg = _build_enhance_prompt(count, style, mood, duration, sound)

    concepts_text = "\n".join(
        f"{i}. {c}" for i, c in enumerate(user_concepts, 1)
    )
    user_msg = (
        f"My scene concepts ({count} total):\n\n{concepts_text}\n\n"
        f"Enhance each concept into a production-ready AI video prompt. "
        f"Return a JSON array of {count} strings only."
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": user_msg},
    ]

    prompts: list[str] | None = None
    last_error: str = ""

    for attempt in range(1 + MAX_JSON_RETRIES):
        temp = max(0.1, PROMPT_TEMPERATURE + attempt * RETRY_TEMP_BUMP)

        try:
            with _GlmSpinner("GLM-4.7 enhancing"):
                response = zai_client.chat.completions.create(
                    model="glm-4.7",
                    messages=messages,
                    temperature=temp,
                    max_tokens=PROMPT_MAX_TOKENS,
                    extra_body={"thinking": {"type": "enabled"}},
                )
        except Exception as exc:
            error(f"GLM API call failed: {exc}")
            sys.exit(1)

        raw = (response.choices[0].message.content or "").strip()
        raw = _strip_json_fences(raw)

        try:
            prompts = json.loads(raw)
            if not isinstance(prompts, list) or not all(isinstance(p, str) for p in prompts):
                raise ValueError("Expected a JSON array of strings")
            break
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = str(exc)
            if attempt < MAX_JSON_RETRIES:
                warn(
                    f"GLM returned invalid JSON (attempt {attempt + 1}/"
                    f"{1 + MAX_JSON_RETRIES}), retrying…"
                )
            continue

    if prompts is None:
        error(f"GLM failed to return valid JSON after {1 + MAX_JSON_RETRIES} attempt(s).")
        error(f"Last parse error: {last_error}")
        error(f"Raw output (first 500 chars): {raw[:500]}")
        sys.exit(1)

    for i, (original, enhanced) in enumerate(zip(user_concepts, prompts), 1):
        print(f"\n  {Fore.CYAN}{Style.BRIGHT}Prompt {i}{Style.RESET_ALL}")
        print(f"  {Style.DIM}Your idea :{Style.RESET_ALL} {original[:80]}{'…' if len(original) > 80 else ''}")
        print(f"  {Style.DIM}Enhanced  :{Style.RESET_ALL}")
        print(_wrap(enhanced))

    return prompts


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline step 3a — Submit a video task to AI Video API
# ─────────────────────────────────────────────────────────────────────────────

def create_video_task(prompt: str, video_cfg: dict,
                      image_url: str | None = None) -> str:
    """
    Submit one video task to the AI video API and return its taskId.
    If image_url is provided, submits an image-to-video task.
    Raises RuntimeError if the API returns a non-200 code.
    """
    input_block: dict = {
        "prompt":          prompt,
        "negative_prompt": NEGATIVE_PROMPT,
        "sound":           video_cfg["sound"],
        "duration":        video_cfg["duration"],
        "aspect_ratio":    video_cfg["aspect_ratio"],
        "mode":            video_cfg["mode"],
        "multi_shots":     video_cfg["multi_shots"],
    }
    if image_url:
        input_block["image_urls"] = [image_url]

    payload = {
        "model": "kling-3.0/video",
        "input": input_block,
    }

    resp = _http_post(
        f"{VIDEO_API_BASE_URL}/jobs/createTask",
        payload=payload,
        headers=VIDEO_API_HEADERS,
    )
    data = resp.json()

    if data.get("code") != 200:
        raise RuntimeError(f"Video API error: {data}")

    return data["data"]["taskId"]


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline step 3b — Poll until the video is ready
# ─────────────────────────────────────────────────────────────────────────────

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def poll_task(task_id: str) -> dict:
    """
    Poll a video task via the /jobs/recordInfo endpoint every
    POLL_INTERVAL_SEC seconds until it succeeds or fails.
    Raises TimeoutError after POLL_TIMEOUT_SEC seconds.

    Status values: GENERATING, SUCCESS,
    CREATE_TASK_FAILED, GENERATE_FAILED.
    """
    url      = f"{VIDEO_API_BASE_URL}/jobs/recordInfo"
    deadline = time.time() + POLL_TIMEOUT_SEC
    tick     = 0

    while time.time() < deadline:
        resp = _http_get(
            f"{url}?taskId={task_id}", headers=VIDEO_API_HEADERS,
        )
        data = resp.json()

        if data.get("code") != 200:
            raise RuntimeError(f"Video API poll error: {data}")

        task    = data.get("data", {})
        state   = (task.get("state") or task.get("status") or "").upper()
        elapsed = int(time.time() - (deadline - POLL_TIMEOUT_SEC))

        spin = _SPINNER[tick % len(_SPINNER)]
        tick += 1
        line = (
            f"  {Fore.CYAN}{spin}{Style.RESET_ALL}  "
            f"{Style.DIM}{task_id[:16]}…{Style.RESET_ALL}  "
            f"status: {Fore.YELLOW}{state}{Style.RESET_ALL}  "
            f"{Style.DIM}({elapsed}s){Style.RESET_ALL}"
        )
        print(f"\r{line}     ", end="", flush=True)

        if state == "SUCCESS":
            print()  # newline after spinner
            return task
        if state in ("CREATE_TASK_FAILED", "GENERATE_FAILED", "FAILED", "ERROR"):
            print()
            raise RuntimeError(f"Task {task_id} failed: {task}")

        time.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError(f"Task {task_id} timed out after {POLL_TIMEOUT_SEC}s")


def extract_video_url(result: dict) -> str | None:
    """
    Extract the video URL from a KIE AI recordInfo response.

    KIE AI nests results in  data.resultJson  (a JSON string) which
    contains  resultUrls  — a list of generated media URLs.
    Falls back to other common key paths for compatibility.
    """
    # Primary path: KIE AI resultJson → resultUrls
    result_json_str = result.get("resultJson")
    if result_json_str:
        try:
            result_json = json.loads(result_json_str) if isinstance(result_json_str, str) else result_json_str
            urls = result_json.get("resultUrls") or result_json.get("result_urls") or []
            if urls:
                return urls[0]
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: flat keys
    for key in ("video_url", "videoUrl", "url", "video"):
        if key in result:
            return result[key]

    # Fallback: nested output/works
    output = result.get("output") or result.get("outputs") or {}
    if isinstance(output, dict):
        for key in ("video_url", "videoUrl", "url", "video"):
            if key in output:
                return output[key]
        works = output.get("works", [])
        if works:
            first = works[0]
            return first.get("video", {}).get("resource") or first.get("resource")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers — directory, filenames, download, manifest
# ─────────────────────────────────────────────────────────────────────────────

def _make_output_dir() -> pathlib.Path:
    """Create video_outputs/ next to the script if it doesn't exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _make_video_filename(run_ts: str, topic: str, style: str,
                         mood: str, index: int) -> str:
    """
    Build a descriptive, filesystem-safe filename for one video.

    Format: {YYYYMMDD_HHMMSS}_{topic}_{style}_{mood}_{NNN}.mp4
    Example: 20260225_161602_technology_dramatic_dynamic_001.mp4

    - Timestamp prefix  → natural chronological sort in any file browser
    - topic/style/mood  → instantly shows what the video is about
    - Zero-padded index → prevents collisions within the same run
    - No spaces/specials → safe on Windows, macOS, Linux, cloud storage
    """
    # Sanitise each label: lowercase, keep only alphanum and hyphens
    def _safe(s: str) -> str:
        return re.sub(r"[^a-z0-9-]", "", s.lower().replace(" ", "-"))[:20]

    return f"{run_ts}_{_safe(topic)}_{_safe(style)}_{_safe(mood)}_{index:03d}.mp4"


def download_video(url: str, dest: pathlib.Path,
                   retries: int = 3, backoff: float = 2.0) -> bool:
    """
    Stream-download a video URL to `dest` with retry on transient errors.

    Retries up to `retries` times with exponential backoff for SSL,
    connection, and timeout errors.  Cleans up partial files on failure.
    Returns True on success, False if all attempts fail.
    """
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, stream=True, timeout=HTTP_TIMEOUT_SEC * 4)
            resp.raise_for_status()
            total    = int(resp.headers.get("content-length", 0))
            received = 0
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
                        received += len(chunk)
                        if total:
                            pct = received / total * 100
                            print(
                                f"\r  {Fore.CYAN}↓{Style.RESET_ALL}  "
                                f"{dest.name}  "
                                f"{Style.DIM}{received // 1024:,} KB / "
                                f"{total // 1024:,} KB  ({pct:.0f}%){Style.RESET_ALL}     ",
                                end="", flush=True,
                            )
            print()  # newline after progress
            return True
        except Exception as exc:
            print()  # newline if progress was mid-line
            # Clean up partial file
            if dest.exists():
                dest.unlink()
            if attempt < retries:
                wait = backoff * attempt
                warn(
                    f"Download attempt {attempt}/{retries} failed: {exc.__class__.__name__}. "
                    f"Retrying in {wait:.0f}s…"
                )
                time.sleep(wait)
            else:
                warn(f"Download failed after {retries} attempts: {exc}")
                return False
    return False


def write_manifest(out_dir: pathlib.Path, run_ts: str,
                   cfg: dict, results: list[dict]) -> pathlib.Path:
    """
    Write a JSON manifest alongside the videos capturing every detail of
    the run — settings, prompts, task IDs, URLs, and saved filenames.
    Makes it easy to audit, re-download, or reproduce any run later.
    """
    manifest = {
        "run_timestamp": run_ts,
        "settings": cfg,
        "videos": [
            {
                "index":     r["index"],
                "filename":  r.get("filename"),
                "task_id":   r["task_id"],
                "url":       r.get("url"),
                "prompt":    r["prompt"],
                "error":     r.get("error"),
            }
            for r in results
        ],
    }
    path = out_dir / f"{run_ts}_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Main — wizard → pipeline → results
# ─────────────────────────────────────────────────────────────────────────────

def poll_all_tasks(task_ids: list[str]) -> dict[str, dict]:
    """
    Poll multiple video tasks in round-robin until all complete or timeout.
    Returns {task_id: result_dict} for each task.
    Much faster than sequential polling for multi-video runs.
    """
    url       = f"{VIDEO_API_BASE_URL}/jobs/recordInfo"
    deadline  = time.time() + POLL_TIMEOUT_SEC
    pending   = set(task_ids)
    results: dict[str, dict]   = {}
    statuses: dict[str, str]   = {tid: "PENDING" for tid in task_ids}
    tick = 0

    while pending and time.time() < deadline:
        for task_id in list(pending):
            try:
                resp = _http_get(
                    f"{url}?taskId={task_id}", headers=VIDEO_API_HEADERS,
                )
                data = resp.json()
                if data.get("code") != 200:
                    raise RuntimeError(f"Poll error: {data}")

                task  = data.get("data", {})
                state = (task.get("state") or task.get("status") or "").upper()
                statuses[task_id] = state

                if state == "SUCCESS":
                    results[task_id] = task
                    pending.discard(task_id)
                elif state in ("CREATE_TASK_FAILED", "GENERATE_FAILED",
                               "FAILED", "ERROR"):
                    results[task_id] = {"error": f"Task failed: {task}"}
                    pending.discard(task_id)
            except Exception as exc:
                results[task_id] = {"error": str(exc)}
                pending.discard(task_id)

        # Show multi-task status line
        elapsed = int(time.time() - (deadline - POLL_TIMEOUT_SEC))
        spin = _SPINNER[tick % len(_SPINNER)]
        tick += 1
        parts = []
        for tid in task_ids:
            st = statuses[tid]
            short = tid[:8]
            if st == "SUCCESS":
                parts.append(f"{Fore.GREEN}{short}✓{Style.RESET_ALL}")
            elif "FAIL" in st or st == "ERROR":
                parts.append(f"{Fore.RED}{short}✗{Style.RESET_ALL}")
            else:
                parts.append(f"{Fore.YELLOW}{short}…{Style.RESET_ALL}")
        status_line = "  ".join(parts)
        print(
            f"\r  {Fore.CYAN}{spin}{Style.RESET_ALL}  {status_line}  "
            f"{Style.DIM}({elapsed}s){Style.RESET_ALL}     ",
            end="", flush=True,
        )

        if pending:
            time.sleep(POLL_INTERVAL_SEC)

    print()  # newline after status line

    # Handle any still-pending tasks as timeouts
    for tid in pending:
        results[tid] = {"error": f"Timed out after {POLL_TIMEOUT_SEC}s"}

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main — wizard → pipeline → results
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # ── Resume mode — re-download failed videos from a manifest ────────────
    if args.resume:
        validate_api_keys()
        resume_from_manifest(args.resume)
        return

    validate_api_keys()

    # Timestamp shared across the whole run — used in filenames & manifest
    run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Determine config source: preset / CLI args / interactive wizard ───
    if args.preset:
        cfg = load_preset(args.preset)
    elif _cli_has_args(args):
        cfg = build_cfg_from_args(args)
    else:
        cfg = configure_interactively()

    # Show a summary and ask the user to confirm before spending API credits
    if not confirm_settings(cfg):
        print(f"\n  {Fore.YELLOW}Cancelled. Run the script again to start over.{Style.RESET_ALL}\n")
        sys.exit(0)

    # Build the video config dict from user choices
    video_cfg = {
        "duration":     str(cfg["duration"]),
        "aspect_ratio": cfg["aspect"],
        "mode":         cfg["mode"],
        "sound":        cfg["sound"],
        "multi_shots":  False,
    }

    image_url = cfg.get("image_url")

    TOTAL_STEPS = 3

    if cfg["pipeline"] == "auto":
        # ── Step 1: Fetch headlines ─────────────────────────────────────────
        step(1, TOTAL_STEPS, f"Fetching headlines  "
             f"{Fore.CYAN}{cfg['region']}{Style.RESET_ALL} / "
             f"{Fore.CYAN}{cfg['topic']}{Style.RESET_ALL} / "
             f"{Fore.CYAN}{cfg['language']}{Style.RESET_ALL}")

        articles = fetch_trending_topics(
            region=cfg["region"],
            language=cfg["language"],
            topic=cfg["topic"],
            count=cfg["trends_count"],
        )
        success(f"{len(articles)} articles fetched (GLM-4.7 will select the best {cfg['videos']})")

        # ── Step 2: Generate prompts ────────────────────────────────────────
        step(2, TOTAL_STEPS, f"Generating {cfg['videos']} video prompt(s) via GLM-4.7  "
             f"style={Fore.CYAN}{cfg['style']}{Style.RESET_ALL}  "
             f"mood={Fore.CYAN}{cfg['mood']}{Style.RESET_ALL}")

        prompts = generate_prompts(
            articles=articles,
            count=cfg["videos"],
            style=cfg["style"],
            mood=cfg["mood"],
            duration=cfg["duration"],
            sound=cfg["sound"],
        )
        success(f"{len(prompts)} prompt(s) generated")

    else:  # manual
        # ── Step 1: Recap user concepts ─────────────────────────────────────
        step(1, TOTAL_STEPS, f"Received {cfg['videos']} scene concept(s)")
        for i, c in enumerate(cfg["user_prompts"], 1):
            print(f"  {Fore.CYAN}{i}.{Style.RESET_ALL} {c[:90]}{'…' if len(c) > 90 else ''}")

        # ── Step 2: Enhance prompts ───────────────────────────────────────
        step(2, TOTAL_STEPS, f"Enhancing {cfg['videos']} prompt(s) via GLM-4.7  "
             f"style={Fore.CYAN}{cfg['style']}{Style.RESET_ALL}  "
             f"mood={Fore.CYAN}{cfg['mood']}{Style.RESET_ALL}")

        prompts = enhance_prompts(
            user_concepts=cfg["user_prompts"],
            style=cfg["style"],
            mood=cfg["mood"],
            duration=cfg["duration"],
            sound=cfg["sound"],
        )
        success(f"{len(prompts)} prompt(s) enhanced")

    # ── Step 3: Submit to AI Video API ──────────────────────────────────
    step(3, TOTAL_STEPS, f"Submitting to AI Video API  "
         f"{Fore.CYAN}{cfg['aspect']}{Style.RESET_ALL}  "
         f"{Fore.CYAN}{cfg['duration']}s{Style.RESET_ALL}  "
         f"mode={Fore.CYAN}{cfg['mode']}{Style.RESET_ALL}"
         f"{f'  image={Fore.CYAN}attached{Style.RESET_ALL}' if image_url else ''}")

    tasks: list[tuple[str, str]] = []
    for i, prompt in enumerate(prompts, 1):
        try:
            task_id = create_video_task(prompt, video_cfg, image_url=image_url)
            success(f"Task {i} submitted → {Style.DIM}{task_id}{Style.RESET_ALL}")
            tasks.append((task_id, prompt))
        except Exception as exc:
            error(f"Task {i} failed to submit: {exc}")

    # ── Polling (parallel round-robin) ─────────────────────────────────
    if not tasks:
        error("No tasks were submitted. Check your VIDEO_API_KEY and credits.")
        sys.exit(1)

    print(f"\n  {Style.DIM}Waiting for {len(tasks)} video(s) to render..."
          f" This usually takes 1–5 minutes.{Style.RESET_ALL}\n")

    task_ids    = [tid for tid, _ in tasks]
    prompt_map  = {tid: prompt for tid, prompt in tasks}
    poll_results = poll_all_tasks(task_ids)

    # ── Download results ─────────────────────────────────────────────
    out_dir = _make_output_dir()
    results: list[dict] = []

    for idx, task_id in enumerate(task_ids, 1):
        result = poll_results.get(task_id, {})
        prompt = prompt_map[task_id]

        if "error" in result:
            error(f"{task_id[:16]}: {result['error']}")
            results.append({"index": idx, "task_id": task_id, "prompt": prompt,
                             "url": None, "filename": None, "error": result["error"]})
            continue

        video_url = extract_video_url(result)
        entry = {"index": idx, "task_id": task_id, "prompt": prompt,
                 "url": video_url}

        if video_url:
            success(f"Video {idx} ready — downloading…")
            fname = _make_video_filename(
                run_ts, cfg["topic"], cfg["style"], cfg["mood"], idx
            )
            dest = out_dir / fname
            saved = download_video(video_url, dest)
            if saved:
                success(f"Saved → {Fore.CYAN}{dest}{Style.RESET_ALL}")
                entry["filename"] = fname
            else:
                entry["filename"] = None
        else:
            warn("Video done but URL not parsed — check raw output below.")
            entry["filename"] = None
            entry["raw"] = result

        results.append(entry)

    # ── Write run manifest ────────────────────────────────────────────
    manifest_path = write_manifest(out_dir, run_ts, cfg, results)
    success(f"Manifest → {Fore.CYAN}{manifest_path}{Style.RESET_ALL}")

    # ── Results summary ─────────────────────────────────────────────
    banner("RESULTS")

    for r in results:
        section(f"Video {r['index']}")
        print(f"  {Style.DIM}Task ID :{Style.RESET_ALL} {r['task_id']}")
        print(f"  {Style.DIM}Prompt  :{Style.RESET_ALL} {r['prompt'][:120]}"
              f"{'...' if len(r['prompt']) > 120 else ''}")
        if r.get("filename"):
            print(f"  {Fore.GREEN}Saved   : {out_dir / r['filename']}{Style.RESET_ALL}")
        elif r.get("url"):
            print(f"  {Fore.YELLOW}URL     : {r['url']}{Style.RESET_ALL}")
        elif r.get("error"):
            print(f"  {Fore.RED}Error   : {r['error']}{Style.RESET_ALL}")
        else:
            print(f"  {Fore.YELLOW}Raw     :{Style.RESET_ALL}")
            print(json.dumps(r.get("raw"), indent=4))

    # ── Output folder reminder ─────────────────────────────────────────
    saved_count = sum(1 for r in results if r.get("filename"))
    print(
        f"\n  {Fore.GREEN}{Style.BRIGHT}All done!{Style.RESET_ALL}  "
        f"{Style.DIM}{saved_count}/{len(results)} video(s) saved to "
        f"{Style.RESET_ALL}{Fore.CYAN}{out_dir}{Style.RESET_ALL}\n"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {Fore.YELLOW}Interrupted — exiting.{Style.RESET_ALL}\n")
        sys.exit(130)
