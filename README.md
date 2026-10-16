# AI Tip of the Day

Collects public AI and data updates from free sources every weekday morning, picks the most
useful one and publishes an Outlook-ready email as `docs/feed.xml`. A Power Automate flow sends
it to the team at 11:00 IST.

Weekly themes: Tool Monday (AI), Prompt Tuesday (AI), Pipeline Wednesday (data engineering),
Viz Thursday (data visualisation), Frontier Friday (AI news and data science). On Wednesday and
Thursday only that day's data sources compete; if they have nothing fresh, all sources do.

- `tip_bot.py` - collect, de-duplicate, score, write and render the tip
- `sources.json` - sources, types, weights, freshness windows, time zone
- `.github/workflows/daily-tip.yml` - weekday schedule (06:47 to 10:17 IST, skips once the tip is built)
- `data/sent.json` - history of tips already sent (prevents repeats)

No email addresses or secrets are stored in this repository.
