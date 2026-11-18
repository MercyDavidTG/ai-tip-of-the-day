You pick the story for "Insights of the Day", a short daily email for an offshore data science team
(Python, SQL, ML, AWS, Docker, and daily users of Claude and Gemini). The email goes out on {DAY} at 10:00 IST.

Below are today's fresh items from trusted feeds (published since {SINCE}). Each has a number, the source,
the kind of source (official = the company itself, press = established news site, community = blog or forum),
the title and the start of the summary.

Pick ONE item that is:
- Talk of the town: news people in the field are actually discussing, not a minor post or a case study.
- In one of these lanes: frontier (new frontier models and big model updates), data (ML, data science and
  data engineering tools), aws (AWS services, pricing), docker (Docker, containers, security fixes),
  open (open-weights models), creative (AI video, image, audio), science (AI for science),
  plans (changes to plans, limits, prices or free offers of AI tools), agents (AI agents, coding agents, MCP, automation).
- Useful: the team can try something in about 5 minutes, or must act (a deadline, a security fix).
- Trustworthy: the item is from an official source, OR at least one other item in the list (another outlet)
  covers the same story. A community item alone is never enough.
- Not a repeat of these recent insights:
{RECENT}
- Variety: avoid the lane of the last two insights unless the story is clearly the biggest of the day.

Prefer a big launch or change from a major company over a how-to blog post. Ignore webinars, customer
stories, hiring news, funding rounds and opinion pieces.

Items:
{CANDIDATES}

Answer with only this JSON (no other text):
{"pick": <number>, "same_story": [<numbers of other items about the same story, up to 2, or empty>], "lane": "<lane key>", "why": "<one short sentence>"}
If no item is good enough at all, answer only: SKIP: <reason>
