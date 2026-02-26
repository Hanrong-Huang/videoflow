"""
video_gen.py — AI Video Generator 1.1
========================================================
Main orchestrator: parses CLI, coordinates the pipeline.

Module layout:
  models.py    — Video model registry (add/remove models here)
  cli.py       — UI helpers, interactive wizard, settings confirmation
  prompts.py   — GLM-4.7 prompt generation, news fetching
  backends.py  — Video API backends (create/poll/download)

Pipeline:
  1. Configure  — wizard, CLI args, or preset file
  2. Prompts    — auto (news→GLM) or manual (user→GLM enhance)
  3. Submit     — send prompts to the selected video backend
  4. Poll       — wait for renders to complete
  5. Download   — save videos + write manifest
"""

import os
import sys
import json
import base64
import pathlib
import datetime
import argparse
import mimetypes
import urllib.request

from openai import OpenAI
from colorama import init as _colorama_init, Fore, Style

from models import MODELS, MODEL_NAMES, is_openai, is_kie
from cli import (
    banner, section, info, success, warn, error, step,
    confirm_settings, configure_interactively,
    _encode_image_base64,
)
from prompts import (
    fetch_trending_topics, generate_prompts, enhance_prompts, web_search,
)
from backends import (
    submit_task, poll_all, download_result,
    make_output_dir, make_video_filename, write_manifest,
    download_video, extract_video_url,
    ensure_openai_client,
)

# Initialise colorama without intercepting stdout (which breaks questionary on Windows)
import colorama
colorama.just_fix_windows_console()

# ── API keys ──────────────────────────────────────────────────────────────────
VIDEO_API_KEY  = os.environ.get("VIDEO_API_KEY", "")
ZAI_API_KEY    = os.environ.get("ZAI_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ── Z.AI / GLM-4.7 client ────────────────────────────────────────────────────
zai_client = OpenAI(
    api_key=ZAI_API_KEY,
    base_url="https://api.z.ai/api/coding/paas/v4/",
)

# ── Project directories ──────────────────────────────────────────────────────
_PROJECT_DIR = pathlib.Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# API key validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_api_keys(model: str = "kling") -> None:
    """Fail fast with a clear message if required API keys are missing."""
    missing: list[str] = []
    if not ZAI_API_KEY:
        missing.append("ZAI_API_KEY")
    if is_kie(model) and not VIDEO_API_KEY:
        missing.append("VIDEO_API_KEY")
    if is_openai(model) and not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
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
        description="AI Video Generator 1.1 — Generate AI videos from "
                    "trending news or your own scene concepts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s                                  Interactive wizard
  %(prog)s --preset presets/sample.json      Load a saved preset
  %(prog)s --resume video_outputs/manifest.json  Re-download failed videos
  %(prog)s --pipeline auto --topic technology --videos 3
""",
    )
    p.add_argument("--preset",   help="Load settings from a preset JSON file")
    p.add_argument("--resume",   help="Resume downloads from a manifest JSON")
    p.add_argument("--pipeline", choices=["auto", "manual"])
    p.add_argument("--model",    choices=MODEL_NAMES,
                   default=None, help=f"Video model: {', '.join(MODEL_NAMES)} (default: kling)")
    p.add_argument("--region",   default=None)
    p.add_argument("--language", default=None)
    p.add_argument("--topic",    default=None)
    p.add_argument("--style",    default=None)
    p.add_argument("--mood",     default=None)
    p.add_argument("--videos",   type=int, default=None)
    p.add_argument("--duration", type=int, default=None)
    p.add_argument("--aspect",   default=None)
    p.add_argument("--mode",     choices=["std", "pro"], default=None)
    p.add_argument("--sound",    action="store_true", default=None)
    p.add_argument("--no-sound", dest="sound", action="store_false")
    p.add_argument("--image",    default=None,
                   help="[Deprecated] Use --images instead")
    p.add_argument("--images",   nargs="*", default=None,
                   help="Reference image URLs or local paths (one per scene)")
    p.add_argument("--prompts",  nargs="+", default=None,
                   help="Scene prompts for manual mode (one per arg)")
    p.add_argument("--research", default=None,
                   help="Web search query for background info (e.g. 'Tesla Cybertruck')")
    return p.parse_args()


def _cli_has_args(args: argparse.Namespace) -> bool:
    """Return True if the user supplied any meaningful CLI arguments."""
    return any([
        args.preset, args.resume, args.pipeline, args.topic, args.style,
        args.mood, args.videos, args.prompts, args.region, args.language,
        args.research, args.image, args.images,
    ])


def build_cfg_from_args(args: argparse.Namespace) -> dict:
    """Build a config dict from CLI args with sensible defaults."""
    pipeline = args.pipeline or "auto"
    # Normalise --image (deprecated) + --images into list[list[str]]
    flat = args.images if args.images else ([args.image] if args.image else [])
    cfg: dict = {
        "pipeline":   pipeline,
        "model":      args.model    or "kling",
        "style":      args.style    or "cinematic",
        "mood":       args.mood     or "dramatic",
        "aspect":     args.aspect   or "16:9",
        "duration":   args.duration or 5,
        "mode":       args.mode     or "std",
        "sound":      args.sound if args.sound is not None else True,
        "image_urls": [[url] for url in flat],
        "research":   args.research,
    }
    if pipeline == "auto":
        cfg["region"]       = args.region       or "US"
        cfg["language"]     = args.language     or "en"
        cfg["topic"]        = args.topic        or "top"
        cfg["videos"]       = args.videos       or 3
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
    cfg = {k: v for k, v in raw.items() if not k.startswith("_")}
    cfg.setdefault("pipeline", "auto")
    cfg.setdefault("model", "kling")
    cfg.setdefault("style", "cinematic")
    cfg.setdefault("mood", "dramatic")
    cfg.setdefault("aspect", "16:9")
    cfg.setdefault("duration", 5)
    cfg.setdefault("mode", "std")
    cfg.setdefault("sound", True)
    cfg.setdefault("research", None)
    # Normalise image_urls to list[list[str]] (supports old flat-list presets)
    imgs = cfg.pop("image_url", None)   # remove legacy singular key if present
    raw_imgs = cfg.get("image_urls", [])
    if raw_imgs and not isinstance(raw_imgs[0], list):
        cfg["image_urls"] = [[u] if u else [] for u in raw_imgs]
    elif not raw_imgs and imgs:
        cfg["image_urls"] = [[imgs]]
    else:
        cfg.setdefault("image_urls", [])
    if cfg["pipeline"] == "auto":
        cfg.setdefault("region", "US")
        cfg.setdefault("language", "en")
        cfg.setdefault("topic", "top")
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
    """Re-download any videos that previously failed from a manifest."""
    p = pathlib.Path(path)
    if not p.exists():
        error(f"Manifest not found: {p}")
        sys.exit(1)
    with p.open(encoding="utf-8") as f:
        manifest = json.load(f)

    banner("RESUME — Re-downloading failed videos")
    out_dir = p.parent
    retried = 0

    for entry in manifest.get("videos", []):
        if entry.get("filename"):
            dest = out_dir / entry["filename"]
            if dest.exists():
                success(f"Already saved: {dest.name}")
                continue

        url = entry.get("url")
        if not url or url.startswith("sora://"):
            warn(f"Video {entry.get('index', '?')}: no downloadable URL")
            continue

        fname = entry.get("filename") or f"resumed_{entry['task_id'][:12]}.mp4"
        dest  = out_dir / fname
        success(f"Downloading video {entry.get('index', '?')}…")
        if download_video(url, dest):
            success(f"Saved → {Fore.CYAN}{dest}{Style.RESET_ALL}")
            entry["filename"] = fname
            retried += 1
        else:
            error(f"Failed to download: {url[:80]}")

    # Update manifest
    if retried:
        p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        success(f"Manifest updated: {retried} video(s) re-downloaded.")
    else:
        warn("Nothing to re-download.")


# ─────────────────────────────────────────────────────────────────────────────
# Image URL resolution — local paths → base64
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_image_url(raw: str | None) -> str | None:
    """
    Resolve any image reference to a base64 data URI.
    - data: URI   → returned as-is (already encoded)
    - http/https  → downloaded and base64-encoded
    - local path  → read from disk and base64-encoded
    Returns None if raw is None/empty.
    """
    if not raw:
        return None
    if raw.startswith("data:"):
        return raw
    if raw.startswith(("http://", "https://")):
        success(f"Downloading image: {Fore.CYAN}{raw[:72]}{'…' if len(raw) > 72 else ''}{Style.RESET_ALL}")
        try:
            with urllib.request.urlopen(raw, timeout=60) as resp:
                data = resp.read()
                content_type = resp.headers.get_content_type() or ""
        except Exception as exc:
            error(f"Failed to download image: {exc}")
            sys.exit(1)
        if not content_type.startswith("image/"):
            # Fall back to guessing from URL
            guessed, _ = mimetypes.guess_type(raw.split("?")[0])
            content_type = guessed or "image/jpeg"
        encoded = base64.b64encode(data).decode()
        return f"data:{content_type};base64,{encoded}"
    # Treat as a local file path
    path = pathlib.Path(raw)
    if not path.is_absolute():
        path = _PROJECT_DIR / path
    if not path.is_file():
        error(f"Image file not found: {path}")
        sys.exit(1)
    success(f"Encoding local image: {Fore.CYAN}{path.name}{Style.RESET_ALL}")
    return _encode_image_base64(path)


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # ── Resume mode ───────────────────────────────────────────────────────
    if args.resume:
        validate_api_keys("kling")
        resume_from_manifest(args.resume)
        return

    run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Determine config source ───────────────────────────────────────────
    if args.preset:
        cfg = load_preset(args.preset)
    elif _cli_has_args(args):
        cfg = build_cfg_from_args(args)
    else:
        cfg = configure_interactively()

    model = cfg.get("model", "kling")
    validate_api_keys(model)

    if is_openai(model):
        ensure_openai_client()

    if not confirm_settings(cfg):
        print(f"\n  {Fore.YELLOW}Cancelled. Run the script again to start over.{Style.RESET_ALL}\n")
        sys.exit(0)

    # ── Build video config ────────────────────────────────────────────────
    video_cfg = {
        "duration":     str(cfg["duration"]),
        "aspect_ratio": cfg["aspect"],
        "mode":         cfg["mode"],
        "sound":        cfg["sound"],
        "multi_shots":  False,
    }
    
    # ── Resolve images ────────────────────────────────────────────────────────
    # image_urls is already list[list[str]] (normalised by wizard/load_preset/
    # build_cfg_from_args). Just resolve any local paths to base64.
    num_vids = cfg.get("videos", 1)
    image_urls: list[list[str]] = [
        [r for url in scene if (r := _resolve_image_url(url))]
        for scene in cfg.get("image_urls", [])
    ]
    # Pad or truncate to match video count
    if len(image_urls) < num_vids:
        image_urls += [[]] * (num_vids - len(image_urls))
    else:
        image_urls = image_urls[:num_vids]

    # ── Optional: Web research ───────────────────────────────────────────────
    research_query = cfg.get("research")
    research_results: list[dict] = []
    if research_query:
        success(f"Searching the web for: {Fore.CYAN}{research_query}{Style.RESET_ALL}")
        research_results = web_search(research_query)
        if research_results:
            success(f"{len(research_results)} research result(s) found — will feed to GLM")
        else:
            warn("No research results found — continuing without background info")

    TOTAL_STEPS = 3

    # ── Step 1 & 2: Prompts ───────────────────────────────────────────────
    if cfg["pipeline"] == "auto":
        step(1, TOTAL_STEPS, f"Fetching headlines  "
             f"{Fore.CYAN}{cfg['region']}{Style.RESET_ALL} / "
             f"{Fore.CYAN}{cfg['topic']}{Style.RESET_ALL} / "
             f"{Fore.CYAN}{cfg['language']}{Style.RESET_ALL}")

        articles = fetch_trending_topics(
            region=cfg["region"], language=cfg["language"],
            topic=cfg["topic"],
        )
        success(f"{len(articles)} articles fetched (GLM-4.7 will select the best {cfg['videos']})")

        step(2, TOTAL_STEPS, f"Generating {cfg['videos']} video prompt(s) via GLM-4.7  "
             f"style={Fore.CYAN}{cfg['style']}{Style.RESET_ALL}  "
             f"mood={Fore.CYAN}{cfg['mood']}{Style.RESET_ALL}")

        prompts = generate_prompts(
            zai_client, articles=articles, count=cfg["videos"],
            style=cfg["style"], mood=cfg["mood"],
            duration=cfg["duration"], sound=cfg["sound"],
            research=research_results or None,
        )
        success(f"{len(prompts)} prompt(s) generated")

    else:  # manual
        step(1, TOTAL_STEPS, f"Received {cfg['videos']} scene concept(s)")
        for i, c in enumerate(cfg["user_prompts"], 1):
            print(f"  {Fore.CYAN}{i}.{Style.RESET_ALL} {c[:90]}{'…' if len(c) > 90 else ''}")

        step(2, TOTAL_STEPS, f"Enhancing {cfg['videos']} prompt(s) via GLM-4.7  "
             f"style={Fore.CYAN}{cfg['style']}{Style.RESET_ALL}  "
             f"mood={Fore.CYAN}{cfg['mood']}{Style.RESET_ALL}")

        prompts = enhance_prompts(
            zai_client, user_concepts=cfg["user_prompts"],
            style=cfg["style"], mood=cfg["mood"],
            duration=cfg["duration"], sound=cfg["sound"],
            research=research_results or None,
        )
        success(f"{len(prompts)} prompt(s) enhanced")

    # ── Step 3: Submit, poll, download ─────────────────────────────────────
    model_label = MODELS[model]["label"]
    step(3, TOTAL_STEPS, f"Submitting to {model_label}  "
         f"{Fore.CYAN}{cfg['aspect']}{Style.RESET_ALL}  "
         f"{Fore.CYAN}{cfg['duration']}s{Style.RESET_ALL}  "
         f"mode={Fore.CYAN}{cfg['mode']}{Style.RESET_ALL}"
         f"{f'  {Fore.CYAN}images attached{Style.RESET_ALL}' if any(image_urls) else ''}")

    tasks: list[tuple[str, str]] = []
    for i, prompt in enumerate(prompts, 1):
        try:
            scene_imgs = image_urls[i - 1]  # list[str], may be empty
            task_id = submit_task(prompt, video_cfg, model,
                                  image_urls=scene_imgs if scene_imgs else None)
            success(f"Task {i} submitted → {Style.DIM}{task_id}{Style.RESET_ALL}")
            tasks.append((task_id, prompt))
        except Exception as exc:
            error(f"Task {i} failed to submit: {exc}")

    if not tasks:
        error("No tasks were submitted. Check your API key and credits.")
        sys.exit(1)

    print(f"\n  {Style.DIM}Waiting for {len(tasks)} video(s) to render..."
          f" This usually takes 1–5 minutes.{Style.RESET_ALL}\n")

    task_ids   = [tid for tid, _ in tasks]
    prompt_map = {tid: prompt for tid, prompt in tasks}

    poll_results = poll_all(task_ids, model)

    # ── Download results ──────────────────────────────────────────────────
    out_dir = make_output_dir()
    results: list[dict] = []

    for idx, task_id in enumerate(task_ids, 1):
        result = poll_results.get(task_id, {})
        prompt = prompt_map[task_id]

        if "error" in result:
            error(f"{task_id[:16]}: {result['error']}")
            results.append({"index": idx, "task_id": task_id, "prompt": prompt,
                             "url": None, "filename": None, "error": result["error"]})
            continue

        fname = make_video_filename(
            run_ts, cfg["topic"], cfg["style"], cfg["mood"], idx
        )
        dest = out_dir / fname

        success(f"Video {idx} ready — downloading…")
        video_url, saved = download_result(result, model, task_id, dest)

        entry: dict = {"index": idx, "task_id": task_id, "prompt": prompt,
                       "url": video_url}
        if video_url is None and not saved:
            warn("Video done but URL not parsed — check raw output below.")
            entry["filename"] = None
            entry["raw"] = result
            results.append(entry)
            continue

        entry["filename"] = fname if saved else None
        if saved:
            success(f"Saved → {Fore.CYAN}{dest}{Style.RESET_ALL}")
        results.append(entry)

    # ── Write manifest ────────────────────────────────────────────────────
    if is_kie(model):
        _cps = {"std": {True: 30, False: 20}, "pro": {True: 40, False: 27}}
        cps = _cps[cfg["mode"]][bool(cfg["sound"])]
        total_credits = cps * int(cfg["duration"]) * int(cfg["videos"])
        cost_usd = total_credits * 0.005
    elif is_openai(model):
        total_credits = None
        _sora_rate = {
            "sora":     {"std": 0.10, "pro": 0.10},
            "sora-pro": {"std": 0.30, "pro": 0.50},
        }
        cost_usd = _sora_rate[model][cfg["mode"]] * int(cfg["duration"]) * int(cfg["videos"])
    else:
        total_credits = None
        cost_usd = 0.0
    manifest_path = write_manifest(out_dir, run_ts, cfg, results, total_credits, cost_usd)
    success(f"Manifest → {Fore.CYAN}{manifest_path}{Style.RESET_ALL}")

    # ── Results summary ───────────────────────────────────────────────────
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
