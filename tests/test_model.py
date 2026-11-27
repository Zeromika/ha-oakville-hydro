"""Tests for the hourly-read model. They need pytest and nothing from Home Assistant."""

from datetime import date, datetime, timedelta, timezone
import importlib.util
from itertools import pairwise
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

_PATH = Path(__file__).parents[1] / "custom_components" / "oakville_hydro" / "model.py"
_SPEC = importlib.util.spec_from_file_location("oakville_hydro_model", _PATH)
model = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = model
_SPEC.loader.exec_module(model)

UTC = timezone.utc
TORONTO = ZoneInfo("America/Toronto")

WATER_USAGE = {
    "categories": ["Mar 10, 2025", "May 12, 2025"],
    "series": [{"data": [6, 9], "cost": [20.5, 30.25], "unit": "CM"}],
}
WATER_CHART = {
    "dataPeriods": ["Jan 08, 2025 - Mar 10, 2025", "Mar 10, 2025 - May 12, 2025"]
}


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
    assert [read.amount for read in reads] == [1.0, 2.0, 3.0, 4.0]
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
    assert read.amount == pytest.approx(0.75)
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


def test_water_periods_take_their_start_from_the_chart():
    periods = model.parse_water_usage(WATER_USAGE, WATER_CHART)
    assert periods == [
        model.BillingPeriod(date(2025, 1, 8), date(2025, 3, 10), 6.0, 20.5),
        model.BillingPeriod(date(2025, 3, 10), date(2025, 5, 12), 9.0, 30.25),
    ]


def test_water_period_without_chart_starts_at_the_previous_reading():
    periods = model.parse_water_usage(WATER_USAGE, None)
    assert periods == [
        model.BillingPeriod(date(2025, 3, 10), date(2025, 5, 12), 9.0, 30.25)
    ]


def test_water_in_another_unit_is_rejected():
    usage = {"categories": ["Mar 10, 2025"], "series": [{"data": [6], "unit": "L"}]}
    with pytest.raises(ValueError):
        model.parse_water_usage(usage, WATER_CHART)


def test_account_without_water_data_gives_no_periods():
    assert model.parse_water_usage({"categories": [], "series": []}, None) == []


def test_period_is_spread_over_every_hour_including_the_fall_back_hour():
    period = model.BillingPeriod(date(2026, 10, 30), date(2026, 11, 1), 4.9, 9.8)
    reads = model.period_hours(period, TORONTO, model.WaterDistribution.SPREAD)
    # 31 October has 24 hours and 1 November, when the clocks go back, has 25.
    assert len(reads) == 49
    assert reads[0].start == datetime(2026, 10, 31, 4, tzinfo=UTC)
    assert reads[-1].start == datetime(2026, 11, 2, 4, tzinfo=UTC)
    assert all(b.start - a.start == timedelta(hours=1) for a, b in pairwise(reads))
    assert sum(read.amount for read in reads) == pytest.approx(4.9)
    assert sum(read.cost for read in reads) == pytest.approx(9.8)


def test_reading_date_puts_the_whole_period_in_the_first_hour_of_that_date():
    period = model.BillingPeriod(date(2025, 3, 10), date(2025, 5, 12), 9.0, 30.25)
    reads = model.period_hours(period, TORONTO, model.WaterDistribution.READING_DATE)
    used = [read for read in reads if read.amount or read.cost]
    assert used == [
        model.HourlyRead(datetime(2025, 5, 12, 4, tzinfo=UTC), 9.0, 30.25)
    ]


def test_both_distributions_cover_the_same_hours_and_end_on_the_same_sums():
    period = model.BillingPeriod(date(2025, 3, 10), date(2025, 5, 12), 9.0, 30.25)
    spread = model.period_hours(period, TORONTO, model.WaterDistribution.SPREAD)
    lump = model.period_hours(period, TORONTO, model.WaterDistribution.READING_DATE)
    assert [read.start for read in spread] == [read.start for read in lump]
    spread_rows, spread_cost = model.statistics_rows(spread, 50.0, 5.0)
    lump_rows, lump_cost = model.statistics_rows(lump, 50.0, 5.0)
    assert spread_rows[-1]["sum"] == pytest.approx(lump_rows[-1]["sum"])
    assert spread_cost[-1]["sum"] == pytest.approx(lump_cost[-1]["sum"])


@pytest.mark.parametrize("distribution", list(model.WaterDistribution))
def test_consecutive_periods_share_no_hour(distribution):
    first, second = model.parse_water_usage(WATER_USAGE, WATER_CHART)
    earlier = model.period_hours(first, TORONTO, distribution)
    later = model.period_hours(second, TORONTO, distribution)
    assert later[0].start - earlier[-1].start == timedelta(hours=1)


def test_unreadable_chart_entry_falls_back_to_the_previous_reading():
    chart = {"dataPeriods": ["Jan 08, 2025 - Mar 10, 2025", "", None]}
    periods = model.parse_water_usage(WATER_USAGE, chart)
    assert [(period.start, period.end) for period in periods] == [
        (date(2025, 1, 8), date(2025, 3, 10)),
        (date(2025, 3, 10), date(2025, 5, 12)),
    ]


def test_newest_first_readings_are_put_in_date_order():
    usage = {
        "categories": ["May 12, 2025", "Mar 10, 2025"],
        "series": [{"data": [9, 6], "cost": [30.25, 20.5], "unit": "CM"}],
    }
    assert model.parse_water_usage(usage, WATER_CHART) == model.parse_water_usage(
        WATER_USAGE, WATER_CHART
    )
