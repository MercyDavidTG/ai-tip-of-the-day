#!/usr/bin/env python3
"""AI Tip of the Day: collects public AI and data updates, picks the best one and renders an Outlook-ready email.

Usage
  python tip_bot.py                  build docs/latest.html + docs/feed.xml (cloud mode, read by Power Automate)
  python tip_bot.py --send-outlook   build, then send it with the Outlook desktop app (local mode)
  python tip_bot.py --check          test every source in sources.json and exit
  python tip_bot.py --dry-run        build preview.html only; history is not updated
  python tip_bot.py --day 3          pretend it is Thursday (0=Mon .. 4=Fri), handy for testing themes

Privacy: this script never reads a mailbox and never stores recipient addresses.
In local mode the recipients come only from the TIP_TO / TIP_BCC environment variables on your own PC.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
HISTORY_FILE = ROOT / "data" / "sent.json"
OUT_DIR = ROOT / "docs"
NOW = dt.datetime.now(dt.timezone.utc)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AI-Tip-of-the-Day/1.0; internal newsletter)"}
MAX_ENTRIES_PER_FEED = 40
HISTORY_LIMIT = 1000
TEAM_PICK_SLOT = "<!--TEAM_PICK-->"  # Power Automate may replace this with an approved team submission

# One theme per weekday (tips go out Monday-Friday only). The tip always comes from the day's source types first;
# only when none of them has a useful, trusted item does the bot fall back: first to a related tip (data on data
# days, AI on AI days), then to the best tip from any source, and if even that fails to an evergreen tip
# (EVERGREEN below), so every weekday gets a tip.
# "think" and "try" are the fallback brainstorm question and 5-minute action when no topic below matches.
THEMES = {
    0: {"name": "Tool Monday", "types": ["official", "newsletter"],
        "keywords": ["launch", "introducing", "now available", "new feature", "rolls out", "released",
                     "docs", "documentation", "best practices", "cookbook", "guide"],
        "think": "Which tool in your daily workflow is overdue for an AI upgrade?",
        "try": "Try this new feature on one real task before lunch."},
    1: {"name": "Prompt Tuesday", "types": ["community", "newsletter"],
        "keywords": ["prompt", "prompts", "how to", "tips", "guide", "tutorial", "workflow"],
        "think": "Which prompt do you type every week that could become a reusable template?",
        "try": "Rewrite one prompt you use often as: role + goal + format + one example."},
    2: {"name": "Pipeline Wednesday", "types": ["dataeng"],
        "keywords": ["sql", "dbt", "spark", "pipeline", "pipelines", "etl", "elt", "data quality", "lakehouse",
                     "warehouse", "orchestration", "streaming", "best practices", "how to", "guide"],
        "think": "If your main pipeline failed tonight, how long until someone noticed?",
        "try": "Check that one key job alerts on failure and gives the same result when rerun."},
    3: {"name": "Viz Thursday", "types": ["dataviz"],
        "keywords": ["chart", "charts", "dashboard", "dashboards", "visualization", "visualisation", "power bi",
                     "dax", "report", "storytelling", "design", "how to", "tips"],
        "think": "What's the one number your audience should remember from your last chart?",
        "try": "Retitle one chart so the title states the finding, and grey out everything but the key series."},
    4: {"name": "Frontier Friday", "types": ["news", "datasci"],
        "keywords": ["breakthrough", "research", "benchmark", "model", "study", "machine learning"],
        "think": "If this becomes normal within a year, what changes for our team?",
        "try": "Discuss it with one colleague for 5 minutes: threat, opportunity, or both?"},
}
DATA_TYPES = {"dataeng", "dataviz", "datasci"}
WEEKEND_THEME = {"name": "AI Tip", "types": [], "keywords": [],
                 "think": THEMES[4]["think"], "try": THEMES[4]["try"]}  # only used for manual weekend runs

# Practical, business-friendly topics score higher; funding/legal gossip scores lower.
KEYWORDS = {
    "how to": 4, "tip": 4, "tips": 4, "guide": 3, "tutorial": 3, "prompt": 3, "prompts": 3, "workflow": 3,
    "copilot": 4, "outlook": 3, "excel": 3, "powerpoint": 3, "chatgpt": 3, "claude": 3, "gemini": 3,
    "agent": 2, "agents": 2, "automation": 3, "automate": 3, "productivity": 3, "free": 2,
    "open source": 2, "introducing": 3, "launch": 2, "now available": 3,
}
PENALTIES = {
    "lawsuit": -5, "sues": -5, "funding": -4, "raises": -3, "valuation": -4, "layoffs": -5,
    "stock": -3, "sponsored": -8, "earnings": -4, "ipo": -4, "help": -6, "eli5": -4,
    "failures": -6, "outage": -6, "bug": -4, "complaint": -6, "refund": -6, "frustrated": -5,
}
# Usefulness: the main tip must be practical (a how-to, a release, a new feature, a best practice) and never
# an event promo, money or legal news, politics, a short quote post or a paid review. Low-trust sources (open
# forums and unreviewed preprints) are never used, not even under "Also new".
USEFUL = re.compile(
    r"\b(how to|how we|how i|guide|tips?|tutorial|best practices?|hands-on|step[- ]by[- ]step|cheat ?sheet|"
    r"templates?|checklist|lessons|playbook|roadmap|introducing|announcing|launch(es|ed)?|now available|"
    r"generally available|preview|new features?|feature summary|what's new|release notes|rolls? out|released|"
    r"build(ing)?|automat(e|es|ed|ing|ion)|workflows?|prompts?|faster|optimi[sz](e|ing|ation)|reduce|cut)\b", re.I)
LOW_VALUE = re.compile(
    r"\b(^quoting|save up to|early bird|early booking|tickets?|register now|webinar|funding|raises|valuation|ipo|"
    r"investors?|revenue|earnings|stocks?|layoffs?|lawsuit|sues|arrested|trump|election|president|war|"
    r"sponsored|celebrate|partners? with|partnership|honest review|discount)\b", re.I)
MIN_QUALITY = 12  # quality (score without the +15 theme-source boost) below which an item is too weak or old to send
GENERIC = ("How could this change the way you work in the next month?",
           "Spend 5 minutes on the source and note one idea you can use this week.")
# Brainstorm bank: the first topic whose words appear in the tip supplies the "Think" question and "Try" action.
TOPICS = [
    (["agent", "agents", "agentic", "autonomous"],
     "Which multi-step task in your week could an AI agent run end-to-end while you just review?",
     "Pick one 5-step task you repeat weekly and ask your AI assistant to design an agent for it."),
    (["code", "coding", "developer", "developers", "codex", "api", "github"],
     "What small internal tool would you build if coding took minutes, not days?",
     "Describe a tool you wish existed and ask an AI to outline how to build it."),
    (["copilot", "excel", "outlook", "powerpoint", "microsoft 365"],
     "Which Office task eats 30 minutes of your week that Copilot could draft in 30 seconds?",
     "Ask Copilot to draft or summarise one real email, sheet or deck from today."),
    (["prompt", "prompts", "prompting"],
     "If you had to brief an AI on your job in 3 sentences, what would you say first?",
     "Rewrite one prompt you use often as: role + goal + format + one example."),
    (["voice", "speech", "audio", "translate", "translation"],
     "Which conversation at work would change if language were no barrier?",
     "Try a 2-minute live voice or translation chat in an AI app."),
    (["image", "images", "visual", "visuals", "design", "photo"],
     "Where could a 30-second AI visual replace a 30-minute design request?",
     "Generate one diagram or slide visual for a task you're working on today."),
    (["security", "trust", "safety", "privacy", "guardrail", "guardrails", "zero-trust", "breach", "risk", "risks"],
     "What would you never paste into an AI tool, and does your team agree?",
     "Open your AI tool's data settings and check what is stored or shared."),
    (["data", "analytics", "spreadsheet", "sql", "dashboard", "insights"],
     "Which question about your data do you skip because it takes too long to answer?",
     "Give an AI a small anonymised table and ask for 3 insights and 1 chart idea."),
    (["search", "browser", "web"],
     "How much of your daily searching could become one answer with sources?",
     "Ask one real work question in an AI search tool and check its sources."),
    (["memory", "context", "remember"],
     "What would you ask an assistant that remembered every project detail you shared?",
     "Give an AI 5 bullets about a current project and ask what risks you're missing."),
    (["research", "paper", "benchmark", "reasoning", "model", "models"],
     "As AI reasoning improves every month, which of your decisions should it challenge first?",
     "Ask an AI to argue against a decision you made this week and note one new point."),
]
# Brainstorm bank for data sources (data engineering, visualisation, data science), used the same way.
DATA_TOPICS = [
    (["dbt", "test", "tests", "quality", "observability", "validation", "contract", "contracts"],
     "Which table would hurt most if bad data reached it unnoticed?",
     "Add one not-null or unique test on a key column of that table."),
    (["spark", "performance", "optimize", "optimise", "partition", "partitioning", "cost", "costs", "query", "queries"],
     "Which query or job is the slowest or most expensive one you own?",
     "Run EXPLAIN on it and note one step where you could filter or partition earlier."),
    (["pipeline", "pipelines", "etl", "elt", "orchestration", "airflow", "ingestion", "streaming", "kafka", "cdc"],
     "If your main pipeline failed tonight, how long until someone noticed?",
     "Check that one key job alerts on failure and gives the same result when rerun."),
    (["lakehouse", "warehouse", "delta", "iceberg", "fabric", "snowflake", "databricks", "catalog", "governance"],
     "Which dataset do people copy into Excel because they can't find the trusted version?",
     "Write a one-line description, grain and owner for one table you use often."),
    (["power bi", "dax", "semantic model", "report", "reports", "tableau", "looker"],
     "Which report do people open every day but never act on?",
     "Turn one calculated column into a DAX measure, or remove one unused visual."),
    (["dashboard", "dashboards", "kpi", "kpis", "metric", "metrics"],
     "What decision is your main dashboard supposed to drive?",
     "Put the most important KPI top-left on one dashboard and remove one low-value tile."),
    (["chart", "charts", "visualization", "visualisation", "dataviz", "graph", "graphs", "map", "maps",
      "color", "colour", "design"],
     "What's the one number your audience should remember from your last chart?",
     "Retitle one chart so the title states the finding, and grey out everything but the key series."),
    (["story", "storytelling", "presentation", "presenting", "slides", "communicate"],
     "Could a non-analyst explain your last analysis in one sentence?",
     "Write the one-sentence takeaway before you build your next chart."),
    (["model", "models", "machine learning", "ml", "forecast", "forecasting", "llm", "ai", "agent", "agents"],
     "How would you know if your model is actually beating a simple baseline?",
     "Score a last-value or mean baseline next to your model on the same data."),
    (["python", "pandas", "polars", "notebook", "notebooks", "sql"],
     "Which manual data step do you repeat every week?",
     "Script one repeated cleaning step in SQL or pandas and save it in the team repo."),
]
# Evergreen tips: official guides, used only when no feed has a useful item for the day (for example a network
# outage). Each is sent once before any repeats; after that the least recently sent one comes back.
# (title, link, source, one-line update, think, try)
EVERGREEN = {
    "Tool Monday": [
        ("What's new in Microsoft 365 Copilot", "https://learn.microsoft.com/en-us/microsoft-365/copilot/release-notes",
         "Microsoft Learn", "Microsoft lists every new Copilot feature for Word, Excel, Outlook, Teams and PowerPoint here.",
         "Which Copilot feature released this month could save you time this week?",
         "Open the release notes, pick one feature for an app you use daily and try it on a real task."),
        ("ChatGPT release notes", "https://help.openai.com/en/articles/6825453-chatgpt-release-notes",
         "OpenAI Help Center", "OpenAI's running list of new ChatGPT features, models and settings.",
         "Is there a ChatGPT feature your team pays for but nobody uses yet?",
         "Skim the last two weeks of notes and try one new feature on today's work."),
        ("Best practices for using GitHub Copilot", "https://docs.github.com/en/copilot/get-started/best-practices",
         "GitHub Docs", "GitHub's guide to what Copilot is good at, what it is not, and how to check its suggestions.",
         "Which repetitive bit of code or SQL do you still type by hand?",
         "Ask Copilot to write that snippet from a comment describing the goal, then review it line by line."),
    ],
    "Prompt Tuesday": [
        ("Prompt engineering guide", "https://developers.openai.com/api/docs/guides/prompt-engineering",
         "OpenAI Docs", "OpenAI's official guide to clear instructions, examples and output formats in prompts.",
         "Which prompt do you type every week that could become a reusable template?",
         "Rewrite one prompt you use often as: role + goal + format + one example."),
        ("Prompt engineering overview", "https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview",
         "Anthropic Docs", "Anthropic's step-by-step techniques for getting better answers from Claude.",
         "Where do you get vague answers because the AI does not know enough context?",
         "Add a short context block (who you are, what the output is for) to one prompt and compare the answers."),
        ("Get started writing prompts in Microsoft Copilot",
         "https://support.microsoft.com/en-us/microsoft-365-copilot/get-started-writing-prompts-in-microsoft-365-copilot",
         "Microsoft Support", "Microsoft's guide to goal, context, source and expectations in a Copilot prompt.",
         "If you had to brief an AI on your job in 3 sentences, what would you say first?",
         "Write one Copilot prompt with all four parts: goal, context, source and expectations."),
        ("Google Workspace with Gemini prompt guide", "https://workspace.google.com/learning/content/gemini-prompt-guide",
         "Google Workspace", "Google's prompt examples for Gmail, Docs, Sheets and Meet, grouped by job role.",
         "Which email or document do you write from scratch that an AI could draft for you?",
         "Find your role in the guide and adapt one example prompt to a task you have today."),
    ],
    "Pipeline Wednesday": [
        ("Add data tests to your DAG", "https://docs.getdbt.com/docs/build/data-tests",
         "dbt Docs", "How to add not-null, unique, accepted-values and relationship tests to dbt models.",
         "Which table would hurt most if bad data reached it unnoticed?",
         "Add one not-null or unique test on a key column of that table."),
        ("How we structure our dbt projects", "https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview",
         "dbt Docs", "dbt Labs' recommended layout of staging, intermediate and mart models.",
         "Could a new joiner find where a metric is calculated in your project in under 5 minutes?",
         "Check one model's name and folder against the guide and rename it if it does not fit."),
        ("Medallion lakehouse architecture in Fabric",
         "https://learn.microsoft.com/en-us/fabric/onelake/onelake-medallion-lakehouse-architecture",
         "Microsoft Learn", "How to organise raw, cleaned and business-ready data as bronze, silver and gold layers.",
         "Do people query your raw data directly because the clean version is missing something?",
         "Map one pipeline you own to bronze, silver and gold and note the layer that is missing."),
        ("Spark SQL performance tuning", "https://spark.apache.org/docs/latest/sql-performance-tuning.html",
         "Apache Spark Docs", "The official tips on caching, partitions, join hints and adaptive query execution.",
         "Which query or job is the slowest or most expensive one you own?",
         "Run EXPLAIN on it and note one step where you could filter or partition earlier."),
    ],
    "Viz Thursday": [
        ("Star schema and why it matters for Power BI", "https://learn.microsoft.com/en-us/power-bi/guidance/star-schema",
         "Microsoft Learn", "Why fact and dimension tables make Power BI models faster and easier to use.",
         "Is any of your reports built on one wide flat table?",
         "Split one dimension, such as product or customer, out of a flat table in a model you own."),
        ("Use variables to improve your DAX formulas", "https://learn.microsoft.com/en-us/dax/best-practices/dax-variables",
         "Microsoft Learn", "How VAR makes DAX measures faster, shorter and easier to debug.",
         "Which DAX measure do you avoid touching because it is hard to read?",
         "Rewrite one repeated expression in that measure as a VAR and check the result matches."),
        ("Tips for designing a great Power BI dashboard",
         "https://learn.microsoft.com/en-us/power-bi/create-reports/service-dashboards-design-tips",
         "Microsoft Learn", "Microsoft's guidance on audience, layout, the most important number and visual choice.",
         "What decision is your main dashboard supposed to drive?",
         "Put the most important KPI top-left on one dashboard and remove one low-value tile."),
        ("FT Visual Vocabulary", "https://github.com/Financial-Times/chart-doctor/tree/main/visual-vocabulary",
         "Financial Times", "The Financial Times' one-page guide to choosing a chart by the message you want to show.",
         "What's the one number your audience should remember from your last chart?",
         "Check your last chart against the guide and switch the chart type if another fits the message better."),
    ],
    "Frontier Friday": [
        ("Building effective AI agents", "https://www.anthropic.com/engineering/building-effective-agents",
         "Anthropic", "Anthropic's practical patterns for agents, and when a simple workflow is the better choice.",
         "Which multi-step task in your week could an AI agent run end-to-end while you just review?",
         "Pick one 5-step task you repeat weekly and sketch it as one of the patterns in the article."),
        ("Common pitfalls and recommended practices", "https://scikit-learn.org/stable/common_pitfalls.html",
         "scikit-learn Docs", "How data leakage and inconsistent preprocessing quietly inflate model results.",
         "How would you know if your model is actually beating a simple baseline?",
         "Check that your latest model fits its preprocessing on training data only, inside a pipeline."),
        ("The AI Index report", "https://hai.stanford.edu/ai-index",
         "Stanford HAI", "Stanford's yearly, data-backed view of AI progress, cost, adoption and policy.",
         "If this becomes normal within a year, what changes for our team?",
         "Read one chapter summary and share the chart that surprised you most with a colleague."),
    ],
}
CTA = {"video": "Watch", "research": "Read the paper"}
AI_TERMS = re.compile(r"\b(ai|a\.i\.|llms?|gpt\S*|chatgpt|claude|gemini|copilot|openai|anthropic|deepmind|agents?|agentic|models?|machine learning|neural|genai|generative|prompts?|mcp|codex|chatbots?|assistants?)\b", re.I)
BOILERPLATE = [
    r"The post .*? appeared first on .*?\.", r"arXiv:\S+\s+Announce Type:\s*\S+\s*Abstract:\s*",
    r"submitted by\s+/u/\S+", r"\[link\]|\[comments\]", r"(Read|Continue reading|Read more)[^.]*$",
    r"(Article URL|Comments URL):\s*\S+", r"Points:\s*\d+", r"#\s*Comments:\s*\d+",  # Hacker News feed metadata
    r"^(In this (post|article|blog post|video),?\s*)", r"^This (blog )?post (explores|covers|shows|explains|describes)\s+",
]



def log(message: str) -> None:
    print(message, flush=True)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def clean_text(raw: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s*[\u2013\u2014]\s*", " - ", html.unescape(text))  # en/em dashes become hyphens
    return re.sub(r"\s+", " ", text).strip()


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def has_word(text: str, word: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\b", text) is not None


# ---------------------------------------------------------------- collect

def fetch_feed(url: str):
    for attempt in (1, 2):  # one retry: YouTube and Reddit feeds fail intermittently
        resp = requests.get(url, headers=HEADERS, timeout=25)
        if resp.ok or attempt == 2:
            resp.raise_for_status()
            return feedparser.parse(resp.content)
        time.sleep(3)


def find_image(entry, summary_html: str) -> str:
    if entry.get("yt_videoid"):
        return f"https://i.ytimg.com/vi/{entry['yt_videoid']}/hqdefault.jpg"
    for thumb in entry.get("media_thumbnail") or []:
        if thumb.get("url"):
            return thumb["url"]
    for media in entry.get("media_content") or []:
        url = media.get("url", "")
        if media.get("medium") == "image" or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I):
            return url
    for enclosure in entry.get("enclosures") or []:
        if str(enclosure.get("type", "")).startswith("image/") and enclosure.get("href"):
            return enclosure["href"]
    match = re.search(r"<img[^>]+src=[\"']([^\"']+)", summary_html or "", re.I)
    return match.group(1) if match else ""


def to_item(entry, src: dict) -> dict:
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    published = dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc) if stamp else None
    summary_html = entry.get("summary") or (entry.get("content") or [{}])[0].get("value", "")
    return {
        "title": clean_text(entry.get("title", "")),
        "link": (entry.get("link") or "").strip(),
        "summary": clean_text(summary_html)[:1500],
        "image": find_image(entry, summary_html),
        "published": published,
        "source": src["name"],
        "type": src.get("type", "news"),
        "weight": float(src.get("weight", 1.0)),
        "trust": src.get("trust", "medium"),  # high = official or established, medium = curated, low = open forums
        "max_age_hours": src.get("max_age_hours"),  # weekly blogs can allow older posts than the default
    }


def collect(sources: list[dict]) -> list[dict]:
    items = []
    for src in sources:
        if not src.get("enabled", True):
            continue
        try:
            feed = fetch_feed(src["url"])
        except Exception as exc:  # one broken source must never stop the daily tip
            log(f"  ! {src['name']}: {exc}")
            continue
        for entry in feed.entries[:MAX_ENTRIES_PER_FEED]:
            item = to_item(entry, src)
            if item["title"] and item["link"].startswith("http"):
                items.append(item)
    return items


def check_sources(sources: list[dict]) -> int:
    failures = 0
    for src in sources:
        if not src.get("enabled", True):
            log(f"  --    {src['name']} (disabled)")
            continue
        try:
            count = len(fetch_feed(src["url"]).entries)
            failures += count == 0
            log(f"  {'OK   ' if count else 'EMPTY'} {src['name']}: {count} items")
        except Exception as exc:
            failures += 1
            log(f"  FAIL  {src['name']}: {exc}")
    log(f"{failures} source(s) need attention." if failures else "All sources are healthy.")
    return 1 if failures else 0


# ---------------------------------------------------------------- rank

def score(item: dict, max_age_hours: float) -> float | None:
    """Base quality of an item on any day: source weight, freshness, practical words, penalties."""
    max_age_hours = float(item.get("max_age_hours") or max_age_hours)
    if item["published"]:
        age_hours = (NOW - item["published"]).total_seconds() / 3600
    else:
        age_hours = max_age_hours / 2
    if age_hours > max_age_hours or age_hours < -24:
        return None
    text = f"{item['title']} {item['summary'][:400]}".lower()
    points = item["weight"] * 10
    points += max(0.0, 48 - age_hours) / 4  # freshness: up to +12
    points += min(12, sum(v for k, v in KEYWORDS.items() if has_word(text, k)))
    points += sum(v for k, v in PENALTIES.items() if has_word(text, k))
    if item["title"].rstrip().endswith("?"):  # questions and opinion pieces rarely make good tips
        points -= 5
    if item["type"] not in DATA_TYPES and not AI_TERMS.search(text):  # general tech posts from AI sources are not AI tips
        points -= 25
    return round(points, 2)


def theme_words(item: dict, theme: dict) -> int:
    text = f"{item['title']} {item['summary'][:400]}".lower()
    return 4 * sum(1 for k in theme["keywords"] if has_word(text, k))


def useful(item: dict, theme: dict) -> bool:
    text = f"{item['title']} {item['summary'][:400]}".lower()
    if LOW_VALUE.search(text):
        return False
    return bool(USEFUL.search(text)) or any(has_word(text, k) for k in theme["keywords"])


def can_lead(item: dict) -> bool:
    """Only strong, non-question items may be the main tip or an "Also new" link."""
    return item["quality"] >= MIN_QUALITY and not item["title"].rstrip().endswith("?")


def candidates(items: list[dict], history: list[dict], theme: dict, max_age_hours: float) -> list[dict]:
    """Fresh, trusted, never sent before and useful: scored and sorted best first."""
    sent_links = {h["link"] for h in history}
    sent_titles = {norm_title(h["title"]) for h in history}
    seen, ranked = set(), []
    for item in items:
        key = norm_title(item["title"])
        if (item["link"] in sent_links or key in sent_titles or key in seen or item["trust"] == "low"
                or not useful(item, theme)):
            continue
        points = score(item, max_age_hours)
        if points is None:
            continue
        seen.add(key)
        item["quality"] = round(points + theme_words(item, theme), 2)  # the day's topic words count as quality
        item["score"] = round(item["quality"] + (15 if item["type"] in theme["types"] else 0), 2)
        ranked.append(item)
    ranked.sort(key=lambda i: i["score"], reverse=True)
    return ranked


def evergreen(theme: dict, history: list[dict]) -> dict:
    """Last resort: an official guide for the day's theme, unsent ones first, then the least recently sent."""
    bank = EVERGREEN.get(theme["name"]) or [tip for tips in EVERGREEN.values() for tip in tips]
    last_sent = {h["link"]: h["date"] for h in history}
    title, link, source, summary, think, action = min(bank, key=lambda tip: last_sent.get(tip[1], ""))
    return {"title": title, "link": link, "summary": summary, "image": "", "published": None, "source": source,
            "type": (theme["types"] or ["official"])[0], "weight": 1.0, "trust": "high", "quality": 0.0, "score": 0.0,
            "think": think, "try": action}


def pick(items: list[dict], history: list[dict], theme: dict, max_age_hours: float):
    """Theme first, then the same family (data or AI), then any trusted source, then an evergreen tip: never empty."""
    pool = candidates(items, history, theme, max_age_hours)
    on_theme = [i for i in pool if i["type"] in theme["types"]]
    data_day = bool(theme["types"]) and set(theme["types"]) <= DATA_TYPES
    related = [i for i in pool if (i["type"] in DATA_TYPES) == data_day]
    main, level = None, ""
    for level, group in (("on theme", on_theme), ("fallback: related " + ("data" if data_day else "AI") + " tip", related),
                         ("fallback: best tip from any source", pool)):
        main = next((i for i in group if can_lead(i)), None)
        if main:
            break
    if not main:
        main, level = evergreen(theme, history), "fallback: evergreen tip"
    extras, used_sources = [], {main["source"]}
    for item in on_theme + pool:  # "Also new" links: the day's theme first
        if item["source"] not in used_sources and item["link"] != main["link"] and can_lead(item):
            extras.append(item)
            used_sources.add(item["source"])
        if len(extras) == 3:
            break
    return main, extras, level


OG_PATTERNS = [
    r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)",
    r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']",
    r"<meta[^>]+name=[\"']twitter:image[\"'][^>]+content=[\"']([^\"']+)",
]


def page_image(url: str) -> str:
    """Fetch the article's preview image (og:image) when the feed has none."""
    try:
        head = requests.get(url, headers=HEADERS, timeout=15).text[:300_000]
    except Exception:
        return ""
    for pattern in OG_PATTERNS:
        match = re.search(pattern, head, re.I)
        if match:
            found = urljoin(url, html.unescape(match.group(1)))
            return found if found.startswith("https://") else ""
    return ""


# ---------------------------------------------------------------- write

def shorten(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.rstrip()
    return " ".join(words[:max_words]).rstrip(",;:-") + "…"


def first_sentence(summary: str, title: str) -> str:
    """One crisp sentence from the feed summary: boilerplate removed, at most 24 words."""
    text = summary
    for pattern in BOILERPLATE:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -:")
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        # skip fragments, questions, first-person chatter and sentences that only repeat the headline
        if (len(sentence.split()) < 6 or sentence.endswith("?") or norm_title(sentence) == norm_title(title)
                or re.match(r"(i|i'm|i’m|i've|i’ve|my|we|we're|we’re)\b", sentence, re.I)):
            continue
        sentence = sentence[0].upper() + sentence[1:]
        return shorten(sentence, 24)
    return ""


def brainstorm(item: dict, theme: dict) -> tuple[str, str]:
    if item["type"] == "video":  # videos get the "watch and share" prompts
        return theme["think"], theme["try"]
    topics = DATA_TOPICS if item["type"] in DATA_TYPES else TOPICS
    # the headline decides first; the summary is only a fallback
    for text in (item["title"].lower(), item["summary"][:600].lower()):
        for words, think, action in topics:
            if any(has_word(text, w) for w in words):
                return think, action
    # the day's own prompts only fit when the item matches the day (e.g. a dbt post on Pipeline Wednesday)
    return (theme["think"], theme["try"]) if item["type"] in theme["types"] else GENERIC


def tip_copy(item: dict, theme: dict) -> dict:
    think, action = (item["think"], item["try"]) if item.get("think") else brainstorm(item, theme)
    personal_post = "reddit.com" in item["link"]  # Reddit summaries are personal stories, not news
    return {
        "headline": shorten(item["title"], 14),
        "update": "" if personal_post else first_sentence(item["summary"], item["title"]),
        "think": think,
        "try_this": action,
    }


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def render_email(main: dict, copy: dict, extras: list[dict], theme: dict, today: dt.datetime,
                 submit_url: str) -> tuple[str, str]:
    def e(value: str) -> str:  # no emojis anywhere in the email, even when a source title has one
        return html.escape(" ".join(EMOJI.sub("", value or "").split()), quote=True)

    headline = " ".join(EMOJI.sub("", copy["headline"]).split())
    subject = f"{theme['name']} · {shorten(headline, 12)}"
    thumb = ""
    if main["image"]:
        thumb = (
            f'<td width="132" valign="top" style="width:132px;padding:0 16px 0 0;">'
            f'<a href="{e(main["link"])}"><img src="{e(main["image"])}" width="132" alt="{e(headline)}" '
            f'style="display:block;width:132px;height:auto;border:0;border-radius:6px;"></a></td>'
        )
    update = (
        f'<div style="font-size:14px;line-height:1.5;color:#374151;padding-top:6px;">{e(copy["update"])}</div>'
        if copy["update"] else ""
    )
    more = ""
    if extras:
        links = " &nbsp;·&nbsp; ".join(
            f'<a href="{e(x["link"])}" style="color:#4338ca;text-decoration:none;">{e(shorten(x["title"], 9))}</a>'
            for x in extras[:2]
        )
        more = (
            '<tr><td style="padding:12px 24px 0;font-size:12.5px;line-height:1.6;color:#6b7280;">'
            f'<b style="color:#4f46e5;">Also new:</b> {links}</td></tr>'
        )
    submit_line = (
        f' · <a href="{e(submit_url)}" style="color:#6b7280;">Share a tip</a>' if submit_url else ""
    )
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5fb;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f5fb"><tr><td align="center" style="padding:20px 12px;">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="width:560px;max-width:560px;background:#ffffff;font-family:'Segoe UI',Arial,sans-serif;color:#1f2937;border:1px solid #e5e7eb;">
<tr><td bgcolor="#1e1b4b" style="padding:12px 24px;font-size:13px;color:#e0e7ff;">
<b style="color:#e0e7ff;">{e(theme['name'])}</b> &nbsp;·&nbsp; {today:%a %d %b}
</td></tr>
<tr><td style="padding:18px 24px 4px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
{thumb}<td valign="top">
<a href="{e(main['link'])}" style="font-size:18px;line-height:1.3;font-weight:bold;color:#111827;text-decoration:none;">{e(headline)}</a>
{update}
</td></tr></table>
</td></tr>
<tr><td style="padding:12px 24px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td bgcolor="#fef3c7" style="padding:9px 12px;font-size:14px;line-height:1.45;color:#78350f;border-left:4px solid #f59e0b;"><b>Think:</b> {e(copy['think'])}</td></tr>
<tr><td height="6" style="font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td bgcolor="#ecfdf5" style="padding:9px 12px;font-size:14px;line-height:1.45;color:#065f46;border-left:4px solid #10b981;"><b>Try (5 min):</b> {e(copy['try_this'])}</td></tr>
</table></td></tr>
<tr><td style="padding:12px 24px 0;font-size:13px;color:#6b7280;">
<a href="{e(main['link'])}" style="color:#4f46e5;font-weight:bold;text-decoration:none;">{CTA.get(main['type'], 'Read the source')} &rarr;</a> &nbsp;·&nbsp; {e(main['source'])}
</td></tr>
{TEAM_PICK_SLOT}
{more}
<tr><td style="padding:14px 24px 16px;font-size:11px;color:#9ca3af;">Curated from trusted public sources · Mon-Fri · no tracking{submit_line}</td></tr>
</table></td></tr></table></body></html>"""
    return subject, body


def tip_day(now: dt.datetime) -> dt.datetime:
    """The weekday this run builds for. Before noon it is today's tip (morning and PC backup runs);
    from noon on it is the next weekday's, so the evening runs have tomorrow's tip ready long before
    the 11:00 send, even when GitHub starts them hours late. Weekends roll forward to Monday."""
    day = now if now.hour < 12 else now + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def write_outputs(subject: str, body: str, main: dict, today: dt.datetime) -> None:
    (OUT_DIR / "archive").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages
    (OUT_DIR / "latest.html").write_text(body, encoding="utf-8")
    (OUT_DIR / "archive" / f"{today:%Y-%m-%d}.html").write_text(body, encoding="utf-8")
    site = os.getenv("PAGES_URL", main["link"])
    cdata = f"<!-- tip-{today:%Y-%m-%d} -->\n" + body.replace("]]>", "]]]]><![CDATA[>")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Insights of the Day</title><link>{html.escape(site)}</link><description>One short insight every weekday</description>
<item>
<title>{html.escape(subject)}</title>
<link>{html.escape(main['link'])}</link>
<guid isPermaLink="false">tip-{today:%Y-%m-%d}</guid>
<pubDate>{format_datetime(NOW)}</pubDate>
<description><![CDATA[{cdata}]]></description>
</item>
</channel></rss>
"""
    (OUT_DIR / "feed.xml").write_text(rss, encoding="utf-8")


def send_via_outlook(subject: str, body: str) -> None:
    """Local mode: send through classic Outlook for Windows on this PC (new Outlook has no COM API)."""
    import win32com.client  # pip install pywin32

    to, bcc = os.getenv("TIP_TO", "").strip(), os.getenv("TIP_BCC", "").strip()
    if not (to or bcc):
        sys.exit("Set TIP_TO and/or TIP_BCC first, e.g.  setx TIP_BCC \"ai-tips-team@yourcompany.com\"")
    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)  # 0 = olMailItem
    mail.To, mail.BCC, mail.Subject, mail.HTMLBody = to, bcc, subject, body.replace(TEAM_PICK_SLOT, "")
    if os.getenv("TIP_FROM"):  # a shared mailbox you have Send-As / Send-on-behalf rights on
        mail.SentOnBehalfOfName = os.environ["TIP_FROM"]
    mail.Send()
    outlook.GetNamespace("MAPI").SendAndReceive(False)  # push it out of the Outbox now
    log("Sent through Outlook.")


# ---------------------------------------------------------------- main

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # emoji-safe on Windows consoles
    parser = argparse.ArgumentParser(description="AI Tip of the Day")
    parser.add_argument("--check", action="store_true", help="test every source and exit")
    parser.add_argument("--dry-run", action="store_true", help="write preview.html only, keep history unchanged")
    parser.add_argument("--send-outlook", action="store_true", help="send with the Outlook desktop app")
    parser.add_argument("--day", type=int, choices=range(7), help="override the day theme (0=Mon .. 4=Fri)")
    args = parser.parse_args()

    config = load_json(SOURCES_FILE, None)
    if not config:
        sys.exit(f"Missing {SOURCES_FILE.name}")
    if args.check:
        return check_sources(config["sources"])

    now = NOW.astimezone(ZoneInfo(os.getenv("TIP_TIMEZONE") or config.get("timezone", "UTC")))
    today = now if args.day is not None else tip_day(now)
    log(f"Building the tip for {today:%a %d %b %Y}")
    if os.getenv("GITHUB_EVENT_NAME") in ("schedule", "issues") and (OUT_DIR / "archive" / f"{today:%Y-%m-%d}.html").exists():
        log("Today's tip is already built by an earlier scheduled run - nothing to do.")
        return 0
    theme = THEMES.get(today.weekday() if args.day is None else args.day, WEEKEND_THEME)
    log(f"Theme: {theme['name']}")

    items = collect(config["sources"])
    log(f"Collected {len(items)} items")
    history = load_json(HISTORY_FILE, [])
    main_item, extras, level = pick(items, history, theme, float(config.get("max_age_hours", 72)))
    if not main_item["image"]:
        main_item["image"] = page_image(main_item["link"])

    copy = tip_copy(main_item, theme)
    submit_url = os.getenv("TIP_SUBMIT_URL") or config.get("submit_url", "")
    subject, body = render_email(main_item, copy, extras, theme, today, submit_url)
    log(f"Picked ({level}) [{main_item['score']}] {main_item['source']}: {main_item['title']}")
    for extra in extras:
        log(f"  + [{extra['score']}] {extra['source']}: {extra['title']}")

    if args.dry_run:
        (ROOT / "preview.html").write_text(body, encoding="utf-8")
        log(f"Preview written to {ROOT / 'preview.html'}")
        return 0

    write_outputs(subject, body, main_item, today)
    if args.send_outlook:
        send_via_outlook(subject, body)
    for item in [main_item, *extras]:
        history.append({"date": f"{today:%Y-%m-%d}", "link": item["link"], "title": item["title"],
                        "source": item["source"]})
    HISTORY_FILE.parent.mkdir(exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history[-HISTORY_LIMIT:], indent=2, ensure_ascii=False), encoding="utf-8")
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
