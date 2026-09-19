import pytest

from app.chatbot import ChatEngine


@pytest.fixture()
def ask(df):
    engine = ChatEngine()
    return lambda msg, sid="s": engine.reply(sid, msg, df)


def test_value(ask):
    r = ask("What is Apple's total revenue?")
    assert r["intent"] == "value" and "$416.2B" in r["reply"] and r["chart"]["type"] == "line"


def test_year(ask):
    assert "$211.9B" in ask("Microsoft revenue in 2023")["reply"]


def test_change_last_year(ask):
    r = ask("How has Tesla's net income changed over the last year?")
    assert r["intent"] == "change" and "decreased" in r["reply"] and "46.1%" in r["reply"]


def test_trend(ask):
    r = ask("Show Tesla's net income trend")
    assert r["intent"] == "trend" and "peaked" in r["reply"]


def test_trend_last_n_years(ask):
    r = ask("Apple revenue over the last 3 years")
    assert r["intent"] == "trend" and len(r["chart"]["labels"]) == 3


def test_compare_two(ask):
    r = ask("Compare Apple and Microsoft revenue")
    assert r["intent"] == "compare" and len(r["chart"]["labels"]) == 2


def test_compare_all_margins(ask):
    r = ask("Compare profit margins")
    assert r["intent"] == "compare" and "Net Margin" in r["reply"]


def test_rank_highest(ask):
    r = ask("Which company has the highest revenue growth?")
    assert r["intent"] == "rank" and r["reply"].startswith("Highest revenue growth")


def test_rank_lowest_and_best_leverage(ask):
    assert ask("Which company has the lowest net margin?")["reply"].startswith("Lowest")
    assert ask("Which company has the best leverage?")["reply"].startswith("Lowest")  # lower leverage is better


def test_most_profitable(ask):
    r = ask("Which company is the most profitable?")
    assert r["intent"] == "rank" and "net margin" in r["reply"].lower()


def test_health(ask):
    r = ask("Is Microsoft financially healthy?")
    assert r["intent"] == "health" and "not investment advice" in r["reply"]


def test_typos_are_tolerated(ask):
    r = ask("whats teslas revnue")
    assert r["intent"] == "value" and "Tesla" in r["reply"]


def test_session_memory(ask):
    ask("What is Tesla's revenue in 2024?", "a")
    r = ask("and its net income?", "a")
    assert "Tesla" in r["reply"]
    assert "Which company" in ask("and its net income?", "other")["reply"]


def test_unknown_year(ask):
    assert "only have" in ask("Apple revenue in 2001")["reply"]


def test_clarify_missing_company(ask):
    assert ask("What is the revenue?")["intent"] == "clarify"


def test_fallback_greeting_help_list(ask):
    assert ask("what is the weather")["intent"] == "fallback"
    assert ask("hello")["intent"] == "greeting"
    assert ask("help")["intent"] == "help"
    assert ask("which companies do you cover")["intent"] == "list"


def test_company_alias(ask):
    assert "Alphabet" in ask("google revenue")["reply"]


def test_since_year_uses_company_latest_year(ask):
    r = ask("Tesla net income since 2021")
    assert r["chart"]["labels"][0] == "2021" and r["chart"]["labels"][-1] == "2025"


def test_offtopic_does_not_reuse_session_company(ask):
    ask("What is Apple's revenue?", "z")
    assert ask("tell me a joke", "z")["intent"] == "fallback"
    assert ask("how is it doing?", "z")["intent"] == "health"
