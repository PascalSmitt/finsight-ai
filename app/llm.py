"""Optional LLM mode: Claude answers using tool calls over the same financial data.

Enabled only when ANTHROPIC_API_KEY is set. The model never sees raw numbers except through
tool results, which keeps answers grounded in the database. Any error returns None so the
caller falls back to the rule-based engine.
"""
from __future__ import annotations

import os

MODEL = os.environ.get("FINSIGHT_MODEL", "claude-sonnet-5")
MAX_TURNS = 5
METRIC_ENUM = ["revenue", "net_income", "total_assets", "total_liabilities", "operating_cash_flow",
               "net_margin", "liabilities_to_assets", "roa", "revenue_growth", "net_income_growth", "cash_conversion"]

SYSTEM = (
    "You are FinSight, a concise financial-data assistant. Answer ONLY with numbers returned by your tools; "
    "never invent or recall figures from memory. Amounts are USD from SEC 10-K filings. If a question cannot be "
    "answered with the tools, say so and suggest what you can do. Keep answers under 120 words, in plain "
    "language, and note that this is data analysis, not investment advice, when giving opinions."
)

_TICKER = {"type": "string", "description": "Company ticker, e.g. AAPL"}
_METRIC = {"type": "string", "enum": METRIC_ENUM}
_YEAR = {"type": "integer", "description": "Fiscal year; omit for the latest"}
TOOLS = [
    {"name": "list_companies", "description": "List covered companies and the fiscal-year range.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_metric", "description": "Value of one metric for one company and fiscal year.",
     "input_schema": {"type": "object", "properties": {"ticker": _TICKER, "metric": _METRIC, "year": _YEAR},
                      "required": ["ticker", "metric"]}},
    {"name": "get_change", "description": "Year-over-year change of a metric for one company.",
     "input_schema": {"type": "object", "properties": {"ticker": _TICKER, "metric": _METRIC, "year": _YEAR},
                      "required": ["ticker", "metric"]}},
    {"name": "get_trend", "description": "Multi-year trend of a metric for one company.",
     "input_schema": {"type": "object", "properties": {"ticker": _TICKER, "metric": _METRIC,
                                                       "years": {"type": "integer", "description": "How many recent years"}},
                      "required": ["ticker", "metric"]}},
    {"name": "compare_companies", "description": "Compare or rank companies on a metric for a fiscal year.",
     "input_schema": {"type": "object", "properties": {"metric": _METRIC, "year": _YEAR,
                                                       "tickers": {"type": "array", "items": {"type": "string"}}},
                      "required": ["metric"]}},
    {"name": "health_summary", "description": "Rule-based financial-health summary for one company.",
     "input_schema": {"type": "object", "properties": {"ticker": _TICKER, "year": _YEAR}, "required": ["ticker"]}},
]


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("FINSIGHT_DISABLE_LLM") != "1"


def _client():
    import anthropic  # imported lazily so the app runs without the package
    return anthropic.Anthropic()


def answer(message: str, history: list[tuple[str, str]], tools, client=None) -> dict | None:
    """Return a reply dict, or None if the LLM path failed (caller falls back to rules)."""
    try:
        client = client or _client()
        msgs = [{"role": r, "content": t} for r, t in history[-8:]] + [{"role": "user", "content": message}]
        for _ in range(MAX_TURNS):
            resp = client.messages.create(model=MODEL, max_tokens=700, system=SYSTEM, tools=TOOLS, messages=msgs)
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
                if not text:
                    return None
                return {"reply": text, "intent": "llm", "chart": tools.chart, "entities": {},
                        "suggestions": ["Compare revenue", "Which company has the highest net margin?"]}
            msgs.append({"role": "assistant", "content": resp.content})
            results = [{"type": "tool_result", "tool_use_id": b.id, "content": tools.run(b.name, dict(b.input))}
                       for b in resp.content if getattr(b, "type", "") == "tool_use"]
            msgs.append({"role": "user", "content": results})
        return None
    except Exception:
        return None
