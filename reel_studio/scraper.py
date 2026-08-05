"""Scrape a site: HTML + JS bundle, extract metadata for LLM context."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from html import unescape
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests


@dataclass
class SiteMetadata:
    url: str
    domain: str
    title: Optional[str] = None
    description: Optional[str] = None
    og_image: Optional[str] = None
    canonical: Optional[str] = None
    theme_color: Optional[str] = None
    pages: list[dict] = field(default_factory=list)   # [{path, label}]
    feature_strings: list[str] = field(default_factory=list)
    palette_hex: list[str] = field(default_factory=list)
    js_bundle_url: Optional[str] = None
    js_bundle_size: int = 0
    raw_text_excerpt: str = ""
    scrape_status: str = "ok"
    scrape_error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


_UA = "ReelStudio/0.1 (+https://github.com/mehyar500/reel-studio)"


def _fetch(url: str, timeout: int = 20) -> str:
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
    r.raise_for_status()
    return r.text


def _extract(html: str, base_url: str) -> tuple[dict, list[dict], Optional[str]]:
    """Pull meta tags, nav links, and the JS bundle path from the HTML."""
    meta: dict = {}

    def _meta(pattern: str, group: int = 1) -> Optional[str]:
        m = re.search(pattern, html, flags=re.I)
        return m.group(group).strip() if m else None

    meta["title"]       = _meta(r"<title>([^<]+)</title>")
    meta["description"] = _meta(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)')
    meta["og_image"]    = _meta(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)')
    meta["canonical"]   = _meta(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)')
    meta["theme_color"] = _meta(r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\']([^"\']+)')

    # nav links from the SEO snapshot block (Base44/Next/Vite SPAs all emit these)
    nav_raw = re.findall(r'<a href="(/[^"#?]+)"[^>]*>\s*([^<]{2,60})\s*</a>', html)
    seen = set()
    pages: list[dict] = []
    for path, label in nav_raw:
        path = path.strip()
        if path in seen or path == "/" or path.startswith("/api/"):
            continue
        seen.add(path)
        pages.append({"path": path, "label": unescape(label).strip()})

    js_path = _meta(r'src=["\'](/assets/index-[A-Za-z0-9_-]+\.js)["\']')
    return meta, pages, js_path


def _js_insights(js: str) -> tuple[list[str], list[str]]:
    """Pull capitalized strings (likely UI labels) and any hex palette from JS bundle."""
    # Words/phrases 2-5 tokens, capitalized, length 4-50
    label_candidates = re.findall(r'"([A-Z][a-zA-Z0-9]+(?: [A-Z][a-zA-Z0-9]+){0,4})"', js)
    from collections import Counter
    common = [s for s, _ in Counter(label_candidates).most_common(80)
              if 4 < len(s) < 50 and not s.startswith(("Http", "Https"))]
    # de-dup
    seen, labels = set(), []
    for s in common:
        if s not in seen:
            seen.add(s)
            labels.append(s)
    palette = sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", js)))[:30]
    return labels, palette


def _text_excerpt(html: str, n: int = 1500) -> str:
    h = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    h = re.sub(r"<style[\s\S]*?</style>", "", h, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", h)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n]


def scrape(url: str) -> SiteMetadata:
    """Public entry point: fetch the URL, follow JS bundle, return metadata."""
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    md = SiteMetadata(url=url, domain=domain)

    try:
        html = _fetch(url)
    except Exception as e:
        md.scrape_status = "html_failed"
        md.scrape_error = f"{type(e).__name__}: {e}"
        return md

    meta, pages, js_path = _extract(html, url)
    md.title       = meta["title"]
    md.description = meta["description"]
    md.og_image    = meta["og_image"]
    md.canonical   = meta["canonical"]
    md.theme_color = meta["theme_color"]
    md.pages       = pages
    md.raw_text_excerpt = _text_excerpt(html)

    if js_path:
        md.js_bundle_url = urljoin(url, js_path)
        try:
            js = _fetch(md.js_bundle_url)
            md.js_bundle_size = len(js)
            labels, palette = _js_insights(js)
            md.feature_strings = labels
            md.palette_hex = palette
        except Exception as e:
            md.scrape_status = "js_failed"
            md.scrape_error = f"bundle: {type(e).__name__}: {e}"

    return md


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m reel_studio.scraper <url>")
        raise SystemExit(2)
    out = scrape(sys.argv[1])
    print(json.dumps(out.to_dict(), indent=2)[:2000])
