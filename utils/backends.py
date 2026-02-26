"""
backends.py — Video generation backends (create, poll, download)
================================================================
Handles all interaction with video generation APIs:
  - kie.ai backends: Kling 3.0, Veo 3.1, Sora 2 Pro (kie.ai)
  - OpenAI direct: Sora 2, Sora 2 Pro

Each backend provides: create_task, poll, download functions.
"""

import io
import os
import re
import sys
import json
import time
import base64
import pathlib

import requests
from openai import OpenAI
from colorama import Fore, Style

from models import MODELS, is_openai, is_kie
from cli import success, warn, error

# ── Constants ────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 15
POLL_TIMEOUT_SEC  = 3600
HTTP_TIMEOUT_SEC  = 15

NEGATIVE_PROMPT = (
    "text, watermarks, logos, subtitles, UI elements, "
    "motion blur, lens distortion, overexposed highlights, "
    "low quality, blurry, grainy, flickering, cartoonish"
)

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ── API config ───────────────────────────────────────────────────────────────
VIDEO_API_KEY      = os.environ.get("VIDEO_API_KEY", "")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")

VIDEO_API_BASE_URL = "https://api.kie.ai/api/v1"
VIDEO_API_HEADERS  = {
    "Authorization": f"Bearer {VIDEO_API_KEY}",
    "Content-Type":  "application/json",
}

# ── OpenAI client (lazy init) ────────────────────────────────────────────────
openai_client: OpenAI | None = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def ensure_openai_client() -> None:
    """Ensure openai_client is initialized for Sora models."""
    global openai_client
    if openai_client is None:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ── Project directories ──────────────────────────────────────────────────────
_PROJECT_DIR = pathlib.Path(__file__).parent.parent
OUTPUT_DIR   = _PROJECT_DIR / "video_outputs"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, headers: dict | None = None,
              retries: int = 2, backoff: float = 1.0) -> requests.Response:
    """GET with automatic retry on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_SEC)
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
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=HTTP_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# kie.ai createTask backend (Kling 3.0, Sora 2 Pro via kie.ai)
# ─────────────────────────────────────────────────────────────────────────────

def create_video_task(prompt: str, video_cfg: dict,
                      model: str = "kling",
                      image_urls: list[str] | None = None) -> str:
    """
    Submit one video task to a kie.ai createTask-compatible model.
    Returns its taskId.
    """
    model_info = MODELS[model]
    api_model  = model_info["api_model"]

    # Build model-specific input block
    if model == "sora-kie":
        _ar_map = {"16:9": "landscape", "9:16": "portrait", "1:1": "square"}
        input_block: dict = {
            "prompt":           prompt,
            "aspect_ratio":     _ar_map.get(video_cfg["aspect_ratio"], "landscape"),
            "n_frames":         str(video_cfg["duration"]),
            "size":             "high" if video_cfg["mode"] == "pro" else "low",
            "remove_watermark": True,
            "upload_method":    "s3",
        }
    else:  # kling (and future createTask models)
        input_block = {
            "prompt":          prompt,
            "negative_prompt": NEGATIVE_PROMPT,
            "sound":           video_cfg["sound"],
            "duration":        video_cfg["duration"],
            "aspect_ratio":    video_cfg["aspect_ratio"],
            "mode":            video_cfg["mode"],
            "multi_shots":     video_cfg["multi_shots"],
        }
        if image_urls:
            input_block["image_urls"] = image_urls

    payload = {"model": api_model, "input": input_block}

    resp = _http_post(
        f"{VIDEO_API_BASE_URL}/jobs/createTask",
        payload=payload, headers=VIDEO_API_HEADERS,
    )
    data = resp.json()

    if data.get("code") != 200:
        raise RuntimeError(f"Video API error: {data}")

    return data["data"]["taskId"]


def poll_task(task_id: str) -> dict:
    """
    Poll a createTask-based task via /jobs/recordInfo until completion.
    """
    url      = f"{VIDEO_API_BASE_URL}/jobs/recordInfo"
    deadline = time.time() + POLL_TIMEOUT_SEC
    tick     = 0

    while time.time() < deadline:
        resp = _http_get(f"{url}?taskId={task_id}", headers=VIDEO_API_HEADERS)
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
            print()
            return task
        if state in ("CREATE_TASK_FAILED", "GENERATE_FAILED", "FAILED", "ERROR"):
            print()
            raise RuntimeError(f"Task {task_id} failed: {task}")

        time.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError(f"Task {task_id} timed out after {POLL_TIMEOUT_SEC}s")


def poll_all_tasks(task_ids: list[str]) -> dict[str, dict]:
    """
    Poll multiple createTask tasks in round-robin until all complete.
    Returns {task_id: result_dict}.
    """
    url       = f"{VIDEO_API_BASE_URL}/jobs/recordInfo"
    deadline  = time.time() + POLL_TIMEOUT_SEC
    pending   = set(task_ids)
    results:  dict[str, dict] = {}
    statuses: dict[str, str]  = {tid: "PENDING" for tid in task_ids}
    tick = 0

    while pending and time.time() < deadline:
        for task_id in list(pending):
            try:
                resp = _http_get(
                    f"{url}?taskId={task_id}", headers=VIDEO_API_HEADERS,
                )
                data = resp.json()
                if data.get("code") != 200:
                    raise RuntimeError(f"Video API poll error: {data}")

                task  = data.get("data", {})
                state = (task.get("state") or task.get("status") or "").upper()
                statuses[task_id] = state

                if state == "SUCCESS":
                    results[task_id] = task
                    pending.discard(task_id)
                elif state in ("CREATE_TASK_FAILED", "GENERATE_FAILED", "FAILED", "ERROR"):
                    results[task_id] = {"error": f"Task failed: {task}"}
                    pending.discard(task_id)
            except Exception as exc:
                results[task_id] = {"error": str(exc)}
                pending.discard(task_id)

        # Status display
        elapsed = int(time.time() - (deadline - POLL_TIMEOUT_SEC))
        spin = _SPINNER[tick % len(_SPINNER)]
        tick += 1
        parts = []
        for tid in task_ids:
            st = statuses[tid]
            short = tid[:8]
            if st == "SUCCESS":
                parts.append(f"{Fore.GREEN}{short}✓{Style.RESET_ALL}")
            elif st in ("CREATE_TASK_FAILED", "GENERATE_FAILED", "FAILED", "ERROR"):
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

    print()
    for tid in pending:
        results[tid] = {"error": f"Timed out after {POLL_TIMEOUT_SEC}s"}
    return results


def extract_video_url(result: dict) -> str | None:
    """
    Extract the video URL from a KIE AI recordInfo response.
    KIE AI nests results in data.resultJson (a JSON string) which
    contains resultUrls — a list of generated media URLs.
    """
    result_json_str = result.get("resultJson")
    if result_json_str:
        try:
            result_json = json.loads(result_json_str) if isinstance(result_json_str, str) else result_json_str
            urls = result_json.get("resultUrls") or result_json.get("result_urls") or []
            if urls:
                return urls[0]
        except (json.JSONDecodeError, TypeError):
            pass

    for key in ("video_url", "videoUrl", "url", "video"):
        if key in result:
            return result[key]

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
# kie.ai Veo 3.1 backend
# ─────────────────────────────────────────────────────────────────────────────

def create_veo_task(prompt: str, video_cfg: dict,
                    image_urls: list[str] | None = None) -> str:
    """Submit a video task to the kie.ai Veo 3.1 endpoint."""
    payload: dict = {
        "prompt":       prompt,
        "model":        "veo3_fast",
        "aspect_ratio": video_cfg["aspect_ratio"],
        "enableTranslation": True,
        "generationType":    "TEXT_2_VIDEO",
    }
    if image_urls:
        payload["imageUrls"] = image_urls
        payload["generationType"] = "REFERENCE_2_VIDEO"

    resp = _http_post(
        f"{VIDEO_API_BASE_URL}/veo/generate",
        payload=payload, headers=VIDEO_API_HEADERS,
    )
    data = resp.json()

    if data.get("code") != 200:
        raise RuntimeError(f"Veo API error: {data}")

    return data["data"]["taskId"]


def poll_all_veo_tasks(task_ids: list[str]) -> dict[str, dict]:
    """
    Poll multiple Veo tasks in round-robin until all complete.
    Uses /api/v1/veo/details for polling.
    """
    url       = f"{VIDEO_API_BASE_URL}/veo/details"
    deadline  = time.time() + POLL_TIMEOUT_SEC
    pending   = set(task_ids)
    results:  dict[str, dict] = {}
    statuses: dict[str, str]  = {tid: "PENDING" for tid in task_ids}
    tick = 0

    while pending and time.time() < deadline:
        for task_id in list(pending):
            try:
                resp = _http_get(
                    f"{url}?taskId={task_id}", headers=VIDEO_API_HEADERS,
                )
                data = resp.json()
                if data.get("code") != 200:
                    raise RuntimeError(f"Veo poll error: {data}")

                task  = data.get("data", {})
                state = (task.get("state") or task.get("status") or "").upper()
                statuses[task_id] = state

                if state == "SUCCESS":
                    results[task_id] = task
                    pending.discard(task_id)
                elif state in ("FAILED", "ERROR", "CREATE_TASK_FAILED"):
                    results[task_id] = {"error": f"Veo task failed: {task}"}
                    pending.discard(task_id)
            except Exception as exc:
                results[task_id] = {"error": str(exc)}
                pending.discard(task_id)

        elapsed = int(time.time() - (deadline - POLL_TIMEOUT_SEC))
        spin = _SPINNER[tick % len(_SPINNER)]
        tick += 1
        parts = []
        for tid in task_ids:
            st = statuses[tid]
            short = tid[:8]
            if st == "SUCCESS":
                parts.append(f"{Fore.GREEN}{short}✓{Style.RESET_ALL}")
            elif st in ("FAILED", "ERROR", "CREATE_TASK_FAILED"):
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

    print()
    for tid in pending:
        results[tid] = {"error": f"Timed out after {POLL_TIMEOUT_SEC}s"}
    return results


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI Sora backend (direct API)
# ─────────────────────────────────────────────────────────────────────────────

def create_sora_openai_task(prompt: str, video_cfg: dict,
                            model: str = "sora",
                            image_urls: list[str] | None = None) -> str:
    """Submit a video generation job to OpenAI Sora 2."""
    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY is not set — cannot use Sora.")

    sdk_model = MODELS[model]["api_model"]

    # Allowed sizes — no square option in Sora API
    _size_map = {
        "16:9": "1792x1024" if video_cfg["mode"] == "pro" else "1280x720",
        "9:16": "1024x1792" if video_cfg["mode"] == "pro" else "720x1280",
        "1:1":  "1280x720",   # closest fallback; API has no square option
    }
    size = _size_map.get(video_cfg["aspect_ratio"], "1280x720")

    # Sora SDK currently accepts "4" / "8" / "12" only — snap to nearest
    raw_dur = int(video_cfg["duration"])
    seconds = str(min([4, 8, 12], key=lambda v: abs(v - raw_dur)))

    kwargs: dict = {
        "model":   sdk_model,
        "prompt":  prompt,
        "seconds": seconds,
        "size":    size,
    }
    if image_urls:
        # Sora requires the reference image to exactly match the video dimensions.
        # Decode the data URI, resize with Pillow, re-encode as JPEG bytes.
        from PIL import Image as _PilImage
        w, h = (int(d) for d in size.split("x"))
        raw = image_urls[0]
        m = re.match(r"data:([^;]+);base64,(.+)", raw)
        if not m:
            raise ValueError(f"Unexpected image format for Sora: {raw[:40]}")
        data = base64.b64decode(m.group(2))
        img = _PilImage.open(io.BytesIO(data)).convert("RGB")
        if img.size != (w, h):
            img = img.resize((w, h), _PilImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        buf.seek(0)
        kwargs["input_reference"] = ("reference.jpg", buf, "image/jpeg")

    result = openai_client.videos.create(**kwargs)  # type: ignore[union-attr]
    return result.id


def poll_sora_task(video_id: str) -> dict:
    """Poll an OpenAI Sora video job until it completes."""
    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    deadline = time.time() + POLL_TIMEOUT_SEC
    tick = 0

    while time.time() < deadline:
        video = openai_client.videos.retrieve(video_id)  # type: ignore[union-attr]
        state = getattr(video, "status", "unknown")
        elapsed = int(time.time() - (deadline - POLL_TIMEOUT_SEC))

        spin = _SPINNER[tick % len(_SPINNER)]
        tick += 1
        line = (
            f"  {Fore.CYAN}{spin}{Style.RESET_ALL}  "
            f"{Style.DIM}{video_id[:16]}…{Style.RESET_ALL}  "
            f"status: {Fore.YELLOW}{state}{Style.RESET_ALL}  "
            f"{Style.DIM}({elapsed}s){Style.RESET_ALL}"
        )
        print(f"\r{line}     ", end="", flush=True)

        if state == "completed":
            print()
            return {"id": video_id, "status": state, "video": video}
        if state in ("failed", "error"):
            print()
            err = getattr(video, "error", None)
            if err:
                code = getattr(err, "code", "unknown")
                msg  = getattr(err, "message", str(err))
                if code == "moderation_blocked":
                    raise RuntimeError(
                        f"Moderation blocked — Sora rejected the prompt. "
                        f"Try using 'kling' which has less restrictive moderation."
                    )
                raise RuntimeError(f"Sora error [{code}]: {msg}")
            raise RuntimeError(f"Sora task failed (no error detail returned)")

        time.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError(f"Sora task {video_id} timed out after {POLL_TIMEOUT_SEC}s")


def poll_all_sora_tasks(video_ids: list[str]) -> dict[str, dict]:
    """Poll multiple Sora tasks in round-robin until all complete."""
    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    deadline  = time.time() + POLL_TIMEOUT_SEC
    pending   = set(video_ids)
    results:  dict[str, dict] = {}
    statuses: dict[str, str]  = {vid: "pending" for vid in video_ids}
    tick = 0

    while pending and time.time() < deadline:
        for vid in list(pending):
            try:
                video = openai_client.videos.retrieve(vid)  # type: ignore[union-attr]
                state = getattr(video, "status", "unknown")
                statuses[vid] = state

                if state == "completed":
                    results[vid] = {"id": vid, "status": state, "video": video}
                    pending.discard(vid)
                elif state in ("failed", "error"):
                    results[vid] = {"error": f"Sora task failed: {video}"}
                    pending.discard(vid)
            except Exception as exc:
                results[vid] = {"error": str(exc)}
                pending.discard(vid)

        elapsed = int(time.time() - (deadline - POLL_TIMEOUT_SEC))
        spin = _SPINNER[tick % len(_SPINNER)]
        tick += 1
        parts = []
        for vid in video_ids:
            st = statuses[vid]
            short = vid[:8]
            if st == "completed":
                parts.append(f"{Fore.GREEN}{short}✓{Style.RESET_ALL}")
            elif st in ("failed", "error"):
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

    print()
    for vid in pending:
        results[vid] = {"error": f"Timed out after {POLL_TIMEOUT_SEC}s"}
    return results


def download_sora_video(video_id: str, dest: pathlib.Path) -> bool:
    """Download a completed Sora video to disk via the OpenAI SDK."""
    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    try:
        stream = openai_client.videos.download_content(video_id)  # type: ignore[union-attr]
        with dest.open("wb") as fh:
            fh.write(stream.read())
        return True
    except Exception as exc:
        warn(f"Sora download failed: {exc}")
        if dest.exists():
            dest.unlink()
        return False


def extract_sora_video_url(result: dict) -> str | None:
    """Sora videos are downloaded via SDK — return a placeholder for manifest."""
    vid = result.get("id")
    return f"sora://video/{vid}" if vid else None


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers — directory, filenames, download, manifest
# ─────────────────────────────────────────────────────────────────────────────

def make_output_dir() -> pathlib.Path:
    """Create video_outputs/ next to the script if it doesn't exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def make_video_filename(run_ts: str, topic: str, style: str,
                        mood: str, index: int) -> str:
    """
    Build a descriptive, filesystem-safe filename for one video.
    Format: {YYYYMMDD_HHMMSS}_{topic}_{style}_{mood}_{NNN}.mp4
    """
    def _safe(s: str) -> str:
        return re.sub(r"[^a-z0-9-]", "", s.lower().replace(" ", "-"))[:20]

    return f"{run_ts}_{_safe(topic)}_{_safe(style)}_{_safe(mood)}_{index:03d}.mp4"


def download_video(url: str, dest: pathlib.Path,
                   retries: int = 3, backoff: float = 2.0) -> bool:
    """
    Stream-download a video URL to `dest` with retry on transient errors.
    Cleans up partial files on failure.
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
            print()
            return True
        except Exception as exc:
            print()
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
                   cfg: dict, results: list[dict],
                   total_credits: int | None = None,
                   cost_usd: float | None = None) -> pathlib.Path:
    """Write a JSON manifest capturing every detail of the run."""
    manifest: dict = {
        "version":   "1.1",
        "run_ts":    run_ts,
        "model":     cfg.get("model", "kling"),
        "pipeline":  cfg.get("pipeline", "auto"),
    }
    if total_credits is not None:
        manifest["estimated_credits"] = total_credits
    if cost_usd is not None:
        manifest["estimated_cost_usd"] = round(cost_usd, 2)

    manifest["settings"] = cfg
    manifest["videos"] = [
        {
            "index":     r["index"],
            "filename":  r.get("filename"),
            "task_id":   r["task_id"],
            "url":       r.get("url"),
            "prompt":    r["prompt"],
            "error":     r.get("error"),
        }
        for r in results
    ]
    path = out_dir / f"{run_ts}_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Unified dispatch helpers
# ─────────────────────────────────────────────────────────────────────────────

def submit_task(prompt: str, video_cfg: dict, model: str,
                image_urls: list[str] | None = None) -> str:
    """Route task creation to the correct backend based on model."""
    model_info = MODELS[model]

    if is_openai(model):
        return create_sora_openai_task(prompt, video_cfg, model=model,
                                       image_urls=image_urls)
    elif model_info.get("endpoint") == "veo":
        return create_veo_task(prompt, video_cfg, image_urls=image_urls)
    else:
        return create_video_task(prompt, video_cfg, model=model,
                                 image_urls=image_urls)


def poll_all(task_ids: list[str], model: str) -> dict[str, dict]:
    """Route polling to the correct backend based on model."""
    model_info = MODELS[model]

    if is_openai(model):
        return poll_all_sora_tasks(task_ids)
    elif model_info.get("endpoint") == "veo":
        return poll_all_veo_tasks(task_ids)
    else:
        return poll_all_tasks(task_ids)


def download_result(result: dict, model: str, task_id: str,
                    dest: pathlib.Path) -> tuple[str | None, bool]:
    """
    Download a poll result from the correct backend.
    Returns (video_url, saved_success).
    """
    if is_openai(model):
        video_url = extract_sora_video_url(result)
        saved = download_sora_video(task_id, dest)
        return video_url, saved
    else:
        video_url = extract_video_url(result)
        if video_url:
            saved = download_video(video_url, dest)
            return video_url, saved
        return None, False
