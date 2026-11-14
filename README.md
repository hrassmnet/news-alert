# Personalized news-alert

A daily Telegram digest of up to **10 high-relevance articles** covering frontier AI, neurotechnology and BCIs, biotechnology, AI safety, recursive self-improvement (RSI), personal AI agents, global geopolitics, **Irish affairs**, and developments at Meta, Anthropic, OpenAI, Accenture and EY.

## How it works

At 07:00 UTC daily, GitHub Actions reads a focused collection of publisher RSS feeds and topic-specific Google News RSS queries. Titles and summaries are scored for topical relevance, sorted by score, and deduplicated by normalized headlines. Known hard-paywall domains are excluded where the source URL exposes the publisher domain. This is a **best-effort** paywall filter, not a guarantee (Google News redirect links can conceal the destination).

If fewer than 10 relevant articles are found, fewer are sent. A persisted daily counter in `daily_state.json` prevents additional manual runs from exceeding 10 that UTC day.

The bot uses **no paid APIs or LLM**. Telegram tokens remain in GitHub Actions secrets.

## Configuration

Edit `FEEDS`, `KEYWORDS`, `CATEGORY_WEIGHT`, `PAYWALLED_DOMAINS`, and `MAX_ARTICLES_PER_DAY` in `news_alert.py`.

Run `python news_alert.py --dry-run` to preview matches without Telegram alerts or changing the saved history.

New stories are tracked in `seen_items.json`. The first run of an empty history silently seeds it. The scheduler runs once daily; manually invoking the workflow is optional.

## Limitations

Matching is keyword-based, not semantic. A broad topic match can be noisy and important stories may be missed. Cross-publisher duplicates with materially different headlines can survive title-based deduplication. Paywall detection is limited by RSS URLs and Google News redirects.
