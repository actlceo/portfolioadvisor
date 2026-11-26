"""Portfolio page. Reads data/snapshot.json written by advisor.py; never calls a broker.

Dev:     python dashboard.py                      -> 127.0.0.1:8082 (no token needed, direct local only)
Server:  PM_DASH_TOKEN=... PM_DASH_PORT=5041      -> token required for every request, behind nginx
Open https://<subdomain>/?token=...  (the token is remembered in a cookie).
"""
from __future__ import annotations

import hmac
import json
import os
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, abort, make_response, request

SNAP = Path(__file__).parent / "data" / "snapshot.json"
HOST = os.getenv("PM_DASH_HOST", "127.0.0.1")
TOKEN = os.getenv("PM_DASH_TOKEN", "")
PORT = int(os.getenv("PM_DASH_PORT", "8082" if HOST == "127.0.0.1" else "5041"))
IST = ZoneInfo("Asia/Kolkata")

app = Flask(__name__)


def _authorised() -> bool:
    """Token whenever one is set. With no token, only a direct local request is allowed:
    a request carrying proxy headers (nginx adds X-Forwarded-For / X-Real-IP) came from
    outside, so it is refused. Fail-closed: a proxied dashboard can never be tokenless."""
    if TOKEN:
        got = request.args.get("token") or request.cookies.get("pm_token") or ""
        return hmac.compare_digest(got, TOKEN)
    proxied = bool(request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP"))
    return HOST == "127.0.0.1" and not proxied


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------

def _grp(n: float) -> str:
    """Indian digit grouping: 24,438,491 -> 2,44,38,491."""
    s, neg = f"{abs(round(n)):d}", n < 0
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + s


def _inr(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} L"
    return f"{sign}₹{_grp(a)}"


def _pct(v, nd=1, sign=True):
    if v is None:
        return '<span class="mute">&ndash;</span>'
    cls = "up" if v > 0 else "down" if v < 0 else "mute"
    return f'<span class="{cls}">{v:+.{nd}f}%</span>' if sign else f'<span class="{cls}">{v:.{nd}f}%</span>'


def _age_minutes(as_of: str) -> float:
    return (datetime.now(IST) - datetime.fromisoformat(as_of)).total_seconds() / 60


def _in_session(t: datetime) -> bool:
    return t.weekday() < 5 and (9, 25) <= (t.hour, t.minute) <= (15, 35)


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#14171f;--mute:#6b7280;--line:#e5e7eb;--up:#0f7b3f;--down:#c0292b;
--warn:#b45309;--warnbg:#fef3c7;--chip:#eef2ff;--accent:#2f4fd8;--bar:#cbd5e1;--over:#c0292b}
@media(prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b24;--ink:#e6e8ee;--mute:#8b93a4;--line:#262c39;
--up:#3ecf7a;--down:#ff6b6e;--warn:#f5b04a;--warnbg:#3a2d12;--chip:#222a44;--accent:#8aa2ff;--bar:#364056;--over:#ff6b6e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:4px 0 2px}h1 small{font-weight:400;color:var(--mute);font-size:13px}
h2{font-size:15px;margin:22px 0 8px}
.sub{color:var(--mute);margin-bottom:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .k{color:var(--mute);font-size:12px}.tile .v{font-size:20px;font-weight:650;margin-top:2px}
.tile .s{font-size:12px;color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.warn{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 12px;margin:10px 0;font-weight:600}
.up{color:var(--up)}.down{color:var(--down)}.mute{color:var(--mute)}
.alert{padding:8px 0;border-bottom:1px solid var(--line)}.alert:last-child{border:0}
.alert b{margin-right:4px}.act{font-weight:650}.kind{color:var(--mute);font-size:12px;margin-left:6px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:650;background:var(--chip);color:var(--accent)}
.pill.EXIT,.pill.CONSOLIDATE{background:#fde8e8;color:#9b1c1c}.pill.TRIM{background:#fff1d6;color:#92400e}
.pill.CORE_HOLD{background:#dcfce7;color:#166534}.pill.HOLD_MONITOR{background:#e0e7ff;color:#3730a3}
@media(prefers-color-scheme:dark){.pill.EXIT,.pill.CONSOLIDATE{background:#4a1d1d;color:#ffb4b4}.pill.TRIM{background:#4a3410;color:#ffd48a}
.pill.CORE_HOLD{background:#14391f;color:#8fe0ac}.pill.HOLD_MONITOR{background:#252d54;color:#aab7ff}}
.bar{height:10px;background:var(--bar);border-radius:5px;position:relative;overflow:hidden;min-width:90px}
.bar i{display:block;height:100%;background:var(--accent)}.bar i.over{background:var(--over)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:760px){.row2{grid-template-columns:1fr}}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 10px}
.chips button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit}
.chips button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:640px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{position:sticky;top:0;background:var(--card);font-size:12px;color:var(--mute);font-weight:600}
td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}
tr.stale td{color:var(--mute)}tr:last-child td{border:0}
.foot{color:var(--mute);font-size:12px;margin:18px 0 30px}
"""

JS = """
const chips=document.querySelectorAll('.chips button');
chips.forEach(b=>b.addEventListener('click',()=>{chips.forEach(x=>x.classList.remove('on'));b.classList.add('on');
const f=b.dataset.f;document.querySelectorAll('#pos tr[data-v]').forEach(r=>{
r.style.display=(f==='ALL'||r.dataset.v===f||(f==='ALERT'&&r.dataset.a==='1'))?'':'none'})}));
"""


def _tiles(s: dict, snap: dict) -> str:
    sm = snap["summary"]
    over = sm["over_stock_limit"]
    sector_over = [x["sector"] for x in sm["sectors"] if x["over"]]
    flags = []
    if over:
        flags.append(f"{len(over)} stock(s) over {sm['limits']['stock']:.0f}%")
    if sector_over:
        flags.append(f"{len(sector_over)} sector(s) over {sm['limits']['sector']:.0f}%")
    t = [
        ("Portfolio value", _inr(snap["total_value"]), f"{snap['n_positions']} positions"),
        ("Today", _pct(sm["day_pnl_pct"], 2), _inr(sm["day_pnl"])),
        ("Top 5 / Top 7", f"{sm['top5_pct']:.0f}% / {sm['top7_pct']:.0f}%", "of the portfolio"),
        ("Alerts now", str(len(snap["alerts"])), f"{len(snap['no_quote'])} without a live quote"),
        ("Limits", "OK" if not flags else "Breached", "; ".join(flags) or "within your limits"),
    ]
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="k">{escape(k)}</div><div class="v">{v}</div>'
        f'<div class="s">{escape(sub)}</div></div>' for k, v, sub in t) + "</div>"


def _alerts(snap: dict) -> str:
    if not snap["alerts"]:
        return '<div class="card mute">No alerts right now.</div>'
    rows = "".join(
        f'<div class="alert"><b>{escape(a["symbol"])}</b>'
        f'<span class="pill {escape(a.get("verdict") or "")}">{escape(a.get("verdict") or "no verdict")}</span> '
        f'{escape(a["msg"])} <span class="kind">{escape(a["kind"])}</span><br>'
        f'<span class="act">{escape(a["action"])}</span></div>' for a in snap["alerts"])
    return f'<div class="card">{rows}</div>'


def _sectors(snap: dict) -> str:
    lim = snap["summary"]["limits"]["sector"]
    rows = "".join(
        f'<tr><td>{escape(x["sector"])}</td><td><div class="bar"><i class="{"over" if x["over"] else ""}" '
        f'style="width:{min(100, x["pct"] / max(lim, 1) * 100 * 0.8):.0f}%"></i></div></td>'
        f'<td>{x["pct"]:.1f}%{" &#9888;" if x["over"] else ""}</td></tr>'
        for x in snap["summary"]["sectors"][:10])
    return (f'<div class="scroll"><table style="min-width:0"><tr><th>Sector</th><th>vs {lim:.0f}% limit</th>'
            f'<th>Share</th></tr>{rows}</table></div>')


def _plan(snap: dict) -> str:
    if not snap.get("plan"):
        return ('<div class="card mute">Plan tracking starts after the first full cycle on the server '
                '(it records your starting quantities).</div>')
    rows = "".join(
        f'<tr><td>{escape(p["symbol"])}</td><td><span class="pill {escape(p["plan"])}">{escape(p["plan"])}</span></td>'
        f'<td>{escape(p["status"])}</td><td>{p["left_pct"]:.0f}% left</td></tr>' for p in snap["plan"])
    since = escape(snap.get("baseline_date") or "")
    return (f'<div class="scroll"><table style="min-width:0"><tr><th>Stock</th><th>Plan</th><th>Status</th>'
            f'<th>Held</th></tr>{rows}</table></div><div class="mute" style="font-size:12px;margin-top:4px">'
            f'Measured against holdings on {since}.</div>')


def _positions(snap: dict) -> str:
    alert_syms = {a["symbol"] for a in snap["alerts"]}
    verdicts = sorted({p["verdict"] for p in snap["positions"] if p.get("verdict")})
    chips = ('<div class="chips"><button class="on" data-f="ALL">All</button>'
             '<button data-f="ALERT">Alerts</button>' +
             "".join(f'<button data-f="{escape(v)}">{escape(v.replace("_", " ").title())}</button>' for v in verdicts) +
             "</div>")
    lim = snap["summary"]["limits"]["stock"]
    rows = []
    for p in snap["positions"]:
        v = p.get("verdict") or ""
        w = p["weight_pct"]
        ltp = "-" if p["ltp"] is None else format(p["ltp"], ",.2f")     # no nested f-string quotes: server runs Python 3.10
        rows.append(
            f'<tr class="{"stale" if p["stale"] else ""}" data-v="{escape(v)}" data-a="{1 if p["symbol"] in alert_syms else 0}">'
            f'<td>{escape(p["symbol"])}{" &#9888;" if w > lim else ""}</td>'
            f'<td><span class="pill {escape(v)}">{escape(v.replace("_", " ")) or "-"}</span></td>'
            f'<td>{ltp}</td><td>{_pct(p["day_pct"], 2)}</td>'
            f'<td>{_pct(p["pnl_pct"])}</td><td>{_grp(p["value"])}</td><td>{w:.2f}%</td>'
            f'<td class="mute">{escape(p.get("sector", ""))}</td></tr>')
    return (chips + '<div class="scroll"><table id="pos"><tr><th>Symbol</th><th>Verdict</th><th>LTP</th><th>Day</th>'
            '<th>P&amp;L vs cost</th><th>Value &#8377;</th><th>Weight</th><th>Sector</th></tr>' + "".join(rows) +
            "</table></div>")


@app.route("/")
def index():
    if not _authorised():
        abort(403)
    snap = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else None
    if not snap or "summary" not in snap:
        body = '<div class="card">No snapshot yet. It appears after the first cycle (market hours).</div>'
    else:
        age = _age_minutes(snap["as_of"])
        now = datetime.now(IST)
        warn = ""
        if _in_session(now) and age > 45:
            warn = (f'<div class="warn">Data is {age:.0f} minutes old during market hours. '
                    "The alert loop may have stopped; run the health check on the server.</div>")
        nq = ", ".join(snap["no_quote"]) or "none"
        body = (
            f'<div class="sub">Updated {escape(snap["as_of"][:16].replace("T", " "))} IST ({age:.0f} min ago) '
            f'&middot; Zerodha live prices &middot; no quote: {escape(nq)}</div>{warn}'
            + _tiles(snap["summary"], snap) +
            "<h2>Alerts now</h2>" + _alerts(snap) +
            '<div class="row2"><div><h2>Sector exposure</h2>' + _sectors(snap) +
            "</div><div><h2>Plan progress (exit / trim / consolidate)</h2>" + _plan(snap) + "</div></div>"
            "<h2>Positions</h2>" + _positions(snap))
    html = ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'><title>Portfolio</title>"
            f"<style>{CSS}</style><main><h1>Portfolio advisor <small>advisory only &middot; no orders</small></h1>"
            f"{body}<div class='foot'>Rule-based alerts on live prices; verdicts come from the weekly research review. "
            "AI-generated research, not SEBI-registered advice. Verify prices at your broker before acting.</div></main>"
            f"<script>{JS}</script>")
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    if TOKEN and request.args.get("token"):
        resp.set_cookie("pm_token", TOKEN, httponly=True, secure=request.is_secure or
                        request.headers.get("X-Forwarded-Proto") == "https", samesite="Strict")
    return resp


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
