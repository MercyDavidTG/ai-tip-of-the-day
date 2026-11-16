"""Insights of the Day v2 - turn v2/insights.json into the email and the feed Power Automate reads.

    python v2/render_v2.py            check every insight, then write docs/v2/
                                      (feed.xml, latest.html, archive/<date>.html, index.html)
    python v2/render_v2.py --check    only check the data

The newest insight (last in the list) is the one in feed.xml. Its guid and the first line of its body
carry "tip-YYYY-MM-DD", the same marker the Sender flow already looks for.
Rules kept from v0: plain hyphens only, no emojis, plain wording.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "v2" / "insights.json"
OUT = ROOT / "docs" / "v2"
SITE = "https://mercydavidtg.github.io/ai-tip-of-the-day/v2/"

LANES = {  # name, text colour, chip background (email-safe, light only)
    "frontier": ("Frontier models", "#5B2FC2", "#EFE9FC"),
    "data": ("ML and data science", "#17693B", "#E3F4EA"),
    "aws": ("AWS", "#9A5208", "#FDF0E1"),
    "docker": ("Docker", "#0A619F", "#E3F1FB"),
    "open": ("Open weights", "#0B6B6A", "#DFF3F2"),
    "creative": ("Creative AI", "#A02A6C", "#FBE6F1"),
    "science": ("AI for science", "#344F8C", "#E6ECF8"),
    "plans": ("Your plans and limits", "#A42727", "#FBE7E7"),
    "agents": ("Agents", "#2E4BE0", "#E6EBFF"),
}
TRUST = {
    "official": ("Official source", "#17693B", "#E3F4EA"),
    "reported": ("Reported, 2+ outlets", "#8A5F00", "#FFF4D6"),
    "mixed": ("Official + reported", "#344F8C", "#E6ECF8"),
}
REQUIRED = ["date", "lane", "trust", "read_min", "headline", "buzz", "stats", "why", "try", "sources"]
BAD = re.compile("[–—\U0001F000-\U0001FAFF☀-➿]")  # en/em dashes and emojis


def check(items: list[dict]) -> list[str]:
    problems, seen = [], set()
    for i, it in enumerate(items):
        tag = it.get("date", f"#{i}")
        for key in REQUIRED:
            if not it.get(key):
                problems.append(f"{tag}: missing {key}")
        if it.get("lane") not in LANES:
            problems.append(f"{tag}: unknown lane {it.get('lane')}")
        if it.get("trust") not in TRUST:
            problems.append(f"{tag}: unknown trust {it.get('trust')}")
        if tag in seen:
            problems.append(f"{tag}: date used twice")
        seen.add(tag)
        if BAD.search(json.dumps(it, ensure_ascii=False)):
            problems.append(f"{tag}: has a long dash or an emoji")
        for _, url in it.get("sources", []):
            if not url.startswith("https://"):
                problems.append(f"{tag}: source is not https: {url}")
    dates = [it.get("date", "") for it in items]
    if dates != sorted(dates):
        problems.append("insights are not in date order (oldest first)")
    return problems


def rich(text: str) -> str:
    """Escape, then turn `code` into a grey monospace span."""
    out = html.escape(text, quote=False)
    return re.sub(r"`([^`]+)`", r'<code style="font-family:Consolas,\'Courier New\',monospace;font-size:13px;background:#E9EEF7;color:#1F2A44;padding:1px 5px;">\1</code>', out)


def render_bench(b: dict) -> str:
    """Benchmark table: {"title", "cols": [3+ model names], "groups": [[group, [[name, v1, v2, ...], ...]], ...], "note"}.
    The best number in each row is highlighted (all numbers are higher-is-better)."""
    mono = "font-family:Consolas,'Courier New',monospace;"
    head = "".join(
        f'<td align="right" bgcolor="#101828" style="background:#101828;padding:7px 8px;font-size:11.5px;font-weight:bold;color:{"#FFFFFF" if i == 0 else "#B8C2D6"};{mono}">{html.escape(c)}</td>'
        for i, c in enumerate(b["cols"]))
    rows = ""
    for group, items in b["groups"]:
        rows += (f'<tr><td colspan="{len(b["cols"]) + 1}" bgcolor="#F3F5F9" style="background:#F3F5F9;padding:6px 8px;font-size:11px;'
                 f'letter-spacing:1px;font-weight:bold;color:#5B6578;{mono}">{html.escape(group.upper())}</td></tr>')
        for name, *vals in items:
            nums = [float(v.rstrip("%")) if v not in ("-", "") else None for v in vals]
            best = max(n for n in nums if n is not None)
            cells = ""
            for i, (v, n) in enumerate(zip(vals, nums)):
                win = n == best
                style = ("font-weight:bold;color:#17693B;background:#E3F4EA;" if win else "color:#5B6578;") + ("" if i else "font-size:14px;")
                cells += f'<td align="right" style="padding:6px 8px;font-size:13px;border-bottom:1px solid #EEF1F6;{style}">{html.escape(v)}</td>'
            rows += (f'<tr><td style="padding:6px 8px;font-size:13px;color:#27324A;border-bottom:1px solid #EEF1F6;">{html.escape(name)}</td>{cells}</tr>')
    return (f'<tr><td style="padding:8px 28px 0;">'
            f'<div style="font-size:11px;letter-spacing:1px;font-weight:bold;color:#2E4BE0;padding-bottom:6px;{mono}">{html.escape(b["title"].upper())}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #DCE2EC;">'
            f'<tr><td bgcolor="#101828" style="background:#101828;padding:7px 8px;font-size:11.5px;font-weight:bold;color:#B8C2D6;{mono}">BENCHMARK</td>{head}</tr>'
            f'{rows}</table>'
            f'<div style="font-size:12px;line-height:1.5;color:#8A94A8;padding-top:6px;">{rich(b.get("note", ""))}</div></td></tr>')


def render(it: dict, prev_link: str = "") -> tuple[str, str]:
    day = dt.date.fromisoformat(it["date"])
    lane, lc, lb = LANES[it["lane"]]
    trust, tc, tb = TRUST[it["trust"]]
    subject = f"{lane} · {it['headline']}"
    stats = "".join(
        f'<td valign="top" style="padding:0 8px 8px 0;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td bgcolor="#F3F5F9" style="padding:8px 12px;background:#F3F5F9;">'
        f'<div style="font-size:17px;font-weight:bold;color:#101828;line-height:1.2;">{html.escape(v)}</div>'
        f'<div style="font-size:12px;color:#5B6578;line-height:1.35;">{html.escape(k)}</div></td></tr></table></td>'
        for v, k in it["stats"])
    act = (f'<tr><td style="padding:12px 28px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
           f'<td bgcolor="#FBE7E7" style="background:#FBE7E7;padding:12px 16px;font-size:14.5px;line-height:1.55;color:#4A1414;">'
           f'<div style="font-size:11px;letter-spacing:1px;font-weight:bold;color:#A42727;font-family:Consolas,\'Courier New\',monospace;">ACT TODAY</div>'
           f'{rich(it["act"])}</td></tr></table></td></tr>') if it.get("act") else ""
    watch = (f'<tr><td style="padding:12px 28px 0;font-size:14px;line-height:1.55;color:#27324A;">'
             f'<div style="font-size:11px;letter-spacing:1px;font-weight:bold;color:#8A5F00;font-family:Consolas,\'Courier New\',monospace;">WATCH OUT</div>'
             + "".join(f'<div style="padding-top:3px;">- {rich(w)}</div>' for w in it["watch"])
             + '</td></tr>') if it.get("watch") else ""
    bench = render_bench(it["bench"]) if it.get("bench") else ""
    also = (f'<tr><td style="padding:10px 28px 0;font-size:13.5px;line-height:1.55;color:#5B6578;">'
            f'<b style="color:#101828;">Also new:</b> {rich(it["also"])}</td></tr>') if it.get("also") else ""
    sources = " &nbsp;·&nbsp; ".join(
        f'<a href="{html.escape(u)}" style="color:#2E4BE0;text-decoration:none;font-weight:bold;">{html.escape(n)}</a>'
        for n, u in it["sources"])
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#EEF1F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#EEF1F6"><tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="width:600px;max-width:600px;background:#FFFFFF;font-family:'Segoe UI',Arial,sans-serif;color:#101828;border:1px solid #DCE2EC;">
<tr><td bgcolor="#101828" style="background:#101828;padding:14px 28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="font-size:12px;letter-spacing:2px;font-weight:bold;color:#FFFFFF;font-family:Consolas,'Courier New',monospace;">INSIGHTS OF THE DAY</td>
<td align="right" style="font-size:12px;color:#B8C2D6;font-family:Consolas,'Courier New',monospace;">{day:%a %d %b %Y} &nbsp;·&nbsp; {it['read_min']} min read</td>
</tr></table></td></tr>
<tr><td style="padding:18px 28px 0;">
<span style="display:inline-block;font-size:12px;font-weight:bold;color:{lc};background:{lb};padding:3px 10px;border-radius:10px;">{lane}</span>
&nbsp;<span style="display:inline-block;font-size:12px;color:{tc};background:{tb};padding:3px 10px;border-radius:10px;">{trust}</span>
</td></tr>
<tr><td style="padding:12px 28px 0;font-size:24px;line-height:1.25;font-weight:bold;color:#101828;">{rich(it['headline'])}</td></tr>
<tr><td style="padding:10px 28px 0;font-size:15px;line-height:1.6;color:#27324A;">{rich(it['buzz'])}</td></tr>
<tr><td style="padding:14px 28px 0;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>{stats}</tr></table></td></tr>
{bench}
<tr><td style="padding:6px 28px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="border-left:3px solid {lc};padding:2px 0 2px 14px;font-size:14.5px;line-height:1.55;color:#27324A;">
<div style="font-size:11px;letter-spacing:1px;font-weight:bold;color:{lc};font-family:Consolas,'Courier New',monospace;">WHY IT MATTERS TO YOU</div>{rich(it['why'])}</td>
</tr></table></td></tr>
<tr><td style="padding:14px 28px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="#F0F4FF" style="background:#F0F4FF;padding:12px 16px;font-size:14.5px;line-height:1.55;color:#27324A;">
<div style="font-size:11px;letter-spacing:1px;font-weight:bold;color:#2E4BE0;font-family:Consolas,'Courier New',monospace;">TRY IT IN 5 MINUTES</div>{rich(it['try'])}</td>
</tr></table></td></tr>
{act}
{watch}
{also}
<tr><td style="padding:14px 28px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="border-top:1px solid #DCE2EC;padding-top:10px;font-size:13.5px;line-height:1.6;color:#5B6578;">Read: {sources}</td></tr></table></td></tr>
<tr><td style="padding:14px 28px 18px;font-size:11.5px;color:#8A94A8;">Curated from trusted public sources · one insight a day, skipped when nothing is worth your time · <a href="{SITE}" style="color:#8A94A8;">all past insights</a></td></tr>
</table></td></tr></table></body></html>"""
    return subject, body


def index_page(items: list[dict]) -> str:
    rows = "".join(
        f'<li><a href="archive/{it["date"]}.html">{dt.date.fromisoformat(it["date"]):%a %d %b}</a> '
        f'<span>{html.escape(LANES[it["lane"]][0])}</span> {html.escape(it["headline"])}</li>'
        for it in reversed(items))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Insights of the Day</title><style>body{{font-family:'Segoe UI',Arial,sans-serif;max-width:760px;margin:30px auto;padding:0 16px;color:#101828;line-height:1.6}}
li{{margin:6px 0}} a{{color:#2E4BE0;font-weight:600}} span{{color:#5B6578;font-size:13px;margin:0 6px}}</style></head>
<body><h1>Insights of the Day</h1><p>One thing a day from AI, ML, data, AWS and Docker that is worth your five minutes.</p><ul>{rows}</ul></body></html>"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    items = json.loads(DATA.read_text(encoding="utf-8"))["insights"]
    problems = check(items)
    for p in problems:
        print("PROBLEM:", p)
    if problems:
        return 1
    print(f"{len(items)} insights OK, newest {items[-1]['date']}")
    if args.check:
        return 0
    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    for it in items:
        _, body = render(it)
        (OUT / "archive" / f"{it['date']}.html").write_text(body, encoding="utf-8")
    newest = items[-1]
    subject, body = render(newest)
    (OUT / "latest.html").write_text(body, encoding="utf-8")
    (OUT / "index.html").write_text(index_page(items), encoding="utf-8")
    cdata = f"<!-- tip-{newest['date']} -->\n" + body.replace("]]>", "]]]]><![CDATA[>")
    link = newest["sources"][0][1]
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Insights of the Day v2</title><link>{SITE}</link><description>One curated insight a day</description>
<item>
<title>{html.escape(subject)}</title>
<link>{html.escape(link)}</link>
<guid isPermaLink="false">tip-{newest['date']}</guid>
<pubDate>{format_datetime(dt.datetime.now(dt.timezone.utc))}</pubDate>
<description><![CDATA[{cdata}]]></description>
</item>
</channel></rss>
"""
    (OUT / "feed.xml").write_text(rss, encoding="utf-8")
    print(f"Wrote {OUT / 'feed.xml'} for {newest['date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
