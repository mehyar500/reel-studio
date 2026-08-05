"""MiniMax CLI wrapper.

Two execution modes:

  BACKEND=subprocess   (default for the CLI when run from a plain terminal)
        Tries the `pi` shim at ~/.minimax/bin/minimax.cmd. On this box
        this needs `~/.pi/agent/auth.json` populated via `minimax /login`,
        which is interactive. Will raise MiniMaxError with a clear message
        if auth isn't set up.

  BACKEND=agent        (used when imported from inside a Hermes agent loop)
        Calls MiniMax-M3 via the hermes delegate_task tool. This is the
        supported path on this box — the agent runtime has working MiniMax
        OAuth credentials via the hermes gateway.

For the MVP, the canonical flow is: the user runs `python -m reel_studio
generate --url <site>` from inside a Hermes agent session (or asks the
agent to do it). The agent picks up `BACKEND=agent`, runs the prompt,
parses JSON, and saves artifacts. The CLI subprocess path exists so the
project is importable and the script structure is testable from any shell
once auth.json is configured.

Set the backend via env var REEL_STUDIO_BACKEND=agent|subprocess.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any


class MiniMaxError(RuntimeError):
    pass


MODEL = "MiniMax-M3"


def _backend() -> str:
    return os.environ.get("REEL_STUDIO_BACKEND", "subprocess").lower()


# ---------------------------------------------------------------- backend: subprocess

def _strip_json_block(text: str) -> str:
    """Pull the first ```json ... ``` block out of a response."""
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text)
    if m:
        return m.group(1).strip()
    text = text.strip()
    if text.startswith(("{", "[")):
        return text
    raise MiniMaxError(f"No JSON block found in minimax output:\n{text[:500]}")


def _call_subprocess(prompt: str, *, model: str, timeout: int, expect_json: bool) -> Any:
    """Invoke the `pi` shim via subprocess. Needs `~/.pi/agent/auth.json` populated."""
    import shutil, subprocess

    cmd = shutil.which("minimax") or r"C:\Users\mehya\.minimax\bin\minimax.cmd"
    if not os.path.isfile(cmd):
        raise MiniMaxError(
            f"minimax CLI not found at {cmd!r}. Either install it or set "
            "REEL_STUDIO_BACKEND=agent and run from inside a Hermes session."
        )

    full_prompt = prompt
    if expect_json:
        full_prompt += "\n\nRespond with ONLY a single ```json ... ``` block, nothing else."

    try:
        proc = subprocess.run(
            [cmd, "--provider", "openai", "--model", model, "-p", full_prompt],
            capture_output=True, text=True, timeout=timeout, shell=True,
        )
    except FileNotFoundError as e:
        raise MiniMaxError(f"minimax CLI not found at {cmd!r}.") from e

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "No API key found" in stderr or "Use /login" in stderr:
            raise MiniMaxError(
                "minimax CLI needs authentication. Run `minimax /login` once in your "
                "shell to populate ~/.pi/agent/auth.json. After that, re-run this command. "
                "OR set REEL_STUDIO_BACKEND=agent and ask a Hermes agent to run the generation."
            )
        raise MiniMaxError(
            f"minimax CLI exited {proc.returncode}\nSTDOUT: {proc.stdout[:500]}\n"
            f"STDERR: {proc.stderr[:500]}"
        )
    out = proc.stdout.strip()
    if expect_json:
        return json.loads(_strip_json_block(out))
    return out


# ---------------------------------------------------------------- backend: agent (in-process)

def _call_agent(prompt: str, *, model: str, timeout: int, expect_json: bool) -> Any:
    """Call MiniMax-M3 via the hermes delegate_task tool.

    Only works when this module is imported from inside a Hermes agent loop.
    The tool is registered on `sys.modules['hermes_tools']` by the agent runtime.
    """
    try:
        from hermes_tools import delegate_task  # type: ignore
    except ImportError as e:
        raise MiniMaxError(
            "REEL_STUDIO_BACKEND=agent requires running inside a Hermes agent loop. "
            "Either unset that env var (will fall back to subprocess CLI) or run "
            "this script from a Hermes session."
        ) from e

    if expect_json:
        prompt = (
            prompt.rstrip()
            + "\n\n---\nRespond with ONLY a single ```json ... ``` block. "
              "No prose before or after. No markdown outside the JSON block."
        )

    full_prompt = (
        "You are a deterministic JSON generator for the reel-studio pipeline. "
        "You will receive ONE prompt, execute the creative direction in it, "
        "and return ONLY a JSON block wrapped in ```json ... ``` fences.\n\n"
        f"USER PROMPT:\n{prompt}"
    )

    t0 = time.time()
    result = delegate_task(
        goal=full_prompt,
        context=(
            "Return ONLY a ```json ... ``` block. "
            "Do not call any tools. Do not explain. Do not preface. "
            "The user will reject any non-JSON output."
        ),
    )
    elapsed = time.time() - t0

    if isinstance(result, list) and result:
        result = result[0]
    if isinstance(result, dict):
        out_text = result.get("result") or result.get("output") or result.get("summary") or json.dumps(result)
    else:
        out_text = str(result)

    if not expect_json:
        return out_text
    try:
        return json.loads(_strip_json_block(out_text))
    except (json.JSONDecodeError, MiniMaxError) as e:
        raise MiniMaxError(
            f"minimax sub-agent returned unparseable JSON after {elapsed:.1f}s: {e}\n"
            f"--- raw output ---\n{out_text[:1500]}"
        )


# ---------------------------------------------------------------- public entry

def call_minimax(prompt: str, *, model: str = MODEL, timeout: int = 600,
                 expect_json: bool = True) -> Any:
    """Run MiniMax (M3) with `prompt` and return parsed JSON (or raw string)."""
    backend = _backend()
    if backend == "agent":
        return _call_agent(prompt, model=model, timeout=timeout, expect_json=expect_json)
    elif backend == "subprocess":
        return _call_subprocess(prompt, model=model, timeout=timeout, expect_json=expect_json)
    raise MiniMaxError(f"Unknown REEL_STUDIO_BACKEND: {backend!r} (use 'agent' or 'subprocess')")


# -------- high-level prompt templates (unchanged shape from prior version) --------

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
