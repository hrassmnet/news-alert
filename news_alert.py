#!/usr/bin/env python3
"""
Focused daily news alert -- ranked topic filtering, no LLM, Telegram output.
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

# Focused sources plus topic-specific Google News RSS searches.
# Google News provides broad coverage without scraping subscriber-only articles.
def news_search(query: str) -> str:
    from urllib.parse import quote
    return "https://news.google.com/rss/search?q=" + quote(query) + "&hl=en-IE&gl=IE&ceid=IE:en"

FEEDS = [
    {"name": "DeepMind", "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "OpenAI", "url": "https://openai.com/blog/rss.xml"},
    {"name": "Google Research", "url": "https://research.google/blog/rss/"},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "AI Safety", "url": news_search('AI alignment OR mechanistic interpretability OR AI evaluations OR "AI safety"')},
    {"name": "AI Agents & RSI", "url": news_search('"AI agents" OR "computer use" OR "recursive self-improvement" OR "AI scientist"')},
    {"name": "Neurotechnology", "url": news_search('"brain computer interface" OR "neural implant" OR "neural decoding" OR neurotechnology')},
    {"name": "Biotechnology", "url": news_search('"gene editing" OR "synthetic biology" OR biocomputing OR "longevity research"')},
    {"name": "World Affairs", "url": news_search('geopolitics OR "US China" OR NATO OR "peace talks" OR sanctions')},
    {"name": "Irish Affairs", "url": news_search('Ireland government OR "Irish economy" OR "Dáil" OR "Irish housing" OR "IDA Ireland"')},
    {"name": "Frontier Companies", "url": news_search('Anthropic OR OpenAI OR "Meta AI" OR "Meta Llama"')},
    {"name": "Consulting Companies", "url": news_search('Accenture AI OR "EY technology" OR "EY artificial intelligence"')},
]

# ---------------------------------------------------------------------------
# Keyword categories (word-boundary matched, case-insensitive)
# ---------------------------------------------------------------------------

# Specific phrases are preferred over generic triggers such as "AI" or "benchmark".
KEYWORDS: dict[str, list[str]] = {
    "AI": ["artificial intelligence", "large language model", "foundation model",
           "frontier model", "reasoning model", "multimodal model", "deep learning",
           "machine learning", "neural network", "GPT", "Claude", "Gemini",
           "DeepSeek", "Qwen", "Llama", "OpenAI", "Anthropic", "DeepMind"],
    "Neurotechnology": ["neurotechnology", "brain-computer interface", "brain computer interface",
           "BCI", "neural implant", "neural decoding", "neural prosthesis",
           "neural stimulation", "neuroprosthetic", "neuroimaging", "Neuralink", "Synchron"],
    "Biotechnology": ["biotechnology", "biotech", "gene editing", "CRISPR",
           "synthetic biology", "biocomputing", "organoid", "cell therapy",
           "gene therapy", "longevity research", "protein design"],
    "AI_Safety": ["AI safety", "AI alignment", "mechanistic interpretability",
           "interpretability", "model evaluation", "AI evaluation",
           "reward hacking", "scalable oversight", "model control", "misalignment"],
    "RSI_AGI": ["recursive self-improvement", "self improving AI", "self-improving AI",
           "AI scientist", "automated AI research", "autonomous AI research",
           "artificial general intelligence", "AGI", "AI R&D automation"],
    "Personal_Agents": ["AI agent", "agentic AI", "personal AI", "personal agent",
           "computer use", "computer-using", "autonomous assistant",
           "agent memory", "persistent memory", "MCP", "model context protocol"],
    "Geopolitics": ["geopolitics", "US-China", "U.S.-China", "Taiwan Strait", "NATO",
           "Ukraine war", "Russia Ukraine", "Middle East conflict",
           "sanctions", "peace negotiations", "trade war", "semiconductor export controls"],
    "Irish_Affairs": ["Ireland", "Irish government", "Dáil", "Taoiseach",
           "Irish economy", "Irish housing", "IDA Ireland", "Irish budget",
           "Irish election", "Irish infrastructure", "Irish data centre"],
    "Companies": ["Meta AI", "Meta Llama", "Anthropic", "OpenAI", "Accenture", "EY",
           "Ernst & Young"],
}

CATEGORY_WEIGHT = {
    "AI": 3, "Neurotechnology": 5, "Biotechnology": 4,
    "AI_Safety": 5, "RSI_AGI": 5, "Personal_Agents": 5,
    "Geopolitics": 4, "Irish_Affairs": 4, "Companies": 2,
}
MAX_ARTICLES_PER_DAY = 10
DAILY_STATE_FILE = Path("daily_state.json")

# Known hard-paywall domains; this is a best-effort filter, not a paywall detector.
PAYWALLED_DOMAINS = {
    "ft.com", "wsj.com", "bloomberg.com", "economist.com",
    "theinformation.com", "barrons.com", "businessinsider.com",
}


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

SEEN_FILE = Path("seen_items.json")
PRUNE_AFTER_DAYS = 30
MAX_ITEM_AGE_DAYS = 14  # ignore entries older than this, even if never seen before
MAX_ALERTS_PER_RUN = MAX_ARTICLES_PER_DAY

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
# Ranking, filtering, and story deduplication
# ---------------------------------------------------------------------------

def paywalled(link: str) -> bool:
    from urllib.parse import urlparse
    domain = (urlparse(link).hostname or "").lower()
    return any(domain == d or domain.endswith("." + d) for d in PAYWALLED_DOMAINS)

def story_signature(title: str) -> str:
    # Drop punctuation and common noise words to consolidate similar headlines.
    words = re.findall(r"[a-z0-9]+", title.lower())
    stop = {"the", "a", "an", "in", "on", "of", "to", "for", "and", "with", "as",
            "says", "report", "reports", "new", "latest"}
    return " ".join(w for w in words if w not in stop)[:180]

def relevance_score(entry: dict, categories: list[str]) -> int:
    title = strip_html(entry.get("title", ""))
    score = max(CATEGORY_WEIGHT[c] for c in categories)
    # A topic in the headline is more meaningful than an incidental summary mention.
    score += sum(3 for c in categories
                 if any(_matches(title, term) for term in KEYWORDS[c]))
    if any(_matches(title, term) for term in
           ["breakthrough", "clinical trial", "peer reviewed", "research",
            "launches", "releases", "major", "agreement", "election",
            "investment", "acquisition"]):
        score += 2
    if len(categories) > 1:
        score += 1
    if any(_matches(title, term) for term in
           ["opinion", "sponsored", "advertisement", "rumour", "rumor"]):
        score -= 6
    return score

def load_daily_state() -> dict:
    try:
        return json.loads(DAILY_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"date": "", "sent": 0}

def save_daily_state(date: str, count: int) -> None:
    DAILY_STATE_FILE.write_text(json.dumps({"date": date, "sent": count}, indent=2) + "\n")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    dry_run = "--dry-run" in sys.argv
    if not dry_run and (not token or not chat_id):
        print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        sys.exit(1)

    seen = load_seen()
    first_run = not seen
    today = datetime.now(timezone.utc).date().isoformat()
    state = load_daily_state()
    sent_today = int(state.get("sent", 0)) if state.get("date") == today else 0
    available = max(0, MAX_ARTICLES_PER_DAY - sent_today)
    now = datetime.now(timezone.utc).isoformat()
    candidates = []

    for feed in FEEDS:
        print(f"Checking {feed['name']}")
        data = fetch_bytes(feed["url"])
        if data is None:
            continue
        for entry in parse_feed(data):
            age = entry_age_days(entry)
            if age is not None and (age > 2 or age < -1):
                continue
            key = entry_key(entry)
            if key in seen:
                continue
            if not dry_run:
                seen[key] = now  # prevent old backlog and repeat alerts
            if first_run:
                continue
            categories = matched_categories(entry)
            if not categories or paywalled(entry.get("link", "")):
                continue
            title = strip_html(entry.get("title", ""))
            score = relevance_score(entry, categories)
            if score < 5:
                continue
            candidates.append((score, title, feed["name"], categories, entry))

    candidates.sort(key=lambda x: (-x[0], x[1].lower()))
    chosen = []
    story_ids = set()
    for score, title, source, categories, entry in candidates:
        signature = story_signature(title)
        if signature in story_ids:
            continue
        story_ids.add(signature)
        chosen.append((score, title, source, categories, entry))

    alerts_sent = 0
    if first_run:
        print(f"First run: seeded {len(seen)} entries, no alerts.")
    else:
        for score, title, source, categories, entry in chosen[:available]:
            print(f"MATCH score={score} [{', '.join(categories)}]: {title}")
            if dry_run:
                alerts_sent += 1
                continue
            message = build_message(source, categories, title, entry.get("link", ""),
                                    entry.get("summary", ""))
            if send_telegram(token, chat_id, message):
                alerts_sent += 1
    if not dry_run:
        save_seen(seen)
        save_daily_state(today, sent_today + alerts_sent)
    print(f"Done: {alerts_sent} alerts; daily cap {MAX_ARTICLES_PER_DAY}.")


if __name__ == "__main__":
    main()
