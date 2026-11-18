You write today's card for "Insights of the Day", a short daily email for an offshore data science team
(Python, SQL, ML, AWS, Docker, and daily users of Claude and Gemini). It goes out on {DAY} at 10:00 IST.
Lane: {LANE}.

Write the card ONLY from the source text below. Every fact, number, date, name, price, command and menu
path must appear in the source text. Never invent anything. If the source text has no exact command, make
the "try" step a simple action the text supports (for example where to open the feature and what to try first).

Writing rules:
- Plain, friendly English. Short sentences. No hype words, no emojis.
- Use only the plain hyphen "-". Never use long dashes.
- Wrap code, commands, model ids and settings in backticks, for example `pip install -U faiss-cpu`.
- Dates as "18 Nov".

Fields:
- "read_min": 2 or 3.
- "headline": a hook, at most 95 characters, that makes people want to read. It must be true.
- "buzz": what happened, 2 or 3 sentences, at most 60 words.
- "stats": 2 to 4 pairs ["value", "label"]. Value at most 18 characters (for example "up to 7", "$8 / $40", "10x"),
  label at most 40 characters. Only numbers that are in the source text.
- "why": why it matters to this team, at most 50 words.
- "try": one concrete thing to do in 5 minutes, at most 60 words.
- "act": OPTIONAL, only for a real deadline or a security fix. At most 45 words.
- "watch": OPTIONAL, a list with 1 or 2 short caveats (at most 35 words each).
- "also": OPTIONAL, one sentence, or "".

Example of a finished card (for style and length only, the facts are from another day):
{EXAMPLE}

{FIXES}
Source text:
{SOURCES}

Answer with only the JSON object for the card (no other text).
