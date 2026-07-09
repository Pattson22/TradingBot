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
to point at whatever real provider you choose. transform_jblanked_event is
the concrete transform for jblanked.com's calendar API (the provider this
bot is currently wired to, see main.py._build_news_guard).

CachingCalendarProvider wraps any provider to avoid re-fetching on every
poll cycle -- important for rate-limited free tiers.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
            try:
                events.append(
                    EconomicEvent(
                        timestamp=datetime.fromisoformat(mapped["timestamp"]),
                        impact=mapped["impact"],
                        currency=mapped["currency"],
                        title=mapped["title"],
                    )
                )
            except (KeyError, ValueError):
                # One malformed/unrecognized event (e.g. an "impact" level
                # outside Low/Medium/High that some providers include, like
                # jblanked.com's "None") shouldn't take down the whole feed
                # and trigger fail-safe blocking -- just skip that event.
                log.warning("Skipping malformed calendar event from %s: %r", self._url, raw)
        return events


class CachingCalendarProvider(CalendarProvider):
    """
    Wraps another CalendarProvider and reuses its last successful
    get_events() result for `ttl_seconds` before fetching again, instead of
    hitting the underlying API on every call. Economic calendars are
    published hours/days ahead of time, so a periodically-refreshed
    snapshot is accurate for "is now inside a news blackout window" checks
    -- that comparison is pure local arithmetic and can run every poll
    cycle without a fresh fetch. This matters most for rate-limited free
    tiers (e.g. jblanked.com's calendar API caps free usage at 1
    request/day -- see config.NEWS_CALENDAR_CACHE_TTL_SECONDS).

    Deliberately does NOT serve stale data if a refresh fails: the
    exception propagates so EconomicCalendarFilter's existing fail-safe
    behaviour (block everything on calendar error) still applies, rather
    than silently trading on a calendar that might be hours out of date.
    """

    def __init__(self, inner: CalendarProvider, ttl_seconds: float, clock: Optional[Callable[[], datetime]] = None):
        self._inner = inner
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._cached_events: Optional[List[EconomicEvent]] = None
        self._cached_at: Optional[datetime] = None

    def get_events(self) -> List[EconomicEvent]:
        now = self._clock()
        if self._cached_events is None or now - self._cached_at >= self._ttl:
            self._cached_events = self._inner.get_events()
            self._cached_at = now
        return self._cached_events


def transform_jblanked_event(raw: dict) -> dict:
    """
    Maps a jblanked.com calendar event (see
    https://www.jblanked.com/news/api/docs/calendar/) to the shape
    HttpJsonCalendarProvider expects.

    Their "Date" field is "YYYY.MM.DD HH:MM:SS" with no timezone marker.
    Their docs describe an `offset` query param (GMT-3=0, GMT=3, EST=7,
    PST=10) to normalize the returned times -- config.NEWS_CALENDAR_URL is
    expected to include `offset=3` (GMT/UTC) so the raw string here can be
    taken as UTC wall-clock time directly. This has NOT been independently
    verified against their server clock; spot-check a known release time
    (e.g. an upcoming NFP Friday, always 12:30 UTC) once a real API key is
    live, and adjust the offset in NEWS_CALENDAR_URL if it's off.
    """
    timestamp = datetime.strptime(raw["Date"], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return {
        "timestamp": timestamp.isoformat(),
        "impact": raw["Impact"],
        "currency": raw["Currency"],
        "title": raw["Name"],
    }
