"""Chat engine: natural-language question -> grounded answer (text + optional chart).

Pipeline:  normalise -> extract entities (company, metric, year) -> classify intent
           -> query the data layer -> compose a plain-English answer.

If an ANTHROPIC_API_KEY is configured, an LLM (with tool calling over the same data
functions) answers instead; on any failure the rule-based engine below is the fallback.
"""
from __future__ import annotations

import difflib
import re
from datetime import date

import pandas as pd

from . import analytics as an
from . import llm

DISCLAIMER = "Figures come from SEC 10-K filings. This is data analysis, not investment advice."
GROWTH_METRICS = {"revenue_growth", "net_income_growth"}
EXTRA_WORDS = {"profitable": "net_margin", "profitability": "net_margin", "leveraged": "liabilities_to_assets",
               "debt": "liabilities_to_assets", "growing": "revenue_growth", "fastest": "revenue_growth"}
EXTRA_ALIASES = {"google": "GOOGL", "alphabet": "GOOGL", "facebook": "META", "nvidia": "NVDA"}

RE_GREETING = re.compile(r"^(hi|hello|hey|yo|good (morning|afternoon|evening))\b")
RE_HELP = re.compile(r"\b(help|what can you do|how do you work|examples?|what (do|can) i ask)\b")
RE_LIST = re.compile(r"\b(which|what|list|show)( all)? (companies|stocks|firms)\b|\bcompanies do you\b")
RE_RANK = re.compile(r"\b(highest|lowest|best|worst|most|least|largest|biggest|smallest|top|leader|leading|fastest)\b")
RE_COMPARE = re.compile(r"\b(compare|comparison|versus|vs|against|difference|differ|between|better than)\b")
RE_CHANGE = re.compile(r"\b(chang(e|ed|es)|grew|grow|grown|increase[d]?|decrease[d]?|rise|rose|fell|fall|drop(ped)?|declin(e|ed))\b")
RE_TREND = re.compile(r"\b(trend|over time|history|historical|evolve|evolution|trajectory|progress|since|year over year|yoy)\b")
RE_PRIOR_CUE = re.compile(r"\b(last year|previous year|prior year|this year|latest|year over year|yoy|past year)\b")
RE_HEALTH = re.compile(r"\b(health|healthy|stable|stability|safe|risk|risky|solid|financial position|doing|performing|"
                       r"performance|overview|summary|summari[sz]e|analy[sz]e|analysis|tell me about|how is)\b")
RE_YEARS = re.compile(r"\b(?:fy\s?)?(20\d{2})\b")
RE_LASTN = re.compile(r"\b(?:last|past) (\d+) years\b")
RE_LOWER = re.compile(r"\b(lowest|least|smallest)\b")


def norm(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"'s\b", " ", t)
    t = re.sub(r"[^a-z0-9%\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def pct_change(new, old):
    if new is None or old in (None, 0):
        return None
    return (new - old) / abs(old) * 100


def fy_label(df: pd.DataFrame, ticker: str, year: int) -> str:
    r = df[(df["ticker"] == ticker) & (df["fiscal_year"] == year)]
    if len(r):
        end = date.fromisoformat(r.iloc[0]["fiscal_year_end"])
        return f"FY{year} (year ended {end.strftime('%b %Y')})"
    return f"FY{year}"


class Understanding:
    """Entities and intent extracted from one message."""

    def __init__(self, text: str, df: pd.DataFrame):
        self.raw = text
        self.names = {t: n for t, n in zip(df["ticker"], df["company"])}
        self.aliases = {t.lower(): t for t in self.names}
        self.aliases.update({n.lower(): t for t, n in self.names.items()})
        self.aliases.update({k: v for k, v in EXTRA_ALIASES.items() if v in self.names})
        self.words = {w: m for m, spec in an.METRICS.items() for w in spec["words"] if " " not in w}
        self.words.update(EXTRA_WORDS)
        self.text = self._correct(norm(text))
        self.tickers = self._tickers()
        self.metric = self._metric()
        self.years = [int(y) for y in RE_YEARS.findall(self.text)]
        m = RE_LASTN.search(self.text)
        self.last_n = int(m.group(1)) if m else None

    def _correct(self, text: str) -> str:
        """Typo tolerance: map near-miss words to company names / metric words."""
        known = list(self.aliases) + list(self.words)
        out = []
        for tok in text.split():
            if len(tok) >= 5 and tok not in known and not tok.isdigit():
                close = difflib.get_close_matches(tok, known, n=1, cutoff=0.84)
                if close and close[0][0] == tok[0]:
                    tok = close[0]
            out.append(tok)
        return " ".join(out)

    def _tickers(self) -> list[str]:
        found: list[str] = []
        for tok in self.text.split():
            t = self.aliases.get(tok)
            if t and t not in found:
                found.append(t)
        return found

    def _metric(self) -> str | None:
        phrases = sorted(((w, m) for m, s in an.METRICS.items() for w in s["words"]), key=lambda p: -len(p[0]))
        phrases += [(w, m) for w, m in EXTRA_WORDS.items()]
        taken: list[tuple[int, int]] = []
        hits: list[tuple[int, str]] = []
        for phrase, metric in phrases:
            for mt in re.finditer(rf"\b{re.escape(phrase)}\b", self.text):
                span = mt.span()
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                taken.append(span)
                hits.append((span[0], metric))
        return min(hits)[1] if hits else None

    def has(self, rx: re.Pattern) -> bool:
        return bool(rx.search(self.text))


class ChatEngine:
    def __init__(self, max_sessions: int = 1000):
        self.sessions: dict[str, dict] = {}
        self.max_sessions = max_sessions

    # ---------------------------------------------------------------- public
    def reply(self, session_id: str, message: str, df: pd.DataFrame) -> dict:
        state = self.sessions.setdefault(session_id, {"ticker": None, "history": []})
        if len(self.sessions) > self.max_sessions:
            self.sessions.pop(next(iter(self.sessions)))
        u = Understanding(message, df)
        mode = "rules"
        result = None
        if llm.available():
            result = llm.answer(message, state["history"], _Tools(df, state))
            mode = "llm" if result else "rules"
        if result is None:
            result = self._rules(u, df, state)
        state["history"] += [("user", message), ("assistant", result["reply"])]
        state["history"] = state["history"][-12:]
        result["mode"] = mode
        result.setdefault("chart", None)
        return result

    # ----------------------------------------------------------------- rules
    def _rules(self, u: Understanding, df: pd.DataFrame, state: dict) -> dict:
        if not u.text:
            return _msg("Ask me about a company's financials, for example: “How has Tesla's net income changed?”",
                        "empty", _suggest_default(df))
        if u.has(RE_GREETING) and not u.metric:
            return _msg("Hi! I answer questions about company financials from SEC 10-K filings. "
                        "Try one of the questions below.", "greeting", _suggest_default(df))
        if u.has(RE_HELP):
            return _msg(_help_text(df), "help", _suggest_default(df))
        if u.has(RE_LIST):
            names = ", ".join(f"{n} ({t})" for t, n in u.names.items())
            return _msg(f"I cover {len(u.names)} companies: {names}. Data spans FY{int(df['fiscal_year'].min())}"
                        f"–FY{int(df['fiscal_year'].max())}.", "list", _suggest_default(df))

        multi = len(u.tickers) >= 2
        if u.has(RE_RANK) and (multi or not u.tickers):
            return self._rank(u, df)
        if u.has(RE_COMPARE) or multi:
            return self._compare(u, df)

        metric = u.metric
        ticker = self._ticker(u, state)
        changing, trending = u.has(RE_CHANGE), u.has(RE_TREND) or u.last_n is not None
        if ticker is None:
            if metric or changing or trending or u.has(RE_HEALTH):
                return _msg("Which company do you mean? I have " + ", ".join(u.names.values()) + ".", "clarify",
                            [f"What is {n}'s revenue?" for n in list(u.names.values())[:3]])
            return _msg("Sorry, I didn't catch a company or a metric. " + _help_text(df), "fallback", _suggest_default(df))
        state["ticker"] = ticker

        if changing or trending:
            metric = metric or "revenue"
            if (u.has(RE_PRIOR_CUE)) or (changing and u.years and not trending):
                return self._change(u, df, ticker, metric)
            return self._trend(u, df, ticker, metric)
        if metric:
            return self._value(u, df, ticker, metric)
        if u.tickers or u.has(RE_HEALTH):  # a bare company name or a health cue; otherwise don't guess
            return self._health(u, df, ticker)
        return _msg("Sorry, I can only answer questions about company financials. " + _help_text(df), "fallback",
                    _suggest_default(df))

    def _ticker(self, u: Understanding, state: dict) -> str | None:
        return u.tickers[0] if u.tickers else state.get("ticker")

    # -------------------------------------------------------------- builders
    def _year_for(self, u: Understanding, df: pd.DataFrame, ticker: str) -> tuple[int | None, str | None]:
        have = sorted(df[df["ticker"] == ticker]["fiscal_year"].astype(int))
        if not u.years:
            return have[-1], None
        y = u.years[0]
        if y not in have:
            return None, f"I only have {u.names[ticker]} data for FY{have[0]}–FY{have[-1]}."
        return y, None

    def _value(self, u, df, ticker, metric) -> dict:
        return build_value(df, ticker, metric, *self._year_for(u, df, ticker))

    def _change(self, u, df, ticker, metric) -> dict:
        return build_change(df, ticker, metric, *self._year_for(u, df, ticker))

    def _trend(self, u, df, ticker, metric) -> dict:
        n = u.last_n
        if u.years and re.search(r"\bsince\b", u.text):
            n = int(df[df["ticker"] == ticker]["fiscal_year"].max()) - u.years[0] + 1
        return build_trend(df, ticker, metric or "revenue", n)

    def _compare(self, u, df) -> dict:
        tickers = u.tickers if len(u.tickers) >= 2 else list(u.names)
        return build_compare(df, u.metric or "revenue", tickers, u.years[0] if u.years else None)

    def _rank(self, u, df) -> dict:
        metric = u.metric or "revenue"
        pool = u.tickers if len(u.tickers) >= 2 else list(u.names)
        lower = u.has(RE_LOWER)
        if "worst" in u.text.split():
            lower = metric not in an.LOWER_IS_BETTER
        elif "best" in u.text.split():
            lower = metric in an.LOWER_IS_BETTER
        return build_compare(df, metric, pool, u.years[0] if u.years else None, ascending=lower, ranking=True)

    def _health(self, u, df, ticker) -> dict:
        return build_health(df, ticker, *self._year_for(u, df, ticker))


# ------------------------------------------------------------------ answer builders
def _msg(text: str, intent: str, suggestions: list[str] | None = None) -> dict:
    return {"reply": text, "intent": intent, "suggestions": suggestions or [], "chart": None, "entities": {}}


def _help_text(df: pd.DataFrame) -> str:
    return ("I can answer things like: revenue, net income, assets, liabilities, cash flow, margins and growth "
            "for a company and fiscal year; how a metric changed or trended; comparisons and rankings across "
            "companies; and a quick financial-health summary.")


def _suggest_default(df: pd.DataFrame) -> list[str]:
    return ["What is Apple's revenue?", "How has Tesla's net income changed?", "Compare profit margins",
            "Which company has the highest revenue growth?", "Is Microsoft financially healthy?"]


def build_value(df, ticker, metric, year, error=None) -> dict:
    name = df[df["ticker"] == ticker].iloc[0]["company"]
    if error or year is None:
        return _msg(error or "No data available.", "error")
    spec = an.METRICS[metric]
    v = an.value(df, ticker, metric, year)
    if v is None:
        return _msg(f"I don't have {spec['label'].lower()} for {name} in FY{year}"
                    + (" (growth needs a prior year)." if metric in GROWTH_METRICS else "."), "value")
    text = f"{name}'s {spec['label'].lower()} in {fy_label(df, ticker, year)} was {an.fmt(v, spec['kind'])}."
    if spec["kind"] == "money":
        ch = pct_change(v, an.value(df, ticker, metric, year - 1))
        if ch is not None:
            text += f" That is {'up' if ch >= 0 else 'down'} {abs(ch):.1f}% from FY{year - 1}."
    pts = an.series(df, ticker, metric)
    return {"reply": text, "intent": "value", "chart": _line_chart(f"{name} – {spec['label']}", pts, spec["kind"], name),
            "suggestions": [f"How has {name}'s {spec['label'].lower()} trended?", f"Is {name} financially healthy?",
                            f"Compare {spec['label'].lower()}"],
            "entities": {"tickers": [ticker], "metric": metric, "year": year}}


def build_change(df, ticker, metric, year, error=None) -> dict:
    name = df[df["ticker"] == ticker].iloc[0]["company"]
    if error or year is None:
        return _msg(error or "No data available.", "error")
    spec = an.METRICS[metric]
    new, old = an.value(df, ticker, metric, year), an.value(df, ticker, metric, year - 1)
    if new is None or old is None:
        return _msg(f"I need both FY{year} and FY{year - 1} to describe the change in {spec['label'].lower()} for {name}.",
                    "change")
    if spec["kind"] == "money":
        ch = pct_change(new, old)
        verb = "increased" if new >= old else "decreased"
        text = (f"{name}'s {spec['label'].lower()} {verb} by {an.fmt(abs(new - old), 'money')}"
                f"{f' ({abs(ch):.1f}%)' if ch is not None else ''} in FY{year}, from {an.fmt(old, 'money')} "
                f"to {an.fmt(new, 'money')}.")
    else:
        verb = "rose" if new >= old else "fell"
        text = (f"{name}'s {spec['label'].lower()} {verb} from {an.fmt(old, spec['kind'])} in FY{year - 1} "
                f"to {an.fmt(new, spec['kind'])} in FY{year}.")
    pts = an.series(df, ticker, metric, 5)
    return {"reply": text, "intent": "change", "chart": _bar_chart(f"{name} – {spec['label']}", [str(y) for y, _ in pts],
            [{"label": name, "data": [round(v, 2) for _, v in pts]}], spec["kind"]),
            "suggestions": [f"How has {name}'s {spec['label'].lower()} trended over time?", f"Is {name} financially healthy?"],
            "entities": {"tickers": [ticker], "metric": metric, "year": year}}


def build_trend(df, ticker, metric, years=None) -> dict:
    name = df[df["ticker"] == ticker].iloc[0]["company"]
    spec = an.METRICS[metric]
    pts = an.series(df, ticker, metric, years)
    if len(pts) < 2:
        return _msg(f"There isn't enough history to show a trend in {spec['label'].lower()} for {name}.", "trend")
    (y0, v0), (y1, v1) = pts[0], pts[-1]
    peak = max(pts, key=lambda p: p[1])
    if spec["kind"] == "money":
        ch = pct_change(v1, v0)
        text = (f"{name}'s {spec['label'].lower()} went from {an.fmt(v0, 'money')} in FY{y0} to {an.fmt(v1, 'money')} "
                f"in FY{y1}")
        if ch is not None:
            text += f" ({ch:+.0f}% overall"
            c = an.cagr(v0, v1, y1 - y0)
            text += f", about {c:.1f}% a year)." if c is not None else ")."
        else:
            text += "."
    else:
        text = (f"{name}'s {spec['label'].lower()} moved from {an.fmt(v0, spec['kind'])} in FY{y0} "
                f"to {an.fmt(v1, spec['kind'])} in FY{y1}.")
    if peak[0] not in (y0, y1):
        text += f" It peaked in FY{peak[0]} at {an.fmt(peak[1], spec['kind'])}."
    return {"reply": text, "intent": "trend", "chart": _line_chart(f"{name} – {spec['label']}", pts, spec["kind"], name),
            "suggestions": [f"Is {name} financially healthy?", f"Compare {spec['label'].lower()}"],
            "entities": {"tickers": [ticker], "metric": metric, "year": y1}}


def build_compare(df, metric, tickers, year=None, ascending=False, ranking=False) -> dict:
    spec = an.METRICS[metric]
    names = {t: df[df["ticker"] == t].iloc[0]["company"] for t in tickers}
    if year is None:  # most recent year in which all selected companies have data
        common = set.intersection(*(set(df[(df["ticker"] == t) & df[metric].notna()]["fiscal_year"]) for t in tickers))
        if not common:
            return _msg("These companies don't have overlapping data for that metric.", "compare")
        year = int(max(common))
    rows = [(t, an.value(df, t, metric, year)) for t in tickers]
    rows = [(t, v) for t, v in rows if v is not None]
    if not rows:
        return _msg(f"No {spec['label'].lower()} data for FY{year}.", "compare")
    rows.sort(key=lambda r: r[1], reverse=not ascending)
    listing = ", ".join(f"{names[t]} {an.fmt(v, spec['kind'])}" for t, v in rows)
    lead = names[rows[0][0]]
    if ranking:
        word = "Lowest" if ascending else "Highest"
        text = f"{word} {spec['label'].lower()} in FY{year}: {lead} at {an.fmt(rows[0][1], spec['kind'])}. Full ranking: {listing}."
    else:
        text = f"{spec['label']} in FY{year}: {listing}. {lead} leads."
        if len(rows) == 2 and spec["kind"] == "money" and rows[1][1] > 0 and rows[0][1] > 0:
            text += f" That is {rows[0][1] / rows[1][1]:.1f}x {names[rows[1][0]]}."
    chart = _bar_chart(f"{spec['label']} – FY{year}", [names[t] for t, _ in rows],
                       [{"label": spec["label"], "data": [round(v, 2) for _, v in rows]}], spec["kind"])
    return {"reply": text, "intent": "rank" if ranking else "compare", "chart": chart,
            "suggestions": [f"How has {names[rows[0][0]]}'s {spec['label'].lower()} trended?",
                            f"Is {names[rows[0][0]]} financially healthy?"],
            "entities": {"tickers": [t for t, _ in rows], "metric": metric, "year": year}}


def build_health(df, ticker, year, error=None) -> dict:
    name = df[df["ticker"] == ticker].iloc[0]["company"]
    if error or year is None:
        return _msg(error or "No data available.", "error")
    p = an.health_profile(df, ticker, year)
    m = p["metrics"]
    bits = []
    if m["net_margin"] is not None:
        bits.append(f"net margin {m['net_margin']:.1f}%")
    if m["liabilities_to_assets"] is not None:
        bits.append(f"liabilities are {m['liabilities_to_assets']:.0f}% of assets")
    if m["cash_conversion"] is not None:
        bits.append(f"operating cash flow is {m['cash_conversion']:.1f}x net income")
    if m["revenue_growth"] is not None:
        bits.append(f"revenue growth {m['revenue_growth']:+.1f}%")
    text = (f"{name} looks {p['verdict']} in {fy_label(df, ticker, year)}: {', '.join(p['notes'])}. "
            f"Details: {'; '.join(bits)}. This is a simple rule-based summary, not investment advice.")
    pts = an.series(df, ticker, "net_margin")
    return {"reply": text, "intent": "health", "chart": _line_chart(f"{name} – Net Margin", pts, "pct", name),
            "suggestions": [f"How has {name}'s revenue trended?", f"Compare {name} with its peers"],
            "entities": {"tickers": [ticker], "metric": None, "year": year}}


def _line_chart(title, pts, kind, label) -> dict:
    return {"type": "line", "title": title, "kind": kind, "labels": [str(y) for y, _ in pts],
            "datasets": [{"label": label, "data": [round(v, 2) for _, v in pts]}]}


def _bar_chart(title, labels, datasets, kind) -> dict:
    return {"type": "bar", "title": title, "kind": kind, "labels": labels, "datasets": datasets}


class _Tools:
    """Data functions exposed to the LLM as tools; each returns text grounded in the database."""

    def __init__(self, df: pd.DataFrame, state: dict):
        self.df, self.state, self.chart = df, state, None

    def _t(self, ticker: str) -> str | None:
        t = (ticker or "").upper()
        return t if t in set(self.df["ticker"]) else None

    def run(self, name: str, args: dict) -> str:
        df = self.df
        try:
            if name == "list_companies":
                return "; ".join(f"{t}={n}" for t, n in dict(zip(df["ticker"], df["company"])).items()) + \
                       f". Fiscal years {int(df['fiscal_year'].min())}-{int(df['fiscal_year'].max())}."
            if name == "compare_companies":
                r = build_compare(df, args.get("metric", "revenue"), [t for t in map(self._t, args.get("tickers") or list(set(df["ticker"]))) if t],
                                  args.get("year"))
            else:
                t = self._t(args.get("ticker", ""))
                if not t:
                    return "Unknown ticker. Use list_companies."
                self.state["ticker"] = t
                year = args.get("year") or an.latest_year(df, t)
                metric = args.get("metric", "revenue")
                r = {"get_metric": lambda: build_value(df, t, metric, year),
                     "get_change": lambda: build_change(df, t, metric, year),
                     "get_trend": lambda: build_trend(df, t, metric, args.get("years")),
                     "health_summary": lambda: build_health(df, t, year)}[name]()
            self.chart = r.get("chart") or self.chart
            return r["reply"]
        except Exception as exc:  # tool errors go back to the model, not the user
            return f"Tool error: {exc}"
