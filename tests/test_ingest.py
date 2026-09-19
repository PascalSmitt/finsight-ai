from datetime import date

from app import ingest


def _fact(start, end, val, filed, form="10-K"):
    e = {"end": end, "val": val, "filed": filed, "form": form}
    if start:
        e["start"] = start
    return e


def test_extract_keeps_full_years_and_latest_filing():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2024-01-01", "2024-12-31", 100, "2025-02-01"),
        _fact("2024-01-01", "2024-12-31", 105, "2026-02-01"),   # restated later -> wins
        _fact("2024-10-01", "2024-12-31", 30, "2025-02-01"),    # a quarter -> ignored
        _fact("2024-01-01", "2024-12-31", 999, "2025-05-01", form="10-Q"),
    ]}}}}}
    assert ingest.extract_annual(facts, ["Revenues"], "flow") == {date(2024, 12, 31): 105}


def test_instant_facts_need_no_start():
    facts = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [_fact(None, "2024-12-31", 50, "2025-02-01")]}}}}}
    assert ingest.extract_annual(facts, ["Assets"], "instant") == {date(2024, 12, 31): 50}


def test_fiscal_year_for_52_53_week_calendars():
    assert ingest.fiscal_year(date(2022, 1, 1)) == 2021
    assert ingest.fiscal_year(date(2025, 9, 27)) == 2025


def test_liabilities_derived_from_equity_when_untagged():
    g = {}
    for tag, start, val in [("Revenues", "2024-01-01", 1e9), ("ProfitLoss", "2024-01-01", 1e8),
                            ("NetCashProvidedByUsedInOperatingActivities", "2024-01-01", 2e8),
                            ("Assets", None, 5e9), ("StockholdersEquity", None, 2e9)]:
        g[tag] = {"units": {"USD": [_fact(start, "2024-12-31", val, "2025-02-01")]}}
    rows = ingest.build_rows("XYZ", "Xyz", {"facts": {"us-gaap": g}})
    assert rows[0]["total_liabilities"] == 3000.0
