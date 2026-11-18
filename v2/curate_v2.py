"""Insights of the Day v2 - pick a fresh insight every weekday with Claude and publish it.

    python v2/curate_v2.py              curate the next send day's insight and publish it
    python v2/curate_v2.py --dry-run    curate only, write v2/preview.html, change nothing else
    python v2/curate_v2.py --date 2026-11-19   curate for a given send day

Send day: before 12:00 IST it is today, from 12:00 on it is the next weekday (weekends roll to Monday).
Publishing writes:
  - v2/insights.json (the new insight, dated with the send day)
  - docs/v2/ (the v2 archive and feed, through render_v2.py)
  - docs/feed.xml, docs/latest.html, docs/archive/<day>.html and data/sent.json, which the v0 Sender
    flow in Power Automate reads at 10:00 IST and mails to the team.
It runs in GitHub Actions (daily-tip.yml), which commits and pushes. Two ways to curate:
  - free (default): read v2/feeds.json, a free GitHub Models AI picks the story and writes the card
    from the article text (prompt_pick.md, prompt_write.md); needs only the workflow token (models: read)
  - paid (only if the ANTHROPIC_API_KEY secret exists): Claude searches the web itself (curate_prompt.md)
Rules kept from v0: plain hyphens only, no emojis, plain wording.
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
import urllib.error
import urllib.request
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_v2  # noqa: E402

ROOT = render_v2.ROOT
DATA = render_v2.DATA
PROMPT = ROOT / "v2" / "curate_prompt.md"  # paid path: Claude API with its own web search
PICK = ROOT / "v2" / "prompt_pick.md"  # free path: feeds + GitHub Models
WRITE = ROOT / "v2" / "prompt_write.md"
V0_DOCS = ROOT / "docs"
SENT = ROOT / "data" / "sent.json"
TRIES = ROOT / "data" / "v2_tries.json"  # cloud runs every hour; cap paid tries per send day
MAX_TRIES = 3
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")  # fixed offset, no tzdata needed
V2_MARK = "Curated from trusted public sources"  # footer line only the v2 email has
FENCE = re.compile(r"```json\s*(\{.*\})\s*```", re.S)


HOLD = ROOT / "v2" / "HOLD"  # while this file exists nothing is written to the team feed (docs/feed.xml)
RUN_LOG = ROOT / "data" / "v2_last_run.log"  # saved in the repo after a cloud run that tried to curate
_lines: list[str] = []


def log(msg: str) -> None:
    line = f"{dt.datetime.now(IST):%Y-%m-%d %H:%M:%S} {msg}"
    _lines.append(line)
    print(line, flush=True)


def send_day(now: dt.datetime) -> dt.date:
    day = now.date() if now.hour < 12 else now.date() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def prev_weekday(day: dt.date) -> dt.date:
    day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def build_prompt(day: dt.date, items: list[dict]) -> str:
    since = prev_weekday(day)
    recent = "\n".join(f"  - {it['date']} [{it['lane']}] {it['headline']}" for it in items[-20:])
    example = next((it for it in reversed(items) if not it.get("bench")), items[-1])
    example = {k: v for k, v in example.items() if k != "date"}
    return (PROMPT.read_text(encoding="utf-8")
            .replace("{DAY}", f"{day:%A %d %b %Y}")
            .replace("{SINCE}", f"{since:%A %d %b %Y}, 00:00")
            .replace("{RECENT}", recent)
            .replace("{EXAMPLE}", json.dumps(example, indent=1, ensure_ascii=False)))


def claude_exe() -> str:
    exe = os.getenv("CLAUDE_EXE") or shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        sys.exit("Claude CLI not found. Install Claude Code or set CLAUDE_EXE.")
    return exe


def ask_claude(prompt: str) -> str:
    """Cloud (GitHub Actions): Claude API with server-side web search and fetch, needs ANTHROPIC_API_KEY.
    Laptop without a key: the Claude Code CLI (kept only as a manual fallback)."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return ask_api(prompt)
    if os.getenv("GITHUB_ACTIONS"):
        raise RuntimeError("ANTHROPIC_API_KEY secret is not set in GitHub - cannot curate in the cloud")
    return ask_cli(prompt)


def ask_api(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 12},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10}]
    messages = [{"role": "user", "content": prompt}]
    for _ in range(5):  # a long search turn can pause; send it back so the server carries on
        with client.beta.messages.stream(
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            max_tokens=32000,
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            tools=tools,
            messages=messages,
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason != "pause_turn":
            break
        messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": resp.content}]
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined: {resp.stop_details}")
    u = resp.usage
    log(f"API usage: {u.input_tokens} in, {u.output_tokens} out")
    return "".join(b.text for b in resp.content if b.type == "text")


def ask_cli(prompt: str) -> str:
    cmd = [claude_exe(), "-p", "--allowedTools", "WebSearch,WebFetch", "--output-format", "text"]
    if os.getenv("CLAUDE_MODEL"):
        cmd += ["--model", os.environ["CLAUDE_MODEL"]]
    work = ROOT.parent / "v2work"  # neutral folder, so no project settings are picked up
    work.mkdir(exist_ok=True)
    res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=work, timeout=1800)
    if res.returncode != 0:
        raise RuntimeError(f"claude exited {res.returncode}: {res.stderr.strip()[-500:]}")
    return res.stdout


def parse(answer: str) -> dict | str:
    """The insight dict, or a 'SKIP: reason' string."""
    m = FENCE.search(answer)
    if not m:
        skip = re.search(r"^SKIP:.*$", answer, re.M)
        if skip:
            return skip.group().strip()
        raise ValueError(f"no JSON block in the answer: {answer.strip()[-400:]}")
    it = json.loads(m.group(1))
    text = json.dumps(it, ensure_ascii=False).replace("—", " - ").replace("–", "-")
    it = json.loads(text)  # long dashes become plain hyphens
    it.pop("date", None)
    return it


def url_ok(url: str) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code in (401, 403, 405, 429)  # page exists, the site just blocks scripts
    except Exception:
        return False


def problems_with(it: dict, day: dt.date, items: list[dict]) -> list[str]:
    probs = render_v2.check(items + [{"date": day.isoformat(), **it}])
    if len(it.get("headline", "")) > 110:
        probs.append("headline is longer than 110 characters")
    if not 2 <= len(it.get("stats", [])) <= 4:
        probs.append("stats must have 2 to 4 pairs")
    for name, url in it.get("sources", []):
        if url.startswith("https://") and not url_ok(url):
            probs.append(f"source does not open: {name} {url}")
    return probs


def curate_web(day: dt.date, items: list[dict]) -> dict | str | None:
    """Paid path: Claude searches the web itself (Claude API key, or Claude Code on a PC)."""
    prompt = build_prompt(day, items)
    it, probs = None, []
    for attempt in (1, 2):
        answer = ask_claude(prompt if attempt == 1 else
                            prompt + "\n\n## Your previous answer had problems\n" + "\n".join(f"- {p}" for p in probs)
                            + "\n\nPrevious answer:\n```json\n" + json.dumps(it, indent=1, ensure_ascii=False)
                            + "\n```\nFix every problem (find a working source URL if one does not open) and return the full JSON again.")
        got = parse(answer)
        if isinstance(got, str):
            return got
        it = got
        probs = problems_with(it, day, items)
        if not probs:
            break
        log(f"Attempt {attempt} problems: " + "; ".join(probs))
    return None if probs else it


# ---------------------------------------------------------------- free path: feeds + GitHub Models

FEEDS = ROOT / "v2" / "feeds.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; Insights-of-the-Day/2.0)"}
GH_MODELS_URL = "https://models.github.ai/inference/chat/completions"
# Tried in this order; a model that is unknown, too busy or over the free limit is skipped.
GH_MODELS = os.getenv("GH_MODELS", "openai/gpt-4.1,openai/gpt-4o,openai/gpt-5-mini,openai/gpt-4.1-mini").split(",")
PER_FEED, MAX_CANDIDATES = 6, 60


def ask_llm(prompt: str) -> str:
    """Free path model: GitHub Models (GITHUB_TOKEN with models: read). On a PC, CURATE_LLM=cli uses Claude Code."""
    log(f"  asking the model ({len(prompt)} characters, about {len(prompt) // 4} tokens)")
    if os.getenv("CURATE_LLM") == "cli" or not os.getenv("GITHUB_TOKEN"):
        return ask_cli(prompt)
    import requests

    head = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Content-Type": "application/json",
            "Accept": "application/vnd.github+json"}
    last = ""
    for model in (m.strip() for m in GH_MODELS if m.strip()):
        r = requests.post(GH_MODELS_URL, headers=head, timeout=240,
                          json={"model": model, "messages": [{"role": "user", "content": prompt}]})
        if r.status_code in (401, 403):
            raise RuntimeError(f"GitHub Models refused the token ({r.status_code}): {r.text[:300]}")
        if r.status_code != 200:
            last = f"{model}: {r.status_code} {r.text[:200]}"
            log(f"  model {last}")
            continue
        log(f"  answered by {model}")
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError(f"no GitHub model answered, last: {last}")


def collect_candidates(since: dt.datetime) -> list[dict]:
    import feedparser
    import requests

    sent = {x["link"] for x in (json.loads(SENT.read_text(encoding="utf-8")) if SENT.exists() else [])}
    done = {it["sources"][0][1] for it in json.loads(DATA.read_text(encoding="utf-8"))["insights"]}
    now = dt.datetime.now(dt.timezone.utc)
    out, seen = [], set()
    for f in json.loads(FEEDS.read_text(encoding="utf-8"))["feeds"]:
        try:
            feed = feedparser.parse(requests.get(f["url"], headers=UA, timeout=25).content)
        except Exception as exc:  # one broken feed never stops the day
            log(f"  ! {f['name']}: {exc}")
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
            if not since <= when <= now + dt.timedelta(hours=1) or link in sent or link in done or key in seen:
                continue
            seen.add(key)
            summary = html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary", "")))
            got.append({"title": title, "link": link, "when": when, "source": f["name"], "kind": f["kind"],
                        "summary": re.sub(r"\s+", " ", summary).strip()[:220]})
        got.sort(key=lambda x: x["when"], reverse=True)
        out += got[:PER_FEED]
    rank = {"official": 0, "press": 1, "community": 2}
    out.sort(key=lambda x: (rank[x["kind"]], -x["when"].timestamp()))
    return out[:MAX_CANDIDATES]


def page_text(url: str, title: str, limit: int) -> str:
    import requests

    try:
        raw = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=25).text
    except Exception:
        return ""
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form|svg|noscript)\b.*?</\1>", " ", raw)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    start = text.find(title[:40]) if title else -1  # skip menus: start at the article title when found
    return text[start if start > 0 else 0:][:limit]


def trust_of(chosen: list[dict]) -> str | None:
    kinds = [c["kind"] for c in chosen]
    if "official" in kinds:
        return "mixed" if "press" in kinds else "official"
    if "press" in kinds and len(kinds) >= 2:
        return "reported"
    return None  # one press or community item alone is not enough


def parse_obj(answer: str) -> dict | str:
    m = FENCE.search(answer) or re.search(r"(\{.*\})", answer, re.S)
    if not m:
        skip = re.search(r"^\s*SKIP:.*$", answer, re.M)
        if skip:
            return skip.group().strip()
        raise ValueError(f"no JSON in the answer: {answer.strip()[-300:]}")
    return json.loads(m.group(1).replace("—", " - ").replace("–", "-"))


def curate_feeds(day: dt.date, items: list[dict]) -> dict | str | None:
    since_ist = dt.datetime.combine(prev_weekday(day), dt.time(0, 0), IST)
    cands = collect_candidates(since_ist.astimezone(dt.timezone.utc))
    log(f"{len(cands)} fresh candidates from the feeds")
    if not cands:
        return "SKIP: no fresh items in the feeds"
    recent = "\n".join(f"  - {it['date']} [{it['lane']}] {it['headline']}" for it in items[-20:])
    listing = "\n".join(f"{i}. [{c['source']} | {c['kind']} | {c['when']:%d %b}] {c['title']} - {c['summary']}"
                        for i, c in enumerate(cands, 1))
    base = (PICK.read_text(encoding="utf-8").replace("{DAY}", f"{day:%A %d %b %Y}")
            .replace("{SINCE}", f"{since_ist:%A %d %b %Y}, 00:00 IST").replace("{RECENT}", recent))
    banned: list[int] = []
    got, chosen, trust, n = {}, [], None, 0
    for _ in range(2):
        note = f"\nDo not pick these numbers again (not enough sources): {banned}\n" if banned else ""
        got = parse_obj(ask_llm(base.replace("{CANDIDATES}", listing + note)))
        if isinstance(got, str):
            return got
        n = int(got["pick"])
        if not 1 <= n <= len(cands):
            banned.append(n)
            continue
        extra = [int(k) for k in got.get("same_story", []) if str(k).isdigit()]
        chosen = [cands[n - 1]] + [cands[k - 1] for k in extra[:2] if 1 <= k <= len(cands) and k != n]
        trust = trust_of(chosen)
        if trust and got.get("lane") in render_v2.LANES:
            break
        banned.append(n)
        log(f"  pick {n} rejected (sources: {[c['kind'] for c in chosen]}, lane {got.get('lane')})")
    else:
        return None
    main = chosen[0]
    log(f"Picked #{n} [{got['lane']}] {main['source']}: {main['title']}")
    texts = [f"=== {c['source']} ({c['when']:%d %b %Y}): {c['title']}\n{c['link']}\n"
             + (page_text(c["link"], c["title"], 9000 if i == 0 else 3000) or c["summary"])
             for i, c in enumerate(chosen)]
    example = next((it for it in reversed(items) if not it.get("bench")), items[-1])
    example = {k: v for k, v in example.items() if k not in ("date", "lane", "trust", "sources", "bench")}
    write = (WRITE.read_text(encoding="utf-8").replace("{DAY}", f"{day:%A %d %b %Y}")
             .replace("{LANE}", render_v2.LANES[got["lane"]][0])
             .replace("{EXAMPLE}", json.dumps(example, indent=1, ensure_ascii=False))
             .replace("{SOURCES}", "\n\n".join(texts)))
    fixes = ""
    for attempt in (1, 2):
        card = parse_obj(ask_llm(write.replace("{FIXES}", fixes)))
        if isinstance(card, str):
            return card
        card = {k: v for k, v in card.items()
                if k in ("read_min", "headline", "buzz", "stats", "why", "try", "act", "watch", "also")}
        card = {"lane": got["lane"], "trust": trust, **card, "sources": [[c["source"], c["link"]] for c in sorted(chosen, key=lambda c: c["kind"] != "official")]}
        probs = problems_with(card, day, items)
        if not probs:
            return card
        log(f"  write attempt {attempt} problems: " + "; ".join(probs))
        fixes = ("Your previous card had these problems, fix them:\n" + "\n".join(f"- {p}" for p in probs)
                 + "\nPrevious card:\n" + json.dumps(card, ensure_ascii=False) + "\n")
    return None


def write_v0(it: dict, day: dt.date) -> None:
    """Put the v2 email where the v0 Sender flow looks for it (docs/feed.xml with marker tip-<day>)."""
    subject, body = render_v2.render(it)
    (V0_DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (V0_DOCS / "latest.html").write_text(body, encoding="utf-8")
    (V0_DOCS / "archive" / f"{day}.html").write_text(body, encoding="utf-8")
    cdata = f"<!-- tip-{day} -->\n" + body.replace("]]>", "]]]]><![CDATA[>")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Insights of the Day</title><link>https://MercyDavidTG.github.io/ai-tip-of-the-day/</link><description>One short insight every weekday</description>
<item>
<title>{html.escape(subject)}</title>
<link>{html.escape(it['sources'][0][1])}</link>
<guid isPermaLink="false">tip-{day}</guid>
<pubDate>{format_datetime(dt.datetime.now(dt.timezone.utc))}</pubDate>
<description><![CDATA[{cdata}]]></description>
</item>
</channel></rss>
"""
    (V0_DOCS / "feed.xml").write_text(rss, encoding="utf-8")
    sent = json.loads(SENT.read_text(encoding="utf-8")) if SENT.exists() else []
    sent += [{"date": f"{day}", "link": u, "title": it["headline"] if i == 0 else n, "source": n}
             for i, (n, u) in enumerate(it["sources"])]
    SENT.write_text(json.dumps(sent[-500:], indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", type=dt.date.fromisoformat)
    args = ap.parse_args()

    day = args.date or send_day(dt.datetime.now(IST))
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    items = doc["insights"]
    archive = V0_DOCS / "archive" / f"{day}.html"
    done = archive.exists() and V2_MARK in archive.read_text(encoding="utf-8")
    if done and not args.dry_run:
        log(f"Insight for {day} is already built - nothing to do.")
        return 0
    existing = next((it for it in items if it["date"] == day.isoformat()), None)
    if existing and not args.dry_run and HOLD.exists():
        log(f"Insight for {day} is curated. HOLD is on (v2/HOLD): the team feed is not touched.")
        return 0
    if existing and not args.dry_run:  # curated earlier (or by hand) but not yet put in the v0 feed
        log(f"Insight for {day} is in insights.json - publishing it.")
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "v2" / "render_v2.py")], check=True)
        write_v0(existing, day)
        return 0
    items = [it for it in items if it["date"] < day.isoformat()]

    tries = json.loads(TRIES.read_text(encoding="utf-8")) if TRIES.exists() else {}
    if tries.get(day.isoformat(), 0) >= MAX_TRIES and not args.dry_run:
        log(f"Already tried {MAX_TRIES} times for {day} - skipping this day (no email).")
        return 0
    if not args.dry_run:  # count the try first, so a crash also counts (the workflow commits data/)
        TRIES.write_text(json.dumps({day.isoformat(): tries.get(day.isoformat(), 0) + 1}), encoding="utf-8")

    log(f"Curating the insight for {day:%a %d %b %Y}")
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("CURATE_MODE") == "web":
        got = curate_web(day, items)
    else:
        got = curate_feeds(day, items)
    if isinstance(got, str):
        log(f"Skipped the day: {got}")
        return 0
    if got is None:
        log("Giving up for now, nothing published.")
        return 1
    it = got

    new = {"date": day.isoformat(), **it}
    log(f"Picked [{new['lane']}/{new['trust']}] {new['headline']}")
    if args.dry_run:
        _, body = render_v2.render(new)
        (ROOT / "v2" / "preview.html").write_text(body, encoding="utf-8")
        (ROOT / "v2" / "preview.json").write_text(json.dumps(new, indent=1, ensure_ascii=False), encoding="utf-8")
        log(f"Dry run: wrote {ROOT / 'v2' / 'preview.html'}")
        return 0

    doc["insights"] = items + [new] + [x for x in doc["insights"] if x["date"] > day.isoformat()]
    DATA.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "v2" / "render_v2.py")], check=True)
    if HOLD.exists():
        log(f"HOLD is on (v2/HOLD): {day} is saved to the v2 test page only, the team feed is not touched.")
        return 0
    write_v0(new, day)
    log(f"Published the insight for {day}.")
    return 0


if __name__ == "__main__":
    import traceback

    try:
        code = main()
    except Exception:
        log("ERROR: " + traceback.format_exc())
        code = 1
    if os.getenv("GITHUB_ACTIONS") and (code or any("Curating" in x for x in _lines)):
        RUN_LOG.write_text(os.linesep.join(_lines) + os.linesep, encoding="utf-8")
    sys.exit(code)
