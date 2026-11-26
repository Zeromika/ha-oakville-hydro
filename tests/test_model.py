"""Tests for the hourly-read model. They need pytest and nothing from Home Assistant."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys

import pytest

_PATH = Path(__file__).parents[1] / "custom_components" / "oakville_hydro" / "model.py"
_SPEC = importlib.util.spec_from_file_location("oakville_hydro_model", _PATH)
model = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = model
_SPEC.loader.exec_module(model)

UTC = timezone.utc


def _row(start: str, value, cost=0.1):
    return {"startTime": start, "duration": 3600, "value": value, "cost": cost}


def test_fall_back_day_keeps_both_one_oclock_hours():
    rows = [
        _row("2026-11-01T00:00:00-04:00", 1.0),
        _row("2026-11-01T01:00:00-04:00", 2.0),
        _row("2026-11-01T01:00:00-05:00", 3.0),
        _row("2026-11-01T02:00:00-05:00", 4.0),
    ]
    reads = model.parse_usage(rows)
    assert [read.kwh for read in reads] == [1.0, 2.0, 3.0, 4.0]
    assert [read.start for read in reads] == [
        datetime(2026, 11, 1, 4, tzinfo=UTC) + timedelta(hours=i) for i in range(4)
    ]


def test_spring_forward_day_has_no_gap_in_utc():
    rows = [
        _row("2026-03-08T01:00:00-05:00", 1.0),
        _row("2026-03-08T03:00:00-04:00", 1.0),
    ]
    first, second = model.parse_usage(rows)
    assert second.start - first.start == timedelta(hours=1)


def test_rows_inside_one_hour_are_added_together():
    rows = [
        _row("2026-11-25T10:00:00-05:00", 0.25, 0.02),
        _row("2026-11-25T10:30:00-05:00", 0.5, 0.04),
    ]
    (read,) = model.parse_usage(rows)
    assert read.start == datetime(2026, 11, 25, 15, tzinfo=UTC)
    assert read.kwh == pytest.approx(0.75)
    assert read.cost == pytest.approx(0.06)


def test_hour_without_a_value_is_left_out():
    assert model.parse_usage([_row("2026-11-25T10:00:00-05:00", None, None)]) == []


def test_start_time_without_offset_is_rejected():
    with pytest.raises(ValueError):
        model.parse_usage([_row("2026-11-25T10:00:00", 1.0)])


def test_running_sums_continue_from_the_bases():
    start = datetime(2026, 11, 20, 5, tzinfo=UTC)
    reads = [model.HourlyRead(start + timedelta(hours=i), 1.5, 0.2) for i in range(3)]
    consumption, cost = model.statistics_rows(reads, 100.0, 10.0)
    assert [row["sum"] for row in consumption] == pytest.approx([101.5, 103.0, 104.5])
    assert [row["sum"] for row in cost] == pytest.approx([10.2, 10.4, 10.6])
    assert [row["start"] for row in consumption] == [read.start for read in reads]


def test_stored_hour_missing_from_a_reread_keeps_its_value():
    start = datetime(2026, 11, 20, 5, tzinfo=UTC)
    stored = [model.HourlyRead(start + timedelta(hours=i), 1.0, 0.1) for i in range(4)]
    # The portal corrects hour 1, leaves out hour 2 and adds hour 4.
    reads = [
        model.HourlyRead(start, 1.0, 0.1),
        model.HourlyRead(start + timedelta(hours=1), 2.0, 0.2),
        model.HourlyRead(start + timedelta(hours=3), 1.0, 0.1),
        model.HourlyRead(start + timedelta(hours=4), 1.0, 0.1),
    ]
    merged = model.with_stored_hours(reads, stored)
    consumption, _ = model.statistics_rows(merged, 100.0, 10.0)
    assert [row["state"] for row in consumption] == [1.0, 2.0, 1.0, 1.0, 1.0]
    assert [row["sum"] for row in consumption] == pytest.approx(
        [101.0, 103.0, 104.0, 105.0, 106.0]
    )
    # Reading the same answer again changes nothing.
    again, _ = model.statistics_rows(model.with_stored_hours(reads, merged), 100.0, 10.0)
    assert again == consumption
