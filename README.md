# Insights of the Day

Collects public AI and data updates from free sources every weekday morning, picks the most
useful one and publishes an Outlook-ready email as `docs/feed.xml`. A Power Automate flow sends
it to the team at 11:00 IST (the flow sends only the tip dated today, and checks again every
15 minutes until 13:00 if it is not there yet) with the subject INSIGHTS OF THE DAY, every day in the same mail thread.
The email has no emojis and no AI branding: just the day's theme, the date, the tip and the source.

Weekly themes: Tool Monday (AI), Prompt Tuesday (AI), Pipeline Wednesday (data engineering),
Viz Thursday (data visualisation), Frontier Friday (AI news and data science).

A tip goes out every weekday, picked in this order:
1. the day's theme sources;
2. if none of them has a useful item: a related tip (data on data days, AI on AI days);
3. then the best tip from any source;
4. if every feed is down: an evergreen tip from an official guide (Microsoft Learn, dbt, OpenAI, Anthropic...).

Only trusted sources are used. Each source in `sources.json` has a trust level: `high` (official
vendor blogs, established publications and experts), `medium` (curated newsletters, YouTube
creators, data science sites) or `low` (open forums such as Reddit and Hacker News, and unreviewed
arXiv preprints). Low-trust sources are switched off and the bot never uses them. A tip must also
be practical (a how-to, release, new feature or best practice) and never an event promo, money or
legal news, politics, a quote post or a paid review.

- `tip_bot.py` - collect, de-duplicate, score, write and render the tip
- `sources.json` - sources, trust levels, types, weights, freshness windows, time zone
- `.github/workflows/daily-tip.yml` - started by Power Automate every evening and morning (it opens a
  "Build insight" issue, which starts the build within seconds; GitHub's own timer is often hours late);
  GitHub's timer stays as a backup. Builds each tip the evening before (Sunday-Thursday, 18:47 to 22:17 IST)
  with a second chance the same morning (06:47 to 10:17 IST); skips once the tip is built
- `data/sent.json` - history of tips already sent (prevents repeats)

## Branches and security

- `main` holds the code and is protected: changes go in through a pull request with a review.
- `site` holds the built tips (`docs/`, `data/sent.json`) and is what GitHub Pages serves. The daily
  workflow checks out `site`, merges in `main`, builds the tip and pushes to `site` only.
- The workflow has no permissions by default; the build job gets `contents: write` and nothing else.
- Repository access is limited to the owner; add collaborators only if they are trusted.

No email addresses or secrets are stored in this repository.
