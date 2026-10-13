from datetime import datetime, timezone

import pytest

import claude_usage_widget as widget
from claude_usage_widget import fmt_reset, parse_usage

# ---------------------------------------------------------------- parse_usage


class TestParseUsageLimitsArray:
    """Newer response shape: a structured `limits` list."""

    def test_session_weekly_and_scoped(self):
        data = {"limits": [
            {"kind": "session", "percent": 12, "resets_at": "2026-10-13T14:00:00Z"},
            {"kind": "weekly_all", "percent": 13.4, "resets_at": "2026-10-18T17:00:00Z"},
            {"kind": "weekly_scoped", "percent": 8,
             "scope": {"model": {"display_name": "Fable"}}},
        ]}
        assert parse_usage(data) == {
            "Session": (12.0, "2026-10-13T14:00:00Z"),
            "Weekly": (13.4, "2026-10-18T17:00:00Z"),
            "Weekly (Fable)": (8.0, None),
        }

    def test_falls_back_to_utilization_when_percent_missing(self):
        data = {"limits": [{"kind": "session", "utilization": 40}]}
        assert parse_usage(data) == {"Session": (40.0, None)}

    def test_percent_wins_over_utilization(self):
        data = {"limits": [{"kind": "session", "percent": 10, "utilization": 99}]}
        assert parse_usage(data)["Session"][0] == 10.0

    def test_zero_percent_is_kept_not_treated_as_missing(self):
        data = {"limits": [{"kind": "session", "percent": 0, "utilization": 50}]}
        assert parse_usage(data) == {"Session": (0.0, None)}

    def test_entry_without_any_percentage_is_skipped(self):
        data = {"limits": [{"kind": "session"}, {"kind": "weekly_all", "percent": 5}]}
        assert parse_usage(data) == {"Weekly": (5.0, None)}

    def test_unknown_kind_is_ignored(self):
        data = {"limits": [{"kind": "monthly", "percent": 50}]}
        assert parse_usage(data) == {}

    def test_numeric_string_percent_is_converted(self):
        data = {"limits": [{"kind": "session", "percent": "12.5"}]}
        assert parse_usage(data) == {"Session": (12.5, None)}

    @pytest.mark.parametrize("entry", [
        {"kind": "weekly_scoped", "percent": 5},
        {"kind": "weekly_scoped", "percent": 5, "scope": {}},
        {"kind": "weekly_scoped", "percent": 5, "scope": None},
        {"kind": "weekly_scoped", "percent": 5, "scope": {"model": None}},
        {"kind": "weekly_scoped", "percent": 5,
         "scope": {"model": {"display_name": None}}},
    ])
    def test_scoped_without_model_name_is_labelled_model(self, entry):
        assert parse_usage({"limits": [entry]}) == {"Weekly (Model)": (5.0, None)}

    def test_non_dict_entries_are_skipped(self):
        data = {"limits": [None, "junk", {"kind": "session", "percent": 1}]}
        assert parse_usage(data) == {"Session": (1.0, None)}


class TestParseUsageFlatKeys:
    """Older response shape: five_hour / seven_day blocks."""

    def test_all_flat_keys(self):
        data = {
            "five_hour": {"utilization": 20, "resets_at": "A"},
            "seven_day": {"utilization": 30, "resets_at": "B"},
            "seven_day_sonnet": {"utilization": 40},
            "seven_day_opus": {"utilization": 50},
        }
        assert parse_usage(data) == {
            "Session": (20.0, "A"),
            "Weekly": (30.0, "B"),
            "Weekly (Sonnet)": (40.0, None),
            "Weekly (Opus)": (50.0, None),
        }

    def test_null_utilization_and_null_blocks_are_skipped(self):
        data = {"five_hour": {"utilization": None}, "seven_day": None}
        assert parse_usage(data) == {}

    def test_null_limits_falls_through_to_flat_keys(self):
        data = {"limits": None, "five_hour": {"utilization": 3}}
        assert parse_usage(data) == {"Session": (3.0, None)}


class TestParseUsageMixed:
    def test_limits_array_takes_precedence_over_flat_keys(self):
        data = {
            "limits": [{"kind": "session", "percent": 11}],
            "five_hour": {"utilization": 99},
            "seven_day": {"utilization": 22},
        }
        # Session comes from `limits`; Weekly is filled in from the flat key.
        assert parse_usage(data) == {"Session": (11.0, None), "Weekly": (22.0, None)}

    def test_empty_response(self):
        assert parse_usage({}) == {}
        assert parse_usage({"limits": []}) == {}


# ---------------------------------------------------------------- fmt_reset

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Pin datetime.now() inside the widget so countdowns are deterministic."""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(widget, "datetime", FrozenDatetime)


class TestFmtReset:
    @pytest.mark.parametrize("iso, expected", [
        ("2026-10-15T15:30:00Z", "resets in 2d 3h"),        # days + hours
        ("2026-10-14T12:00:00Z", "resets in 1d 0h"),        # exactly one day
        ("2026-10-13T13:05:00Z", "resets in 1h 5m"),        # hours + minutes
        ("2026-10-13T14:00:00Z", "resets in 2h 0m"),        # whole hours
        ("2026-10-13T12:40:00Z", "resets in 40m"),          # minutes only
        ("2026-10-13T12:00:30Z", "resets in 0m"),           # under a minute
        ("2026-10-13T12:00:00Z", "resets soon"),            # exactly now
        ("2026-10-13T11:00:00Z", "resets soon"),            # already passed
    ])
    def test_countdown(self, iso, expected):
        assert fmt_reset(iso) == expected

    def test_numeric_utc_offset(self):
        # 15:00 at +02:00 is 13:00 UTC, one hour from NOW
        assert fmt_reset("2026-10-13T15:00:00+02:00") == "resets in 1h 0m"

    def test_fractional_seconds(self):
        assert fmt_reset("2026-10-13T12:40:00.123456Z") == "resets in 40m"

    def test_timestamp_without_timezone_is_treated_as_utc(self):
        assert fmt_reset("2026-10-13T13:00:00") == "resets in 1h 0m"

    @pytest.mark.parametrize("value", [None, ""])
    def test_missing_value(self, value):
        assert fmt_reset(value) == ""

    @pytest.mark.parametrize("value", ["garbage", "2026-13-45", 1700000000, {}])
    def test_unparseable_value_returns_empty(self, value):
        assert fmt_reset(value) == ""
