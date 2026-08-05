"""Idea selection: pick the best concept from MiniMax that we haven't used yet."""
from __future__ import annotations

from typing import Iterable


def pick_unused_idea(ideas: list[dict], used_angles: Iterable[str],
                     used_hashes: set[str], new_idea_hashes: dict) -> dict | None:
    """Return the first idea whose angle AND hash we haven't generated before.

    Args:
        ideas: list from MiniMax idea pass.
        used_angles: angles already on file for this site.
        used_hashes: hashes of any reels already saved for this site.
        new_idea_hashes: dict we'll mutate: idea_title+hook -> hash. The caller
            computes hashes via Store.make_reel_id and passes them in.

    Falls back to the first idea if all are repeats (we'll at least produce
    *something*; the dedup hash will still catch literal duplicates on retry).
    """
    used_angles = set(used_angles)
    for idea in ideas:
        h = new_idea_hashes.get((idea["title"], idea["hook"]))
        if h in used_hashes:
            continue
        if idea.get("angle") in used_angles:
            continue
        return idea
    # fallback: first idea whose hash is new
    for idea in ideas:
        h = new_idea_hashes.get((idea["title"], idea["hook"]))
        if h not in used_hashes:
            return idea
    return ideas[0] if ideas else None
