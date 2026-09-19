"""Data pipeline: pull annual financials from the SEC EDGAR XBRL API.

    python -m app.ingest            # fetch live data, write data/seed_financials.csv, load the DB
    python -m app.ingest --offline  # (re)load the DB from the committed CSV only

The SEC requires a descriptive User-Agent (set SEC_USER_AGENT to your name/email).
All amounts are stored in USD millions.
"""
from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import date

import httpx

from . import db

# ticker -> (display name, SEC CIK)
COMPANIES: dict[str, tuple[str, int]] = {
    "AAPL": ("Apple", 320193),
    "MSFT": ("Microsoft", 789019),
    "TSLA": ("Tesla", 1318605),
    "GOOGL": ("Alphabet", 1652044),
    "AMZN": ("Amazon", 1018724),
    "NVDA": ("NVIDIA", 1045810),
    "META": ("Meta", 1326801),
}

# metric -> (US-GAAP tags in order of preference, "flow" = over a period, "instant" = point in time)
TAGS: dict[str, tuple[list[str], str]] = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"], "flow"),
    # ProfitLoss = consolidated net income (incl. non-controlling interests)
    "net_income": (["ProfitLoss", "NetIncomeLoss"], "flow"),
    "total_assets": (["Assets"], "instant"),
    "total_liabilities": (["Liabilities"], "instant"),
    "operating_cash_flow": (["NetCashProvidedByUsedInOperatingActivities"], "flow"),
}
MIN_YEAR = 2019
CSV_PATH = os.path.join(db.DATA_DIR, "seed_financials.csv")
FIELDS = ["ticker", "company", "fiscal_year", "fiscal_year_end", "revenue", "net_income",
          "total_assets", "total_liabilities", "operating_cash_flow"]


def fiscal_year(end: date) -> int:
    """Year of the period end; a 52/53-week year ending on 1-3 Jan belongs to the previous year."""
    return end.year - 1 if end.month == 1 and end.day <= 3 else end.year


def extract_annual(facts: dict, tags: list[str], kind: str) -> dict[date, float]:
    """Return {period_end_date: value_in_USD} from 10-K facts; the latest filing wins per period."""
    best: dict[date, tuple[str, float]] = {}  # end -> (filed, value)
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        for e in gaap.get(tag, {}).get("units", {}).get("USD", []):
            if not e.get("form", "").startswith("10-K"):
                continue
            end = date.fromisoformat(e["end"])
            if kind == "flow":
                if "start" not in e:
                    continue
                days = (end - date.fromisoformat(e["start"])).days
                if not 350 <= days <= 380:  # full-year periods only, not quarters
                    continue
            if end not in best or e["filed"] > best[end][0]:
                best[end] = (e["filed"], e["val"])
    return {end: val for end, (_, val) in best.items()}


def build_rows(ticker: str, name: str, facts: dict) -> list[dict]:
    per_metric: dict[str, dict[date, float]] = {}
    for metric, (tags, kind) in TAGS.items():
        merged: dict[date, float] = {}
        for tag in tags:  # earlier tags win; later tags fill gaps
            for end, val in extract_annual(facts, [tag], kind).items():
                merged.setdefault(end, val)
        per_metric[metric] = merged
    # Some filers (e.g. Amazon) do not tag total liabilities: derive it as assets - total equity.
    equity = {}
    for tag in ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"):
        for end, val in extract_annual(facts, [tag], "instant").items():
            equity.setdefault(end, val)
    for end, assets in per_metric["total_assets"].items():
        if end not in per_metric["total_liabilities"] and end in equity:
            per_metric["total_liabilities"][end] = assets - equity[end]
    ends = sorted(set.intersection(*(set(m) for m in per_metric.values())))
    rows = []
    for end in ends:
        fy = fiscal_year(end)
        if fy < MIN_YEAR:
            continue
        row = {"ticker": ticker, "company": name, "fiscal_year": fy, "fiscal_year_end": end.isoformat()}
        for metric in TAGS:
            row[metric] = round(per_metric[metric][end] / 1e6, 1)
        rows.append(row)
    return rows


def fetch_company(client: httpx.Client, cik: int) -> dict:
    r = client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_all(user_agent: str | None = None) -> list[dict]:
    ua = user_agent or os.environ.get("SEC_USER_AGENT", "FinSight-AI portfolio project contact@example.com")
    rows: list[dict] = []
    with httpx.Client(headers={"User-Agent": ua}) as client:
        for ticker, (name, cik) in COMPANIES.items():
            try:
                got = build_rows(ticker, name, fetch_company(client, cik))
                print(f"{ticker}: {len(got)} fiscal years")
                rows += got
            except Exception as exc:  # keep going if one company fails
                print(f"{ticker}: FAILED ({exc})")
            time.sleep(0.2)  # SEC fair-access limit is 10 requests/second
    return rows


def write_csv(rows: list[dict], path: str = CSV_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["ticker"], r["fiscal_year"])))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="only load the committed CSV into the DB")
    args = ap.parse_args()
    if not args.offline:
        rows = fetch_all()
        if not rows:
            raise SystemExit("No data fetched; aborting so the existing CSV is not overwritten.")
        write_csv(rows)
    db.init_db()
    n = db.load_csv(CSV_PATH, replace=True)
    print(f"Loaded {n} rows into {db.DB_PATH}")


if __name__ == "__main__":
    main()
