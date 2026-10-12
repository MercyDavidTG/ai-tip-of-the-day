# AI Tip of the Day

Fetches public AI updates from 28 free sources every day at 09:00 IST, picks the most useful one
and publishes an Outlook-ready email as `docs/feed.xml`. A Power Automate flow sends it to the
team at 11:00 IST.

- `tip_bot.py` – collect, de-duplicate, score, summarise, render
- `sources.json` – sources, weights, time zone
- `.github/workflows/daily-tip.yml` – daily schedule (03:30 UTC = 09:00 IST)
- `data/sent.json` – history of tips already sent (prevents repeats)

No email addresses or secrets are stored in this repository.
