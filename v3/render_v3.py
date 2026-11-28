"""Insights of the Day v3 - the compact 30-second email.

Order: hook headline -> yesterday's riddle answer -> THE TREND -> TRY THIS (tip + steps) -> number + THINK ABOUT IT
-> source -> RIDDLE (hint hidden until selected) -> streak bar.
Built for Outlook (desktop, web, mobile): tables only, inline styles, bgcolor on every coloured cell, no images,
no web fonts, no scripts. Plain hyphens only, no emojis.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
RIDDLES = json.loads((HERE / "riddles.json").read_text(encoding="utf-8"))["riddles"]
SITE = "https://mercydavidtg.github.io/ai-tip-of-the-day/v3/"
MARK = "Curated from trusted public sources"

INK, PAPER, CARD, CREAM = "#111111", "#EEEAE2", "#FFFFFF", "#FFFBEA"
SUN, GREY, SOFT = "#FFD23F", "#5F5A52", "#8A847A"
PURPLE, LILAC, DEEP = "#5B21B6", "#F1EBFF", "#24104F"
FONT = "'Segoe UI',Arial,Helvetica,sans-serif"
MONO = "Consolas,'Courier New',monospace"
LANES = {"frontier": "FRONTIER AI", "data": "DATA + ML", "agents": "AI AGENTS", "open": "OPEN MODELS",
         "creative": "CREATIVE AI", "science": "AI FOR SCIENCE", "plans": "AI TOOLS", "aws": "CLOUD", "docker": "DEV TOOLS"}
TRUST = {"official": "official source", "mixed": "official + press", "reported": "reported by 2+ outlets"}


def esc(t) -> str:
    return html.escape(str(t or ""), quote=True)


def plain(t: str) -> str:
    return re.sub(r"`([^`]+)`", r"\1", t or "")


def rich(t: str, size: int) -> str:
    out = html.escape(t or "", quote=False)
    return re.sub(r"`([^`]+)`", lambda m: (
        f'<span style="font-family:{MONO};font-size:{size - 1}px;background:#F2EFE8;color:{INK};padding:0 4px;'
        f'border:1px solid #DDD6C8;">{m.group(1)}</span>'), out)


def label(text: str, color: str = GREY) -> str:
    return (f'<div style="font-family:{MONO};font-size:10.5px;letter-spacing:2px;font-weight:bold;color:{color};'
            f'padding-bottom:3px;">{text}</div>')


def week(day: dt.date) -> str:
    cells = []
    for i, n in enumerate(["M", "T", "W", "T", "F"]):
        bg, fg = (INK, "#FFFFFF") if i < day.weekday() else ((SUN, INK) if i == day.weekday() else (CARD, SOFT))
        cells.append(f'<td width="22" height="22" align="center" bgcolor="{bg}" style="background:{bg};border:2px solid {INK};'
                     f'font-family:{FONT};font-size:11px;font-weight:bold;color:{fg};">{n}</td>'
                     '<td width="4" style="font-size:0;">&nbsp;</td>')
    return '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>' + "".join(cells) + "</tr></table>"


def render(card: dict, reveal: dict | None) -> tuple[str, str]:
    day = dt.date.fromisoformat(card["date"])
    subject = f"{plain(card['subject'])} | Insights #{card['no']}"
    web = f"{SITE}archive/{day}.html"
    evergreen = card.get("kind") == "evergreen"
    tag = "CLASSIC TIP" if evergreen else LANES.get(card.get("lane", ""), "AI + TECH")
    ctx_label, angle_label = ("THE PROBLEM", "WHY IT MATTERS") if evergreen else ("THE TREND", "THINK ABOUT IT")
    rid = RIDDLES[card["riddle_index"]]
    nxt = "Monday's" if day.weekday() == 4 else "tomorrow's"
    src_name, src_url = card["sources"][0]
    share = "mailto:?" + urllib.parse.urlencode(
        {"subject": f"Worth 30 seconds: {plain(card['subject'])}",
         "body": f"From today's Insights of the Day:\n\n{plain(card['tip'])}\n\n{web}"}, quote_via=urllib.parse.quote)
    filler = "&#847;&zwnj;&nbsp;" * 60

    if reveal:
        top = (f'<b>{esc(reveal["label"])}:</b> <i>{esc(reveal["q"])}</i> <b>Answer: {esc(reveal["a"])}.</b> '
               f'{esc(reveal["why"])}')
    else:
        top = "<b>New here?</b> Every mail ends with a riddle, and the answer opens the next mail."

    steps = "".join(
        f'<tr><td width="26" valign="top" style="padding:0 9px 6px 0;"><table role="presentation" cellpadding="0" '
        f'cellspacing="0" border="0"><tr><td width="20" height="20" align="center" bgcolor="{SUN}" style="background:{SUN};'
        f'border:2px solid {INK};font-family:{FONT};font-size:12px;font-weight:bold;color:{INK};">{i}</td></tr></table></td>'
        f'<td valign="top" style="padding:1px 0 6px;font-family:{FONT};font-size:14px;line-height:1.4;color:{INK};">'
        f'{rich(s, 14)}</td></tr>' for i, s in enumerate(card["steps"], 1))

    num = ""
    if card.get("number"):
        v, lab = card["number"]
        num = (f'<td width="30%" valign="middle" bgcolor="{INK}" style="background:{INK};padding:10px 12px;">'
               f'<div style="font-family:{FONT};font-size:22px;line-height:1.1;font-weight:bold;color:{SUN};">{esc(v)}</div>'
               f'<div style="font-family:{FONT};font-size:11px;line-height:1.3;color:#E8E3D8;padding-top:3px;">{esc(lab)}</div></td>'
               '<td width="10" style="font-size:0;">&nbsp;</td>')

    body = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">
<title>{esc(subject)}</title></head>
<body style="margin:0;padding:0;background:{PAPER};" bgcolor="{PAPER}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{PAPER};">{esc(card.get('preheader'))}{filler}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" style="background:{PAPER};"><tr><td align="center" style="padding:14px 8px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

<tr><td style="padding:0 2px 7px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="font-family:{MONO};font-size:11.5px;letter-spacing:2px;font-weight:bold;color:{INK};">INSIGHTS OF THE DAY #{card['no']}</td>
<td align="right" style="font-family:{FONT};font-size:11.5px;color:{GREY};">{day:%a %d %b} | 30-second read | <a href="{web}" style="color:{GREY};">browser</a></td>
</tr></table></td></tr>

<tr><td bgcolor="{CARD}" style="background:{CARD};border:3px solid {INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">

<tr><td bgcolor="{INK}" style="background:{INK};padding:16px 22px 18px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="{SUN}" style="background:{SUN};padding:3px 9px;font-family:{MONO};font-size:10.5px;letter-spacing:2px;font-weight:bold;color:{INK};">{tag}</td></tr></table>
<div style="font-family:{FONT};font-size:23px;line-height:1.2;font-weight:bold;color:#FFFFFF;padding-top:10px;">{esc(plain(card['subject']))}</div>
</td></tr>

<tr><td bgcolor="{SUN}" style="background:{SUN};padding:8px 22px;border-bottom:3px solid {INK};font-family:{FONT};font-size:12.5px;line-height:1.4;color:{INK};">{top}</td></tr>

<tr><td style="padding:14px 22px 0;">{label(ctx_label)}<div style="font-family:{FONT};font-size:14.5px;line-height:1.45;color:{INK};">{rich(card['context'], 14)}</div></td></tr>

<tr><td style="padding:12px 22px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="{CREAM}" style="background:{CREAM};border:3px solid {INK};padding:11px 14px 6px;">{label("TRY THIS", INK)}
<div style="font-family:{FONT};font-size:16.5px;line-height:1.35;font-weight:bold;color:{INK};padding-bottom:9px;">{rich(card['tip'], 16)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{steps}</table></td></tr></table></td></tr>

<tr><td style="padding:12px 22px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{num}
<td valign="middle" style="border-left:4px solid {SUN};padding:2px 0 2px 12px;">{label(angle_label)}<div style="font-family:{FONT};font-size:14px;line-height:1.4;color:{INK};">{rich(card['angle'], 14)}</div></td>
</tr></table></td></tr>

<tr><td style="padding:10px 22px 14px;font-family:{FONT};font-size:12px;color:{GREY};">Source: <a href="{esc(src_url)}" style="color:{INK};font-weight:bold;">{esc(src_name)}</a> ({TRUST.get(card.get('trust'), 'checked source')})</td></tr>

<tr><td style="padding:0 22px 18px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="{LILAC}" style="background:{LILAC};border:2px dashed {PURPLE};padding:11px 14px;">{label("RIDDLE OF THE DAY", PURPLE)}
<div style="font-family:{FONT};font-size:15px;line-height:1.4;font-weight:bold;color:{DEEP};">{esc(rid['q'])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:7px;"><tr>
<td width="1%" nowrap style="font-family:{FONT};font-size:11.5px;color:#4B3A75;padding-right:8px;">Hint (select it):</td>
<td bgcolor="{PURPLE}" style="background:{PURPLE};padding:3px 8px;font-family:{FONT};font-size:12px;color:{PURPLE};">{esc(rid['hint'])}</td></tr></table>
<div style="font-family:{FONT};font-size:12px;color:{DEEP};padding-top:6px;"><b>Answer in {nxt} mail at 10:00 IST.</b></div>
</td></tr></table></td></tr>

</table></td></tr>

<tr><td style="padding:10px 2px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td valign="middle">{week(day)}</td>
<td align="right" valign="middle" style="font-family:{FONT};font-size:11.5px;line-height:1.5;color:{GREY};">
<a href="{esc(share)}" style="color:{INK};font-weight:bold;">Forward to a teammate</a> | <a href="{SITE}" style="color:{GREY};">All insights</a></td>
</tr></table></td></tr>
<tr><td align="center" style="padding:8px 2px 0;font-family:{FONT};font-size:11px;color:{SOFT};">{MARK}, checked before sending. One insight a day, under 30 seconds.</td></tr>

</table></td></tr></table>
</body></html>
"""
    return subject, body
