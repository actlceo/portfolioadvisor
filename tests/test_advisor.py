import json
from pathlib import Path

import openpyxl
import pytest

import advisor as A
import holdings as H

FX = Path(__file__).parent / "fixtures"


def test_parser_csv_skips_footer_zero_close_and_fund_rows():
    rows = {h["symbol"]: h for h in H.load_holdings(FX / "holdings.csv")}
    assert set(rows) == {"NMDC", "MAHSEAMLES", "BBTC", "DELISTEDCO", "SOME FUND UNIT SCHEME", "HAL"}
    assert rows["BBTC"]["cost"] is None                  # blank Buy Avg = cost unknown
    assert rows["NMDC"]["quotable"] is True
    assert rows["DELISTEDCO"]["quotable"] is False       # zero close
    assert rows["SOME FUND UNIT SCHEME"]["quotable"] is False   # not an NSE ticker


def test_parser_xlsx_finds_header_row(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Client Holding"
    ws.append(["BROKER"]); ws.append(["Client Holding"]); ws.append([])
    ws.append(["Scrip Code", "ISIN", "Security Name", "Total Holding", "Close Rate", "Buy Avg"])
    ws.append(["500001", "INE1", "NMDC", 10, 66.69, 69.61])
    ws.append([None, None, None, 999, None, None])        # totals footer
    p = tmp_path / "h.xlsx"
    wb.save(p)
    assert [h["symbol"] for h in H.load_holdings(p)] == ["NMDC"]


def test_parser_rejects_changed_layout(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError):
        H.load_holdings(p)


BANDS = {"buy_below": {"NMDC": 68}, "sell_above": {"MAHSEAMLES": 560}}


def kinds(alerts):
    return {a["kind"] for a in alerts}


def test_buy_below_core_hold_is_actionable():
    a = A.evaluate("NMDC", {"ltp": 67.26, "prev_close": 66.69, "volume": 0}, None, BANDS, "CORE_HOLD")
    assert kinds(a) == {"buy_below"} and a[0]["action"].startswith("ACT")


def test_buy_below_on_non_core_is_watch_only():
    a = A.evaluate("NMDC", {"ltp": 67, "prev_close": 67, "volume": 0}, None, BANDS, "HOLD_MONITOR")
    assert a[0]["action"].startswith("WATCH")


def test_exit_name_at_52w_low_says_continue_exit():
    a = A.evaluate("MAHSEAMLES", {"ltp": 448.5, "prev_close": 460.35, "volume": 0},
                   {"high52": 900, "low52": 458.15, "avg_vol20": 0}, BANDS, "EXIT")
    assert "low52" in kinds(a) and "continue the exit" in [x for x in a if x["kind"] == "low52"][0]["action"]


def test_day_move_up_on_exit_name_sells_into_strength():
    a = A.evaluate("JISLJALEQS", {"ltp": 28.07, "prev_close": 26.66, "volume": 0}, None, {}, "EXIT")
    assert kinds(a) == {"day_move"} and "sell into strength" in a[0]["action"]


def test_day_move_down_never_says_average_down():
    a = A.evaluate("X", {"ltp": 90, "prev_close": 100, "volume": 0}, None, {}, "HOLD_MONITOR")
    assert "do not average down" in a[0]["action"]


def test_volume_spike_needs_range_data_and_ratio():
    q = {"ltp": 100, "prev_close": 100, "volume": 300000}
    assert "volume" in kinds(A.evaluate("X", q, {"high52": 200, "low52": 50, "avg_vol20": 100000}, {}, None))
    assert not A.evaluate("X", q, None, {}, None)


def test_no_alert_when_price_missing():
    assert A.evaluate("X", {"ltp": 0, "prev_close": 10, "volume": 0}, None, BANDS, None) == []


def test_dedupe_same_day_and_realert_on_3pct_move():
    al = [{"symbol": "NMDC", "kind": "buy_below", "price": 67.0}]
    fresh, st = A.filter_new(al, {}, "2026-11-26")
    assert len(fresh) == 1
    fresh, st = A.filter_new([{"symbol": "NMDC", "kind": "buy_below", "price": 66.5}], st, "2026-11-26")
    assert fresh == []                                    # 0.7% move: suppressed
    fresh, st = A.filter_new([{"symbol": "NMDC", "kind": "buy_below", "price": 64.5}], st, "2026-11-26")
    assert len(fresh) == 1                                # >3% further: re-alert
    fresh, st = A.filter_new(al, st, "2026-11-27")
    assert len(fresh) == 1                                # new day resets


def test_verdict_map_strips_annotations():
    m = {"verdicts": {"EXIT": ["UCOBANK(tranches, by 2027-03-31)"], "CORE_HOLD": ["JPL=Jio Platforms(x)"],
                      "TRIM": {"INDHOTEL": "to 5-6%"}}}
    v = A.verdict_map(m)
    assert v == {"UCOBANK": "EXIT", "JPL": "CORE_HOLD", "INDHOTEL": "TRIM"}


def test_replay_cycle_end_to_end(tmp_path, monkeypatch):
    for name in ("SNAPSHOT_PATH", "STATE_PATH", "RANGE_PATH"):
        monkeypatch.setattr(A, name, tmp_path / f"{name}.json")
    monkeypatch.setattr(A, "DATA", tmp_path)
    fx = json.loads((FX / "day.json").read_text())
    snap = A.run_cycle(lambda s: fx["quotes"].get(s), lambda s: fx["ranges"].get(s), send=False,
                       holdings_path=FX / "holdings.csv", memory_path=FX / "memory.json")
    assert snap["no_quote"] == []                         # unquotable rows are not "missing quotes"
    got = {(a["symbol"], a["kind"]) for a in snap["alerts"]}
    assert ("NMDC", "buy_below") in got and ("MAHSEAMLES", "low52") in got
    assert ("BBTC", "day_move") in got                    # -7.4%
    assert not any(s == "HAL" for s, _ in got)            # 4936 > 4200 and flat
    assert abs(sum(p["weight_pct"] for p in snap["positions"]) - 100) < 0.5
    bbtc = next(p for p in snap["positions"] if p["symbol"] == "BBTC")
    assert bbtc["pnl_pct"] is None                        # cost unknown -> no fake P&L
