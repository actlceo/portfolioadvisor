"""Read the broker holdings export (ACML 'Client Holding' layout) into plain dicts.

The header is not on row 1 (there are title rows above it) and there is a totals
footer, so the header row is found by name and rows without a scrip are skipped.
Works on .xlsx and .csv. Never writes anywhere.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

REQUIRED = ("Security Name", "Total Holding", "Close Rate")
_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "NA", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read_rows(path: Path) -> list[list]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb["Client Holding"] if "Client Holding" in wb.sheetnames else wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [list(r) for r in csv.reader(f)]


def load_holdings(path: str | Path) -> list[dict]:
    rows = _read_rows(Path(path))
    hdr_i, cols = None, {}
    for i, r in enumerate(rows):
        names = [str(c).strip() if c is not None else "" for c in r]
        if all(h in names for h in REQUIRED):
            hdr_i, cols = i, {n: j for j, n in enumerate(names) if n}
            break
    if hdr_i is None:
        raise ValueError(f"{path}: header row with {REQUIRED} not found "
                         "(broker export layout changed?)")

    out = []
    for r in rows[hdr_i + 1:]:
        def get(col):
            j = cols.get(col)
            return r[j] if j is not None and j < len(r) else None

        name = str(get("Security Name") or "").strip()
        scrip = str(get("Scrip Code") or "").strip()
        if not name or not scrip:          # blank rows and the totals footer
            continue
        qty = _num(get("Total Holding")) or 0.0
        if qty <= 0:
            continue
        close = _num(get("Close Rate")) or 0.0
        cost = _num(get("Buy Avg"))
        out.append({
            "symbol": name.upper(),
            "isin": str(get("ISIN") or "").strip(),
            "qty": qty,
            "cost": cost if cost else None,      # None = cost unknown; never treat P&L as real
            "sheet_close": close,
            # Names with spaces ('... LIMITED EQ EQ', fund units) are not NSE tickers,
            # and a zero close means suspended/delisted: no quote lookup for those.
            "quotable": bool(_SYMBOL.match(name.upper())) and close > 0,
        })
    return out
