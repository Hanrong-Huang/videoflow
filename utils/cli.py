"""
cli.py — UI helpers, interactive wizard, and settings confirmation
==================================================================
Contains all user-facing I/O: banners, prompts, the setup wizard,
region tables, and the confirmation screen.
"""

import pathlib
import base64

from colorama import Fore, Style
from models import MODELS, MODEL_NAMES, model_descriptions, is_openai, is_kie

# ── Optional: questionary for arrow-key menus ──────────────────────────────
try:
    import questionary
    from questionary import Choice, Style as QStyle
    _Q_STYLE = QStyle([
        ("qmark",          "fg:#CC785C bold"),
        ("question",       "fg:#E8DCC8 bold"),
        ("pointer",        "fg:#CC785C bold"),
        ("highlighted",    "fg:#E8DCC8 bold"),
        ("selected",       "fg:#A0522D"),
        ("answer",         "fg:#CC785C bold"),
        ("instruction",    "fg:#6B6B6B"),
        ("text",           "fg:#C8B89A"),
        ("disabled",       "fg:#555555 italic"),
        ("separator",      "fg:#3D3D3D"),
    ])
    HAS_QUESTIONARY = True
except ImportError:
    HAS_QUESTIONARY = False

# ── Project directories ───────────────────────────────────────────────────────
_PROJECT_DIR = pathlib.Path(__file__).parent.parent
IMAGES_DIR   = _PROJECT_DIR / "images"

# ── Image handling ────────────────────────────────────────────────────────────
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# ── Google News topic sections (used in wizard) ──────────────────────────────
_TOPIC_SECTIONS: dict[str, str] = {
    "top":           "",
    "world":         "WORLD",
    "nation":        "NATION",
    "business":      "BUSINESS",
    "technology":    "TECHNOLOGY",
    "entertainment": "ENTERTAINMENT",
    "sports":        "SPORTS",
    "science":       "SCIENCE",
    "health":        "HEALTH",
}

# ── Supported Google News regions ─────────────────────────────────────────────
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
# UI primitives — banners, labels, colors
# ─────────────────────────────────────────────────────────────────────────────

W = 64  # total banner width


def banner(title: str, subtitle: str = "") -> None:
    """Print a full-width double-bordered banner box."""
    iw = W - 4  # inner width (minus border + padding)
    line = "═" * (W - 2)
    print(f"\n{Fore.YELLOW}  ╔{line}╗")
    print(f"  ║{' ' * iw}  ║")
    print(f"  ║  {Style.RESET_ALL}{Style.BRIGHT}{title.center(iw)}{Style.RESET_ALL}{Fore.YELLOW}║")
    if subtitle:
        print(f"  ║  {Style.RESET_ALL}{Style.DIM}{subtitle.center(iw)}{Style.RESET_ALL}{Fore.YELLOW}║")
    print(f"  ║{' ' * iw}  ║")
    print(f"  ╚{line}╝{Style.RESET_ALL}")


def section(title: str) -> None:
    """Print a section divider with a highlighted title."""
    pad = W - 6 - len(title)
    bar = "─" * max(pad, 0)
    print(f"\n{Style.DIM}  ┌──{Style.RESET_ALL} {Fore.YELLOW}{Style.BRIGHT}{title}{Style.RESET_ALL} {Style.DIM}{bar}┐{Style.RESET_ALL}")


def divider() -> None:
    """Print a thin horizontal rule."""
    print(f"  {Style.DIM}{'─' * (W - 4)}{Style.RESET_ALL}")


def info(label: str, value: str) -> None:
    print(f"  {Style.DIM}│{Style.RESET_ALL}  {Style.DIM}{label:<12}{Style.RESET_ALL}{Fore.WHITE}{Style.BRIGHT}{value}{Style.RESET_ALL}")


def success(msg: str) -> None:
    print(f"  {Fore.GREEN}✔{Style.RESET_ALL}  {msg}")


def warn(msg: str) -> None:
    print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL}  {msg}")


def error(msg: str) -> None:
    print(f"  {Fore.RED}✖{Style.RESET_ALL}  {msg}")


def step(n: int, total: int, msg: str) -> None:
    """Print a pipeline step with a visual progress indicator."""
    # Build progress bar: ●━━━━○━━━━○
    parts: list[str] = []
    for i in range(1, total + 1):
        if i < n:
            parts.append(f"{Fore.GREEN}━━{Style.RESET_ALL}")
        elif i == n:
            parts.append(f"{Fore.YELLOW}{Style.BRIGHT}●{Style.RESET_ALL}")
        else:
            parts.append(f"{Style.DIM}○{Style.RESET_ALL}")
        if i < total:
            if i < n:
                parts.append(f"{Fore.GREEN}━━━{Style.RESET_ALL}")
            else:
                parts.append(f"{Style.DIM}───{Style.RESET_ALL}")
    progress = "".join(parts)
    label = f"Step {n}/{total}"
    print(f"\n  {progress}  {Fore.YELLOW}{Style.BRIGHT}{label}{Style.RESET_ALL}")
    print(f"  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive prompt helpers
# ─────────────────────────────────────────────────────────────────────────────

def print_region_table() -> None:
    """Print all supported regions grouped by geography."""
    print(f"\n  {Style.BRIGHT}Supported regions:{Style.RESET_ALL}")
    for group, entries in REGIONS.items():
        print(f"\n  {Fore.YELLOW}{Style.BRIGHT}{group}{Style.RESET_ALL}")
        for i in range(0, len(entries), 3):
            row = entries[i:i + 3]
            line = "   ".join(
                f"{Fore.YELLOW}{Style.BRIGHT}{code}{Style.RESET_ALL} {Style.DIM}{name:<16}{Style.RESET_ALL}"
                for code, name in row
            )
            print(f"    {line}")
    print(
        f"\n  {Fore.YELLOW}⚠{Style.RESET_ALL}  China (CN) is not supported — Google is blocked."
        f"\n     Use {Fore.YELLOW}HK{Style.RESET_ALL} or "
        f"{Fore.YELLOW}TW{Style.RESET_ALL} with language {Fore.YELLOW}zh{Style.RESET_ALL} instead."
    )


def ask_choice(prompt: str, options: list[str], default: str,
               descriptions: dict[str, str] | None = None,
               allow_freetext: bool = False) -> str:
    """
    Show a list of options and return the user's pick.
    Uses arrow-key navigation if questionary is installed,
    otherwise falls back to numbered input.
    """
    if HAS_QUESTIONARY:
        # Build Choice objects with descriptions
        choices = []
        for opt in options:
            desc = descriptions[opt] if descriptions and opt in descriptions else ""
            label = f"{opt:<16} — {desc}" if desc else opt
            choices.append(Choice(title=label, value=opt))
        if allow_freetext:
            choices.append(Choice(title="Other (type a keyword)", value="__freetext__"))

        result = questionary.select(
            prompt,
            choices=choices,
            default=default,
            style=_Q_STYLE,
            use_indicator=True,
            use_shortcuts=False,
            use_jk_keys=True,
            instruction="(↑↓ arrows or j/k to move, enter to select)",
        ).ask()

        if result is None:  # Ctrl+C
            raise KeyboardInterrupt
        if result == "__freetext__":
            raw = input(f"  {Fore.YELLOW}▸{Style.RESET_ALL} Enter keyword: ").strip()
            if raw:
                print(f"  {Style.DIM}→ Using '{raw}' as a keyword search{Style.RESET_ALL}")
                return raw
            return default
        return result

    # Fallback: numbered input
    print(f"\n  {Fore.WHITE}{Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    for i, opt in enumerate(options, 1):
        marker = f"{Fore.YELLOW}▸" if opt == default else f"{Style.DIM} {Style.RESET_ALL}"
        desc   = f"  {Style.DIM}— {descriptions[opt]}{Style.RESET_ALL}" if descriptions and opt in descriptions else ""
        dflt   = f"  {Fore.GREEN}(default){Style.RESET_ALL}" if opt == default else ""
        print(f"    {marker}{Style.BRIGHT}{i:>2}){Style.RESET_ALL}  {Fore.WHITE}{opt:<16}{Style.RESET_ALL}{desc}{dflt}")

    while True:
        raw = input(
            f"\n  {Fore.YELLOW}▸{Style.RESET_ALL} Enter number or value "
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

        lower_options = [o.lower() for o in options]
        if raw.lower() not in lower_options:
            print(f"  {Style.DIM}→ Using '{raw}' as a keyword search{Style.RESET_ALL}")
        return raw


def ask_free(prompt: str, default: str, note: str = "") -> str:
    """Prompt for a free-text value; shows a note line if provided."""
    print(f"\n  {Fore.WHITE}{Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    if note:
        print(f"  {Style.DIM}{note}{Style.RESET_ALL}")
    raw = input(
        f"  {Fore.YELLOW}▸{Style.RESET_ALL} Enter value "
        f"{Style.DIM}[{default}]{Style.RESET_ALL}: "
    ).strip()
    return raw if raw else default


def ask_int(prompt: str, default: int, min_val: int, max_val: int) -> int:
    """Prompt for an integer within [min_val, max_val]."""
    print(f"\n  {Fore.WHITE}{Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    while True:
        raw = input(
            f"  {Fore.YELLOW}▸{Style.RESET_ALL} Enter number "
            f"{Style.DIM}[{default}] range {min_val}–{max_val}{Style.RESET_ALL}: "
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
    One scene per entry → one video per scene (1:1).
    End a line with ``\\`` to continue on the next line.
    Press Enter on an empty line when done.
    """
    print(f"\n  {Fore.WHITE}{Style.BRIGHT}Enter your scene concept(s){Style.RESET_ALL}")
    print(f"  {Style.DIM}One scene per entry = one video.  Each scene is enhanced by GLM.{Style.RESET_ALL}")
    print(f"  {Style.DIM}End a line with \\ to continue on the next line.{Style.RESET_ALL}")
    print(f"  {Style.DIM}Press Enter on an empty line when done.{Style.RESET_ALL}")
    prompts: list[str] = []
    while True:
        parts: list[str] = []
        first = True
        while True:
            label = (
                f"  {Fore.YELLOW}▸ Scene {len(prompts) + 1}{Style.RESET_ALL}: "
                if first
                else f"  {Fore.YELLOW}  ...{Style.RESET_ALL}    : "
            )
            raw = input(label).rstrip()
            first = False
            if raw.endswith("\\"):
                parts.append(raw[:-1].strip())
                continue
            else:
                parts.append(raw.strip())
                break

        concept = " ".join(p for p in parts if p)
        if not concept:
            if not prompts:
                error("Please enter at least one scene concept.")
                continue
            break
        prompts.append(concept)
        success(f"Got it ({len(concept)} chars)")
    return prompts


# ─────────────────────────────────────────────────────────────────────────────
# Image helpers
# ─────────────────────────────────────────────────────────────────────────────

def _list_images() -> list[pathlib.Path]:
    """Return sorted list of image files in the images/ folder."""
    if not IMAGES_DIR.is_dir():
        return []
    return sorted(
        p for p in IMAGES_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    )


def _encode_image_base64(path: pathlib.Path) -> str:
    """Read a local image and return a base64 data URI."""
    mime = {".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp"}
    ext = path.suffix.lower()
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime.get(ext, 'application/octet-stream')};base64,{data}"


def ask_image() -> str | None:
    """
    Interactive helper: let the user pick an image from images/ folder,
    enter a URL, or skip entirely.  Returns a URL string or None.
    """
    images = _list_images()

    if images:
        print(f"\n  {Fore.WHITE}{Style.BRIGHT}Reference image (for image-to-video):{Style.RESET_ALL}")
        print(f"    {Fore.YELLOW}{Style.BRIGHT} 0){Style.RESET_ALL}  No image — text-to-video  {Style.DIM}(default){Style.RESET_ALL}")
        for i, img in enumerate(images, 1):
            print(f"     {Fore.YELLOW}{Style.BRIGHT}{i:>2}){Style.RESET_ALL}  {img.name}")
        print(f"     {Fore.YELLOW}{Style.BRIGHT}{len(images) + 1:>2}){Style.RESET_ALL}  Enter a URL manually")

        while True:
            raw = input(
                f"\n  {Fore.YELLOW}▸{Style.RESET_ALL} Enter number or URL "
                f"{Style.DIM}[0]{Style.RESET_ALL}: "
            ).strip()
            if not raw or raw == "0":
                return None
            if raw.startswith(("http://", "https://", "data:")):
                return raw
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(images):
                    return _encode_image_base64(images[idx])
                if idx == len(images):
                    url = input(f"  {Fore.YELLOW}▸{Style.RESET_ALL} Image URL: ").strip()
                    return url if url else None
            except ValueError:
                pass
            error(f"Please enter 0–{len(images) + 1} or a URL.")
    else:
        raw = input(
            f"\n  {Fore.WHITE}{Style.BRIGHT}Reference image URL{Style.RESET_ALL} "
            f"{Style.DIM}(blank to skip){Style.RESET_ALL}: "
        ).strip()
        return raw if raw else None


# ─────────────────────────────────────────────────────────────────────────────
# Settings confirmation — bordered table
# ─────────────────────────────────────────────────────────────────────────────

def confirm_settings(cfg: dict) -> bool:
    """Show a bordered summary of all settings and ask for confirmation."""
    banner("REVIEW YOUR SETTINGS", "Confirm before spending API credits")

    if cfg["pipeline"] == "auto":
        section("News source")
        info("Region",     cfg["region"])
        info("Language",   cfg["language"])
        info("Topic",      cfg["topic"])
    else:
        section("Your scene concepts")
        for i, p in enumerate(cfg["user_prompts"], 1):
            print(f"  {Style.DIM}│{Style.RESET_ALL}  {Style.DIM}{i}.{Style.RESET_ALL} {p[:75]}{'…' if len(p) > 75 else ''}")

    section("Prompt style")
    info("Style",    cfg["style"])
    info("Mood",     cfg["mood"])
    info("Videos",   str(cfg["videos"]))
    info("Research", cfg.get("research") or "none")

    section("Video output")
    model_name = cfg.get("model", "kling")
    model_label = MODELS.get(model_name, {}).get("label", model_name)
    info("Model",    f"{model_label}  {Style.DIM}({model_name}){Style.RESET_ALL}")
    info("Aspect",   cfg["aspect"])
    info("Duration", f"{cfg['duration']}s")
    info("Mode",     f"{'1080P' if cfg['mode'] == 'pro' else '720P'}  {Style.DIM}({cfg['mode']}){Style.RESET_ALL}")
    info("Sound",    f"{'on' if cfg['sound'] else 'off'}")
    info("Image",    "attached" if cfg.get("image_url") else "none (text-to-video)")

    divider()

    print()
    raw = input(
        f"  {Fore.GREEN}{Style.BRIGHT}▸ Start generating?{Style.RESET_ALL} "
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
    banner("VideoFlow", "AI Video Generator v1.1")
    print(f"  {Style.DIM}  Press Enter at any prompt to accept the default value.{Style.RESET_ALL}")
    print(f"  {Style.DIM}  Press Ctrl+C at any time to quit.{Style.RESET_ALL}")

    # ── Pipeline mode ─────────────────────────────────────────────────────────
    pipeline = ask_choice(
        "Pipeline mode:",
        options=["auto", "manual"],
        default="auto",
        descriptions={
            "auto":   "Trending news → GLM → AI video",
            "manual": "Your scenes → GLM enhance → AI video",
        },
    )

    user_prompts: list[str] = []

    if pipeline == "auto":
        section("STEP 1 / 3 — News source")
        print_region_table()
        region = ask_free(
            "Which region? (enter the 2-letter code)", default="US",
        ).upper()
        language = ask_free(
            "Language code?", default="en",
            note="en English │ es Spanish │ fr French │ de German │ ja Japanese\n"
                 "  zh Chinese │ ko Korean  │ pt Portuguese │ ar Arabic",
        ).lower()
        topic = ask_choice(
            "Which news topic?",
            options=list(_TOPIC_SECTIONS.keys()),
            default="top",
            allow_freetext=True,
            descriptions={
                "top":           "Top headlines across all categories",
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
    else:
        section("STEP 1 / 3 — Your scene concepts")
        user_prompts = ask_prompts()

    # ── Section 2: Prompt style ───────────────────────────────────────────────
    section("STEP 2 / 3 — Prompt style")

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
            "How many videos to generate?", default=3, min_val=1, max_val=10,
        )
    else:
        videos = len(user_prompts)
        print(f"\n  {Style.DIM}Videos: {videos} (one per scene concept){Style.RESET_ALL}")

    # ── Optional: Web research ────────────────────────────────────────────────
    research_query = ask_free(
        "Research topic for background info (optional):",
        default="",
        note="Keywords or a short sentence — GLM will use these facts to enrich the prompts.\n"
             "  e.g. 'Diagno Energy'  |  'CES 2026'  |  'Sydney housing market'\n"
             "  e.g. 'Tesla new model launch Australia 2026'\n"
             "  Leave blank to skip.",
    ).strip()

    # ── Section 3: Video output ───────────────────────────────────────────────
    section("STEP 3 / 3 — Video output")

    model = ask_choice(
        "Video model:", options=MODEL_NAMES, default="kling",
        descriptions=model_descriptions(),
    )

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

    duration = ask_int("Clip duration (seconds):", default=5, min_val=3, max_val=15)

    mode = ask_choice(
        "Render mode  (controls resolution & cost):",
        options=["std", "pro"],
        default="std",
        descriptions={
            "std": "720P  — faster & cheaper",
            "pro": "1080P — higher quality",
        },
    )

    sound = ask_choice(
        "Auto-generated sound?:",
        options=["yes", "no"],
        default="yes",
        descriptions={
            "yes": "AI generates matching ambient / music audio",
            "no":  "Silent video — lower cost",
        },
    )

    # ── Cost estimate ─────────────────────────────────────────────────────────
    if is_kie(model):
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
    elif is_openai(model):
        _sora_rate = {
            "sora":     {"std": 0.10, "pro": 0.10},
            "sora-pro": {"std": 0.30, "pro": 0.50},
        }
        rate = _sora_rate[model][mode]
        usd  = rate * duration * videos
        print(
            f"\n  {Style.DIM}Estimated cost: "
            f"${rate:.2f}/sec × {duration}s × {videos} video(s)"
            f" = {Style.RESET_ALL}{Fore.YELLOW}{Style.BRIGHT}${usd:.2f} USD{Style.RESET_ALL}"
        )

    # ── Build config dict ─────────────────────────────────────────────────────
    image_url = ask_image()

    cfg: dict = {
        "pipeline":     pipeline,
        "model":        model,
        "style":        style,
        "mood":         mood,
        "videos":       videos,
        "aspect":       aspect,
        "duration":     duration,
        "mode":         mode,
        "sound":        sound == "yes",
        "image_url":    image_url,
        "research":     research_query if research_query else None,
    }

    if pipeline == "auto":
        cfg.update({
            "region":       region,
            "language":     language,
            "topic":        topic,
        })
    else:
        cfg["user_prompts"] = user_prompts
        cfg["topic"] = "custom"

    return cfg
