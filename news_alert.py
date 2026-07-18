#!/usr/bin/env python3
"""
RSS news alert -- keyword filtering, no LLM, Telegram output.
First run seeds seen_items.json without sending any alerts.
Subsequent runs alert on new matching items only.
"""

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Feed sources
# ---------------------------------------------------------------------------

FEEDS = [
    # AI research (Anthropic has no public RSS feed as of 2025)
    {"name": "DeepMind", "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "OpenAI", "url": "https://openai.com/blog/rss.xml"},
    {"name": "Microsoft AI", "url": "https://blogs.microsoft.com/ai/feed/"},
    {"name": "Microsoft Blog", "url": "https://blogs.microsoft.com/feed/"},
    {"name": "Google Research", "url": "https://research.google/blog/rss/"},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml"},
    # Tech news
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
    # Defence & security
    {"name": "Breaking Defense", "url": "https://breakingdefense.com/feed/"},
    {"name": "Defense One", "url": "https://www.defenseone.com/rss/technology/"},
    {"name": "War on the Rocks", "url": "https://warontherocks.com/feed/"},
    {"name": "The Debrief", "url": "https://thedebrief.org/feed/"},
    {"name": "Defense News", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/"},
    # Power Platform / Copilot Studio (work)
    {"name": "Power Platform Blog", "url": "https://www.microsoft.com/en-us/power-platform/blog/feed/"},
    # Unverified -- Microsoft has changed this URL before. Run --dry-run first;
    # if it 404s in the console output, just delete this line.
    {"name": "Copilot Studio Blog", "url": "https://www.microsoft.com/en-us/microsoft-copilot/blog/copilot-studio/feed/"},
]

# ---------------------------------------------------------------------------
# Keyword categories (word-boundary matched, case-insensitive)
# ---------------------------------------------------------------------------

KEYWORDS: dict[str, list[str]] = {
    "AI": [
        "artificial intelligence", "machine learning", "deep learning",
        "neural network", "large language model", "LLM", "foundation model",
        "generative AI", "GenAI", "Claude", "GPT", "ChatGPT", "OpenAI", "AI",
        "Gemini", "Grok", "Kimi", "Moonshot AI", "DeepSeek", "Qwen", "Llama",
        "Mistral AI", "AI model", "AI system", "AI safety", "AI alignment",
        "AGI", "reinforcement learning", "computer vision", "natural language",
        "transformer model", "AI agent", "multimodal", "frontier model",
        "reasoning model", "state of the art", "SOTA", "breakthrough",
        "benchmark",
    ],
    "Aerospace": [
        "aerospace", "hypersonic", "ICBM", "ballistic missile",
        "satellite constellation", "orbital launch", "launch vehicle",
        "reusable rocket", "SpaceX", "Starship", "Falcon 9",
        "Ariane", "Rocket Lab", "Blue Origin", "ULA",
        "space launch", "spacecraft", "orbital", "reentry vehicle",
    ],
    "Autonomous_Vehicles": [
        "autonomous vehicle", "self-driving", "autonomous driving",
        "robotaxi", "driverless", "Waymo", "Tesla Autopilot",
        "lidar", "autonomous robot", "drone delivery",
        "unmanned aerial", "UAV", "UAS", "drone swarm",
        "eVTOL", "air taxi", "autonomous system",
    ],
    "AR_VR": [
        "augmented reality", "virtual reality", "mixed reality",
        "extended reality", "spatial computing", "HoloLens",
        "Vision Pro", "Meta Quest", "VR headset", "AR headset",
        "holographic", "metaverse",
    ],
    "Defence_Tech": [
        "defense technology", "defence technology", "military AI",
        "autonomous weapon", "lethal autonomous", "drone warfare",
        "cyber warfare", "electronic warfare", "directed energy weapon",
        "hypersonic weapon", "missile defense", "JADC2",
        "DARPA", "Pentagon", "DoD", "military robotics",
        "counter-drone", "battlefield AI", "C2 system",
        "signals intelligence", "SIGINT", "ISR",
    ],
    "Power_Platform": [
        "Copilot Studio", "Power Automate", "Power Apps", "Power Platform",
        "Dataverse", "Power Pages", "Power Fx", "MCP connector",
        "release wave", "Power Platform admin",
    ],
}

# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

SEEN_FILE = Path("seen_items.json")
PRUNE_AFTER_DAYS = 30
MAX_ITEM_AGE_DAYS = 14  # ignore entries older than this, even if never seen before
MAX_ALERTS_PER_RUN = 25  # safety cap

# ---------------------------------------------------------------------------
# Feed fetching & parsing
# ---------------------------------------------------------------------------

ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

def fetch_bytes(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "NewsAlertBot/1.0 (+https://github.com)"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {url}")
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
    return None

def _text(el, *tags) -> str:
    for tag in tags:
        val = el.findtext(tag, "")
        if val:
            return val
    return ""

def parse_feed(data: bytes) -> list[dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        print(f"  XML parse error: {e}")
        return []

    entries = []
    tag = root.tag

    if tag in (f"{{{ATOM_NS}}}feed", "feed"):
        # Atom
        for e in root.findall(f"{{{ATOM_NS}}}entry"):
            link_el = e.find(f"{{{ATOM_NS}}}link")
            link = link_el.get("href", "") if link_el is not None else ""
            published = (
                e.findtext(f"{{{ATOM_NS}}}published", "")
                or e.findtext(f"{{{ATOM_NS}}}updated", "")
            )
            entries.append({
                "title": e.findtext(f"{{{ATOM_NS}}}title", ""),
                "link": link,
                "summary": e.findtext(f"{{{ATOM_NS}}}summary", "")
                    or e.findtext(f"{{{ATOM_NS}}}content", ""),
                "id": e.findtext(f"{{{ATOM_NS}}}id", link),
                "published": published,
            })
    else:
        # RSS 2.0 (root is <rss> or <rdf:RDF>)
        channel = root.find("channel")
        if channel is None:
            channel = root
        for item in channel.findall("item"):
            entries.append({
                "title": item.findtext("title", ""),
                "link": item.findtext("link", ""),
                "summary": item.findtext("description", "")
                    or item.findtext(f"{{{CONTENT_NS}}}encoded", ""),
                "id": item.findtext("guid", item.findtext("link", "")),
                "published": item.findtext("pubDate", ""),
            })

    return entries

def entry_key(entry: dict) -> str:
    uid = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha1(uid.encode()).hexdigest()

def entry_age_days(entry: dict) -> float | None:
    """Return how many days old an entry is, or None if no usable date."""
    raw = entry.get("published", "")
    if not raw:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return delta.total_seconds() / 86400

# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def _matches(text: str, keyword: str) -> bool:
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return bool(re.search(pattern, text, re.IGNORECASE))

def matched_categories(entry: dict) -> list[str]:
    raw = strip_html(entry.get("title", "")) + " " + strip_html(entry.get("summary", ""))
    found = []
    for category, keywords in KEYWORDS.items():
        if any(_matches(raw, kw) for kw in keywords):
            found.append(category)
    return found

def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            if not result.get("ok"):
                print(f"  Telegram error: {result.get('description')}")
                return False
            return True
    except Exception as e:
        print(f"  Telegram send error: {e}")
        return False

def build_message(source: str, categories: list[str], title: str,
                   link: str, summary: str) -> str:
    tags = " ".join(f"#{c}" for c in categories)
    snippet = strip_html(summary)[:220].strip()
    if snippet:
        snippet = f"\n<i>{snippet}...</i>"
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"<b>[{source}]</b> {tags}\n"
        f"<b>{safe_title}</b>"
        f"{snippet}\n"
        f'<a href="{link}">Read more</a>'
    )

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}

def save_seen(seen: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PRUNE_AFTER_DAYS)).isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_FILE.write_text(json.dumps(pruned, indent=2))

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    dry_run = "--dry-run" in sys.argv
    if not dry_run and (not token or not chat_id):
        print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.")
        print("  Use --dry-run to test without sending.")
        sys.exit(1)

    seen = load_seen()
    first_run = len(seen) == 0
    if first_run:
        print("First run detected -- seeding state without sending alerts.")

    now = datetime.now(timezone.utc).isoformat()
    alerts_sent = 0

    for feed in FEEDS:
        name = feed["name"]
        url = feed["url"]
        print(f"\n[{name}]")

        data = fetch_bytes(url)
        if data is None:
            continue

        entries = parse_feed(data)
        print(f"  {len(entries)} entries parsed")

        for entry in entries:
            age_days = entry_age_days(entry)
            if age_days is not None and age_days > MAX_ITEM_AGE_DAYS:
                continue  # too old -- never alert, never remember

            key = entry_key(entry)

            if key in seen:
                continue
            seen[key] = now  # mark seen regardless of keyword match

            if first_run:
                continue  # don't alert on seed run

            categories = matched_categories(entry)
            if not categories:
                continue

            title = strip_html(entry.get("title", "(no title)"))
            link = entry.get("link", "")
            summary = entry.get("summary", "")

            print(f"  MATCH [{', '.join(categories)}]: {title[:90]}")

            if dry_run:
                print("    (dry-run, not sending)")
                alerts_sent += 1
                continue

            if alerts_sent >= MAX_ALERTS_PER_RUN:
                print(f"  Reached MAX_ALERTS_PER_RUN={MAX_ALERTS_PER_RUN}, "
                      f"skipping remaining sends for this run (still marking items seen).")
                continue

            msg = build_message(name, categories, title, link, summary)
            if send_telegram(token, chat_id, msg):
                alerts_sent += 1

    save_seen(seen)

    print()
    if first_run:
        print(f"Seeded {len(seen)} items. No alerts sent. Run again tomorrow for real alerts.")
    else:
        action = "matched (dry-run)" if dry_run else "alerts sent"
        print(f"Done -- {alerts_sent} {action}.")

if __name__ == "__main__":
    main()
