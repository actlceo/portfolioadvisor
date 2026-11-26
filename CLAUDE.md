# CLAUDE.md - portfolio_advisor

Rules for any Claude session in this repo. They override defaults.

1. **Advisory only. No order code, ever.** There is no `place_order`, no broker write call, no
   order endpoint in this project, and none may be added without the owner's explicit written
   instruction in the current session. It reads prices (OpenAlgo `/quotes`, `/history`) and sends
   alerts. The owner places any order at the broker. (AI Trader v2's paper-only rule is separate and
   untouched; this project must not import `ai_trader_v2/trader.py`.)
2. **No LLM in this loop.** `advisor.py` is rule-based and replayable. The weekly/monthly research is
   done by Claude cloud routines that write `data/memory.json` (verdicts + alert bands); this project
   only reads that file.
3. **Stay small.** Five core files: `holdings.py`, `quotes.py`, `advisor.py`, `notify.py`,
   `dashboard.py`. No frameworks.
4. **Never invent a price.** No quote -> the position is shown stale (sheet close, greyed) and listed
   under `no_quote`. A blank `Buy Avg` means cost unknown: show no P&L for it.
5. **Secrets stay in `.env`** (gitignored): OpenAlgo key, SMTP password, dashboard token. Never log or
   print them. The dashboard fails closed on a public bind without `PM_DASH_TOKEN`.
6. Client name/ID in the broker export is personal: never put it in logs, alerts or the snapshot.
   (`holdings.py` does not read those columns.)

## Run
    pip install flask requests openpyxl pytest
    python -m pytest tests -q
    python advisor.py --replay tests/fixtures/day.json      # no network, no email
    python advisor.py --once        # needs OPENALGO_BASE_URL / OPENALGO_API_KEY
    python advisor.py --loop        # market hours only, every PM_CYCLE_MIN minutes
    python dashboard.py             # 127.0.0.1:8082

## Data files (all gitignored, in `data/`)
`holdings.xlsx` (drop the fresh broker export here, same name), `memory.json` (from the research
routines), `snapshot.json`, `alert_state.json`, `range_cache.json`, `outbox/`.
