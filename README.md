# Reel Studio 🎬

**MVP**: scrape a website → use MiniMax CLI to generate Instagram Reel concepts + scripts + sound design + captions → save everything locally → SQLite-backed to avoid duplicates and track lineage.

## What it does

1. **Scrape** a target site (HTML + JS bundle) and extract: name, tagline, pricing pages, about, features, color palette, audience signals.
2. **Idea pass** via MiniMax: 3 distinct reel concepts per site (hook, format, target emotion).
3. **Script pass** for the chosen idea: 15-30s scene-by-scene script.
4. **Sound design pass**: royalty-free sound picks + on-screen caption timing.
5. **Caption pass**: 5 caption variants (short / medium / story / CTA / hashtag).
6. **Persist** all of the above + a hash of (site + idea) so we never re-generate the same reel.
7. **Idea rotation**: track which angles were used so next run suggests fresh ones.

## Quick start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Make sure OPENAI_API_KEY is in env (for MiniMax CLI)
export OPENAI_API_KEY=sk-...

# 3. Generate a reel for a site
python -m reel_studio generate --url https://rizza.app --output ./output

# 4. List what you've made so far
python -m reel_studio list

# 5. Inspect a specific reel
python -m reel_studio show <reel_id>
```

## Architecture

```
reel-studio/
├── reel_studio/
│   ├── __init__.py
│   ├── cli.py              # entrypoint: generate / list / show
│   ├── scraper.py          # fetch HTML + JS, extract metadata
│   ├── llm.py              # MiniMax CLI wrapper (prompts + parsing)
│   ├── store.py            # SQLite: sites, reels, ideas, scripts, captions
│   ├── generator.py        # orchestrator: scrape → ideas → script → sound → caption
│   └── ideas.py            # idea-rotation logic, dedup hashes
├── output/                 # generated reel JSON + future media
│   └── rizza.app/
│       └── <reel_id>/
│           ├── idea.json
│           ├── script.json
│           ├── sound.json
│           ├── caption.json
│           └── README.md   # human-readable summary
├── data/
│   └── reel_studio.db      # SQLite (gitignored)
├── requirements.txt
└── .env.example
```

## MVP scope (current)

- ✅ Scrape any URL → metadata JSON
- ✅ Idea generation (3 concepts) via MiniMax
- ✅ Script + sound + caption generation
- ✅ SQLite persistence + dedup
- ✅ Local-first output (no posting)
- ⏳ Actual video rendering (text/JSON only for now)
- ⏳ Image/clip fetching for scenes
- ⏳ Auto-posting to Instagram

## Why MiniMax CLI

The user runs MiniMax end-to-end. Every prompt is reproducible (saved with the reel) and the LLM is responsible for the **whole creative chain**: idea → script → sound → caption. We never stitch templates.
