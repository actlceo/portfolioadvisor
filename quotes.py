"""Read-only price client for OpenAlgo (Zerodha session). Same pattern as AI Trader v2.

This module has no order code at all. OpenAlgo's /quotes and /history are data reads.
Paced at >= 400 ms between calls (OpenAlgo 429s above ~2.5/sec).
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

import requests

BASE_URL = os.getenv("OPENALGO_BASE_URL", "http://127.0.0.1:5000")
API_KEY = os.getenv("OPENALGO_API_KEY", "")
DELAY_S = max(0.4, int(os.getenv("QUOTE_FETCH_DELAY_MS", "400")) / 1000.0)

_last = 0.0


def _pace() -> None:
    global _last
    wait = DELAY_S - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


def _post(path: str, payload: dict, timeout: int) -> dict | list | None:
    _pace()
    payload = {"apikey": API_KEY, **payload}
    r = requests.post(f"{BASE_URL}{path}", json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(data.get("message", "OpenAlgo error"))
    return data.get("data", data) if isinstance(data, dict) else data


def get_quote(symbol: str, exchange: str = "NSE") -> dict | None:
    try:
        q = _post("/api/v1/quotes", {"symbol": symbol, "exchange": exchange}, 5)
        return {
            "ltp": float(q.get("ltp") or q.get("last_price") or 0),
            "prev_close": float(q.get("close") or q.get("prev_close") or 0),
            "volume": float(q.get("volume") or 0),
        }
    except Exception as e:                       # one bad symbol must not stop the cycle
        print(f"  [WARN] quote failed for {symbol}: {e}", flush=True)
        return None


def get_range(symbol: str, days: int = 370) -> dict | None:
    """52-week high/low and 20-day average volume from daily candles. Best effort."""
    try:
        end = date.today()
        c = _post("/api/v1/history", {
            "symbol": symbol, "exchange": "NSE", "interval": "D",
            "start_date": (end - timedelta(days=days)).isoformat(),
            "end_date": end.isoformat()}, 10)
        if not isinstance(c, list) or len(c) < 20:
            return None
        highs = [float(x["high"]) for x in c]
        lows = [float(x["low"]) for x in c]
        vols = [float(x.get("volume") or 0) for x in c[-21:-1]]
        return {"high52": max(highs), "low52": min(lows),
                "avg_vol20": sum(vols) / len(vols) if vols else 0.0}
    except Exception as e:
        print(f"  [WARN] history failed for {symbol}: {e}", flush=True)
        return None
