"""CLI: `python -m reel_studio generate --url <site>` and friends."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()  # picks up REEL_STUDIO_OPENAI_KEY, OPENAI_API_KEY, etc.
except ImportError:
    pass  # python-dotenv optional; env vars already work

from .generator import generate
from .store import Store


def _default_db() -> str:
    return os.environ.get("REEL_STUDIO_DB_PATH", "./data/reel_studio.db")


def _default_output() -> str:
    return os.environ.get("REEL_STUDIO_OUTPUT_DIR", "./output")


def cmd_generate(args: argparse.Namespace) -> int:
    result = generate(
        url=args.url, db_path=args.db, output_root=args.output,
        duration_s=args.duration, model=args.model,
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = Store(args.db)
    rows = store.list_reels(site_domain=args.site, limit=args.limit)
    if not rows:
        print("(no reels yet)")
        return 0
    print(f"{'REEL ID':<20} {'SITE':<25} {'ANGLE':<20} {'STATUS':<10} {'TITLE'}")
    print("-" * 110)
    for r in rows:
        title = (r["idea_title"] or "")[:40]
        print(f"{r['id']:<20} {r['domain']:<25} {r['angle']:<20} {r['status']:<10} {title}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = Store(args.db)
    reel = store.get_reel(args.reel_id)
    if not reel:
        print(f"reel {args.reel_id} not found", file=sys.stderr)
        return 2
    print(json.dumps(reel, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reel-studio",
                                description="Scrape a site → MiniMax → reel concept/script/sound/caption")
    p.add_argument("--db", default=_default_db(), help="SQLite path")
    p.add_argument("--output", default=_default_output(), help="output root")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="Generate a reel for a URL")
    g.add_argument("--url", required=True)
    g.add_argument("--duration", type=int, default=30, help="target duration in seconds")
    g.add_argument("--model", default=os.environ.get("REEL_STUDIO_MODEL", "MiniMax-M3"))
    g.set_defaults(func=cmd_generate)

    l = sub.add_parser("list", help="List reels")
    l.add_argument("--site", help="filter by domain")
    l.add_argument("--limit", type=int, default=50)
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="Show full reel record")
    s.add_argument("reel_id")
    s.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
