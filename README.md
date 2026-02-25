# AI Video Generator 1.0

Automate the process of turning trending news headlines or personal scene concepts into professional AI-generated videos.

---

## Project Structure

```
workflow/
├── README.md                   
├── utils/
│   └── video_gen.py            (Main script)
├── presets/
│   ├── sample_tech_news.json   (Auto-mode template)
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
pip install requests openai colorama

# Interactive wizard (step-by-step mode)
python utils/video_gen.py

# Full CLI mode (no interaction needed)
python utils/video_gen.py --pipeline auto --topic technology --style cinematic --videos 3

# Load a preset (auto mode)
python utils/video_gen.py --preset presets/auto_example.json

# Load a preset (manual mode)
python utils/video_gen.py --preset presets/manual_example.json
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
│  │ Google News   │    │   Z.AI GLM-4.7   │    │  AI Video API   │   │
│  │ RSS Feed      │───▶│   Prompt Engine  │───▶│  Generation    │   │
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
3. Each prompt is submitted to the AI video API, polled in parallel, and downloaded.

### Manual Mode (Your Ideas to Video)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MANUAL MODE PIPELINE                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────┐    ┌──────────────────┐    ┌─────────────────┐   │
│  │ Your Scene    │    │   Z.AI GLM-4.7   │    │  AI Video API   │   │
│  │ Concepts      │───▶│   Enhancement    │───▶│  Generation    │   │
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

## Usage Modes

### 1. Interactive Wizard

Recommended for first-time use. It walks you through every option and provides standard defaults.
```bash
python utils/video_gen.py
```

### 2. CLI Arguments

For automation or scripting. Any omitted argument falls back to its default.

```bash
python utils/video_gen.py --pipeline auto --topic technology --style cinematic --videos 3
```

**Available Arguments:**

| Argument          | Default      | Description                                |
|-------------------|-------------|--------------------------------------------|
| `--pipeline`      | `auto`      | `auto` (news) or `manual` (custom scenes)  |
| `--region`        | `US`        | 2-letter country code                      |
| `--language`      | `en`        | 2-letter language code                     |
| `--topic`         | `top`       | News category or search keyword            |
| `--trends-count`  | `5`         | Number of headlines to fetch               |
| `--style`         | `cinematic` | Visual rendering style                     |
| `--mood`          | `dynamic`   | Tone and atmosphere                        |
| `--videos`        | `3`         | Number of videos to generate               |
| `--aspect`        | `16:9`      | Aspect ratio (`16:9`, `9:16`, `1:1`)       |
| `--duration`      | `5`         | Clip length in seconds                     |
| `--mode`          | `std`       | `std` (720p) or `pro` (1080p)              |
| `--sound`         | on          | Use `--no-sound` to disable audio          |
| `--image`         | none        | URL/local path for image-to-video          |
| `--prompts`       | —           | Scene descriptions (required for manual)   |

**Example (Manual CLI):**
```bash
python utils/video_gen.py --pipeline manual \
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

| Variable        | Purpose                              |
|----------------|--------------------------------------|
| `VIDEO_API_KEY` | AI video generation API bearer token |
| `ZAI_API_KEY`   | Z.AI (GLM-4.7) API key               |

You **must** set these environment variables before running the script.

**Mac/Linux:**
```bash
export VIDEO_API_KEY="your_api_key_here"
export ZAI_API_KEY="your_zai_key_here"
```

**Windows (PowerShell):**
```powershell
$env:VIDEO_API_KEY="your_api_key_here"
$env:ZAI_API_KEY="your_zai_key_here"
```

---

## Requirements

- Python 3.11+
- `requests`
- `openai`
- `colorama`
