import json
from datetime import datetime, timezone
from urllib.error import URLError

import pytest

import economic_calendar as ec


def _event(**overrides):
    base = dict(
        timestamp=datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc),
        impact="High",
        currency="USD",
        title="Fed Interest Rate Decision",
    )
    base.update(overrides)
    return ec.EconomicEvent(**base)


class TestEconomicEvent:
    def test_valid_event_constructs(self):
        event = _event()
        assert event.impact == "High"

    def test_rejects_invalid_impact(self):
        with pytest.raises(ValueError):
            _event(impact="Extreme")

    def test_rejects_naive_timestamp(self):
        with pytest.raises(ValueError):
            _event(timestamp=datetime(2026, 7, 9, 18, 0))  # no tzinfo


class TestMockCalendarProvider:
    def test_returns_configured_events(self):
        events = [_event(currency="USD"), _event(currency="EUR")]
        provider = ec.MockCalendarProvider(events)
        assert provider.get_events() == events

    def test_returned_list_is_a_copy(self):
        provider = ec.MockCalendarProvider([_event()])
        result = provider.get_events()
        result.append(_event(currency="GBP"))
        assert len(provider.get_events()) == 1


class TestHttpJsonCalendarProvider:
    def test_parses_valid_response(self, monkeypatch):
        payload = [
            {"timestamp": "2026-07-09T18:00:00+00:00", "impact": "High", "currency": "USD", "title": "FOMC"},
        ]

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(payload).encode()

        monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: FakeResponse())

        provider = ec.HttpJsonCalendarProvider("https://example.com/calendar")
        events = provider.get_events()
        assert len(events) == 1
        assert events[0].currency == "USD"
        assert events[0].impact == "High"

    def test_applies_transform_hook(self, monkeypatch):
        payload = [{"when": "2026-07-09T18:00:00+00:00", "level": "High", "ccy": "USD", "name": "FOMC"}]

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(payload).encode()

        monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: FakeResponse())

        transform = lambda raw: {
            "timestamp": raw["when"], "impact": raw["level"], "currency": raw["ccy"], "title": raw["name"],
        }
        provider = ec.HttpJsonCalendarProvider("https://example.com/calendar", transform=transform)
        events = provider.get_events()
        assert events[0].title == "FOMC"

    def test_raises_calendar_unavailable_on_network_error(self, monkeypatch):
        def raise_error(req, timeout):
            raise URLError("connection refused")

        monkeypatch.setattr(ec.urllib.request, "urlopen", raise_error)

        provider = ec.HttpJsonCalendarProvider("https://example.com/calendar")
        with pytest.raises(ec.CalendarUnavailableError):
            provider.get_events()

    def test_raises_calendar_unavailable_on_bad_json(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"not json"

        monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: FakeResponse())

        provider = ec.HttpJsonCalendarProvider("https://example.com/calendar")
        with pytest.raises(ec.CalendarUnavailableError):
            provider.get_events()
