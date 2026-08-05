"""Orchestrator: scrape → ideas → script → sound → caption. Local-first."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import llm
from .ideas import pick_unused_idea
from .scraper import scrape
from .store import Store


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s.lower())[:64]


def generate(*, url: str, db_path: str | Path, output_root: str | Path,
             duration_s: int = 30, model: str = "MiniMax-M3") -> dict:
    """Full generation pipeline for one site.

    Returns a dict with keys: site, idea, script, sound, captions, output_dir, reel_id.
    Raises llm.MiniMaxError on LLM failure; Store keeps partial state via inserts.
    """
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)

    # 1. Scrape
    md = scrape(url)
    site_id = store.upsert_site(
        url=md.url, domain=md.domain, title=md.title,
        description=md.description, metadata=md.to_dict(),
    )
    site_row = store.get_site(md.domain)

    # 2. Idea pass
    prompt = llm.SITE_CONTEXT_TEMPLATE.format(metadata_json=json.dumps(md.to_dict(), indent=2)[:6000])
    raw = llm.call_minimax(prompt, model=model)
    idea_blob = llm.parse_json(raw)
    ideas = idea_blob.get("ideas", [])
    if not ideas:
        raise llm.MiniMaxError(f"minimax returned no ideas. blob keys: {list(idea_blob.keys())}")

    # 3. Pick a fresh idea (avoid angle + hash collisions)
    used_angles = store.used_angles(site_id)
    used_hashes = {r["idea_hash"] for r in store.list_reels(md.domain, limit=500)}
    new_hashes = {}
    for it in ideas:
        rid, ih = store.make_reel_id(site_id, it["title"], it["hook"])
        new_hashes[(it["title"], it["hook"])] = ih

    chosen = pick_unused_idea(ideas, used_angles, used_hashes, new_hashes)
    if chosen is None:
        raise llm.MiniMaxError("All ideas were duplicates and no fallback available.")
    chosen_hash = new_hashes[(chosen["title"], chosen["hook"])]
    reel_id, _ = store.make_reel_id(site_id, chosen["title"], chosen["hook"])

    # make output dir
    out_dir = output_root / md.domain / reel_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Insert reel row (idempotent via OR IGNORE on PK)
    store.insert_reel(
        reel_id=reel_id, site_id=site_id, idea_hash=chosen_hash,
        angle=chosen.get("angle", ""), idea_title=chosen["title"],
        idea_hook=chosen["hook"], format_=chosen.get("format"),
        target_emotion=chosen.get("target_emotion"), output_dir=str(out_dir),
    )

    # 4. Script pass
    script_prompt = llm.SCRIPT_TEMPLATE.format(
        duration=duration_s,
        brand_summary=idea_blob.get("brand_summary", md.title or md.domain),
        audience=idea_blob.get("audience", "general consumer"),
        tone=idea_blob.get("tone", "playful"),
        idea_title=chosen["title"], angle=chosen.get("angle", ""),
        hook=chosen["hook"], format=chosen.get("format", "talking-head"),
        target_emotion=chosen.get("target_emotion", "curiosity"),
    )
    script_blob = llm.parse_json(llm.call_minimax(script_prompt, model=model))
    scenes = script_blob.get("scenes", [])
    store.add_script(reel_id, scenes, duration_s)

    # 5. Sound pass
    duration_div3 = max(2, duration_s // 3)
    cta_ts = duration_s - 3
    sound_prompt = llm.SOUND_TEMPLATE.format(
        tone=idea_blob.get("tone", "playful"),
        target_emotion=chosen.get("target_emotion", "curiosity"),
        duration_div3=duration_div3, cta_ts=cta_ts,
    )
    sound_blob = llm.parse_json(llm.call_minimax(sound_prompt, model=model))
    cues = sound_blob.get("cues", [])
    store.add_sound(
        reel_id,
        track_name=sound_blob.get("track_name"),
        mood=sound_blob.get("mood"),
        notes=sound_blob.get("notes"),
        cues=cues,
    )

    # 6. Caption pass
    caption_prompt = llm.CAPTION_TEMPLATE.format(
        title=md.title or md.domain, domain=md.domain,
        idea_title=chosen["title"], hook=chosen["hook"],
        tone=idea_blob.get("tone", "playful"),
    )
    cap_blob = llm.parse_json(llm.call_minimax(caption_prompt, model=model))
    captions = cap_blob.get("captions", [])
    store.add_captions(reel_id, captions)

    store.mark_complete(reel_id)

    # 7. Write all artifacts to disk
    artifacts = {
        "site": {"domain": md.domain, "url": md.url, "title": md.title,
                 "description": md.description, "metadata": md.to_dict()},
        "idea_pass": idea_blob,
        "chosen_idea": chosen,
        "script": script_blob,
        "sound": sound_blob,
        "captions": cap_blob,
    }
    for fname, obj in [
        ("idea.json",    {"idea_pass": idea_blob, "chosen_idea": chosen}),
        ("script.json",  script_blob),
        ("sound.json",   sound_blob),
        ("captions.json", cap_blob),
        ("site.json",    artifacts["site"]),
        ("README.md",    _to_markdown(artifacts, reel_id)),
    ]:
        path = out_dir / fname
        if fname.endswith(".md"):
            path.write_text(obj, encoding="utf-8")
        else:
            path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "reel_id": reel_id,
        "output_dir": str(out_dir),
        "site": md.domain,
        "idea": chosen,
        "scenes_count": len(scenes),
        "captions_count": len(captions),
    }


def _to_markdown(artifacts: dict, reel_id: str) -> str:
    chosen = artifacts["chosen_idea"]
    idea_pass = artifacts["idea_pass"]
    script = artifacts["script"]
    sound = artifacts["sound"]
    captions = artifacts["captions"]
    site = artifacts["site"]

    lines = [
        f"# Reel — {chosen['title']}",
        "",
        f"**Reel ID:** `{reel_id}`  ",
        f"**Site:** [{site['title'] or site['domain']}]({site['url']})  ",
        f"**Angle:** `{chosen.get('angle','')}` · **Format:** `{chosen.get('format','')}` · **Emotion:** `{chosen.get('target_emotion','')}`  ",
        f"**Tone:** {idea_pass.get('tone','?')}  ",
        "",
        f"> **Hook:** {chosen['hook']}",
        "",
        f"_{chosen.get('why_this_works','')}_",
        "",
        "## Script",
        "",
        f"Duration: **{script.get('duration_s', '?')}s**",
        "",
        "| Time | Scene | VO | On screen |",
        "|------|-------|----|-----------|",
    ]
    for s in script.get("scenes", []):
        lines.append(f"| `{s.get('ts','')}` | {s.get('scene','')} | {s.get('vo','')} | {s.get('on_screen','')} |")

    lines += [
        "",
        "## Sound",
        "",
        f"- **Track:** {sound.get('track_name','?')}",
        f"- **Mood:** {sound.get('mood','?')}",
        f"- **Notes:** {sound.get('notes','')}",
        f"- **Alt:** {sound.get('alt_track','-')}",
        "",
        "**Cues:**",
    ]
    for c in sound.get("cues", []):
        lines.append(f"- `{c.get('ts','')}` → **{c.get('action','')}**")
    lines += ["", "## Captions", ""]
    for cap in captions.get("captions", []):
        tags = " ".join(cap.get("hashtags", []))
        lines.append(f"### {cap.get('variant','').title()}")
        lines.append("")
        lines.append(cap.get("body",""))
        if tags:
            lines.append("")
            lines.append(tags)
        lines.append("")
    return "\n".join(lines)
