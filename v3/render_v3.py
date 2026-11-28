"""Insights of the Day v3 - the 30-second email: hook subject, one tip box, do-it-now steps, a riddle.

    python v3/render_v3.py --sample      write docs/v3/sample.html from the newest v3 card (for a look)

Built for Outlook (desktop, web and mobile): tables only, inline styles, bgcolor on every coloured cell,
no web fonts, no images, no scripts. Rules kept from v0: plain hyphens only, no emojis, no long dashes.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://mercydavidtg.github.io/ai-tip-of-the-day/v3/"
MARK = "Curated from trusted public sources"  # same footer mark v2 uses, so v2 sees the day as built

INK, PAPER, CARD = "#111111", "#EEEAE2", "#FFFFFF"
SUN, SUN_INK = "#FFD23F", "#111111"  # the signature yellow
GREY, SOFT = "#5F5A52", "#8A847A"
PURPLE, LILAC = "#5B21B6", "#F1EBFF"
RED, ROSE = "#B42318", "#FDECEA"
FONT = "'Segoe UI',Arial,Helvetica,sans-serif"
MONO = "Consolas,'Courier New',monospace"
DEFAULT_LABEL = "YESTERDAY'S RIDDLE, SOLVED"

LANE_NAMES = {
    "frontier": "FRONTIER MODELS", "data": "ML + DATA", "aws": "AWS", "docker": "DOCKER",
    "open": "OPEN WEIGHTS", "creative": "CREATIVE AI", "science": "AI FOR SCIENCE",
    "plans": "PLANS + LIMITS", "agents": "AGENTS", "classic": "CLASSIC TIP",
}
TRUST_NAMES = {"official": "Official source", "mixed": "Official + reported", "reported": "Reported by 2+ outlets"}


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def rich(text: str, size: int = 14) -> str:
    """Escape, then `code` becomes an inline monospace chip."""
    out = html.escape(text or "", quote=False)
    return re.sub(r"`([^`]+)`", lambda m: (
        f'<span style="font-family:{MONO};font-size:{size - 1}px;background:#F2EFE8;color:{INK};'
        f'padding:1px 5px;border:1px solid #DDD6C8;">{m.group(1)}</span>'), out)


def plain(text: str) -> str:
    return re.sub(r"`([^`]+)`", r"\1", text or "")


def subject_line(card: dict) -> str:
    return f"{plain(card['subject'])} | Insights #{card['no']}"


def week_tracker(day: dt.date) -> str:
    cells = []
    for i, name in enumerate(["MON", "TUE", "WED", "THU", "FRI"]):
        if i < day.weekday():
            bg, fg, border = INK, "#FFFFFF", INK
        elif i == day.weekday():
            bg, fg, border = SUN, SUN_INK, INK
        else:
            bg, fg, border = CARD, SOFT, "#CFC8BB"
        cells.append(f'<td width="20%" align="center" bgcolor="{bg}" style="background:{bg};border:2px solid {border};'
                     f'padding:7px 0;font-family:{FONT};font-size:11px;font-weight:bold;letter-spacing:1px;color:{fg};">{name}</td>'
                     + ('<td width="6" style="font-size:0;line-height:0;">&nbsp;</td>' if i < 4 else ""))
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            + "".join(cells) + "</tr></table>")


def render(card: dict, reveal: dict | None) -> tuple[str, str]:
    """card: the v3 card (see curate_v3.py). reveal: the previous mail's riddle, or None."""
    day = dt.date.fromisoformat(card["date"])
    subject = subject_line(card)
    lane = LANE_NAMES.get(card.get("lane", ""), "TIP")
    web = f"{SITE}archive/{day}.html"
    share = "mailto:?" + urllib.parse.urlencode(
        {"subject": f"Worth 30 seconds: {plain(card['subject'])}",
         "body": f"Saw this in today's Insights of the Day and thought of you:\n\n{plain(card['tip'])}\n\n{web}"},
        quote_via=urllib.parse.quote)
    src_name, src_url = card["sources"][0]
    trust = TRUST_NAMES.get(card.get("trust", ""), "Checked source")
    pre = esc(card.get("preheader", ""))
    filler = "&#847;&zwnj;&nbsp;" * 60  # stops Outlook from pulling body text into the preview line

    steps = "".join(
        f'<tr><td width="34" valign="top" style="padding:0 12px 10px 0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="30" height="30" align="center" valign="middle" bgcolor="{SUN}" style="background:{SUN};border:2px solid {INK};'
        f'font-family:{FONT};font-size:15px;font-weight:bold;color:{SUN_INK};">{i}</td></tr></table></td>'
        f'<td valign="middle" style="padding:0 0 10px 0;font-family:{FONT};font-size:15px;line-height:1.45;color:{INK};">{rich(s, 15)}</td></tr>'
        for i, s in enumerate(card["steps"], 1))

    num_html = ""
    if card.get("number"):
        value, label = card["number"]
        num_html = (f'<td width="38%" valign="top" bgcolor="{INK}" style="background:{INK};padding:16px 18px;">'
                    f'<div style="font-family:{FONT};font-size:30px;line-height:1.05;font-weight:bold;color:{SUN};">{esc(value)}</div>'
                    f'<div style="font-family:{FONT};font-size:12px;line-height:1.35;color:#E8E3D8;padding-top:6px;">{esc(label)}</div></td>'
                    '<td width="12" style="font-size:0;">&nbsp;</td>')
    why_html = (f'<td valign="top" bgcolor="{CARD}" style="background:{CARD};border:2px solid {INK};padding:14px 16px;">'
                f'<div style="font-family:{MONO};font-size:11px;letter-spacing:2px;font-weight:bold;color:{GREY};">WHY YOU WILL CARE</div>'
                f'<div style="font-family:{FONT};font-size:14.5px;line-height:1.45;color:{INK};padding-top:5px;">{rich(card["why"])}</div></td>')

    deadline = ""
    if card.get("deadline"):
        deadline = (f'<tr><td style="padding:12px 28px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
                    f'<tr><td bgcolor="{ROSE}" style="background:{ROSE};border-left:6px solid {RED};padding:10px 14px;font-family:{FONT};'
                    f'font-size:14px;line-height:1.45;color:#4A1410;"><b style="color:{RED};">ACT BY THE DEADLINE:</b> {rich(card["deadline"])}</td></tr></table></td></tr>')

    reveal_html = ""
    if reveal:
        reveal_html = (f'<tr><td bgcolor="{SUN}" style="background:{SUN};padding:12px 28px;border-bottom:3px solid {INK};">'
                       f'<div style="font-family:{MONO};font-size:11px;letter-spacing:2px;font-weight:bold;color:{SUN_INK};">{esc(reveal.get("label") or DEFAULT_LABEL)}</div>'
                       f'<div style="font-family:{FONT};font-size:13.5px;line-height:1.45;color:{SUN_INK};padding-top:4px;">'
                       f'<i>{esc(reveal["q"])}</i><br><b>Answer: {esc(reveal["a"])}.</b> {esc(reveal["why"])}</div></td></tr>')
    else:
        reveal_html = (f'<tr><td bgcolor="{SUN}" style="background:{SUN};padding:10px 28px;border-bottom:3px solid {INK};font-family:{FONT};'
                       f'font-size:13.5px;line-height:1.45;color:{SUN_INK};"><b>New here?</b> Every mail ends with a riddle. '
                       'The answer opens the next mail.</td></tr>')

    rid = card["riddle"]
    nxt = "Monday's" if day.weekday() == 4 else "tomorrow's"
    body = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">
<title>{esc(subject)}</title></head>
<body style="margin:0;padding:0;background:{PAPER};" bgcolor="{PAPER}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{PAPER};">{pre}{filler}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" style="background:{PAPER};"><tr><td align="center" style="padding:18px 8px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

<tr><td style="padding:0 4px 10px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="font-family:{MONO};font-size:12px;letter-spacing:2px;font-weight:bold;color:{INK};">INSIGHTS OF THE DAY &nbsp;#{card['no']}</td>
<td align="right" style="font-family:{FONT};font-size:12px;color:{GREY};">{day:%a %d %b} &nbsp;|&nbsp; <a href="{web}" style="color:{GREY};text-decoration:underline;">open in browser</a></td>
</tr></table></td></tr>

<tr><td bgcolor="{CARD}" style="background:{CARD};border:3px solid {INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">

<tr><td bgcolor="{INK}" style="background:{INK};padding:24px 28px 26px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="{SUN}" style="background:{SUN};padding:4px 10px;font-family:{MONO};font-size:11px;letter-spacing:2px;font-weight:bold;color:{SUN_INK};">{lane}</td>
<td style="padding-left:10px;font-family:{MONO};font-size:11px;letter-spacing:2px;font-weight:bold;color:#BDB6A8;">30-SECOND READ</td>
</tr></table>
<div style="font-family:{FONT};font-size:27px;line-height:1.18;font-weight:bold;color:#FFFFFF;padding-top:14px;">{esc(plain(card['subject']))}</div>
<div style="font-family:{FONT};font-size:15.5px;line-height:1.5;color:#E8E3D8;padding-top:10px;">{rich(card['hook'], 15)}</div>
</td></tr>

{reveal_html}

<tr><td style="padding:24px 28px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="{INK}" style="background:{INK};padding:5px 12px;font-family:{MONO};font-size:11px;letter-spacing:2px;font-weight:bold;color:{SUN};" width="1%" nowrap>TODAY'S TIP</td><td>&nbsp;</td></tr>
<tr><td colspan="2" bgcolor="#FFFBEA" style="background:#FFFBEA;border:3px solid {INK};padding:16px 18px;font-family:{FONT};font-size:19px;line-height:1.4;font-weight:bold;color:{INK};">{rich(card['tip'], 18)}</td></tr>
</table></td></tr>

<tr><td style="padding:20px 28px 4px;">
<div style="font-family:{MONO};font-size:11px;letter-spacing:2px;font-weight:bold;color:{GREY};padding-bottom:10px;">DO IT NOW</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{steps}</table>
</td></tr>

<tr><td style="padding:8px 28px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{num_html}{why_html}</tr></table></td></tr>
{deadline}

<tr><td style="padding:18px 28px 22px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="{INK}" style="background:{INK};padding:11px 18px;"><a href="{esc(src_url)}" style="font-family:{FONT};font-size:14px;font-weight:bold;color:#FFFFFF;text-decoration:none;">Read the source &gt;</a></td>
<td style="padding-left:14px;font-family:{FONT};font-size:12.5px;line-height:1.4;color:{GREY};">{esc(src_name)}<br>{trust}</td>
</tr></table></td></tr>

<tr><td style="padding:0 28px 26px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="{LILAC}" style="background:{LILAC};border:2px dashed {PURPLE};padding:18px 20px;">
<div style="font-family:{MONO};font-size:11px;letter-spacing:2px;font-weight:bold;color:{PURPLE};">RIDDLE OF THE DAY</div>
<div style="font-family:{FONT};font-size:17px;line-height:1.45;font-weight:bold;color:#24104F;padding-top:6px;">{esc(rid['q'])}</div>
<div style="font-family:{FONT};font-size:12.5px;line-height:1.4;color:#4B3A75;padding-top:10px;">Stuck? Select the purple bar to see a hint:</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:5px;"><tr>
<td bgcolor="{PURPLE}" style="background:{PURPLE};padding:6px 10px;font-family:{FONT};font-size:13px;line-height:1.4;color:{PURPLE};">{esc(rid['hint'])}</td></tr></table>
<div style="font-family:{FONT};font-size:13px;line-height:1.45;color:#24104F;padding-top:10px;"><b>The answer opens {nxt} mail at 10:00 IST.</b> Keep it to yourself till then.</div>
</td></tr></table></td></tr>

</table></td></tr>

<tr><td style="padding:16px 4px 0;">{week_tracker(day)}</td></tr>
<tr><td style="padding:10px 4px 0;font-family:{FONT};font-size:13px;line-height:1.5;color:{INK};" align="center">
<b>One tip a day, under 30 seconds.</b> Don't break the streak.</td></tr>
<tr><td style="padding:12px 4px 0;font-family:{FONT};font-size:12px;line-height:1.6;color:{GREY};" align="center">
<a href="{esc(share)}" style="color:{INK};font-weight:bold;text-decoration:underline;">Forward to a teammate who needs this</a>
&nbsp;|&nbsp; <a href="{SITE}" style="color:{GREY};text-decoration:underline;">All past tips</a><br>
{MARK}, checked before sending. Insights of the Day.</td></tr>

</table></td></tr></table>
</body></html>
"""
    return subject, body


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cards = json.loads((ROOT / "data" / "v3_cards.json").read_text(encoding="utf-8"))["cards"]
    out = ROOT / "docs" / "v3" / "sample.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    s, b = render(cards[-1], None)
    out.write_text(b, encoding="utf-8")
    print(s, "->", out)
