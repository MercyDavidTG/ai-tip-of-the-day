"""Insights of the Day v3 - the 30-second email with a hook subject, one tip box and a riddle chain.

v3 extends v2. It runs inside the same GitHub Actions job (curate_v2.py hands over to it while v3/ACTIVE
exists), so the workflow file does not change:

  1. v2 picks the day's story from the trusted feeds and writes its checked card (v2/insights.json).
  2. v3 rewrites that card into the 30-second format with Gemini (prompt_v3.md): hook subject, inbox
     preview line, hook, the tip, 2-3 do-it-now steps, why, one number. Only facts from the card are allowed;
     every number is checked against the card. If Gemini is busy, a plain rewrite of the card is used.
  3. If v2 finds no good tip in time (or uses all its tries), v3 takes the next unused tip from
     v3/evergreen.json (checked official docs tips), so a weekday is never skipped.
  4. Every mail ends with a riddle from v3/riddles.json; the next mail opens with the answer.

Switches (files on the site branch):
  v3/ACTIVE   v3 builds every day. Without v3/LIVE the email goes only to docs/v3/ (test feed and archive).
  v3/LIVE     v3 owns the team feed (docs/feed.xml) and v2 no longer writes it. Turn the v3 Sender on with it.

    python v3/curate_v3.py                         normal run (called by curate_v2.py in the cloud)
    python v3/curate_v3.py --preview 2026-11-26    v3 email from that day's v2 card -> docs/v3/preview-<day>.html
    python v3/curate_v3.py --preview-evergreen ID  same for one evergreen tip
    python v3/curate_v3.py --test-feed 2026-11-28  rewrite docs/v3/test-feed.xml with that day's marker, for a test send
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from email.utils import format_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "v2"))
sys.path.insert(0, str(HERE))
import curate_v2 as v2  # noqa: E402
import render_v3  # noqa: E402

ACTIVE, LIVE = HERE / "ACTIVE", HERE / "LIVE"
CARDS = ROOT / "data" / "v3_cards.json"
OUT = ROOT / "docs" / "v3"
PROMPT = HERE / "prompt_v3.md"
RIDDLES = json.loads((HERE / "riddles.json").read_text(encoding="utf-8"))["riddles"]
EVERGREEN = json.loads((HERE / "evergreen.json").read_text(encoding="utf-8"))["tips"]
LAST_CHANCE = dt.time(5, 30)  # from 05:30 IST on the send day, no more waiting for a news tip
MAX_WORDS = 120  # hook + tip + steps + why + deadline: about 30 seconds of reading
BAD = re.compile("[\\u2013\\u2014\\U0001F000-\\U0001FAFF\\u2600-\\u27bf]")  # long dashes and emojis


def log(msg: str) -> None:
    v2.log(msg)  # v2 may be swapped for the running __main__ module, so look it up each time


# ---------------------------------------------------------------- data

def load_cards() -> list[dict]:
    return json.loads(CARDS.read_text(encoding="utf-8"))["cards"] if CARDS.exists() else []


def save_cards(cards: list[dict]) -> None:
    cards.sort(key=lambda c: c["date"])
    CARDS.write_text(json.dumps({"cards": cards}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def body_words(c: dict) -> int:
    return sum(words(c.get(k) or "") for k in ("hook", "tip", "why", "deadline")) + sum(words(s) for s in c["steps"])


# ---------------------------------------------------------------- checks

def problems(c: dict, source_text: str | None) -> list[str]:
    p = []
    for k in ("subject", "preheader", "hook", "tip", "steps", "why", "sources"):
        if not c.get(k):
            p.append(f"missing {k}")
    if p:
        return p
    if len(c["subject"]) > 60:
        p.append(f"subject has {len(c['subject'])} characters, at most 55")
    if len(c["preheader"]) > 95:
        p.append(f"preheader has {len(c['preheader'])} characters, at most 85")
    if "`" in c["subject"] + c["preheader"]:
        p.append("no backticks in subject or preheader")
    if "!" in c["subject"]:
        p.append("no exclamation marks in the subject")
    if c["subject"].strip().lower() == c["preheader"].strip().lower():
        p.append("preheader repeats the subject")
    if not 2 <= len(c["steps"]) <= 3:
        p.append("steps must have 2 or 3 items")
    for name, limit in (("hook", 34), ("tip", 32), ("why", 26), ("deadline", 34)):
        if words(c.get(name) or "") > limit:
            p.append(f"{name} has {words(c.get(name) or '')} words, at most {limit - 4}")
    for s in c["steps"]:
        if words(s) > 19:
            p.append(f"step too long ({words(s)} words, at most 16): {s}")
    if body_words(c) > MAX_WORDS:
        p.append(f"the mail body has {body_words(c)} words, at most {MAX_WORDS} (30 seconds)")
    num = c.get("number")
    if num and (len(num) != 2 or len(str(num[0])) > 16 or len(str(num[1])) > 44):
        p.append("number must be [value up to 14 characters, label up to 40 characters]")
    blob = json.dumps({k: c.get(k) for k in ("subject", "preheader", "hook", "tip", "steps", "why", "number", "deadline")},
                      ensure_ascii=False)
    if BAD.search(blob):
        p.append("has a long dash or an emoji, use only the plain hyphen and no emojis")
    for w in re.findall(r"\b[A-Z]{4,}\b", c["subject"] + " " + c["hook"]):
        if source_text is not None and w not in source_text:
            p.append(f"no shouting: '{w}' in capitals")
    if source_text is not None:  # every number must come from the checked card
        for n in set(re.findall(r"\d+(?:[.,]\d+)?", blob)):
            if n not in source_text:
                p.append(f"the number {n} is not in the card - use only the card's facts")
    return p


# ---------------------------------------------------------------- building a card

def from_v2_with_ai(day: dt.date, card2: dict) -> dict | None:
    src = {k: card2.get(k) for k in ("headline", "buzz", "stats", "why", "try", "act", "watch")}
    text = json.dumps(src, ensure_ascii=False)
    base = (PROMPT.read_text(encoding="utf-8").replace("{DAY}", f"{day:%A %d %b %Y}")
            .replace("{CARD}", json.dumps(src, indent=1, ensure_ascii=False)))
    fixes = ""
    for attempt in (1, 2):
        try:
            got = v2.parse_obj(v2.ask_llm(base.replace("{FIXES}", fixes)))
        except Exception as exc:
            log(f"  v3 rewrite: model failed ({str(exc)[:200]})")
            return None
        if isinstance(got, str):
            log(f"  v3 rewrite: model answered {got[:120]}")
            return None
        c = {k: got.get(k) for k in ("subject", "preheader", "hook", "tip", "steps", "why", "number", "deadline")}
        c["subject"] = re.sub(r"\s+", " ", (c.get("subject") or "").strip().rstrip("."))
        c["sources"] = card2["sources"]
        probs = problems(c, text)
        if not probs:
            return c
        log(f"  v3 rewrite attempt {attempt} problems: " + "; ".join(probs))
        fixes = ("Your previous answer had these problems, fix all of them:\n" + "\n".join(f"- {x}" for x in probs)
                 + "\nPrevious answer:\n" + json.dumps(c, ensure_ascii=False) + "\n")
    return None


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z`])", text or "") if s.strip()]


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit - 3].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def from_v2_plain(card2: dict) -> dict:
    """No model needed: a faithful, shorter layout of the checked v2 card."""
    tries = sentences(card2.get("try", ""))
    steps = [clip(s.rstrip("."), 110) for s in tries[1:4]] or ["Open the source below and try it on one small project"]
    if len(steps) == 1:
        steps.append("Share what you found with the team")
    stat = (card2.get("stats") or [None])[0]
    return {
        "subject": clip(html.unescape(card2["headline"]).replace("`", ""), 60),
        "preheader": clip(sentences(card2.get("buzz", ""))[0].replace("`", ""), 90),
        "hook": clip(sentences(card2.get("buzz", ""))[0], 200),
        "tip": clip(tries[0] if tries else card2.get("try", ""), 220),
        "steps": steps,
        "why": clip(sentences(card2.get("why", ""))[0], 160),
        "number": stat if stat and len(stat[0]) <= 16 else None,
        "deadline": card2.get("act") or None,
        "sources": card2["sources"],
    }


def pick_evergreen(cards: list[dict]) -> dict:
    used = [c.get("evergreen_id") for c in cards if c.get("evergreen_id")]
    fresh = [t for t in EVERGREEN if t["id"] not in used]
    if fresh:
        return fresh[0]
    return min(EVERGREEN, key=lambda t: max(i for i, u in enumerate(used) if u == t["id"]))  # least recently used


def chain(day: dt.date, cards: list[dict], live: bool) -> tuple[int, int, dict | None]:
    """Issue number, riddle index, and the riddle to reveal (the previous mail of the same kind)."""
    before = [c for c in cards if c["date"] < day.isoformat()]
    mine = [c for c in before if c.get("live") == live]
    no = len(mine) + 1
    idx = len(before) % len(RIDDLES)
    reveal = None
    if mine:
        prev = mine[-1]
        pday = dt.date.fromisoformat(prev["date"])
        label = "YESTERDAY'S RIDDLE, SOLVED" if (day - pday).days == 1 else f"{pday:%A}'S RIDDLE, SOLVED".upper()
        reveal = {**prev["riddle"], "label": label}
    return no, idx, reveal


# ---------------------------------------------------------------- publishing

def rss(subject: str, link: str, body: str, mark: str) -> str:
    cdata = f"<!-- tip-{mark} -->\n" + body.replace("]]>", "]]]]><![CDATA[>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Insights of the Day</title><link>https://MercyDavidTG.github.io/ai-tip-of-the-day/</link><description>One 30-second tip every weekday</description>
<item>
<title>{html.escape(subject)}</title>
<link>{html.escape(link)}</link>
<guid isPermaLink="false">tip-{mark}</guid>
<pubDate>{format_datetime(dt.datetime.now(dt.timezone.utc))}</pubDate>
<description><![CDATA[{cdata}]]></description>
</item>
</channel></rss>
"""


def reveal_for(card: dict, cards: list[dict]) -> dict | None:
    return chain(dt.date.fromisoformat(card["date"]), cards, card.get("live", False))[2]


def write_pages(card: dict, cards: list[dict], live: bool) -> tuple[str, str]:
    subject, body = render_v3.render(card, reveal_for(card, cards))
    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    (OUT / "archive" / f"{card['date']}.html").write_text(body, encoding="utf-8")
    (OUT / "latest.html").write_text(body, encoding="utf-8")
    (OUT / "test-feed.xml").write_text(rss(subject, card["sources"][0][1], body, card["date"]), encoding="utf-8")
    shown = [c for c in cards if c.get("live", False) == live]
    rows = "".join(f'<li><a href="archive/{c["date"]}.html">{dt.date.fromisoformat(c["date"]):%a %d %b %Y}</a> - '
                   f'{html.escape(render_v3.plain(c["subject"]))}</li>' for c in reversed(shown))
    (OUT / "index.html").write_text(
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
        'initial-scale=1"><title>Insights of the Day - all tips</title></head><body style="font-family:Segoe UI,Arial,'
        'sans-serif;max-width:640px;margin:24px auto;padding:0 16px;background:#EEEAE2;color:#111;">'
        '<h1>Insights of the Day</h1><p>One 30-second tip every weekday, with a riddle at the end.</p>'
        f'<ul style="line-height:1.8;">{rows}</ul></body></html>', encoding="utf-8")
    return subject, body


def publish_team(card: dict, subject: str, body: str) -> None:
    docs = ROOT / "docs"
    (docs / "archive").mkdir(parents=True, exist_ok=True)
    (docs / "archive" / f"{card['date']}.html").write_text(body, encoding="utf-8")
    (docs / "latest.html").write_text(body, encoding="utf-8")
    (docs / "feed.xml").write_text(rss(subject, card["sources"][0][1], body, card["date"]), encoding="utf-8")
    sent = json.loads(v2.SENT.read_text(encoding="utf-8")) if v2.SENT.exists() else []
    sent = [s for s in sent if s.get("date") != card["date"]]
    sent += [{"date": card["date"], "link": u, "title": render_v3.plain(card["subject"]) if i == 0 else n, "source": n}
             for i, (n, u) in enumerate(card["sources"])]
    v2.SENT.write_text(json.dumps(sent[-500:], indent=2, ensure_ascii=False), encoding="utf-8")


def team_has(day: dt.date) -> bool:
    feed = ROOT / "docs" / "feed.xml"
    return feed.exists() and f"tip-{day}" in feed.read_text(encoding="utf-8") and "RIDDLE OF THE DAY" in feed.read_text(encoding="utf-8")


# ---------------------------------------------------------------- main

def build(day: dt.date, live: bool, now: dt.datetime) -> int:
    cards = load_cards()
    have = next((c for c in cards if c["date"] == day.isoformat()), None)
    if have and have.get("live", False) == live:
        if live and not team_has(day):
            subject, body = write_pages(have, cards, live)
            publish_team(have, subject, body)
            log(f"v3: card for {day} was built, now put in the team feed.")
        else:
            log(f"v3: card for {day} is already built.")
        return 0
    cards = [c for c in cards if c["date"] != day.isoformat()]  # a test card is rebuilt when going live

    items = json.loads(v2.DATA.read_text(encoding="utf-8"))["insights"]
    card2 = next((x for x in items if x["date"] == day.isoformat()), None)
    tries = json.loads(v2.TRIES.read_text(encoding="utf-8")) if v2.TRIES.exists() else {}
    last = now >= dt.datetime.combine(day, LAST_CHANCE, v2.IST) or tries.get(day.isoformat(), 0) >= v2.MAX_TRIES
    if card2:
        log(f"v3: rewriting the v2 card for {day} into the 30-second format")
        c = from_v2_with_ai(day, card2)
        if c is None and not last:
            log("v3: rewrite failed, the next hourly run tries again.")
            return 1
        kind = "news"
        if c is None:
            log("v3: last chance - using the plain layout of the checked v2 card.")
            c, kind = from_v2_plain(card2), "news-plain"
        c.update(lane=card2["lane"], trust=card2["trust"])
    elif last:
        t = pick_evergreen(cards)
        log(f"v3: no news tip for {day} in time - using the evergreen tip '{t['id']}'.")
        c = {k: v for k, v in t.items() if k != "id"}
        c["evergreen_id"], kind = t["id"], "evergreen"
    else:
        log(f"v3: no v2 card for {day} yet - waiting (evergreen fallback from {LAST_CHANCE:%H:%M} IST that day).")
        return 0

    no, idx, _ = chain(day, cards, live)
    card = {"date": day.isoformat(), "no": no, "live": live, "kind": kind, **c,
            "riddle_index": idx, "riddle": RIDDLES[idx]}
    cards.append(card)
    save_cards(cards)
    cards = load_cards()
    subject, body = write_pages(card, cards, live)
    log(f"v3: built #{no} ({kind}) for {day}: {subject} [{body_words(card)} words]")
    if live:
        publish_team(card, subject, body)
        log("v3: published to the team feed (docs/feed.xml).")
    else:
        log("v3: test mode (no v3/LIVE): written to docs/v3/ only, the team feed is not touched.")
    return 0


def preview(card: dict, name: str) -> None:
    card = {"date": card.get("date") or dt.date.today().isoformat(), "no": 1, "live": False, **card,
            "riddle": RIDDLES[0]}
    s, b = render_v3.render(card, {**RIDDLES[1], "label": "YESTERDAY'S RIDDLE, SOLVED"})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"preview-{name}.html").write_text(b, encoding="utf-8")
    print(f"{s}\n  {body_words(card)} words -> {OUT / f'preview-{name}.html'}")
    print(json.dumps({k: card.get(k) for k in ("subject", "preheader", "hook", "tip", "steps", "why", "number", "deadline")},
                     indent=1, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=dt.date.fromisoformat)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview", type=dt.date.fromisoformat)
    ap.add_argument("--preview-evergreen")
    ap.add_argument("--plain", action="store_true", help="with --preview: no model, plain layout")
    ap.add_argument("--test-feed", type=dt.date.fromisoformat)
    args = ap.parse_args(argv)

    if args.preview:
        card2 = next(x for x in json.loads(v2.DATA.read_text(encoding="utf-8"))["insights"]
                     if x["date"] == args.preview.isoformat())
        c = from_v2_plain(card2) if args.plain else from_v2_with_ai(args.preview, card2)
        if c is None:
            print("rewrite failed")
            return 1
        preview({"date": args.preview.isoformat(), "lane": card2["lane"], "trust": card2["trust"], **c},
                f"{args.preview}{'-plain' if args.plain else ''}")
        return 0
    if args.preview_evergreen:
        t = next(t for t in EVERGREEN if t["id"] == args.preview_evergreen)
        preview({k: v for k, v in t.items() if k != "id"}, args.preview_evergreen)
        return 0
    if args.test_feed:  # newest v3 card, marked with another day so a manual test send finds it today
        cards = load_cards()
        card = cards[-1]
        s, b = render_v3.render(card, reveal_for(card, cards))
        (OUT / "test-feed.xml").write_text(rss(s, card["sources"][0][1], b, args.test_feed.isoformat()), encoding="utf-8")
        print(f"test feed: {s} (marker tip-{args.test_feed})")
        return 0

    live = LIVE.exists()
    code2 = 0
    if not args.dry_run:
        if live:
            v2.HOLD = LIVE  # v2 still picks and checks the story, but never writes the team feed
        saved = sys.argv
        sys.argv = [saved[0]] + (["--date", args.date.isoformat()] if args.date else [])
        try:
            code2 = v2.main()
        except Exception as exc:
            log(f"v2 step failed: {exc}")
            code2 = 1
        finally:
            sys.argv = saved
    now = dt.datetime.now(v2.IST)
    day = args.date or v2.send_day(now)
    try:
        code3 = build(day, live, now)
    except Exception:
        import traceback

        log("v3: ERROR " + traceback.format_exc()[-600:])
        code3 = 1
        card2 = next((x for x in json.loads(v2.DATA.read_text(encoding="utf-8"))["insights"]
                      if x["date"] == day.isoformat()), None)
        if live and card2 and not (ROOT / "docs" / "archive" / f"{day}.html").exists():
            v2.write_v0(card2, day)  # safety net: the team still gets the checked v2 email today
            log("v3: failed, so the v2 email for the day was put in the team feed instead.")
    return code3 if code3 else (code2 if not live else 0)


if __name__ == "__main__":
    import traceback

    try:
        code = main()
    except Exception:
        log("ERROR: " + traceback.format_exc())
        code = 1
    sys.exit(code)
