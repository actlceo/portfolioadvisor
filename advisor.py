"""Portfolio advisor: live prices -> rule-based alerts -> snapshot + email.

ADVISORY ONLY. There is no order code in this project and none may be added.
Rule-based and replayable: no LLM in this loop (the weekly/monthly research is a
separate Claude routine that writes data/memory.json; this file only reads it).

    python advisor.py --once                 one cycle now (prints, writes snapshot, emails)
    python advisor.py --loop                 every PM_CYCLE_MIN min during market hours
    python advisor.py --replay tests/fixtures/day.json   canned quotes, no network, no email
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import holdings as H
import notify

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
HOLDINGS_PATH = Path(os.getenv("PM_HOLDINGS_PATH", DATA / "holdings.xlsx"))
MEMORY_PATH = Path(os.getenv("PM_MEMORY_PATH", DATA / "memory.json"))
SNAPSHOT_PATH = DATA / "snapshot.json"
STATE_PATH = DATA / "alert_state.json"
RANGE_PATH = DATA / "range_cache.json"

DAY_MOVE_PCT = float(os.getenv("PM_DAY_MOVE_PCT", "5"))
REALERT_PCT = float(os.getenv("PM_REALERT_PCT", "3"))
VOL_SPIKE = float(os.getenv("PM_VOL_SPIKE", "2.5"))
CYCLE_MIN = int(os.getenv("PM_CYCLE_MIN", "15"))

# Same 2026 list as AI Trader v2 (extend yearly; PM_EXTRA_HOLIDAYS for ad-hoc closures).
NSE_HOLIDAYS = frozenset({
    "2026-01-15", "2026-01-26", "2026-02-15", "2026-03-03", "2026-03-21",
    "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-28", "2026-06-26", "2026-08-15", "2026-09-14", "2026-10-02",
    "2026-10-20", "2026-11-08", "2026-11-10", "2026-11-24", "2026-12-25",
}) | {d.strip() for d in os.getenv("PM_EXTRA_HOLIDAYS", "").split(",") if d.strip()}


def now_ist() -> datetime:
    return datetime.now(IST)


def log(msg: str) -> None:
    print(f"[{now_ist():%H:%M:%S}] {msg}", flush=True)


def is_trading_day(t: datetime) -> bool:
    return t.weekday() < 5 and t.strftime("%Y-%m-%d") not in NSE_HOLIDAYS


def in_session(t: datetime) -> bool:
    return is_trading_day(t) and (9, 20) <= (t.hour, t.minute) <= (15, 35)


# ---------------------------------------------------------------------------
# Memory (written by the Claude research routines, read-only here)
# ---------------------------------------------------------------------------

def _name(entry: str) -> str:
    return entry.split("(")[0].split("=")[0].strip().upper()


def load_memory(path: Path = MEMORY_PATH) -> dict:
    if not path.exists():
        return {"verdicts": {}, "alert_bands": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def verdict_map(memory: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, v in (memory.get("verdicts") or {}).items():
        names = v.keys() if isinstance(v, dict) else v
        for n in names:
            out[_name(n)] = label
    return out


# ---------------------------------------------------------------------------
# Rules (pure functions: easy to replay and test)
# ---------------------------------------------------------------------------

SELL_SIDE = {"EXIT", "CONSOLIDATE", "TRIM"}


def evaluate(sym: str, q: dict, rng: dict | None, bands: dict, verdict: str | None) -> list[dict]:
    ltp, prev = q["ltp"], q["prev_close"]
    if ltp <= 0:
        return []
    alerts: list[dict] = []

    def add(kind: str, msg: str, action: str) -> None:
        alerts.append({"symbol": sym, "kind": kind, "price": ltp, "msg": msg,
                       "action": action, "verdict": verdict})

    buy = (bands.get("buy_below") or {}).get(sym)
    sell = (bands.get("sell_above") or {}).get(sym)
    if buy and ltp <= buy:
        act = ("ACT (small): next tranche of a staggered buy" if verdict == "CORE_HOLD"
               else "WATCH: below the add zone, re-check the thesis before buying")
        add("buy_below", f"{ltp:.2f} <= buy-below {buy}", act)
    if sell and ltp >= sell:
        act = ("ACT: sell/trim per plan" if verdict in SELL_SIDE
               else "WATCH: above the trim zone, consider trimming")
        add("sell_above", f"{ltp:.2f} >= sell-above {sell}", act)

    if prev > 0:
        chg = (ltp / prev - 1) * 100
        if abs(chg) >= DAY_MOVE_PCT:
            if chg > 0:
                act = "ACT: sell into strength (limit order)" if verdict in SELL_SIDE else "WATCH: do not chase"
            else:
                act = "WATCH: check news before any action; do not average down"
            add("day_move", f"{chg:+.1f}% on the day", act)

    if rng:
        if ltp <= rng["low52"]:
            act = ("Plan unchanged: continue the exit" if verdict in SELL_SIDE
                   else "WATCH: new 52-week low, re-test the thesis")
            add("low52", f"new 52-week low (prior {rng['low52']:.2f})", act)
        elif ltp >= rng["high52"]:
            add("high52", f"new 52-week high (prior {rng['high52']:.2f})",
                "WATCH: consider the trim zone" if verdict != "EXIT" else "ACT: sell into strength")
        if rng.get("avg_vol20") and q.get("volume"):
            r = q["volume"] / rng["avg_vol20"]
            if r >= VOL_SPIKE:
                add("volume", f"volume {r:.1f}x the 20-day average",
                    "WATCH: unusual activity, look for news")
    return alerts


STOCK_LIMIT = float(os.getenv("PM_MAX_STOCK_PCT", "10"))
SECTOR_LIMIT = float(os.getenv("PM_MAX_SECTOR_PCT", "25"))
SECTORS_PATH = ROOT / "sectors.json"          # symbol -> sector (approximate, committed)
BASELINE_PATH = DATA / "plan_baseline.json"   # quantities when the plan started


def summarize(positions: list[dict], sectors: dict) -> dict:
    """Totals, concentration and sector exposure against the owner's limits."""
    total = sum(p["value"] for p in positions) or 1.0
    ranked = sorted(positions, key=lambda p: -p["value"])
    by_sector: dict[str, float] = {}
    for p in positions:
        s = sectors.get(p["symbol"], "Unclassified")
        by_sector[s] = by_sector.get(s, 0.0) + p["value"]
    rows = [{"sector": s, "value": round(v, 2), "pct": round(v / total * 100, 2),
             "over": v / total * 100 > SECTOR_LIMIT}
            for s, v in sorted(by_sector.items(), key=lambda kv: -kv[1])]
    day_pnl = sum(p.get("day_pnl") or 0.0 for p in positions)
    return {
        "day_pnl": round(day_pnl, 2),
        "day_pnl_pct": round(day_pnl / (total - day_pnl) * 100, 2) if total - day_pnl else 0.0,
        "top5_pct": round(sum(p["value"] for p in ranked[:5]) / total * 100, 1),
        "top7_pct": round(sum(p["value"] for p in ranked[:7]) / total * 100, 1),
        "over_stock_limit": [p["symbol"] for p in ranked if p["value"] / total * 100 > STOCK_LIMIT],
        "sectors": rows,
        "limits": {"stock": STOCK_LIMIT, "sector": SECTOR_LIMIT},
    }


def plan_progress(positions: list[dict], memory: dict, baseline: dict | None) -> list[dict]:
    """For every EXIT / CONSOLIDATE / TRIM name: how much of the starting quantity is left."""
    if not baseline:
        return []
    base = baseline.get("qty", {})
    now = {p["symbol"]: p["qty"] for p in positions}
    v = memory.get("verdicts") or {}
    todo: list[tuple[str, str, str]] = []
    for label in ("EXIT", "CONSOLIDATE"):
        todo += [(_name(n), label, "") for n in v.get(label, [])]
    for n, hint in (v.get("TRIM") or {}).items():
        todo.append((_name(n), "TRIM", hint))
    rows = []
    for sym, label, hint in todo:
        b = base.get(sym)
        if not b:
            continue
        q = now.get(sym, 0.0)
        left = q / b * 100
        status = "Done" if q <= 0 else ("Not started" if left > 98 else "In progress")
        rows.append({"symbol": sym, "plan": label, "hint": hint, "start_qty": b,
                     "qty": q, "left_pct": round(left, 0), "status": status})
    order = {"In progress": 0, "Not started": 1, "Done": 2}
    return sorted(rows, key=lambda r: (order[r["status"]], r["symbol"]))


def filter_new(alerts: list[dict], state: dict, today: str) -> tuple[list[dict], dict]:
    """Same symbol+kind is not re-sent the same day unless price moved >= REALERT_PCT."""
    if state.get("date") != today:
        state = {"date": today, "sent": {}}
    fresh = []
    for a in alerts:
        key = f"{a['symbol']}|{a['kind']}"
        last = state["sent"].get(key)
        if last is None or abs(a["price"] / last - 1) * 100 >= REALERT_PCT:
            fresh.append(a)
            state["sent"][key] = a["price"]
    return fresh, state


# ---------------------------------------------------------------------------
# One cycle
# ---------------------------------------------------------------------------

def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def run_cycle(quote_fn, range_fn, send: bool = True, holdings_path: Path = HOLDINGS_PATH,
              memory_path: Path = MEMORY_PATH, now: datetime | None = None) -> dict:
    now = now or now_ist()
    today = now.strftime("%Y-%m-%d")
    hold = H.load_holdings(holdings_path)
    memory = load_memory(memory_path)
    bands = memory.get("alert_bands") or {}
    verdicts = verdict_map(memory)

    rcache = _load_json(RANGE_PATH, {})
    if rcache.get("date") != today:
        rcache = {"date": today, "r": {}}

    positions, alerts, missing = [], [], []
    for h in hold:
        sym = h["symbol"]
        q = quote_fn(sym) if h["quotable"] else None
        if not q or q["ltp"] <= 0:
            if h["quotable"]:
                missing.append(sym)
            positions.append({**h, "ltp": None, "day_pct": None, "value": h["qty"] * h["sheet_close"],
                              "pnl_pct": None, "verdict": verdicts.get(sym), "stale": True})
            continue
        if sym not in rcache["r"]:
            rcache["r"][sym] = range_fn(sym)
        rng = rcache["r"][sym]
        alerts += evaluate(sym, q, rng, bands, verdicts.get(sym))
        positions.append({
            **h, "ltp": q["ltp"], "prev_close": q["prev_close"],
            "day_pnl": round(h["qty"] * (q["ltp"] - q["prev_close"]), 2) if q["prev_close"] else None,
            "day_pct": round((q["ltp"] / q["prev_close"] - 1) * 100, 2) if q["prev_close"] else None,
            "value": round(h["qty"] * q["ltp"], 2),
            "pnl_pct": round((q["ltp"] / h["cost"] - 1) * 100, 1) if h["cost"] else None,
            "verdict": verdicts.get(sym), "stale": False})

    total = sum(p["value"] for p in positions) or 1.0
    for p in positions:
        p["weight_pct"] = round(p["value"] / total * 100, 2)

    state = _load_json(STATE_PATH, {})
    fresh, state = filter_new(alerts, state, today)

    DATA.mkdir(exist_ok=True)
    baseline = _load_json(BASELINE_PATH, None)
    if baseline is None and send:        # first live cycle: remember the starting quantities
        baseline = {"date": today, "qty": {p["symbol"]: p["qty"] for p in positions}}
        BASELINE_PATH.write_text(json.dumps(baseline), encoding="utf-8")
    sectors = _load_json(SECTORS_PATH, {})

    snap = {"as_of": now.isoformat(timespec="seconds"), "total_value": round(total, 2),
            "n_positions": len(positions), "no_quote": missing, "alerts": alerts,
            "summary": summarize(positions, sectors),
            "plan": plan_progress(positions, memory, baseline),
            "baseline_date": baseline.get("date") if baseline else None,
            "positions": sorted(positions, key=lambda p: -p["value"])}
    for p in snap["positions"]:
        p["sector"] = sectors.get(p["symbol"], "Unclassified")
    SNAPSHOT_PATH.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    RANGE_PATH.write_text(json.dumps(rcache), encoding="utf-8")

    if send:
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        if fresh:
            lines = [f"{a['symbol']} {a['price']:.2f} - {a['kind']}: {a['msg']} [{a['verdict'] or 'no verdict'}]"
                     f"\n    -> {a['action']}" for a in fresh]
            body = ("PORTFOLIO ALERT (Zerodha live prices via OpenAlgo)\n\n" + "\n".join(lines) +
                    "\n\nRule-based alert; research verdicts come from the weekly review."
                    "\nAdvisory only. Verify prices at your broker before acting.")
            subj = "[Portfolio ALERT] " + "; ".join(sorted({f"{a['symbol']} {a['kind']}" for a in fresh}))[:120]
            notify.send(subj, body)
    log(f"cycle: {len(positions)} positions, {len(missing)} no quote, "
        f"{len(alerts)} alerts ({len(fresh)} new)")
    return snap


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    g.add_argument("--replay", metavar="FIXTURE")
    a = ap.parse_args()

    if a.replay:
        fx = json.loads(Path(a.replay).read_text(encoding="utf-8"))
        snap = run_cycle(lambda s: fx["quotes"].get(s), lambda s: fx.get("ranges", {}).get(s),
                         send=False, holdings_path=Path(fx["holdings"]),
                         memory_path=Path(fx["memory"]))
        print(json.dumps([{k: x[k] for k in ("symbol", "kind", "action")} for x in snap["alerts"]], indent=1))
        return

    import quotes
    if a.once:
        run_cycle(quotes.get_quote, quotes.get_range)
        return
    while True:
        t = now_ist()
        if in_session(t):
            try:
                run_cycle(quotes.get_quote, quotes.get_range)
            except Exception as e:                       # keep the loop alive; fail loudly in the log
                log(f"[ERROR] cycle failed: {e}")
            time.sleep(CYCLE_MIN * 60)
        else:
            time.sleep(60)


if __name__ == "__main__":
    main()
