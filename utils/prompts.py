"""
prompts.py — GLM-4.7 prompt generation, news fetching, and web research
=======================================================================
Handles:
  - Google News RSS fetching
  - Web search for background research (DuckDuckGo)
  - GLM system prompt construction
  - Auto-mode prompt generation (news → prompts)
  - Manual-mode prompt enhancement (user scenes → enhanced prompts)
"""

import re
import sys
import json
import threading
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import requests
from openai import OpenAI
from colorama import Fore, Style

from cli import success, warn, error

# ── Tunable parameters ───────────────────────────────────────────────────────
PROMPT_TEMPERATURE   = 0.95
PROMPT_MAX_TOKENS    = 4096
SNIPPET_DISPLAY_LEN  = 100
SNIPPET_PROMPT_LEN   = 250
HEADLINES_POOL       = 15
HTTP_TIMEOUT_SEC     = 15
MAX_JSON_RETRIES     = 1
RETRY_TEMP_BUMP      = -0.10
WEB_SEARCH_RESULTS   = 4
WEB_SNIPPET_LEN      = 250

# ── Topic sections ───────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# GLM spinner context manager
# ─────────────────────────────────────────────────────────────────────────────

class _GlmSpinner:
    """Context manager that spins in a background thread during GLM calls."""

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "GLM-4.7 thinking"):
        self._msg = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _spin(self):
        import time
        idx = 0
        while not self._stop.is_set():
            frame = self._FRAMES[idx % len(self._FRAMES)]
            print(
                f"\r  {Fore.CYAN}{frame}{Style.RESET_ALL}  "
                f"{Style.DIM}{self._msg}…{Style.RESET_ALL}     ",
                end="", flush=True,
            )
            idx += 1
            self._stop.wait(0.1)
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
    import time
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


# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
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


# ─────────────────────────────────────────────────────────────────────────────
# Web search (DuckDuckGo) — used for background research
# ─────────────────────────────────────────────────────────────────────────────

_QUERY_STRIP = re.compile(
    r"^(?:search(?:\s+the\s+web)?\s+for[\s:]+|"
    r"find(?:\s+info(?:rmation)?)?\s+(?:about|on)[\s:]+|"
    r"look\s+up[\s:]+|"
    r"research[\s:]+)",
    re.IGNORECASE,
)


def _clean_query(raw: str) -> str:
    """Strip accidental instructional prefixes users sometimes type."""
    return _QUERY_STRIP.sub("", raw).strip()


def web_search(query: str, max_results: int = WEB_SEARCH_RESULTS) -> list[dict]:
    """
    Search the web via DuckDuckGo and return a list of
    {"title": "...", "snippet": "...", "url": "..."} dicts.

    Falls back gracefully if ddgs is not installed.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        warn("ddgs not installed — run: pip install ddgs")
        return []

    query = _clean_query(query)

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw_results = list(DDGS().text(query, max_results=max_results))
    except Exception as exc:
        warn(f"Web search failed: {exc}")
        return []

    results: list[dict] = []
    for r in raw_results:
        results.append({
            "title":   r.get("title", ""),
            "snippet": (r.get("body", "") or "")[:WEB_SNIPPET_LEN],
            "url":     r.get("href", ""),
        })

    # Display results
    for i, r in enumerate(results, 1):
        print(f"  {Fore.CYAN}{i}.{Style.RESET_ALL} {r['title']}")
        if r["snippet"]:
            trunc = r["snippet"][:SNIPPET_DISPLAY_LEN]
            ellip = "…" if len(r["snippet"]) > SNIPPET_DISPLAY_LEN else ""
            print(f"     {Style.DIM}{trunc}{ellip}{Style.RESET_ALL}")

    return results


def _research_context_block(research: list[dict]) -> str:
    """
    Format web search results into a text block for GLM context injection.
    Returns empty string if no research results.
    """
    if not research:
        return ""
    lines = ["\n\nBACKGROUND RESEARCH (use these facts to make prompts more accurate):"]
    for i, r in enumerate(research, 1):
        lines.append(f"{i}. {r['title']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# News fetching
# ─────────────────────────────────────────────────────────────────────────────

def build_rss_url(region: str, language: str, topic: str) -> str:
    """Construct the Google News RSS URL for the chosen region/language/topic."""
    lang_region = f"{language}-{region}"
    ceid        = f"{region}:{language}"
    params      = f"hl={lang_region}&gl={region}&ceid={ceid}"

    topic_lower = topic.strip().lower()
    if topic_lower == "top":
        return f"https://news.google.com/rss?{params}"
    if topic_lower in _TOPIC_SECTIONS and _TOPIC_SECTIONS[topic_lower]:
        return (
            f"https://news.google.com/rss/headlines/section/topic"
            f"/{_TOPIC_SECTIONS[topic_lower]}?{params}"
        )
    return f"https://news.google.com/rss/search?q={quote_plus(topic)}&{params}"


def _strip_html(raw: str) -> str:
    """Strip HTML tags, decode HTML entities, and collapse whitespace."""
    from html import unescape
    text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_trending_topics(region: str, language: str, topic: str) -> list[dict]:
    """
    Fetch headlines from Google News RSS and return a list of dicts:
      {"title": "...", "snippet": "..."}
    """
    rss_url  = build_rss_url(region, language, topic)
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
        title   = title_el.text.strip().rsplit(" - ", 1)[0]
        snippet = _strip_html(desc_el.text or "") if desc_el is not None else ""
        articles.append({"title": title, "snippet": snippet})
        if len(articles) >= HEADLINES_POOL:
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
# System prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _prompt_length_guidance(duration: int) -> tuple[str, str, str]:
    """Return (sentence_count, arc_guide, camera_guide) scaled to clip duration."""
    if duration <= 5:
        return (
            "2–3 sentences",
            "one clear action or movement that completes within the clip",
            "single locked or very slow push/pull — no cuts, one angle only",
        )
    if duration <= 10:
        return (
            "3–5 sentences",
            "two distinct beats with one smooth transition between them",
            "one to two angles — an establishing move that resolves into a closer reveal",
        )
    return (
        "5–8 sentences",
        "a three-phase arc: establish → develop → resolve, each beat visually distinct",
        "two to three deliberate angle changes with motivated camera movement between each",
    )


def _audio_element(sound: bool) -> str:
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
    sentences, arc_guide, camera_guide = _prompt_length_guidance(duration)
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
   Use {camera_guide}.
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


def _build_enhance_prompt(count: int, style: str, mood: str,
                          duration: int, sound: bool) -> str:
    sentences, arc_guide, camera_guide = _prompt_length_guidance(duration)
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
   Use {camera_guide}.
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


# ─────────────────────────────────────────────────────────────────────────────
# Prompt generation (auto mode)
# ─────────────────────────────────────────────────────────────────────────────

def generate_prompts(zai_client: OpenAI, articles: list[dict], count: int,
                     style: str, mood: str, duration: int,
                     sound: bool,
                     research: list[dict] | None = None) -> list[str]:
    """
    Ask GLM-4.7 to select the best articles and write one AI video prompt
    per selected story.  Includes retry logic for JSON parsing.
    Optionally accepts web research results for richer context.
    """
    system_msg = _build_system_prompt(count, style, mood, duration, sound)

    articles_text = "\n".join(
        f"{i}. {a['title']}"
        + (f"\n   Context: {a['snippet'][:SNIPPET_PROMPT_LEN]}" if a["snippet"] else "")
        for i, a in enumerate(articles, 1)
    )
    research_block = _research_context_block(research or [])
    user_msg = (
        f"News articles pool ({len(articles)} total):\n\n{articles_text}"
        f"{research_block}\n\n"
        f"Select the {count} most visually compelling stories and write one "
        f"AI video prompt for each. Return a JSON array of {count} strings only."
    )

    prompts = _call_glm(zai_client, system_msg, user_msg, "GLM-4.7 thinking")
    for i, p in enumerate(prompts, 1):
        print(f"\n  {Fore.CYAN}{Style.BRIGHT}Prompt {i}{Style.RESET_ALL}")
        print(_wrap(p))
    return prompts


def enhance_prompts(zai_client: OpenAI, user_concepts: list[str],
                    style: str, mood: str, duration: int,
                    sound: bool,
                    research: list[dict] | None = None) -> list[str]:
    """
    Enhance the user's raw scene concepts into production-ready prompts.
    Optionally accepts web research results for richer context.
    """
    count = len(user_concepts)
    system_msg = _build_enhance_prompt(count, style, mood, duration, sound)

    concepts_text = "\n".join(f"{i}. {c}" for i, c in enumerate(user_concepts, 1))
    research_block = _research_context_block(research or [])
    user_msg = (
        f"My scene concepts ({count} total):\n\n{concepts_text}"
        f"{research_block}\n\n"
        f"Enhance each concept into a production-ready AI video prompt. "
        f"Return a JSON array of {count} strings only."
    )

    prompts = _call_glm(zai_client, system_msg, user_msg, "GLM-4.7 enhancing")

    for i, (original, enhanced) in enumerate(zip(user_concepts, prompts), 1):
        print(f"\n  {Fore.CYAN}{Style.BRIGHT}Prompt {i}{Style.RESET_ALL}")
        print(f"  {Style.DIM}Your idea :{Style.RESET_ALL} {original[:80]}{'…' if len(original) > 80 else ''}")
        print(f"  {Style.DIM}Enhanced  :{Style.RESET_ALL}")
        print(_wrap(enhanced))

    return prompts


def _call_glm(zai_client: OpenAI, system_msg: str, user_msg: str,
              spinner_label: str) -> list[str]:
    """Shared GLM call logic with retry and JSON parsing."""
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": user_msg},
    ]

    prompts: list[str] | None = None
    last_error: str = ""
    raw: str = ""

    for attempt in range(1 + MAX_JSON_RETRIES):
        temp = max(0.1, PROMPT_TEMPERATURE + attempt * RETRY_TEMP_BUMP)

        try:
            with _GlmSpinner(spinner_label):
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

    # Deduplicate
    seen: set[str] = set()
    unique: list[str] = []
    for p in prompts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
        else:
            warn("Duplicate prompt detected and removed.")

    return unique
