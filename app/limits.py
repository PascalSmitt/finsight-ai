"""Abuse protection for a public deployment: per-client rate limit and a daily cap on LLM calls."""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from datetime import date


class RateLimiter:
    """Sliding-window limiter: at most `limit` hits per `window` seconds for each key."""

    def __init__(self, limit: int, window: float = 60.0, max_keys: int = 10_000):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if len(self.hits) > self.max_keys:  # bound memory under a flood of distinct clients
            self.hits.clear()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


class DailyBudget:
    """Caps how many LLM-backed answers are served per UTC day (protects the API bill)."""

    def __init__(self, limit: int):
        self.limit, self.day, self.used = limit, date.today(), 0

    def take(self) -> bool:
        today = date.today()
        if today != self.day:
            self.day, self.used = today, 0
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def from_env() -> tuple[RateLimiter, DailyBudget]:
    return (RateLimiter(int(os.environ.get("CHAT_RATE_PER_MIN", "20"))),
            DailyBudget(int(os.environ.get("LLM_DAILY_LIMIT", "200"))))
