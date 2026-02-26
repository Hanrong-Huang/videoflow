# AI Video Generator 1.1

Automate the process of turning trending news headlines or personal scene concepts into professional AI-generated videos.

Supports multiple video backends: **Kling 3.0**, **Veo 3.1**, **Sora 2 Pro** (via kie.ai) and **OpenAI Sora 2 / Sora 2 Pro** (direct).

---

## Project Structure

```
videoflow/
├── README.md                   
├── utils/
│   ├── video_gen.py            (Main orchestrator — run this)
│   ├── models.py               (Video model registry)
│   ├── cli.py                  (Interactive wizard & UI helpers)
│   ├── prompts.py              (GLM-4.7 prompt generation & news)
│   └── backends.py             (Video API backends — create/poll/download)
├── presets/
│   ├── auto_example.json       (Auto-mode template)
│   └── manual_example.json     (Manual-mode template)
├── images/
│   └── README.md               (Reference images directory)
└── video_outputs/              (Generated videos and manifests)
    ├── 20260225_*.mp4
    └── *_manifest.json
```

---

## Quick Start

```bash
# Install dependencies (one-time)
pip install requests openai colorama questionary Pillow ddgs

# Interactive wizard (step-by-step mode)
python utils/video_gen.py

# Full CLI mode — Kling (default)
python utils/video_gen.py --pipeline auto --topic technology --style cinematic --videos 3

# Full CLI mode — OpenAI Sora 2
python utils/video_gen.py --pipeline auto --model sora --topic technology --videos 3

# Full CLI mode — Veo 3.1 via kie.ai
python utils/video_gen.py --pipeline auto --model veo3 --topic technology --videos 3

# Load a preset
python utils/video_gen.py --preset presets/auto_example.json
```

---

## Pipeline Overview

The script operates in two primary modes:

### Auto Mode (News to Video)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AUTO MODE PIPELINE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────┐    ┌──────────────────┐    ┌─────────────────┐   │
│  │ Google News   │    │   Z.AI GLM-4.7   │    │  Video Backend  │   │
│  │ RSS Feed      │───▶│   Prompt Engine  │───▶│  (Kling / Sora) │   │
│  │               │    │                  │    │                 │   │
│  │ • Region      │    │ • Selects best   │    │ • Renders .mp4  │   │
│  │ • Language    │    │   headlines      │    │ • Adds audio    │   │
│  │ • Topic       │    │ • Writes rich    │    │ • Downloads     │   │
│  │ • Count       │    │   5-element      │    │   to disk       │   │
│  │               │    │   prompts        │    │                 │   │
│  └───────────────┘    └──────────────────┘    └─────────────────┘   │
│                                                                     │
│  STEP 1: Fetch          STEP 2: Generate        STEP 3: Render      │
│  headlines              video prompts           & download          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Workflow:**
1. Fetches trending headlines from Google News RSS based on region and topic.
2. GLM-4.7 analyzes the headlines, selects the most visually compelling ones, and writes detailed video prompts.
3. Each prompt is submitted to the chosen video backend (Kling or Sora), polled in parallel, and downloaded.

### Manual Mode (Your Ideas to Video)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MANUAL MODE PIPELINE                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────┐    ┌──────────────────┐    ┌─────────────────┐   │
│  │ Your Scene    │    │   Z.AI GLM-4.7   │    │  Video Backend  │   │
│  │ Concepts      │───▶│   Enhancement    │───▶│  (Kling / Sora) │   │
│  │               │    │                  │    │                 │   │
│  │ Short text    │    │ • Keeps your     │    │ • Renders .mp4  │   │
│  │ descriptions  │    │   original idea  │    │ • Adds audio    │   │
│  │ of scenes     │    │ • Adds camera,   │    │ • Downloads     │   │
│  │ you want      │    │   lighting,      │    │   to disk       │   │
│  │               │    │   motion detail  │    │                 │   │
│  └───────────────┘    └──────────────────┘    └─────────────────┘   │
│                                                                     │
│  STEP 1: Input          STEP 2: Enhance         STEP 3: Render      │
│  scene concepts         with AI                 & download          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Workflow:**
1. You provide short scene descriptions (e.g., "A drone flying over a solar farm").
2. GLM-4.7 enhances each description into a detailed, production-ready prompt including camera angles and lighting.
3. Each enhanced prompt is generated, polled, and downloaded.

---

## Video Models

Models are defined in `utils/models.py`. Edit that file to add or remove models.

| CLI Name    | Model                  | Backend       | Notes                                    |
|-------------|------------------------|---------------|------------------------------------------|
| `kling`     | Kling 3.0              | kie.ai        | Default. Multi-shot, native audio        |
| `veo3`      | Google Veo 3.1 Fast    | kie.ai        | Cinematic, audio, 1080p                  |
| `sora-kie`  | OpenAI Sora 2 Pro      | kie.ai        | Credit-based billing                     |
| `sora`      | OpenAI Sora 2          | OpenAI direct | $0.10/sec                                |
| `sora-pro`  | OpenAI Sora 2 Pro      | OpenAI direct | $0.30–0.50/sec                           |

Select via `--model kling`, `--model veo3`, `--model sora-kie`, etc. Default is `kling`.

---

## Usage Modes

### 1. Interactive Wizard

Recommended for first-time use. It walks you through every option and provides standard defaults.
```bash
python utils/video_gen.py
```

### 2. CLI Arguments

For automation or scripting. Any omitted argument falls back to its default.

```bash
# Kling (default)
python utils/video_gen.py --pipeline auto --topic technology --style cinematic --videos 3

# Sora
python utils/video_gen.py --pipeline auto --model sora --topic technology --videos 3
```

**Available Arguments:**

| Argument          | Default      | Description                                |
|-------------------|-------------|---------------------------------------------|
| `--pipeline`      | `auto`      | `auto` (news) or `manual` (custom scenes)   |
| `--model`         | `kling`     | `kling`, `veo3`, `sora-kie`, `sora`, `sora-pro`  |
| `--region`        | `US`        | 2-letter country code                        |
| `--language`      | `en`        | 2-letter language code                       |
| `--topic`         | `top`       | News category or search keyword              |
| `--trends-count`  | `5`         | Number of headlines to fetch                 |
| `--style`         | `cinematic` | Visual rendering style                       |
| `--mood`          | `dynamic`   | Tone and atmosphere                          |
| `--videos`        | `3`         | Number of videos to generate                 |
| `--aspect`        | `16:9`      | Aspect ratio (`16:9`, `9:16`, `1:1`)         |
| `--duration`      | `5`         | Clip length in seconds                       |
| `--mode`          | `std`       | `std` (720p) or `pro` (1080p)                |
| `--sound`         | on          | Use `--no-sound` to disable audio            |
| `--image`         | none        | URL/local path for image-to-video            |
| `--prompts`       | —           | Scene descriptions (required for manual)     |

**Example (Manual CLI with Sora):**
```bash
python utils/video_gen.py --pipeline manual --model sora \
  --prompts "A drone over a solar farm" "Robot assembling a circuit board" \
  --style cinematic --mood epic --duration 10
```

### 3. Preset Files

Presets are JSON files that pre-fill configurations. You can copy the templates in the `presets/` directory to create custom workflows.

```bash
python utils/video_gen.py --preset presets/auto_example.json
```

### 4. Resume Failed Downloads

If a run fails or is interrupted, you can resume downloading existing finished videos without consuming extra API credits.

```bash
python utils/video_gen.py --resume video_outputs/20260225_manifest.json
```

*Note: You can also manually download complete videos by copying the `"url"` field directly from the manifest JSON.*

---

## Image-to-Video

Provide a reference image to serve as the first frame of the generated video.

**Methods to attach an image:**
1. Place images in `images/` and select via the wizard menu.
2. Paste an HTTP/HTTPS URL during the prompt.
3. Pass via CLI: `--image https://example.com/photo.jpg` or `--image images/photo.png`.

*Supported formats: `.png`, `.jpg`, `.jpeg`, `.webp`*

---

## Prompt Elements

Each AI-enhanced prompt from GLM-4.7 structured into core elements:

1. **Camera & Movement:** e.g., "Slow dolly push-in, shallow depth of field"
2. **Scene & Subject:** e.g., "A humanoid robot hand tracing a holographic display"
3. **Lighting & Color:** e.g., "Cool blue-white LEDs, warm amber key light"
4. **Motion & Dynamics:** e.g., "Fingers tap rhythmically, particles orbit"
5. **Temporal Arc:** e.g., "Starts tight, pulls back to reveal full scene"
6. **Audio (if enabled):** e.g., "Low hum, mechanical clicks, ambient reverb"

---

## Output

Completed runs generate:
- **Video files:** Fixed naming format `{timestamp}_{topic}_{style}_{mood}_{001}.mp4`
- **Manifest File:** `{timestamp}_manifest.json` containing settings, task IDs, and direct URLs.

---

## Environment Variables

| Variable         | Required For                         | Purpose                              |
|-----------------|--------------------------------------|--------------------------------------|
| `ZAI_API_KEY`    | All models                           | Z.AI (GLM-4.7) API key              |
| `VIDEO_API_KEY`  | kie.ai models (`kling`, `veo3`, `sora-kie`) | Kie.ai API bearer token       |
| `OPENAI_API_KEY` | OpenAI models (`sora`, `sora-pro`)   | OpenAI API key                       |

Set the keys for the backend(s) you plan to use. Only the relevant keys are validated at runtime.

**Mac/Linux:**
```bash
export ZAI_API_KEY="your_zai_key_here"
export VIDEO_API_KEY="your_kie_api_key_here"      # for Kling
export OPENAI_API_KEY="your_openai_key_here"      # for Sora
```

**Windows (PowerShell):**
```powershell
$env:ZAI_API_KEY="your_zai_key_here"
$env:VIDEO_API_KEY="your_kie_api_key_here"        # for Kling
$env:OPENAI_API_KEY="your_openai_key_here"        # for Sora
```

---

## Requirements

- Python 3.11+
- `requests`
- `openai`
- `colorama`
- `questionary` — interactive wizard menus
- `Pillow` — image resizing for Sora image-to-video
- `ddgs` — web search for background research (`pip install ddgs`)
