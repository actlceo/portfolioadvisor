"""Portfolio page. Reads data/snapshot.json written by advisor.py; never calls a broker.

Dev:     python dashboard.py                      -> 127.0.0.1:8082 (no token)
Server:  PM_DASH_HOST=0.0.0.0 PM_DASH_TOKEN=...   -> :5041, token-gated, fail-closed
Open https://host:5041/?token=...  (the token is remembered in a cookie).
"""
from __future__ import annotations

import hmac
import json
import os
from html import escape
from pathlib import Path

from flask import Flask, abort, make_response, request

SNAP = Path(__file__).parent / "data" / "snapshot.json"
HOST = os.getenv("PM_DASH_HOST", "127.0.0.1")
TOKEN = os.getenv("PM_DASH_TOKEN", "")
PORT = int(os.getenv("PM_DASH_PORT", "8082" if HOST == "127.0.0.1" else "5041"))

app = Flask(__name__)


def _authorised() -> bool:
    if HOST == "127.0.0.1":
        return True
    if not TOKEN:                                   # fail-closed on a public bind
        return False
    got = request.args.get("token") or request.cookies.get("pm_token") or ""
    return hmac.compare_digest(got, TOKEN)


def _fmt(v, nd=2, suffix=""):
    return "-" if v is None else f"{v:,.{nd}f}{suffix}"


@app.route("/")
def index():
    if not _authorised():
        abort(403)
    snap = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else None
    if not snap:
        body = "<p>No snapshot yet. Run <code>python advisor.py --once</code>.</p>"
    else:
        rows = "".join(
            f"<tr class='{'stale' if p['stale'] else ''}'><td>{escape(p['symbol'])}</td>"
            f"<td>{escape(p.get('verdict') or '-')}</td><td>{_fmt(p['ltp'])}</td>"
            f"<td>{_fmt(p['day_pct'], 2, '%')}</td><td>{_fmt(p['pnl_pct'], 1, '%')}</td>"
            f"<td>{_fmt(p['value'], 0)}</td><td>{_fmt(p['weight_pct'], 2, '%')}</td></tr>"
            for p in snap["positions"])
        alerts = "".join(
            f"<li><b>{escape(a['symbol'])}</b> {escape(a['kind'])}: {escape(a['msg'])} "
            f"&mdash; {escape(a['action'])}</li>" for a in snap["alerts"]) or "<li>none</li>"
        body = (f"<p>As of {escape(snap['as_of'])} &middot; value ₹{snap['total_value']:,.0f} &middot; "
                f"{snap['n_positions']} positions &middot; no quote: {escape(', '.join(snap['no_quote']) or 'none')}</p>"
                f"<h3>Alerts now</h3><ul>{alerts}</ul>"
                "<table><tr><th>Symbol</th><th>Verdict</th><th>LTP</th><th>Day</th><th>P&amp;L vs cost</th>"
                f"<th>Value ₹</th><th>Weight</th></tr>{rows}</table>")
    html = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Portfolio</title><style>body{font:14px system-ui;margin:20px;color:#111}"
            "table{border-collapse:collapse}td,th{padding:4px 10px;border-bottom:1px solid #ddd;text-align:right}"
            "td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}"
            "tr.stale{color:#888}</style><h2>Portfolio advisor <small>(advisory only, no orders)</small></h2>"
            + body)
    resp = make_response(html)
    if TOKEN and request.args.get("token"):
        resp.set_cookie("pm_token", TOKEN, httponly=True, samesite="Strict")
    return resp


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
