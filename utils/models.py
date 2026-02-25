"""
models.py — Video Model Registry
=================================
Central registry of all supported video generation models.
To add or remove a model, edit the MODELS dict below.

Each entry maps a short name (used in --model CLI / presets / wizard)
to its configuration:

    api_model   — model string sent to the API
    backend     — "kie" (kie.ai unified API) or "openai" (OpenAI direct)
    endpoint    — kie.ai endpoint variant: "createTask" | "veo" | None
    label       — display name for UI
    desc        — one-line description shown in the wizard
"""

# ─── Backend identifiers ─────────────────────────────────────────────────────
BACKEND_KIE    = "kie"
BACKEND_OPENAI = "openai"

# ─── Model registry ──────────────────────────────────────────────────────────
MODELS: dict[str, dict] = {

    # ── kie.ai backend ────────────────────────────────────────────────────
    "kling": {
        "api_model": "kling-3.0/video",
        "backend":   BACKEND_KIE,
        "endpoint":  "createTask",
        "label":     "Kling 3.0",
        "desc":      "Kling 3.0 via kie.ai — multi-shot, native audio",
    },
    "veo3": {
        "api_model": "veo3_fast",
        "backend":   BACKEND_KIE,
        "endpoint":  "veo",
        "label":     "Veo 3.1 Fast",
        "desc":      "Google Veo 3.1 Fast via kie.ai — cinematic, audio",
    },
    "sora-kie": {
        "api_model": "sora-2-pro-text-to-video",
        "backend":   BACKEND_KIE,
        "endpoint":  "createTask",
        "label":     "Sora 2 Pro (kie.ai)",
        "desc":      "OpenAI Sora 2 Pro via kie.ai — credit-based",
    },

    # ── OpenAI direct backend ─────────────────────────────────────────────
    "sora": {
        "api_model": "sora-2",
        "backend":   BACKEND_OPENAI,
        "endpoint":  None,
        "label":     "Sora 2",
        "desc":      "OpenAI Sora 2 direct — $0.10/sec",
    },
    "sora-pro": {
        "api_model": "sora-2-pro",
        "backend":   BACKEND_OPENAI,
        "endpoint":  None,
        "label":     "Sora 2 Pro",
        "desc":      "OpenAI Sora 2 Pro direct — $0.30–0.50/sec",
    },
}

# ─── Convenience exports ─────────────────────────────────────────────────────
MODEL_NAMES: list[str] = list(MODELS.keys())


def model_descriptions() -> dict[str, str]:
    """Return {name: desc} dict for use in wizard ask_choice."""
    return {k: v["desc"] for k, v in MODELS.items()}


def is_openai(model: str) -> bool:
    """Check if a model uses the OpenAI direct backend."""
    return MODELS.get(model, {}).get("backend") == BACKEND_OPENAI


def is_kie(model: str) -> bool:
    """Check if a model uses the kie.ai backend."""
    return MODELS.get(model, {}).get("backend") == BACKEND_KIE


def needs_openai_key(model: str) -> bool:
    """Check if a model requires OPENAI_API_KEY."""
    return is_openai(model)


def needs_video_api_key(model: str) -> bool:
    """Check if a model requires VIDEO_API_KEY (kie.ai)."""
    return is_kie(model)
