You pick today's TIP for "Insights of the Day", a short daily email for an offshore data science team
(Python, SQL, ML, AWS, Docker, and daily users of Claude and Gemini). The email goes out on {DAY} at 10:00 IST.

Below are today's fresh items from trusted feeds (published since {SINCE}). Each has a number, the source,
the kind of source (official = the company itself, press = established news site, community = blog or forum),
the title and the start of the summary.

The team wants a practical TIP they can use at work today, not news. A headline about a launch, a deal,
a research result or a company is not enough on its own: the item must carry something the team can DO.

Pick ONE item that is:
- A usable tip: a new feature, setting, command, API option, prompting technique, workflow trick or
  security practice, with a concrete step the team can apply. Fresh is better: new this week.
- In one of these lanes: frontier (new frontier models and big model updates), data (ML, data science and
  data engineering tools), aws (AWS services, pricing), docker (Docker, containers, security fixes),
  open (open-weights models), creative (AI video, image, audio), science (AI for science),
  plans (changes to plans, limits, prices or free offers of AI tools), agents (AI agents, coding agents, MCP, automation).
- Useful: the team can try it in about 5 minutes, or must act (a deadline, a security fix).
- Trustworthy: the item is from an official source, OR at least one other item in the list (another outlet)
  covers the same story. A community item alone is never enough.
- Not a repeat of these recent insights:
{RECENT}
- Variety: avoid the lane of the last two insights unless the story is clearly the biggest of the day.

Prefer official docs, changelogs, release notes and prompting or how-to guides with clear steps. A big
launch is fine only when the item says how to use it (a model id, a menu path, a command, a setting).
Ignore pure news with no step to take: research results, benchmarks alone, company news, lawsuits,
webinars, customer stories, hiring news, funding rounds and opinion pieces.

Items:
{CANDIDATES}

Answer with only this JSON (no other text):
{"pick": <number>, "same_story": [<numbers of other items about the same story, up to 2, or empty>], "lane": "<lane key>", "why": "<one short sentence>"}
If no item is good enough at all, answer only: SKIP: <reason>
