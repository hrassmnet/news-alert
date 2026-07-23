# news-alert

A daily news filter that pushes to Telegram. It checks ~18 RSS feeds,
matches them against keyword categories, and sends me anything relevant.
Runs itself on GitHub Actions — no server, no dependencies.

I built it because I was checking the same set of blogs and news sites
every morning and mostly finding nothing. This does that part for me.

## What it covers

| Category | Examples |
|---|---|
| AI | DeepMind, OpenAI, Google Research, Hugging Face, Microsoft AI |
| Tech news | Ars Technica, MIT Tech Review, The Verge |
| Defence & security | Breaking Defense, Defense One, War on the Rocks |
| Aerospace | SpaceNews, NASA |
| Power Platform | Power Platform and Copilot Studio blogs |

Keyword matching is word-boundary and case-insensitive, so "AI model"
matches but "said" doesn't trip on "AI". An item can match more than one
category and gets tagged with all of them.

## How it works

Each run parses every feed (both RSS 2.0 and Atom), hashes each entry's
GUID, and skips anything already in `seen_items.json`. New entries get
keyword-matched, and matches are sent to Telegram with their category tags.

Two things worth knowing:

- **The first run stays quiet.** It records what's currently in the feeds
  without sending anything, so you don't get months of backlog dumped on
  your phone at once. Alerts start on the second run.
- **State is committed back to the repo.** GitHub Actions runners are
  wiped between runs, so `seen_items.json` gets committed at the end of
  each run. Entries older than 30 days are pruned so it doesn't grow
  forever.

There's also a cap of 25 alerts per run — if a feed dumps its whole
archive, everything still gets marked as seen, but only the first 25 send.

## Running it

Needs Python 3.12+. No packages to install — standard library only.

```bash
python news_alert.py --dry-run    # prints matches, sends nothing
```

To actually send, set two environment variables:

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python news_alert.py
```

On GitHub Actions these come from repository secrets. The workflow runs
daily at 07:00 UTC and can be triggered manually from the Actions tab.

## Changing what it watches

Feeds live in `FEEDS` and keywords in `KEYWORDS`, both at the top of
`news_alert.py`. Add a category by adding a key to the dict — nothing
else needs to change.
