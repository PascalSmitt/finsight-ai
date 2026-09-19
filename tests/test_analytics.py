import json
import math

from app import analytics as an


def test_known_apple_values_match_10k(df):
    assert an.value(df, "AAPL", "revenue", 2025) == 416161
    assert an.value(df, "AAPL", "net_income", 2024) == 93736


def test_growth_and_ratios(df):
    g = an.value(df, "MSFT", "revenue_growth", 2025)
    assert math.isclose(g, (281724 / 245122 - 1) * 100, rel_tol=1e-6)
    assert math.isclose(an.value(df, "TSLA", "net_margin", 2025), 3855 / 94827 * 100, rel_tol=1e-6)


def test_first_year_growth_is_missing_not_zero(df):
    first = int(df[df.ticker == "AAPL"].fiscal_year.min())
    assert an.value(df, "AAPL", "revenue_growth", first) is None


def test_fmt():
    assert an.fmt(416161, "money") == "$416.2B"
    assert an.fmt(1_200_000, "money") == "$1.20T"
    assert an.fmt(850, "money") == "$850M"
    assert an.fmt(None, "pct") == "n/a"
    assert an.fmt(12.345, "pct") == "12.3%"


def test_cagr():
    assert math.isclose(an.cagr(100, 121, 2), 10.0)
    assert an.cagr(-1, 5, 2) is None


def test_records_are_json_safe(df):
    json.dumps(an.records(df), allow_nan=False)  # raises if any NaN slipped through
