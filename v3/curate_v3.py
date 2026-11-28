"""Insights of the Day v3 - stand-alone engine (does not use v0, v1 or v2 code or data).

Runs every hour in GitHub Actions (.github/workflows/insights-v3.yml). Each run:

  1. Works out the send day: before 10:00 IST on a weekday it is today, otherwise the next weekday.
     From 09:15 IST on the send day the mail is frozen: runs only log and stop until the 10:00 send.
  2. Reads the trusted feeds (v3/feeds.json): items since 00:00 IST of the weekday before the send day,
     minus stories already mailed. Stories covered by several outlets are marked as trending.
  3. If there is no insight for the send day yet, Gemini picks one (prompt_pick.md) and writes the card from
     the article text (prompt_write.md). The script checks it: facts and numbers must be in the source text,
     word limits for a 30-second read, no long dashes or emojis, working source link.
  4. If an insight exists and new items have arrived since the last look, Gemini may swap it for a clearly
     better one (at most 3 swaps a day), so the mail follows the day's trends until the freeze.
  5. If nothing good is ready, an evergreen tip (v3/evergreen.json, checked against official docs) is put
     in place at once, so a mail is always ready. Later runs can still replace it with a fresh insight.
  6. The riddle is the first unused riddle (v3/riddles.json) whose topics match the insight's topic.
     The next mail opens with its answer.
  7. Publishes: test mode -> docs/v3/test-feed.xml; with v3/LIVE -> docs/feed.xml (the Sender's feed).

    python v3/curate_v3.py                          normal hourly run
    python v3/curate_v3.py --now 2026-11-29T20:00   pretend the time (testing)
    python v3/curate_v3.py --preview-evergreen ID   render one evergreen tip -> docs/v3/preview-ID.html
    python v3/curate_v3.py --test-feed 2026-11-30   rewrite docs/v3/test-feed.xml with that day's marker
Plain hyphens only, no emojis (house rules).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from email.utils import format_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import render_v3  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")
SEND_AT, FREEZE_AT = dt.time(10, 0), dt.time(9, 15)
LIVE, ACTIVE = HERE / "LIVE", HERE / "ACTIVE"
CARDS = ROOT / "data" / "v3_cards.json"
STATE = ROOT / "data" / "v3_state.json"
STATUS = ROOT / "data" / "v3_status.json"
RUN_LOG = ROOT / "data" / "v3_last_run.log"
OUT = ROOT / "docs" / "v3"
FEEDS = HERE / "feeds.json"
PICK, WRITE = HERE / "prompt_pick.md", HERE / "prompt_write.md"
RIDDLES = json.loads((HERE / "riddles.json").read_text(encoding="utf-8"))["riddles"]
EVERGREEN = json.loads((HERE / "evergreen.json").read_text(encoding="utf-8"))["tips"]
LANES = {"frontier", "data", "agents", "open", "creative", "science", "plans", "aws", "docker"}
TOPICS = {"ai", "prompting", "ml", "stats", "data", "python", "dev", "cloud", "security"}
MAX_ROUNDS, MAX_SWAPS = 10, 3  # model rounds and swaps per send day
PER_FEED, MAX_CANDIDATES = 6, 60
MAX_WORDS = 100
UA = {"User-Agent": "Mozilla/5.0 (compatible; Insights-of-the-Day/3.0)"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
GEMINI_MODELS = os.getenv("GEMINI_MODELS",
                          "gemini-3.8-flash,gemini-4-hydrogen,gemini-3.6-flash,gemini-3.5-flash-lite").split(",")
BAD = re.compile("[\\u2013\\u2014\\U0001F000-\\U0001FAFF\\u2600-\\u27bf]")  # long dashes and emojis
STOP = set("the a an and or for with from into your you new how why what this that are is was will can our its "
           "now more than over about using use after their they them have has his her to of in on at by as it be "
           "not all out up".split())
_lines: list[str] = []


def log(msg: str) -> None:
    line = f"{dt.datetime.now(IST):%Y-%m-%d %H:%M:%S} {msg}"
    _lines.append(line)
    print(line, flush=True)


def jload(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def jsave(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- time

def next_weekday(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def prev_weekday(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def send_day(now: dt.datetime) -> dt.date:
    if now.weekday() < 5 and now.time() < SEND_AT:
        return now.date()
    return next_weekday(now.date())


# ---------------------------------------------------------------- model

def gemini_text(body: dict) -> str:
    cand = (body.get("candidates") or [{}])[0]
    return "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought")).strip()


def ask_cli(prompt: str) -> str:
    exe = os.getenv("CLAUDE_EXE") or shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        raise RuntimeError("no GEMINI_API_KEY and no Claude CLI on this machine")
    work = ROOT.parent / "v2work"
    work.mkdir(exist_ok=True)
    res = subprocess.run([exe, "-p", "--output-format", "text"], input=prompt, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=work, timeout=900)
    if res.returncode != 0:
        raise RuntimeError(f"claude exited {res.returncode}: {res.stderr.strip()[-300:]}")
    return res.stdout


def ask_llm(prompt: str, temperature: float = 0.4) -> str:
    """Google Gemini API (free key in the GEMINI_API_KEY secret). On a PC without the key: Claude Code CLI."""
    log(f"  asking the model ({len(prompt)} characters)")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        if os.getenv("GITHUB_ACTIONS"):
            raise RuntimeError("the GEMINI_API_KEY repository secret is not set")
        return ask_cli(prompt)
    import requests

    head = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": temperature}}
    last = ""
    for model in (m.strip() for m in GEMINI_MODELS if m.strip()):
        for attempt in (1, 2):
            try:
                r = requests.post(GEMINI_URL.format(model), headers=head, json=body, timeout=180)
            except requests.RequestException as exc:
                last = f"{model}: {exc}"
                break
            if r.status_code in (401, 403) or "API_KEY_INVALID" in r.text:
                raise RuntimeError(f"Gemini refused the key ({r.status_code})")
            if r.status_code in (429, 500, 503) and attempt == 1:
                log(f"  {model}: {r.status_code}, waiting 20 s")
                time.sleep(20)
                continue
            try:
                text = gemini_text(r.json()) if r.status_code == 200 else ""
            except ValueError:
                text = ""
            if text:
                log(f"  answered by {model}")
                return text
            last = f"{model}: {r.status_code}"
            break
    raise RuntimeError(f"no Gemini model answered (last: {last})")


def parse_obj(answer: str) -> dict | str:
    m = re.search(r"```json\s*(\{.*\})\s*```", answer, re.S) or re.search(r"(\{.*\})", answer, re.S)
    if not m:
        skip = re.search(r"^\s*SKIP:.*$", answer, re.M)
        if skip:
            return skip.group().strip()
        raise ValueError(f"no JSON in the answer: {answer.strip()[-200:]}")
    return json.loads(m.group(1).replace("\u2014", " - ").replace("\u2013", "-"))  # long dashes -> hyphen


# ---------------------------------------------------------------- feeds

def words_of(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.+-]{2,}", title.lower()) if w not in STOP}


def collect(since: dt.datetime, skip_links: set[str]) -> list[dict]:
    import feedparser
    import requests

    now = dt.datetime.now(dt.timezone.utc)
    out, seen = [], set()
    for f in jload(FEEDS, {"feeds": []})["feeds"]:
        try:
            feed = feedparser.parse(requests.get(f["url"], headers=UA, timeout=25).content)
        except Exception as exc:  # one broken feed never stops the run
            log(f"  ! {f['name']}: {str(exc)[:120]}")
            continue
        got = []
        for e in feed.entries[:80]:
            stamp = e.get("published_parsed") or e.get("updated_parsed")
            link = (e.get("link") or "").strip()
            title = html.unescape(re.sub(r"<[^>]+>", "", e.get("title", ""))).strip()
            if not (stamp and link.startswith("https://") and title):
                continue
            when = dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc)
            key = re.sub(r"\W+", " ", title.lower()).strip()
            if not since <= when <= now + dt.timedelta(hours=1) or link in skip_links or key in seen:
                continue
            seen.add(key)
            summary = html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary", "")))
            got.append({"title": title, "link": link, "when": when.isoformat(), "source": f["name"],
                        "kind": f["kind"], "summary": re.sub(r"\s+", " ", summary).strip()[:200]})
        got.sort(key=lambda x: x["when"], reverse=True)
        out += got[:PER_FEED]
    for c in out:  # trend signal: other outlets with a similar title
        mine = words_of(c["title"])
        c["covered"] = 1 + sum(1 for o in out if o["source"] != c["source"]
                               and len(mine & words_of(o["title"])) >= 2
                               and len(mine & words_of(o["title"])) / max(1, len(mine | words_of(o["title"]))) >= 0.2)
    rank = {"official": 0, "press": 1, "community": 2}
    out.sort(key=lambda x: (-min(x["covered"], 3), rank[x["kind"]], -dt.datetime.fromisoformat(x["when"]).timestamp()))
    return out[:MAX_CANDIDATES]


def page_text(url: str, title: str, limit: int) -> str:
    import requests

    try:
        raw = requests.get(url, headers=BROWSER, timeout=25).text
    except Exception:
        return ""
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form|svg|noscript)\b.*?</\1>", " ", raw)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    start = text.find(title[:40]) if title else -1
    return text[start if start > 0 else 0:][:limit]


def url_ok(url: str) -> bool:
    import requests

    try:
        r = requests.get(url, headers=BROWSER, timeout=20, stream=True)
        return r.status_code < 400 or r.status_code in (401, 403, 405, 429)
    except Exception:
        return False


def trust_of(chosen: list[dict]) -> str | None:
    kinds = [c["kind"] for c in chosen]
    if "official" in kinds:
        return "mixed" if "press" in kinds else "official"
    if kinds.count("press") >= 2 or ("press" in kinds and len(kinds) >= 2):
        return "reported"
    return None


# ---------------------------------------------------------------- checks

def nwords(text: str | None) -> int:
    return len(re.findall(r"\S+", text or ""))


def body_words(c: dict) -> int:
    return sum(nwords(c.get(k)) for k in ("context", "tip", "angle")) + sum(nwords(s) for s in c.get("steps") or [])


def problems(c: dict, source: str) -> list[str]:
    p = [f"missing {k}" for k in ("subject", "preheader", "context", "tip", "steps", "angle") if not c.get(k)]
    if p:
        return p
    if len(c["subject"]) > 60:
        p.append(f"subject has {len(c['subject'])} characters, at most 55")
    if len(c["preheader"]) > 95:
        p.append(f"preheader has {len(c['preheader'])} characters, at most 85")
    if "`" in c["subject"] + c["preheader"]:
        p.append("no backticks in subject or preheader")
    if "!" in c["subject"] + c["preheader"]:
        p.append("no exclamation marks")
    if c["subject"].strip().lower() == c["preheader"].strip().lower():
        p.append("preheader repeats the subject")
    if not isinstance(c["steps"], list) or not 2 <= len(c["steps"]) <= 3:
        p.append("steps must be a list of 2 or 3 items")
    else:
        p += [f"step has {nwords(s)} words, at most 12: {s}" for s in c["steps"] if nwords(s) > 14]
    for name, limit in (("context", 30), ("tip", 26), ("angle", 20)):
        if nwords(c[name]) > limit + 4:
            p.append(f"{name} has {nwords(c[name])} words, at most {limit}")
    if body_words(c) > MAX_WORDS:
        p.append(f"the mail has {body_words(c)} words, at most {MAX_WORDS} (30-second read)")
    num = c.get("number")
    if num and (not isinstance(num, list) or len(num) != 2 or len(str(num[0])) > 16 or len(str(num[1])) > 40):
        p.append("number must be [value up to 14 characters, label up to 36 characters] or null")
    blob = json.dumps({k: c.get(k) for k in ("subject", "preheader", "context", "tip", "steps", "angle", "number")},
                      ensure_ascii=False)
    if BAD.search(blob):
        p.append("has a long dash or an emoji: use only the plain hyphen and no emojis")
    for w in re.findall(r"\b[A-Z]{4,}\b", c["subject"] + " " + c["context"]):
        if w not in source:
            p.append(f"no words in capitals: {w}")
    for n in sorted(set(re.findall(r"\d+(?:[.,]\d+)?", blob))):
        if n not in source:
            p.append(f"the number {n} is not in the source text - use only facts from the source")
    return p


# ---------------------------------------------------------------- picking and writing

def listing(cands: list[dict]) -> str:
    return "\n".join(f"{i}. [{c['source']} | {c['kind']} | {c['when'][5:10]}]"
                     + (f" [covered by {c['covered']} sources]" if c["covered"] > 1 else "")
                     + f" {c['title']} - {c['summary']}" for i, c in enumerate(cands, 1))


def pick(day: dt.date, cands: list[dict], recent: str, incumbent: dict | None) -> tuple[dict, list[dict]] | str | None:
    inc = ""
    if incumbent:
        n = next((i for i, c in enumerate(cands, 1) if c["link"] == incumbent["sources"][0][1]), None)
        inc = (f"\nCURRENT PICK for this mail: item {n}. Keep it (answer pick {n}) unless another item is clearly "
               "more useful and more important for the readers.\n" if n else
               f"\nCURRENT PICK for this mail (not in the list): \"{incumbent['subject']}\". Pick a new item only if "
               "it is clearly better; to keep the current pick answer SKIP: keep.\n")
    since = dt.datetime.combine(prev_weekday(day), dt.time(0, 0), IST)
    base = (PICK.read_text(encoding="utf-8").replace("{DAY}", f"{day:%A %d %b %Y}")
            .replace("{SINCE}", f"{since:%A %d %b}, 00:00 IST").replace("{RECENT}", recent or "  (none yet)")
            .replace("{INCUMBENT}", inc))
    banned: list[int] = []
    for _ in range(2):
        note = f"\nDo not pick these numbers (not enough sources or wrong lane): {banned}\n" if banned else ""
        got = parse_obj(ask_llm(base.replace("{CANDIDATES}", listing(cands) + note)))
        if isinstance(got, str):
            return got
        try:
            n = int(got.get("pick"))
        except (TypeError, ValueError):
            n = 0
        if not 1 <= n <= len(cands):
            banned.append(n)
            continue
        extra = [int(k) for k in got.get("same_story", []) if str(k).isdigit()]
        chosen = [cands[n - 1]] + [cands[k - 1] for k in extra[:2] if 1 <= k <= len(cands) and k != n]
        if trust_of(chosen) and got.get("lane") in LANES and got.get("topic") in TOPICS:
            return got, chosen
        banned.append(n)
        log(f"  pick {n} rejected (sources {[c['kind'] for c in chosen]}, lane {got.get('lane')}, topic {got.get('topic')})")
    return None


def write(day: dt.date, got: dict, chosen: list[dict]) -> dict | None:
    texts = [f"=== {c['source']} ({c['when'][:10]}): {c['title']}\n{c['link']}\n"
             + (page_text(c["link"], c["title"], 14000 if i == 0 else 6000) or c["summary"])
             for i, c in enumerate(chosen)]
    source = "\n\n".join(texts)
    if not url_ok(chosen[0]["link"]):
        log(f"  source does not open: {chosen[0]['link']}")
        return None
    base = WRITE.read_text(encoding="utf-8").replace("{DAY}", f"{day:%A %d %b %Y}").replace("{SOURCES}", source)
    fixes = ""
    for attempt in (1, 2):
        c = parse_obj(ask_llm(base.replace("{FIXES}", fixes), temperature=0.6))
        if isinstance(c, str):
            log(f"  writer answered {c[:100]}")
            return None
        c = {k: c.get(k) for k in ("subject", "preheader", "context", "tip", "steps", "angle", "number")}
        c["subject"] = re.sub(r"\s+", " ", str(c.get("subject") or "").strip().rstrip("."))
        probs = problems(c, source)
        if not probs:
            c.update(lane=got["lane"], topic=got["topic"], trust=trust_of(chosen),
                     sources=[[x["source"], x["link"]] for x in sorted(chosen, key=lambda x: x["kind"] != "official")])
            return c
        log(f"  write attempt {attempt} problems: " + "; ".join(probs))
        fixes = ("Your previous answer had these problems, fix all of them:\n" + "\n".join(f"- {x}" for x in probs)
                 + "\nPrevious answer:\n" + json.dumps(c, ensure_ascii=False) + "\n")
    return None


def fresh_insight(day: dt.date, cands: list[dict], recent: str, incumbent: dict | None) -> dict | None:
    try:
        got = pick(day, cands, recent, incumbent)
    except Exception as exc:
        log(f"  pick failed: {str(exc)[:200]}")
        return None
    if isinstance(got, str):
        log(f"  pick: {got[:150]}")
        return None
    if got is None:
        log("  pick: no item passed the source and lane checks")
        return None
    meta, chosen = got
    if incumbent and chosen[0]["link"] == incumbent["sources"][0][1]:
        log("  pick: keeping the current insight")
        return None
    log(f"  picked [{meta['lane']}/{meta['topic']}] {chosen[0]['source']}: {chosen[0]['title']}")
    try:
        return write(day, meta, chosen)
    except Exception as exc:
        log(f"  write failed: {str(exc)[:200]}")
        return None


def evergreen(sent: list[dict]) -> dict:
    used = [c.get("evergreen_id") for c in sent if c.get("evergreen_id")]
    fresh = [t for t in EVERGREEN if t["id"] not in used]
    t = fresh[0] if fresh else min(EVERGREEN, key=lambda t: max(i for i, u in enumerate(used) if u == t["id"]))
    return {**{k: v for k, v in t.items() if k != "id"}, "evergreen_id": t["id"], "kind": "evergreen"}


def riddle_for(card: dict, sent: list[dict]) -> int:
    """First unused riddle whose topics include the card's topic; else the first unused; else least recently used."""
    used = [c["riddle_index"] for c in sent if "riddle_index" in c]
    unused = [i for i in range(len(RIDDLES)) if i not in used]
    match = [i for i in unused if card.get("topic") in RIDDLES[i]["topics"]]
    if match:
        return match[0]
    if unused:
        return unused[0]
    return min(range(len(RIDDLES)), key=lambda i: max(k for k, u in enumerate(used) if u == i))


# ---------------------------------------------------------------- publishing

def rss(subject: str, link: str, body: str, mark: str) -> str:
    cdata = f"<!-- tip-{mark} -->\n" + body.replace("]]>", "]]]]><![CDATA[>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n<title>Insights of the Day</title>'
            f"<link>https://MercyDavidTG.github.io/ai-tip-of-the-day/</link><description>One 30-second insight every "
            f"weekday</description>\n<item>\n<title>{html.escape(subject)}</title>\n<link>{html.escape(link)}</link>\n"
            f'<guid isPermaLink="false">tip-{mark}</guid>\n<pubDate>{format_datetime(dt.datetime.now(dt.timezone.utc))}'
            f"</pubDate>\n<description><![CDATA[{cdata}]]></description>\n</item>\n</channel></rss>\n")


def reveal_for(card: dict, cards: list[dict]) -> dict | None:
    prev = [c for c in cards if c["date"] < card["date"] and c.get("live") == card.get("live")]
    if not prev:
        return None
    p = prev[-1]
    pday, day = dt.date.fromisoformat(p["date"]), dt.date.fromisoformat(card["date"])
    label = "YESTERDAY'S RIDDLE" if (day - pday).days == 1 else f"{pday:%A}'S RIDDLE".upper()
    return {**RIDDLES[p["riddle_index"]], "label": label}


def publish(card: dict, cards: list[dict], live: bool) -> str:
    subject, body = render_v3.render(card, reveal_for(card, cards))
    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    (OUT / "archive" / f"{card['date']}.html").write_text(body, encoding="utf-8")
    (OUT / "latest.html").write_text(body, encoding="utf-8")
    (OUT / "test-feed.xml").write_text(rss(subject, card["sources"][0][1], body, card["date"]), encoding="utf-8")
    shown = [c for c in cards if c.get("live") == live]
    rows = "".join(f'<li><a href="archive/{c["date"]}.html">{dt.date.fromisoformat(c["date"]):%a %d %b %Y}</a> - '
                   f'{html.escape(c["subject"])}</li>' for c in reversed(shown))
    (OUT / "index.html").write_text(
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
        'initial-scale=1"><title>Insights of the Day - all insights</title></head><body style="font-family:Segoe UI,'
        'Arial,sans-serif;max-width:640px;margin:24px auto;padding:0 16px;background:#EEEAE2;color:#111;">'
        '<h1>Insights of the Day</h1><p>One 30-second insight every weekday, with a riddle at the end.</p>'
        f'<ul style="line-height:1.8;">{rows}</ul></body></html>', encoding="utf-8")
    if live:
        docs = ROOT / "docs"
        (docs / "archive").mkdir(parents=True, exist_ok=True)
        (docs / "archive" / f"{card['date']}.html").write_text(body, encoding="utf-8")
        (docs / "latest.html").write_text(body, encoding="utf-8")
        (docs / "feed.xml").write_text(rss(subject, card["sources"][0][1], body, card["date"]), encoding="utf-8")
    return subject


# ---------------------------------------------------------------- one run

def run(now: dt.datetime) -> int:
    live = LIVE.exists()
    if live and (ROOT / "v2" / "ACTIVE").exists() and not (ROOT / "v2" / "HOLD").exists():
        log("WARNING: v3 is LIVE but v2 can still write the team feed - add v2/HOLD.")
    day = send_day(now)
    cards = jload(CARDS, {"cards": []})["cards"]
    card = next((c for c in cards if c["date"] == day.isoformat() and c.get("live") == live), None)
    status = {"last_run_ist": f"{now:%Y-%m-%d %H:%M}", "send_day": day.isoformat(), "mode": "live" if live else "test"}
    if now.date() == day and now.time() >= FREEZE_AT:
        log(f"Mail for {day} is frozen since {FREEZE_AT:%H:%M} IST ({card['subject'] if card else 'NO CARD'}).")
        if card is None:  # should never happen; make sure a mail exists anyway
            log("No card at freeze time - putting in an evergreen tip now.")
        else:
            jsave(STATUS, {**status, "state": "frozen", "subject": card["subject"], "kind": card["kind"]})
            return 0

    sent = [c for c in cards if c["date"] < day.isoformat() and c.get("live") == live]
    sent_links = {u for c in sent for _, u in c.get("sources", [])}
    recent = "\n".join(f"  - {c['date']} [{c.get('topic')}] {c['subject']}" for c in sent[-15:])
    state = jload(STATE, {})
    if state.get("day") != day.isoformat() or state.get("live") != live:
        state = {"day": day.isoformat(), "live": live, "seen": [], "rounds": 0, "swaps": 0}

    since = dt.datetime.combine(prev_weekday(day), dt.time(0, 0), IST).astimezone(dt.timezone.utc)
    cands = collect(since, sent_links)
    new = [c for c in cands if c["link"] not in state["seen"]]
    state["seen"] = sorted(set(state["seen"]) | {c["link"] for c in cands})
    log(f"Send day {day:%a %d %b} ({'LIVE' if live else 'test'}): {len(cands)} candidates, {len(new)} new since last run"
        + (f", current: [{card['kind']}] {card['subject']}" if card else ", no mail yet"))

    before = json.dumps(card, sort_keys=True) if card else ""
    want = cands and state["rounds"] < MAX_ROUNDS and (
        card is None or card["kind"] == "evergreen" or (new and state["swaps"] < MAX_SWAPS))
    if want and not (now.date() == day and now.time() >= FREEZE_AT):
        state["rounds"] += 1
        got = fresh_insight(day, cands, recent, card if card and card["kind"] == "news" else None)
        if got:
            if card and card["kind"] == "news":
                state["swaps"] += 1
                log(f"Swapped the insight for a better one: {got['subject']}")
            card = {**got, "kind": "news"}
    if card is None:
        card = evergreen(sent)
        log(f"No fresh insight ready - evergreen tip '{card['evergreen_id']}' is in place (later runs may replace it).")

    card = {**{k: v for k, v in card.items() if k not in ("date", "no", "live", "riddle_index")},
            "date": day.isoformat(), "no": len(sent) + 1, "live": live}
    card["riddle_index"] = riddle_for(card, sent)
    changed = json.dumps(card, sort_keys=True) != before or not (OUT / "archive" / f"{day}.html").exists()
    if changed:
        cards = [c for c in cards if not (c["date"] == day.isoformat() and c.get("live") == live)] + [card]
        cards.sort(key=lambda c: (c["date"], c.get("live", False)))
        jsave(CARDS, {"cards": cards})
        subject = publish(card, cards, live)
        log(f"Published #{card['no']} [{card['kind']}/{card.get('topic')}] {subject} "
            f"({body_words(card)} words, riddle {card['riddle_index']}) to "
            + ("the TEAM feed" if live else "the v3 test feed only"))
    jsave(STATE, state)
    jsave(STATUS, {**status, "state": "ready", "subject": card["subject"], "kind": card["kind"],
                   "candidates": len(cands), "rounds": state["rounds"], "swaps": state["swaps"]})
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", help="pretend time, IST, e.g. 2026-11-29T20:00")
    ap.add_argument("--preview-evergreen")
    ap.add_argument("--test-feed", type=dt.date.fromisoformat)
    a = ap.parse_args(argv)
    if a.preview_evergreen:
        t = next(t for t in EVERGREEN if t["id"] == a.preview_evergreen)
        card = {**{k: v for k, v in t.items() if k != "id"}, "kind": "evergreen", "date": dt.date.today().isoformat(),
                "no": 1, "live": False, "riddle_index": 0}
        s, b = render_v3.render(card, {**RIDDLES[1], "label": "YESTERDAY'S RIDDLE"})
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"preview-{a.preview_evergreen}.html").write_text(b, encoding="utf-8")
        print(s, body_words(card), "words")
        return 0
    if a.test_feed:
        cards = jload(CARDS, {"cards": []})["cards"]
        card = cards[-1]
        s, b = render_v3.render(card, reveal_for(card, cards))
        (OUT / "test-feed.xml").write_text(rss(s, card["sources"][0][1], b, a.test_feed.isoformat()), encoding="utf-8")
        print(f"test feed: {s} (marker tip-{a.test_feed})")
        return 0
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=IST) if a.now else dt.datetime.now(IST)
    return run(now)


if __name__ == "__main__":
    import traceback

    try:
        code = main()
    except Exception:
        log("ERROR: " + traceback.format_exc())
        code = 1
    if os.getenv("GITHUB_ACTIONS"):
        RUN_LOG.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    sys.exit(code)
