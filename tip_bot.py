#!/usr/bin/env python3
"""AI Tip of the Day: collects public AI updates, picks the best one and renders an Outlook-ready email.

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

# One theme per weekday (tips go out Monday-Friday only). Sources of the matching type get a big boost.
# "think" and "try" are the fallback brainstorm question and 5-minute action when no topic below matches.
THEMES = {
    0: {"name": "Tool Monday", "emoji": "🛠️", "types": ["official", "newsletter"],
        "keywords": ["launch", "introducing", "now available", "new feature", "rolls out", "released"],
        "think": "Which tool in your daily workflow is overdue for an AI upgrade?",
        "try": "Try this new feature on one real task before lunch."},
    1: {"name": "Prompt Tuesday", "emoji": "💬", "types": ["community"],
        "keywords": ["prompt", "prompts", "how to", "tips", "guide", "tutorial", "workflow"],
        "think": "Which prompt do you type every week that could become a reusable template?",
        "try": "Rewrite one prompt you use often as: role + goal + format + one example."},
    2: {"name": "Docs Wednesday", "emoji": "📘", "types": ["official"],
        "keywords": ["docs", "documentation", "api", "best practices", "cookbook", "guide"],
        "think": "What does the official guidance change about how you use this tool today?",
        "try": "Skim the post's best-practice section and adopt one rule this week."},
    3: {"name": "Watch Thursday", "emoji": "🎬", "types": ["video"], "keywords": [],
        "think": "What's one idea from this video you could pitch to your team?",
        "try": "Watch it at 1.5x in a coffee break and share one takeaway in your team chat."},
    4: {"name": "Frontier Friday", "emoji": "🚀", "types": ["news"],
        "keywords": ["breakthrough", "research", "benchmark", "model", "study"],
        "think": "If this becomes normal within a year, what changes for our team?",
        "try": "Discuss it with one colleague for 5 minutes: threat, opportunity, or both?"},
}
WEEKEND_THEME = {"name": "AI Tip", "emoji": "⚡", "types": [], "keywords": [],
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
CTA = {"video": "▶ Watch", "research": "Read the paper"}
AI_TERMS = re.compile(r"\b(ai|a\.i\.|llms?|gpt\S*|chatgpt|claude|gemini|copilot|openai|anthropic|deepmind|agents?|agentic|models?|machine learning|neural|genai|generative|prompts?|mcp|codex|chatbots?|assistants?)\b", re.I)
BOILERPLATE = [
    r"The post .*? appeared first on .*?\.", r"arXiv:\S+\s+Announce Type:\s*\S+\s*Abstract:\s*",
    r"submitted by\s+/u/\S+", r"\[link\]|\[comments\]", r"(Read|Continue reading|Read more)[^.]*$",
    r"^(In this (post|article|blog post|video),?\s*)", r"^This (blog )?post (explores|covers|shows|explains|describes)\s+",
]

PROMPT = """You write "AI Tip of the Day": a 20-second email that keeps a busy business team up to date with AI.
Today's theme: {theme}.
Using ONLY the source below, return a JSON object with exactly these keys:
  "headline": at most 9 words, punchy, plain English, no clickbait
  "update": 1 sentence, at most 22 words: what is new and why it matters at work
  "think": 1 thought-provoking question, at most 18 words, that sparks ideas for the reader's own work
  "try_this": 1 concrete action a non-technical colleague can do in 5 minutes, at most 18 words
Do not invent facts that are not in the source.

SOURCE: {source}
TITLE: {title}
TEXT: {text}
"""


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
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


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

def score(item: dict, theme: dict, max_age_hours: float) -> float | None:
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
    if not AI_TERMS.search(text):  # general tech posts (e.g. a Python release) are not AI tips
        points -= 25
    if item["type"] in theme["types"]:
        points += 15
    points += 4 * sum(1 for k in theme["keywords"] if has_word(text, k))
    return round(points, 2)


def pick(items: list[dict], history: list[dict], theme: dict, max_age_hours: float):
    sent_links = {h["link"] for h in history}
    sent_titles = {norm_title(h["title"]) for h in history}
    seen, ranked = set(), []
    for item in items:
        key = norm_title(item["title"])
        if item["link"] in sent_links or key in sent_titles or key in seen:
            continue
        points = score(item, theme, max_age_hours)
        if points is None:
            continue
        seen.add(key)
        item["score"] = points
        ranked.append(item)
    ranked.sort(key=lambda i: i["score"], reverse=True)
    if not ranked:
        return None, []
    main, extras, used_sources = ranked[0], [], {ranked[0]["source"]}
    for item in ranked[1:]:
        if item["source"] not in used_sources:
            extras.append(item)
            used_sources.add(item["source"])
        if len(extras) == 3:
            break
    return main, extras


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

def ask_gemini(prompt: str) -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def ask_ollama(prompt: str) -> str:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    resp = requests.post(
        f"{host}/api/generate",
        json={"model": os.getenv("OLLAMA_MODEL", "llama3.2"), "prompt": prompt, "stream": False, "format": "json"},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def ai_copy(item: dict, theme: dict) -> dict | None:
    provider = os.getenv("LLM_PROVIDER", "none").strip().lower()
    if provider not in ("gemini", "ollama"):
        return None
    prompt = PROMPT.format(theme=theme["name"], source=item["source"], title=item["title"],
                           text=item["summary"][:3000])
    try:
        raw = ask_gemini(prompt) if provider == "gemini" else ask_ollama(prompt)
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        return {key: clean_text(str(data[key])) for key in ("headline", "update", "think", "try_this")}
    except Exception as exc:  # the email still goes out with the plain wording
        log(f"  ! AI wording skipped ({exc}); using the plain wording")
        return None


def shorten(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.rstrip()
    return " ".join(words[:max_words]).rstrip(",;:-–") + "…"


def first_sentence(summary: str, title: str) -> str:
    """One crisp sentence from the feed summary: boilerplate removed, at most 24 words."""
    text = summary
    for pattern in BOILERPLATE:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -–:")
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
    # the headline decides first; the summary is only a fallback
    for text in (item["title"].lower(), item["summary"][:600].lower()):
        for words, think, action in TOPICS:
            if any(has_word(text, w) for w in words):
                return think, action
    # the day's own prompts only fit when the item matches the day (e.g. a video on Watch Thursday)
    return (theme["think"], theme["try"]) if item["type"] in theme["types"] else GENERIC


def plain_copy(item: dict, theme: dict) -> dict:
    think, action = brainstorm(item, theme)
    personal_post = "reddit.com" in item["link"]  # Reddit summaries are personal stories, not news
    return {
        "headline": shorten(item["title"], 14),
        "update": "" if personal_post else first_sentence(item["summary"], item["title"]),
        "think": think,
        "try_this": action,
    }


def render_email(main: dict, copy: dict, extras: list[dict], theme: dict, today: dt.datetime,
                 submit_url: str) -> tuple[str, str]:
    def e(value: str) -> str:
        return html.escape(value or "", quote=True)

    headline = copy["headline"]
    subject = f"{theme['emoji']} AI Tip · {theme['name']} · {shorten(headline, 12)}"
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
<b style="color:#a5b4fc;letter-spacing:1.5px;">⚡ AI TIP OF THE DAY</b> &nbsp;·&nbsp; {theme['emoji']} {e(theme['name'])} &nbsp;·&nbsp; {today:%a %d %b}
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
<tr><td bgcolor="#fef3c7" style="padding:9px 12px;font-size:14px;line-height:1.45;color:#78350f;border-left:4px solid #f59e0b;"><b>🧠 Think:</b> {e(copy['think'])}</td></tr>
<tr><td height="6" style="font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td bgcolor="#ecfdf5" style="padding:9px 12px;font-size:14px;line-height:1.45;color:#065f46;border-left:4px solid #10b981;"><b>✅ Try (5 min):</b> {e(copy['try_this'])}</td></tr>
</table></td></tr>
<tr><td style="padding:12px 24px 0;font-size:13px;color:#6b7280;">
<a href="{e(main['link'])}" style="color:#4f46e5;font-weight:bold;text-decoration:none;">{CTA.get(main['type'], 'Read the source')} &rarr;</a> &nbsp;·&nbsp; {e(main['source'])}
</td></tr>
{TEAM_PICK_SLOT}
{more}
<tr><td style="padding:14px 24px 16px;font-size:11px;color:#9ca3af;">Curated automatically from trusted public AI sources · Mon–Fri · no tracking{submit_line}</td></tr>
</table></td></tr></table></body></html>"""
    return subject, body


def write_outputs(subject: str, body: str, main: dict, today: dt.datetime) -> None:
    (OUT_DIR / "archive").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages
    (OUT_DIR / "latest.html").write_text(body, encoding="utf-8")
    (OUT_DIR / "archive" / f"{today:%Y-%m-%d}.html").write_text(body, encoding="utf-8")
    site = os.getenv("PAGES_URL", main["link"])
    cdata = body.replace("]]>", "]]]]><![CDATA[>")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>AI Tip of the Day</title><link>{html.escape(site)}</link><description>One short AI tip every weekday</description>
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

    today = NOW.astimezone(ZoneInfo(os.getenv("TIP_TIMEZONE") or config.get("timezone", "UTC")))
    if os.getenv("GITHUB_EVENT_NAME") == "schedule" and (OUT_DIR / "archive" / f"{today:%Y-%m-%d}.html").exists():
        log("Today's tip is already built by an earlier scheduled run - nothing to do.")
        return 0
    theme = THEMES.get(today.weekday() if args.day is None else args.day, WEEKEND_THEME)
    log(f"Theme: {theme['emoji']} {theme['name']}")

    items = collect(config["sources"])
    log(f"Collected {len(items)} items")
    history = load_json(HISTORY_FILE, [])
    main_item, extras = pick(items, history, theme, float(config.get("max_age_hours", 72)))
    if not main_item:
        log("Nothing new today - no email built.")
        return 0
    if not main_item["image"]:
        main_item["image"] = page_image(main_item["link"])

    copy = ai_copy(main_item, theme) or plain_copy(main_item, theme)
    submit_url = os.getenv("TIP_SUBMIT_URL") or config.get("submit_url", "")
    subject, body = render_email(main_item, copy, extras, theme, today, submit_url)
    log(f"Picked [{main_item['score']}] {main_item['source']}: {main_item['title']}")
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
