"""SQLite-backed store: sites, reels, ideas, scripts, captions. Dedup by content hash."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    domain        TEXT UNIQUE NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT,
    description   TEXT,
    metadata_json TEXT NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reels (
    id            TEXT PRIMARY KEY,            -- short hash
    site_id       INTEGER NOT NULL REFERENCES sites(id),
    idea_hash     TEXT UNIQUE NOT NULL,       -- dedup key: hash(site_id + idea_title + hook)
    angle         TEXT NOT NULL,              -- short tag for variety tracking
    idea_title    TEXT NOT NULL,
    idea_hook     TEXT NOT NULL,
    format        TEXT,                       -- "talking-head", "screen-record", "b-roll", etc.
    target_emotion TEXT,
    status        TEXT NOT NULL DEFAULT 'draft', -- draft|complete|posted|archived
    output_dir    TEXT NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scripts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reel_id     TEXT NOT NULL REFERENCES reels(id),
    duration_s  INTEGER,
    scenes_json TEXT NOT NULL,                -- [{ts, scene, vo, on_screen}]
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sounds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reel_id     TEXT NOT NULL REFERENCES reels(id),
    track_name  TEXT,
    mood        TEXT,
    notes       TEXT,
    cue_json    TEXT NOT NULL,                -- [{ts, action: 'start'|'duck'|'cut'}]
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS captions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reel_id     TEXT NOT NULL REFERENCES reels(id),
    variant     TEXT NOT NULL,                -- short|medium|story|cta|hashtags
    body        TEXT NOT NULL,
    hashtags    TEXT,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reels_site   ON reels(site_id);
CREATE INDEX IF NOT EXISTS idx_reels_angle  ON reels(angle);
CREATE INDEX IF NOT EXISTS idx_scripts_reel ON scripts(reel_id);
CREATE INDEX IF NOT EXISTS idx_sounds_reel  ON sounds(reel_id);
CREATE INDEX IF NOT EXISTS idx_captions_reel ON captions(reel_id);
"""


class Store:
    """Thin wrapper over sqlite3 with dict-shaped rows."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # -------- sites --------

    def upsert_site(self, url: str, domain: str, title: Optional[str],
                    description: Optional[str], metadata: dict) -> int:
        now = time.time()
        with self._conn() as c:
            row = c.execute("SELECT id FROM sites WHERE domain = ?", (domain,)).fetchone()
            if row:
                c.execute("""UPDATE sites SET url=?, title=?, description=?, metadata_json=?, updated_at=?
                             WHERE id=?""",
                          (url, title, description, json.dumps(metadata), now, row["id"]))
                return row["id"]
            cur = c.execute("""INSERT INTO sites (domain, url, title, description, metadata_json,
                                                   created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (domain, url, title, description, json.dumps(metadata), now, now))
            return cur.lastrowid

    def get_site(self, domain: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sites WHERE domain = ?", (domain,)).fetchone()
            return dict(row) if row else None

    # -------- reels --------

    @staticmethod
    def make_reel_id(site_id: int, idea_title: str, hook: str) -> tuple[str, str]:
        """Returns (reel_id, idea_hash). The idea_hash is the dedup key."""
        idea_hash_input = f"{site_id}|{idea_title.strip().lower()}|{hook.strip().lower()}"
        idea_hash = hashlib.sha256(idea_hash_input.encode()).hexdigest()[:16]
        reel_id = "r_" + idea_hash
        return reel_id, idea_hash

    def idea_exists(self, idea_hash: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM reels WHERE idea_hash = ?", (idea_hash,)).fetchone()
            return row is not None

    def used_angles(self, site_id: int) -> list[str]:
        with self._conn() as c:
            return [r["angle"] for r in c.execute(
                "SELECT DISTINCT angle FROM reels WHERE site_id = ? AND angle IS NOT NULL",
                (site_id,)).fetchall()]

    def insert_reel(self, reel_id: str, site_id: int, idea_hash: str, angle: str,
                    idea_title: str, idea_hook: str, format_: Optional[str],
                    target_emotion: Optional[str], output_dir: str) -> None:
        now = time.time()
        with self._conn() as c:
            c.execute("""INSERT OR IGNORE INTO reels
                (id, site_id, idea_hash, angle, idea_title, idea_hook, format,
                 target_emotion, status, output_dir, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)""",
                      (reel_id, site_id, idea_hash, angle, idea_title, idea_hook,
                       format_, target_emotion, output_dir, now, now))

    def mark_complete(self, reel_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE reels SET status='complete', updated_at=? WHERE id=?",
                      (time.time(), reel_id))

    def add_script(self, reel_id: str, scenes: list[dict], duration_s: Optional[int]) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO scripts (reel_id, duration_s, scenes_json, created_at) VALUES (?,?,?,?)",
                      (reel_id, duration_s, json.dumps(scenes), time.time()))

    def add_sound(self, reel_id: str, track_name: Optional[str], mood: Optional[str],
                  notes: Optional[str], cues: list[dict]) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO sounds (reel_id, track_name, mood, notes, cue_json, created_at) VALUES (?,?,?,?,?,?)",
                      (reel_id, track_name, mood, notes, json.dumps(cues), time.time()))

    def add_captions(self, reel_id: str, captions: list[dict]) -> None:
        with self._conn() as c:
            for cap in captions:
                c.execute("INSERT INTO captions (reel_id, variant, body, hashtags, created_at) VALUES (?,?,?,?,?)",
                          (reel_id, cap["variant"], cap["body"],
                           " ".join(cap.get("hashtags", [])), time.time()))

    # -------- read --------

    def list_reels(self, site_domain: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            if site_domain:
                rows = c.execute("""SELECT r.*, s.domain FROM reels r
                                    JOIN sites s ON s.id = r.site_id
                                    WHERE s.domain = ?
                                    ORDER BY r.created_at DESC LIMIT ?""",
                                 (site_domain, limit)).fetchall()
            else:
                rows = c.execute("""SELECT r.*, s.domain FROM reels r
                                    JOIN sites s ON s.id = r.site_id
                                    ORDER BY r.created_at DESC LIMIT ?""",
                                 (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_reel(self, reel_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("""SELECT r.*, s.domain, s.url FROM reels r
                               JOIN sites s ON s.id = r.site_id
                               WHERE r.id = ?""", (reel_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["scripts"]  = [dict(r) for r in c.execute(
                "SELECT * FROM scripts WHERE reel_id = ? ORDER BY created_at DESC", (reel_id,))]
            d["sounds"]   = [dict(r) for r in c.execute(
                "SELECT * FROM sounds WHERE reel_id = ? ORDER BY created_at DESC", (reel_id,))]
            d["captions"] = [dict(r) for r in c.execute(
                "SELECT * FROM captions WHERE reel_id = ? ORDER BY created_at DESC", (reel_id,))]
            return d


if __name__ == "__main__":
    import sys, tempfile
    db = Store(Path(tempfile.gettempdir()) / "rs_smoke.db")
    sid = db.upsert_site("https://x.test", "x.test", "X", "test", {"hello": "world"})
    rid, ih = db.make_reel_id(sid, "Idea A", "Hook here")
    db.insert_reel(rid, sid, ih, "demo", "Idea A", "Hook here", "talking-head", "curiosity", "/tmp/out")
    db.mark_complete(rid)
    db.add_script(rid, [{"ts": "0-3s", "scene": "open", "vo": "hey"}], 30)
    db.add_sound(rid, "Beat X", "energetic", "duck on CTA", [{"ts": "0s", "action": "start"}])
    db.add_captions(rid, [{"variant": "short", "body": "Hello", "hashtags": ["#demo"]}])
    print("site_id", sid, "reel_id", rid, "idea_hash", ih)
    print("exists?", db.idea_exists(ih))
    print("reels:", json.dumps(db.list_reels("x.test"), indent=2, default=str))
