"""MiniMax CLI wrapper.

Calls the local `minimax` CLI as a subprocess. The CLI is responsible for the
whole creative chain — we just pass it well-shaped prompts and parse JSON back.

The CLI on this Windows box is the `pi` coding-agent shim at
`C:\\Users\\mehya\\.minimax\\bin\\minimax.cmd`. Real flags:
  --provider <name> --model <id> --api-key <key> -p <prompt>

Credentials come from OPENAI_API_KEY in env, but we ALSO support an explicit
override via REEL_STUDIO_OPENAI_KEY for cases where the bashrc export has
been rotated. Set it in a .env and `python-dotenv` (in cli.py) will load it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# -------- MiniMax CLI wrapper --------

class MiniMaxError(RuntimeError):
    pass


# On this Windows host the shim lives at ~/.minimax/bin/minimax.cmd and is NOT
# on PATH for subprocess without shell=True. Fall back through known locations.
_KNOWN_LOCATIONS = [
    os.path.expanduser("~/.minimax/bin/minimax.cmd"),
    os.path.expanduser("~/.minimax/bin/minimax"),
    shutil.which("minimax"),
    "minimax",
]


def _resolve_minimax_cmd() -> str:
    for p in _KNOWN_LOCATIONS:
        if p and os.path.isfile(p):
            return p
    return "minimax"


def _load_openai_key() -> str | None:
    """Pull OPENAI_API_KEY from env, falling back to ~/.bashrc export line."""
    k = os.environ.get("OPENAI_API_KEY") or os.environ.get("REEL_STUDIO_OPENAI_KEY")
    if k:
        return k
    bashrc = Path(os.path.expanduser("~/.bashrc"))
    if bashrc.exists():
        m = re.search(r'export\s+OPENAI_API_KEY=["\']?([^"\'\s]+)', bashrc.read_text())
        if m:
            return m.group(1)
    return None


def call_minimax(prompt: str, *, model: str = "MiniMax-M3", timeout: int = 180,
                 expect_json: bool = True) -> str:
    """Invoke the MiniMax CLI and return its stdout."""
    cmd = _resolve_minimax_cmd()
    full_prompt = prompt
    if expect_json:
        full_prompt += "\n\nRespond with ONLY a single ```json ... ``` block, nothing else."

    # Build argv — `pi` (the actual shim) wants --provider/--model/--api-key/-p
    api_key = _load_openai_key()
    argv = [cmd, "--provider", "openai", "--model", model, "-p", full_prompt]
    if api_key:
        argv.insert(1, api_key); argv.insert(1, "--api-key")

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=True)
    except FileNotFoundError as e:
        raise MiniMaxError(f"minimax CLI not found at {cmd!r}. Set REEL_STUDIO_MINIMAX_CMD.") from e
    if proc.returncode != 0:
        raise MiniMaxError(
            f"minimax CLI exited {proc.returncode}\nSTDOUT: {proc.stdout[:500]}\n"
            f"STDERR: {proc.stderr[:500]}"
        )
    out = proc.stdout.strip()
    if expect_json:
        out = _strip_json_block(out)
    return out


def _strip_json_block(text: str) -> str:
    """Pull the first ```json ... ``` block out of the response."""
    import re
    m = re.search(r"```json\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text)
    if m:
        return m.group(1).strip()
    # fallback: maybe it's already raw JSON
    text = text.strip()
    if text.startswith(("{", "[")):
        return text
    raise MiniMaxError(f"No JSON block found in minimax output:\n{text[:500]}")


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise MiniMaxError(f"minimax returned invalid JSON: {e}\nText: {text[:500]}")


# -------- high-level prompt helpers --------

SITE_CONTEXT_TEMPLATE = """\
You are the creative director for an Instagram Reels account that promotes \
useful web apps. Below is everything we know about a target site.

Generate a JSON object with EXACTLY this shape:

{{
  "brand_summary": "1 sentence — what the product is and who it's for",
  "audience": "1 sentence — the person most likely to use this",
  "tone": "one of: playful, premium, urgent, raw, educational, irreverent",
  "ideas": [
    {{
      "title": "short, punchy reel title (≤ 6 words)",
      "angle": "lowercase tag like 'pain-mirror', 'demo-sprint', 'before-after', 'founder-story', 'objection-killer'",
      "hook": "the literal first line on screen (≤ 12 words)",
      "format": "talking-head | screen-record | b-roll-voiceover | ugc-selfie | split-screen | pov",
      "target_emotion": "curiosity | relief | excitement | smugness | belonging",
      "why_this_works": "1 sentence — why this angle fits THIS site"
    }},
    ...3 total, each with a DIFFERENT angle...
  ]
}}

SITE METADATA:
{metadata_json}

Rules:
- All 3 ideas MUST use different angles (don't repeat 'demo-sprint' three times).
- Hooks must be specific to THIS product, not generic ("Stop wasting time" is banned).
- Output ONLY the JSON block, no commentary.
"""

SCRIPT_TEMPLATE = """\
You are a Reels scriptwriter. Write a {duration}s script for the chosen idea.

Site context: {brand_summary}
Audience: {audience}
Tone: {tone}

Idea:
- Title: {idea_title}
- Angle: {angle}
- Hook: {hook}
- Format: {format}
- Target emotion: {target_emotion}

Output JSON of EXACTLY this shape:

{{
  "duration_s": 30,
  "scenes": [
    {{"ts": "0-3s", "scene": "what we see on screen", "vo": "what the narrator says", "on_screen": "the text overlay (≤ 6 words)"}},
    ...up to 8 scenes covering the full duration...
  ]
}}

Rules:
- Scene 1 must literally deliver the HOOK on screen.
- Every scene needs a specific, visible action — no "we see the app" generic shots.
- VO and on_screen text should NOT be identical (they serve different jobs).
- Last scene must end on a CTA (follow, comment, save, or share).
- Output ONLY the JSON.
"""

SOUND_TEMPLATE = """\
You are the sound designer for a Reel. Output JSON:

{{
  "track_name": "a placeholder track name (descriptive, e.g. 'uptempo lo-fi beat w/ subtle bass drops')",
  "mood": "energetic | chill | dramatic | playful | tense",
  "notes": "1 sentence on the overall sonic feel",
  "cues": [
    {{"ts": "0s", "action": "start"}},
    {{"ts": "{duration_div3}s", "action": "duck"}},
    {{"ts": "{cta_ts}s", "action": "lift"}}
  ],
  "alt_track": "a second option with a different mood so the creator has variety"
}}

Site tone: {tone}
Script target emotion: {target_emotion}

Rules:
- 3-5 cues that follow the script's natural beats.
- The "lift" cue MUST land on the final CTA scene.
- Tracks must be royalty-free (note this in the name like 'royalty-free').
- Output ONLY the JSON.
"""

CAPTION_TEMPLATE = """\
You are an Instagram caption writer. Output JSON:

{{
  "captions": [
    {{"variant": "short",  "body": "≤ 80 char punchy one-liner", "hashtags": ["#relevant", "#specific"]}},
    {{"variant": "medium", "body": "2-3 sentence story w/ a punchline", "hashtags": ["#x", "#y"]}},
    {{"variant": "story",  "body": "first-person 4-6 sentence story (founder POV ok)", "hashtags": ["#founder", "#buildinpublic"]}},
    {{"variant": "cta",    "body": "engagement bait: question or save-prompt", "hashtags": ["#comment", "#save"]}},
    {{"variant": "hashtags", "body": "the caption text + a hashtag block of exactly 8 niche tags", "hashtags": ["8", "niche", "tags"]}}
  ]
}}

Context:
- Site: {title} ({domain})
- Reel idea: {idea_title}
- Hook: {hook}
- Tone: {tone}

Rules:
- Hashtags MUST be specific to this niche (no generic #viral #fyp #reels).
- The 'story' variant must read like a real human wrote it, not a brand voice.
- The 'cta' variant must ask a question OR prompt a save (pick one, not both).
- Output ONLY the JSON.
"""
