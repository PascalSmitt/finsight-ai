from fastapi.testclient import TestClient

from app import limits, llm, main
from app.chatbot import ChatEngine


def test_rate_limiter_window():
    rl = limits.RateLimiter(2, window=60)
    assert rl.allow("a", now=0) and rl.allow("a", now=1)
    assert not rl.allow("a", now=2)
    assert rl.allow("b", now=2)            # other clients are unaffected
    assert rl.allow("a", now=61.5)         # old hits expire


def test_daily_budget_caps_and_resets(monkeypatch):
    b = limits.DailyBudget(2)
    assert b.take() and b.take() and not b.take()
    b.day = b.day.replace(year=b.day.year - 1)   # simulate a new day
    assert b.take()


def test_api_returns_429_when_rate_limited(monkeypatch):
    monkeypatch.setattr(main, "limiter", limits.RateLimiter(2, 60))
    client = TestClient(main.app)
    codes = [client.post("/api/chat", json={"message": "hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_llm_not_called_when_budget_exhausted(df, monkeypatch):
    called = []
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "answer", lambda *a, **k: called.append(1))
    r = ChatEngine().reply("s", "What is Apple's revenue?", df, allow_llm=False)
    assert not called and r["mode"] == "rules"


def test_forwarded_ip_is_used_for_limiting(monkeypatch):
    monkeypatch.setattr(main, "limiter", limits.RateLimiter(1, 60))
    client = TestClient(main.app)
    ok = lambda ip: client.post("/api/chat", json={"message": "hi"}, headers={"x-forwarded-for": ip}).status_code
    assert [ok("1.1.1.1"), ok("2.2.2.2"), ok("1.1.1.1")] == [200, 200, 429]
