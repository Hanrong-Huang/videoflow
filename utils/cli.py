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
BACK_VALUE = "__back__"


class BackRequest(Exception):
    """Raised when the user wants to return to the previous wizard step."""


def _check_back(raw: str, allow_back: bool) -> str:
    """Normalize raw input and raise BackRequest on a back command."""
    raw = raw.strip()
    if allow_back and raw.lower() == "back":
        raise BackRequest
    return raw


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
               allow_freetext: bool = False,
               allow_back: bool = False) -> str:
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
        if allow_back:
            choices.append(Choice(title="← Back  [b]", value=BACK_VALUE, shortcut_key="b"))

        result = questionary.select(
            prompt,
            choices=choices,
            default=default,
            style=_Q_STYLE,
            use_indicator=True,
            use_shortcuts=allow_back,
            use_jk_keys=True,
            instruction="(↑↓ or j/k move, enter select, b back)",
        ).ask()

        if result is None:  # Ctrl+C
            raise KeyboardInterrupt
        if result == BACK_VALUE:
            raise BackRequest
        if result == "__freetext__":
            raw = _check_back(
                input(
                    f"  {Fore.YELLOW}▸{Style.RESET_ALL} Enter keyword "
                    f"{Style.DIM}[type 'back' to return]{Style.RESET_ALL}: "
                ),
                allow_back,
            )
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
    if allow_back:
        print(f"    {Style.DIM}Type 'back' to return to the previous step.{Style.RESET_ALL}")

    while True:
        raw = _check_back(
            input(
                f"\n  {Fore.YELLOW}▸{Style.RESET_ALL} Enter number or value "
                f"{Style.DIM}[{default}]{Style.RESET_ALL}: "
            ),
            allow_back,
        )

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


def ask_free(prompt: str, default: str, note: str = "",
             allow_back: bool = False) -> str:
    """Prompt for a free-text value; shows a note line if provided."""
    print(f"\n  {Fore.WHITE}{Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    if note:
        print(f"  {Style.DIM}{note}{Style.RESET_ALL}")
    if allow_back:
        print(f"  {Style.DIM}Type 'back' to return to the previous step.{Style.RESET_ALL}")
    raw = _check_back(
        input(
            f"  {Fore.YELLOW}▸{Style.RESET_ALL} Enter value "
            f"{Style.DIM}[{default}]{Style.RESET_ALL}: "
        ),
        allow_back,
    )
    return raw if raw else default


def ask_int(prompt: str, default: int, min_val: int, max_val: int,
            allow_back: bool = False) -> int:
    """Prompt for an integer within [min_val, max_val]."""
    print(f"\n  {Fore.WHITE}{Style.BRIGHT}{prompt}{Style.RESET_ALL}")
    if allow_back:
        print(f"  {Style.DIM}Type 'back' to return to the previous step.{Style.RESET_ALL}")
    while True:
        raw = _check_back(
            input(
                f"  {Fore.YELLOW}▸{Style.RESET_ALL} Enter number "
                f"{Style.DIM}[{default}] range {min_val}–{max_val}{Style.RESET_ALL}: "
            ),
            allow_back,
        )
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


def ask_prompts(allow_back: bool = False) -> list[str]:
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
    if allow_back:
        print(f"  {Style.DIM}Type 'back' on a new scene to return to the previous step.{Style.RESET_ALL}")
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
            if allow_back and first is False and not parts and raw.strip().lower() == "back":
                if prompts:
                    removed = prompts.pop()
                    warn(f"Removed previous scene: {removed[:60]}{'…' if len(removed) > 60 else ''}")
                    first = True
                    continue
                raise BackRequest
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


def _resolve_local_path(raw: str) -> str | None:
    """Try to resolve *raw* as a local image path; return base64 or None."""
    path = pathlib.Path(raw)
    if not path.is_absolute():
        path = _PROJECT_DIR / path
    if path.is_file() and path.suffix.lower() in _IMAGE_EXTS:
        success(f"Using local image: {Fore.CYAN}{path.name}{Style.RESET_ALL}")
        return _encode_image_base64(path)
    return None


def ask_images(prompt_label: str = "Reference images",
               allow_back: bool = False) -> list[str]:
    """
    Let the user pick one or more reference images for a scene.
    - questionary available: checkbox UI with arrow keys + space to select.
    - fallback: numbered menu with comma-separated input.
    - no images/ folder: free-entry for URLs/paths line by line.
    Returns a list of resolved URL/data-URI strings (may be empty).
    """
    images = _list_images()
    selected: list[str] = []

    if images:
        n_url  = len(images) + 1
        n_path = len(images) + 2

        def _clean_pasted_value(raw: str) -> str:
            """Trim whitespace and optional wrapping quotes from pasted input."""
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
                return raw[1:-1].strip()
            return raw

        def _prompt_url() -> str | None:
            print(
                f"  {Style.DIM}Paste the full image URL, then press Enter."
                f"{Style.RESET_ALL}"
            )
            print(
                f"  {Style.DIM}Example: https://example.com/reference.jpg"
                f"{Style.RESET_ALL}"
            )
            url = _clean_pasted_value(
                input(
                    f"  {Fore.YELLOW}▸{Style.RESET_ALL} Image URL "
                    f"{Style.DIM}[blank = cancel]{Style.RESET_ALL}: "
                )
            )
            return url if url else None

        def _prompt_path() -> str | None:
            print(
                f"  {Style.DIM}Paste a local image path, then press Enter."
                f"{Style.RESET_ALL}"
            )
            print(
                f"  {Style.DIM}Project root: {_PROJECT_DIR}{Style.RESET_ALL}"
            )
            print(
                f"  {Style.DIM}Examples: images/cat.png  |  C:\\Users\\name\\Desktop\\cat.png"
                f"{Style.RESET_ALL}"
            )
            p = _clean_pasted_value(
                input(
                    f"  {Fore.YELLOW}▸{Style.RESET_ALL} File path "
                    f"{Style.DIM}[relative or absolute, blank = cancel]{Style.RESET_ALL}: "
                )
            )
            if not p:
                return None
            result = _resolve_local_path(p)
            if not result:
                error(f"File not found or unsupported image: {p}")
            return result

        if HAS_QUESTIONARY:
            # ── Arrow-key action menu + direct text input ────────────────
            while True:
                title = prompt_label
                if selected:
                    title += f"  [{len(selected)} selected]"
                else:
                    title += "  [add images one by one]"

                choices = []
                if allow_back:
                    choices.append(Choice(title="← Back  [b]", value=BACK_VALUE, shortcut_key="b"))
                if not selected:
                    choices.append(Choice(title="No image  (text-to-video)", value="done"))
                else:
                    choices.append(Choice(title="Done", value="done"))
                choices.append(Choice(title="Paste image URL...", value="url"))
                choices.append(Choice(title="Paste local file path...", value="path"))
                for i, img in enumerate(images, 1):
                    choices.append(Choice(title=f"{img.name}", value=str(i)))

                pick = questionary.select(
                    title,
                    choices=choices,
                    style=_Q_STYLE,
                    use_indicator=True,
                    use_shortcuts=allow_back,
                    use_jk_keys=True,
                    instruction="(↑↓ or j/k move, enter add/select, b back; add multiple images one by one)",
                ).ask()

                if pick is None:
                    raise KeyboardInterrupt
                if pick == BACK_VALUE:
                    raise BackRequest
                if pick == "done":
                    break
                if pick == "url":
                    url = questionary.text(
                        "Image URL",
                        style=_Q_STYLE,
                        instruction="Paste the full image URL, then press Enter",
                    ).ask()
                    if url:
                        selected.append(_clean_pasted_value(url))
                    continue
                if pick == "path":
                    raw_path = questionary.text(
                        "Local file path",
                        style=_Q_STYLE,
                        instruction=(
                            f"Relative to project root or absolute path. "
                            f"Example: images/cat.png  |  {_PROJECT_DIR}"
                        ),
                    ).ask()
                    if raw_path:
                        result = _resolve_local_path(_clean_pasted_value(raw_path))
                        if result:
                            selected.append(result)
                        else:
                            error(f"File not found or unsupported image: {raw_path}")
                    continue
                selected.append(_encode_image_base64(images[int(pick) - 1]))

        else:
            # ── Fallback: numbered menu + comma-separated input ───────────
            def _resolve_token(token: str) -> None:
                token = token.strip()
                if not token or token == "0":
                    return
                if token.startswith(("http://", "https://", "data:")):
                    selected.append(token)
                    return
                local = _resolve_local_path(token)
                if local:
                    selected.append(local)
                    return
                try:
                    idx = int(token)
                    if idx == 0:
                        return
                    if 1 <= idx <= len(images):
                        selected.append(_encode_image_base64(images[idx - 1]))
                    elif idx == n_url:
                        url = _prompt_url()
                        if url:
                            selected.append(url)
                    elif idx == n_path:
                        p = _prompt_path()
                        if p:
                            selected.append(p)
                    else:
                        error(f"Please enter 0–{n_path}.")
                except ValueError:
                    error(f"Unrecognised input: '{token}'")

            print(f"\n  {Fore.WHITE}{Style.BRIGHT}{prompt_label}:{Style.RESET_ALL}")
            print(f"  {Style.DIM}Enter number(s) separated by commas, a URL, or blank to skip.{Style.RESET_ALL}")
            print(f"     {Fore.YELLOW}{Style.BRIGHT} 0){Style.RESET_ALL}  No image  {Style.DIM}(text-to-video){Style.RESET_ALL}")
            for i, img in enumerate(images, 1):
                print(f"     {Fore.YELLOW}{Style.BRIGHT}{i:>2}){Style.RESET_ALL}  {img.name}")
            print(f"     {Fore.YELLOW}{Style.BRIGHT}{n_url:>2}){Style.RESET_ALL}  Enter a URL")
            print(f"     {Fore.YELLOW}{Style.BRIGHT}{n_path:>2}){Style.RESET_ALL}  Enter a local file path")

            raw = input(
                f"\n  {Fore.YELLOW}▸{Style.RESET_ALL} Selection "
                f"{Style.DIM}[0 or blank = skip]{Style.RESET_ALL}: "
            ).strip()
            if allow_back and raw.lower() == "back":
                raise BackRequest
            if raw and raw != "0":
                for token in raw.split(","):
                    _resolve_token(token)

            if selected:
                while True:
                    more = input(
                        f"  {Style.DIM}✔ {len(selected)} selected.  "
                        f"Add more?{Style.RESET_ALL} "
                        f"{Style.DIM}[number/URL/path or blank = done]{Style.RESET_ALL}: "
                    ).strip()
                    if allow_back and more.lower() == "back":
                        raise BackRequest
                    if not more:
                        break
                    _resolve_token(more)

    else:
        # No images/ folder — free-entry for URLs and paths
        print(f"\n  {Fore.WHITE}{Style.BRIGHT}{prompt_label}{Style.RESET_ALL}  "
              f"{Style.DIM}(URL or file path — blank to skip){Style.RESET_ALL}")
        while True:
            raw = input(
                f"  {Fore.YELLOW}▸{Style.RESET_ALL} Image {len(selected) + 1} "
                f"{Style.DIM}[blank = done]{Style.RESET_ALL}: "
            ).strip()
            if allow_back and raw.lower() == "back":
                raise BackRequest
            if not raw:
                break
            if raw.startswith(("http://", "https://", "data:")):
                selected.append(raw)
            else:
                local = _resolve_local_path(raw)
                if local:
                    selected.append(local)
                else:
                    error(f"Not a valid URL or image file: {raw}")

    return selected


# ─────────────────────────────────────────────────────────────────────────────
# Settings confirmation — bordered table
# ─────────────────────────────────────────────────────────────────────────────

def confirm_settings(cfg: dict) -> str:
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
    
    scene_images = cfg.get("image_urls", [])
    total_images = sum(len(s) for s in scene_images)
    if total_images:
        scenes_with = sum(1 for s in scene_images if s)
        info("Images", f"{total_images} across {scenes_with} scene(s)")
    else:
        info("Image", "none (text-to-video)")

    divider()

    print()
    raw = input(
        f"  {Fore.GREEN}{Style.BRIGHT}▸ Start generating?{Style.RESET_ALL} "
        f"{Style.DIM}[Y = start / back = edit / n = cancel]{Style.RESET_ALL}: "
    ).strip().lower()
    if raw in ("", "y", "yes"):
        return "start"
    if raw == "back":
        return "back"
    return "cancel"


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
    print(f"  {Style.DIM}  Type 'back' to return to the previous step when available.{Style.RESET_ALL}")
    print(f"  {Style.DIM}  Press Ctrl+C at any time to quit.{Style.RESET_ALL}")
    state: dict = {
        "pipeline": "auto",
        "region": "US",
        "language": "en",
        "topic": "top",
        "user_prompts": [],
        "style": "cinematic",
        "mood": "dramatic",
        "videos": 3,
        "research": "",
        "model": "kling",
        "aspect": "16:9",
        "duration": 5,
        "mode": "std",
        "sound": "yes",
        "image_urls": [],
    }
    step_idx = 0

    while True:
        if step_idx == 0:
            state["pipeline"] = ask_choice(
                "Pipeline mode:",
                options=["auto", "manual"],
                default=state["pipeline"],
                descriptions={
                    "auto":   "Trending news → GLM → AI video",
                    "manual": "Your scenes → GLM enhance → AI video",
                },
            )
            step_idx = 1
            continue

        if step_idx == 1:
            try:
                if state["pipeline"] == "auto":
                    section("STEP 1 / 4 — News source")
                    print_region_table()
                    state["region"] = ask_free(
                        "Which region? (enter the 2-letter code)",
                        default=state["region"],
                        allow_back=True,
                    ).upper()
                    state["language"] = ask_free(
                        "Language code?",
                        default=state["language"],
                        note="en English │ es Spanish │ fr French │ de German │ ja Japanese\n"
                             "  zh Chinese │ ko Korean  │ pt Portuguese │ ar Arabic",
                        allow_back=True,
                    ).lower()
                    state["topic"] = ask_choice(
                        "Which news topic?",
                        options=list(_TOPIC_SECTIONS.keys()),
                        default=state["topic"],
                        allow_freetext=True,
                        allow_back=True,
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
                    section("STEP 1 / 4 — Your scene concepts")
                    state["user_prompts"] = ask_prompts(allow_back=True)
                step_idx = 2
                continue
            except BackRequest:
                step_idx = 0
                continue

        if step_idx == 2:
            try:
                section("STEP 2 / 4 — Prompt style")
                state["style"] = ask_choice(
                    "Visual style for the generated prompts:",
                    options=["cinematic", "documentary", "commercial", "comedy",
                             "anime", "retro", "aerial", "cyberpunk", "horror", "minimalist"],
                    default=state["style"],
                    allow_back=True,
                    descriptions={
                        "cinematic":    "Film-quality narrative, lens effects, shallow depth of field",
                        "documentary":  "Raw, handheld, observational, natural imperfections",
                        "commercial":   "Polished, brand-ready, clean product-focused shots",
                        "comedy":       "Bright, wide shots, exaggerated staging, expressive framing",
                        "anime":        "Cel-shaded, vibrant colors, stylised exaggerated motion",
                        "retro":        "Film grain, VHS artifacts, 70s/80s vintage color science",
                        "aerial":       "Drone / bird's-eye, sweeping wide-angle landscapes",
                        "cyberpunk":    "Neon-drenched, rain-slicked, holographic, dystopian urban",
                        "horror":       "Dutch angles, deep shadows, unsettling tight framing",
                        "minimalist":   "Clean negative space, limited palette, geometric symmetry",
                    },
                )
                state["mood"] = ask_choice(
                    "Mood / tone:",
                    options=["dramatic", "funny", "epic", "serene", "dark",
                             "inspirational", "mysterious", "nostalgic", "tense", "playful"],
                    default=state["mood"],
                    allow_back=True,
                    descriptions={
                        "dramatic":      "Intense, high-stakes, powerful emotional weight",
                        "funny":         "Comedic, absurd, lighthearted and laugh-out-loud",
                        "epic":          "Grand scale, sweeping, monumental scope",
                        "serene":        "Calm, peaceful, slow movement",
                        "dark":          "Moody, shadowy, ominous atmosphere",
                        "inspirational": "Hopeful, motivating, uplifting tone",
                        "mysterious":    "Enigmatic, atmospheric, fog and haze",
                        "nostalgic":     "Warm, wistful, memory-like softness",
                        "tense":         "Suspense, unease, tight framing",
                        "playful":       "Whimsical, bouncy, vibrant energy",
                    },
                )
                if state["pipeline"] == "auto":
                    state["videos"] = ask_int(
                        "How many videos to generate?",
                        default=state["videos"],
                        min_val=1,
                        max_val=10,
                        allow_back=True,
                    )
                else:
                    state["videos"] = len(state["user_prompts"])
                    print(f"\n  {Style.DIM}Videos: {state['videos']} (one per scene concept){Style.RESET_ALL}")
                state["research"] = ask_free(
                    "Research topic for background info (optional):",
                    default=state["research"],
                    note="One search query — type all keywords on one line, space or comma separated.\n"
                         "  GLM will use the results to enrich the prompts.\n"
                         "  e.g.  Diagno Energy,  CES 2026 AI chips,  Tesla launch Australia 2026\n"
                         "  Leave blank to skip.",
                    allow_back=True,
                ).strip()
                step_idx = 3
                continue
            except BackRequest:
                step_idx = 1
                continue

        if step_idx == 3:
            try:
                section("STEP 3 / 4 — Video output")
                state["model"] = ask_choice(
                    "Video model:",
                    options=MODEL_NAMES,
                    default=state["model"],
                    descriptions=model_descriptions(),
                    allow_back=True,
                )
                state["aspect"] = ask_choice(
                    "Aspect ratio:",
                    options=["16:9", "9:16", "1:1"],
                    default=state["aspect"],
                    allow_back=True,
                    descriptions={
                        "16:9": "Landscape — YouTube, TV, desktop",
                        "9:16": "Vertical  — TikTok, Instagram Reels, Shorts",
                        "1:1":  "Square    — Instagram feed, general social",
                    },
                )
                state["duration"] = ask_int(
                    "Clip duration (seconds):",
                    default=state["duration"],
                    min_val=3,
                    max_val=15,
                    allow_back=True,
                )
                state["mode"] = ask_choice(
                    "Render mode  (controls resolution & cost):",
                    options=["std", "pro"],
                    default=state["mode"],
                    allow_back=True,
                    descriptions={
                        "std": "720P  — faster & cheaper",
                        "pro": "1080P — higher quality",
                    },
                )
                state["sound"] = ask_choice(
                    "Auto-generated sound?:",
                    options=["yes", "no"],
                    default=state["sound"],
                    allow_back=True,
                    descriptions={
                        "yes": "AI generates matching ambient / music audio",
                        "no":  "Silent video — lower cost",
                    },
                )
                step_idx = 4
                continue
            except BackRequest:
                step_idx = 2
                continue

        if step_idx == 4:
            section("STEP 4 / 4 — Reference images  (optional)")
            print(f"  {Style.DIM}│ Select one or more images per scene, or skip for text-to-video.{Style.RESET_ALL}")
            image_urls: list[list[str]] = []
            scene_idx = 0
            try:
                while scene_idx < state["videos"]:
                    existing = state["image_urls"][scene_idx] if scene_idx < len(state["image_urls"]) else []
                    if existing:
                        info("Current", f"Scene {scene_idx + 1}: {len(existing)} image(s) already selected")
                    imgs = ask_images(
                        prompt_label=f"Images for scene {scene_idx + 1} of {state['videos']}",
                        allow_back=True,
                    )
                    image_urls.append(imgs)
                    scene_idx += 1
                state["image_urls"] = image_urls
                cfg: dict = {
                    "pipeline":   state["pipeline"],
                    "model":      state["model"],
                    "style":      state["style"],
                    "mood":       state["mood"],
                    "videos":     state["videos"],
                    "aspect":     state["aspect"],
                    "duration":   state["duration"],
                    "mode":       state["mode"],
                    "sound":      state["sound"] == "yes",
                    "image_urls": state["image_urls"],
                    "research":   state["research"] or None,
                }
                if state["pipeline"] == "auto":
                    cfg.update({
                        "region":   state["region"],
                        "language": state["language"],
                        "topic":    state["topic"],
                    })
                else:
                    cfg["user_prompts"] = state["user_prompts"]
                    cfg["topic"] = "custom"
                return cfg
            except BackRequest:
                step_idx = 3
                continue
