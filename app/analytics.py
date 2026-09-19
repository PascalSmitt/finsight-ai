"""Financial analytics: derived metrics, lookups and number formatting."""
from __future__ import annotations

import math

import pandas as pd

from . import db

# key -> label, display kind, words users may type for it
METRICS: dict[str, dict] = {
    "revenue": {"label": "Total Revenue", "kind": "money", "words": ["revenue", "sales", "turnover", "top line"]},
    "net_income": {"label": "Net Income", "kind": "money", "words": ["net income", "profit", "earnings", "bottom line"]},
    "total_assets": {"label": "Total Assets", "kind": "money", "words": ["total assets", "assets"]},
    "total_liabilities": {"label": "Total Liabilities", "kind": "money", "words": ["total liabilities", "liabilities", "owes"]},
    "operating_cash_flow": {"label": "Operating Cash Flow", "kind": "money", "words": ["operating cash flow", "cash flow", "cash"]},
    "net_margin": {"label": "Net Margin", "kind": "pct", "words": ["net margin", "profit margin", "margin"]},
    "liabilities_to_assets": {"label": "Liabilities / Assets", "kind": "pct", "words": ["leverage", "debt ratio", "liabilities to assets", "debt to assets"]},
    "roa": {"label": "Return on Assets", "kind": "pct", "words": ["return on assets", "roa"]},
    "revenue_growth": {"label": "Revenue Growth", "kind": "pct", "words": ["revenue growth", "sales growth", "growth"]},
    "net_income_growth": {"label": "Net Income Growth", "kind": "pct", "words": ["net income growth", "profit growth", "earnings growth"]},
    "cash_conversion": {"label": "Cash Conversion (OCF / NI)", "kind": "ratio", "words": ["cash conversion"]},
}
# For "which company has the lowest ...": lower is better only for these.
LOWER_IS_BETTER = {"total_liabilities", "liabilities_to_assets"}


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)
    g = df.groupby("ticker")
    df["revenue_growth"] = g["revenue"].pct_change() * 100
    df["net_income_growth"] = g["net_income"].pct_change() * 100
    df["ocf_growth"] = g["operating_cash_flow"].pct_change() * 100
    df["net_margin"] = df["net_income"] / df["revenue"] * 100
    df["equity"] = df["total_assets"] - df["total_liabilities"]
    df["liabilities_to_assets"] = df["total_liabilities"] / df["total_assets"] * 100
    df["roa"] = df["net_income"] / df["total_assets"] * 100
    df["cash_conversion"] = df["operating_cash_flow"] / df["net_income"].where(df["net_income"] > 0)
    return df


def get_frame() -> pd.DataFrame:
    return enrich(pd.DataFrame(db.fetch_financials()))


def clean(v):
    """NaN/inf -> None so results serialise to valid JSON."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return v


def records(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.to_dict("records"):
        out.append({k: clean(round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    return out


def latest_year(df: pd.DataFrame, ticker: str | None = None) -> int | None:
    d = df if ticker is None else df[df["ticker"] == ticker]
    return int(d["fiscal_year"].max()) if len(d) else None


def value(df: pd.DataFrame, ticker: str, metric: str, year: int):
    r = df[(df["ticker"] == ticker) & (df["fiscal_year"] == year)]
    return clean(float(r.iloc[0][metric])) if len(r) and pd.notna(r.iloc[0][metric]) else None


def series(df: pd.DataFrame, ticker: str, metric: str, years: int | None = None) -> list[tuple[int, float]]:
    d = df[(df["ticker"] == ticker) & df[metric].notna()].sort_values("fiscal_year")
    pts = [(int(y), float(v)) for y, v in zip(d["fiscal_year"], d[metric])]
    return pts[-years:] if years else pts


def cagr(first: float, last: float, periods: int) -> float | None:
    if periods <= 0 or first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1 / periods) - 1) * 100


def fmt(v: float | None, kind: str) -> str:
    """Human-friendly number: money is stored in USD millions."""
    if v is None:
        return "n/a"
    if kind == "money":
        sign = "-" if v < 0 else ""
        a = abs(v)
        return f"{sign}${a / 1e6:,.2f}T" if a >= 1e6 else f"{sign}${a / 1e3:,.1f}B" if a >= 1e3 else f"{sign}${a:,.0f}M"
    if kind == "pct":
        return f"{v:.1f}%"
    return f"{v:.2f}x"


def health_profile(df: pd.DataFrame, ticker: str, year: int) -> dict:
    """Plain-language assessment from leverage, margin, cash conversion and growth."""
    m = {k: value(df, ticker, k, year) for k in
         ["net_margin", "liabilities_to_assets", "cash_conversion", "revenue_growth", "net_income_growth"]}
    points, notes = 0, []
    if m["net_margin"] is not None:
        if m["net_margin"] >= 20:
            points += 2; notes.append("highly profitable")
        elif m["net_margin"] >= 8:
            points += 1; notes.append("solidly profitable")
        else:
            notes.append("thin profit margins")
    if m["liabilities_to_assets"] is not None:
        if m["liabilities_to_assets"] < 50:
            points += 2; notes.append("low leverage")
        elif m["liabilities_to_assets"] < 75:
            points += 1; notes.append("moderate leverage")
        else:
            notes.append("high leverage")
    if m["cash_conversion"] is not None and m["cash_conversion"] >= 1:
        points += 1; notes.append("earnings backed by cash")
    if m["revenue_growth"] is not None:
        if m["revenue_growth"] >= 8:
            points += 1; notes.append("growing quickly")
        elif m["revenue_growth"] < 0:
            points -= 1; notes.append("shrinking revenue")
    verdict = "strong" if points >= 5 else "healthy" if points >= 3 else "mixed" if points >= 1 else "under pressure"
    return {"verdict": verdict, "notes": notes, "metrics": m}
