from types import SimpleNamespace as NS

from app import llm
from app.chatbot import ChatEngine, _Tools


class FakeClient:
    """Scripted stand-in for the Anthropic client: one tool call, then a final answer."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            block = NS(type="tool_use", id="t1", name="get_metric", input={"ticker": "AAPL", "metric": "revenue"})
            return NS(stop_reason="tool_use", content=[block])
        return NS(stop_reason="end_turn", content=[NS(type="text", text="Apple's revenue was $416.2B.")])


class Boom:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        raise RuntimeError("network")


def test_llm_tool_loop_returns_grounded_answer_and_chart(df):
    r = llm.answer("apple revenue?", [], _Tools(df, {"ticker": None}), client=FakeClient())
    assert r["reply"].startswith("Apple's revenue") and r["chart"]["type"] == "line"


def test_llm_failure_returns_none(df):
    assert llm.answer("x", [], _Tools(df, {}), client=Boom()) is None


def test_tool_rejects_unknown_ticker(df):
    assert "Unknown ticker" in _Tools(df, {}).run("get_metric", {"ticker": "ZZZ", "metric": "revenue"})


def test_engine_falls_back_to_rules_when_llm_fails(df, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "answer", lambda *a, **k: None)
    r = ChatEngine().reply("s", "What is Apple's revenue?", df)
    assert r["mode"] == "rules" and "$416.2B" in r["reply"]
