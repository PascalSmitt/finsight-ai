from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["rows"] > 0


def test_companies_and_metrics():
    assert {c["ticker"] for c in client.get("/api/companies").json()} >= {"AAPL", "MSFT", "TSLA"}
    assert "net_margin" in client.get("/api/metrics").json()


def test_financials_filters():
    rows = client.get("/api/financials?tickers=aapl&from_year=2024").json()
    assert rows and all(r["ticker"] == "AAPL" and r["fiscal_year"] >= 2024 for r in rows)
    assert client.get("/api/financials?tickers=ZZZ").status_code == 404


def test_summary():
    body = client.get("/api/summary/tsla").json()
    assert body["ticker"] == "TSLA" and "verdict" in body["health"]
    assert client.get("/api/summary/nope").status_code == 404


def test_chat_roundtrip_and_history():
    r = client.post("/api/chat", json={"message": "What is Apple's revenue?", "session_id": "test-1"})
    body = r.json()
    assert r.status_code == 200 and "Apple" in body["reply"] and body["session_id"] == "test-1"
    hist = client.get("/api/chat/history/test-1").json()
    assert [h["role"] for h in hist][-2:] == ["user", "bot"]


def test_chat_validation():
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 501}).status_code == 422
    assert client.post("/api/chat", json={"message": "hi", "session_id": "bad id!"}).status_code == 422
    assert client.get("/api/chat/history/bad%20id").status_code == 400


def test_refresh_disabled_by_default():
    assert client.post("/api/refresh").status_code == 403


def test_frontend_served():
    assert "FinSight AI" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
