"""FastAPI application: REST API + static single-page frontend."""
from __future__ import annotations

import os
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analytics as an
from . import db, ingest, limits, llm
from .chatbot import DISCLAIMER, ChatEngine

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
engine = ChatEngine()
limiter, budget = limits.from_env()


def client_ip(request: Request) -> str:
    """Real client address behind a proxy (Render sets X-Forwarded-For)."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.seed_if_empty(ingest.CSV_PATH)
    yield


app = FastAPI(title="FinSight AI", version="1.0.0",
              description="Financial analytics dashboard and chatbot built on SEC 10-K data.", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


@app.get("/api/health")
def health():
    df = an.get_frame()
    return {"status": "ok", "rows": len(df), "companies": int(df["ticker"].nunique()),
            "llm_enabled": llm.available()}


@app.get("/api/companies")
def companies():
    df = an.get_frame()
    out = []
    for t, g in df.groupby("ticker"):
        out.append({"ticker": t, "name": g.iloc[0]["company"], "first_year": int(g["fiscal_year"].min()),
                    "latest_year": int(g["fiscal_year"].max())})
    return out


@app.get("/api/metrics")
def metrics():
    return {k: {"label": v["label"], "kind": v["kind"]} for k, v in an.METRICS.items()}


@app.get("/api/financials")
def financials(tickers: str | None = Query(default=None, description="Comma-separated, e.g. AAPL,MSFT"),
               from_year: int | None = None, to_year: int | None = None):
    df = an.get_frame()
    if tickers:
        wanted = {t.strip().upper() for t in tickers.split(",") if t.strip()}
        unknown = wanted - set(df["ticker"])
        if unknown:
            raise HTTPException(404, f"Unknown ticker(s): {', '.join(sorted(unknown))}")
        df = df[df["ticker"].isin(wanted)]
    if from_year is not None:
        df = df[df["fiscal_year"] >= from_year]
    if to_year is not None:
        df = df[df["fiscal_year"] <= to_year]
    return an.records(df)


@app.get("/api/summary/{ticker}")
def summary(ticker: str):
    df = an.get_frame()
    t = ticker.upper()
    if t not in set(df["ticker"]):
        raise HTTPException(404, f"Unknown ticker: {ticker}")
    year = an.latest_year(df, t)
    row = an.records(df[(df["ticker"] == t) & (df["fiscal_year"] == year)])[0]
    return {"ticker": t, "fiscal_year": year, "latest": row, "health": an.health_profile(df, t, year)}


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    if not limiter.allow(client_ip(request)):
        raise HTTPException(429, "You are sending messages too quickly. Please wait a moment and try again.")
    sid = req.session_id or uuid.uuid4().hex[:16]
    message = re.sub(r"\s+", " ", req.message).strip()
    result = engine.reply(sid, message, an.get_frame(), allow_llm=llm.available() and budget.take())
    db.log_message(sid, "user", message)
    db.log_message(sid, "bot", result["reply"], result.get("intent"))
    return {**result, "session_id": sid, "disclaimer": DISCLAIMER}


@app.get("/api/chat/history/{session_id}")
def history(session_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
        raise HTTPException(400, "Invalid session id")
    return db.chat_history(session_id)


@app.post("/api/refresh")
def refresh():
    """Re-pull filings from SEC EDGAR. Disabled unless ENABLE_REFRESH=1 (it makes ~7 external requests)."""
    if os.environ.get("ENABLE_REFRESH") != "1":
        raise HTTPException(403, "Refresh is disabled. Set ENABLE_REFRESH=1 to allow it.")
    rows = ingest.fetch_all()
    if not rows:
        raise HTTPException(502, "Could not fetch data from the SEC.")
    ingest.write_csv(rows)
    return {"loaded": db.load_csv(ingest.CSV_PATH, replace=True)}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
