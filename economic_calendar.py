"""
Economic calendar data model and event providers, used by news_filter.py to
protect the bot from high-impact macro news (the "news pause" guardrail).

Providers are pluggable: MockCalendarProvider is a fully in-memory,
deterministic source used for tests and the demo in news_filter.py.
HttpJsonCalendarProvider is a generic adapter for any real calendar API that
returns JSON — point it at your provider of choice (e.g. a paid economic-
calendar API, or a self-hosted proxy in front of one) via config.py's
NEWS_CALENDAR_URL / NEWS_CALENDAR_API_KEY env vars. Deliberately NOT
included: scraping a specific site's HTML (e.g. Forex Factory) — that's
fragile against markup changes and often against the target site's terms of
service. A generic JSON adapter with a `transform` hook is safer and easier
to point at whatever real provider you choose.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from logger_setup import get_logger

log = get_logger(__name__)

VALID_IMPACTS = ("Low", "Medium", "High")


class CalendarUnavailableError(RuntimeError):
    """Raised when a calendar provider cannot return event data. Callers
    (news_filter.EconomicCalendarFilter) treat this as a signal to fail
    safe rather than trade blind through an unknown news window."""


@dataclass(frozen=True)
class EconomicEvent:
    timestamp: datetime  # UTC, tz-aware
    impact: str  # "Low" / "Medium" / "High"
    currency: str  # ISO 4217 code, e.g. "USD", "EUR"
    title: str

    def __post_init__(self):
        if self.impact not in VALID_IMPACTS:
            raise ValueError(f"impact must be one of {VALID_IMPACTS}, got {self.impact!r}")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")


class CalendarProvider(ABC):
    """Interface every calendar source implements."""

    @abstractmethod
    def get_events(self) -> List[EconomicEvent]:
        """Return upcoming/recent events. Must raise CalendarUnavailableError
        (or let the underlying exception propagate) on failure rather than
        returning a partial or empty result silently — callers rely on the
        exception to trigger fail-safe behaviour, and a silently-empty
        result would look identical to "no news scheduled"."""


class MockCalendarProvider(CalendarProvider):
    """In-memory provider for tests and demos: returns a fixed event list."""

    def __init__(self, events: List[EconomicEvent]):
        self._events = list(events)

    def get_events(self) -> List[EconomicEvent]:
        return list(self._events)


class HttpJsonCalendarProvider(CalendarProvider):
    """
    Generic adapter for any calendar API/proxy that serves a JSON array of
    events. Expects each element to look like:

        {"timestamp": "2026-07-09T18:00:00+00:00", "impact": "High",
         "currency": "USD", "title": "Fed Interest Rate Decision"}

    If your provider's schema differs, pass a `transform` callable that maps
    its raw JSON dict to that shape. Pass credentials via `headers` (e.g.
    {"Authorization": f"Bearer {api_key}"}) — never hardcode an API key in
    source; config.py reads it from the NEWS_CALENDAR_API_KEY env var.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[dict] = None,
        timeout_seconds: float = 5.0,
        transform: Optional[Callable[[dict], dict]] = None,
    ):
        self._url = url
        self._headers = headers or {}
        self._timeout_seconds = timeout_seconds
        self._transform = transform or (lambda raw: raw)

    def get_events(self) -> List[EconomicEvent]:
        request = urllib.request.Request(self._url, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            raise CalendarUnavailableError(f"Failed to fetch calendar from {self._url}: {exc}") from exc

        events = []
        for raw in payload:
            mapped = self._transform(raw)
            events.append(
                EconomicEvent(
                    timestamp=datetime.fromisoformat(mapped["timestamp"]),
                    impact=mapped["impact"],
                    currency=mapped["currency"],
                    title=mapped["title"],
                )
            )
        return events
