"""Meter data from the portal and the statistics rows built from it."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}


@dataclass(frozen=True)
class HourlyRead:
    """What the meter measured, and its cost, in the hour that begins at start (UTC).

    The amount is kWh for electricity and cubic metres for water.
    """

    start: datetime
    amount: float
    cost: float


@dataclass(frozen=True)
class BillingPeriod:
    """Water used, and its consumption charge, between two meter readings."""

    start: date
    end: date
    volume: float
    cost: float


def parse_usage(rows: Iterable[dict[str, Any]]) -> list[HourlyRead]:
    """Turn the portal's electricity rows into one read per UTC hour, oldest first."""
    totals: dict[datetime, tuple[float, float]] = {}
    for row in rows:
        if row.get("value") is None:
            continue
        local_start = datetime.fromisoformat(row["startTime"])
        if local_start.tzinfo is None:
            raise ValueError(f"startTime has no UTC offset: {row['startTime']}")
        start = local_start.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        amount, cost = totals.get(start, (0.0, 0.0))
        totals[start] = (
            amount + float(row["value"]),
            cost + float(row.get("cost") or 0.0),
        )
    return [HourlyRead(start, *totals[start]) for start in sorted(totals)]


def parse_water_usage(
    usage: dict[str, Any], chart: dict[str, Any] | None
) -> list[BillingPeriod]:
    """Turn the portal's water chart into billing periods, oldest first.

    usage lists each period's end date, volume and cost. chart lists each
    period as "start - end"; where it has no entry for a period, the period
    starts on the previous period's end date. A period whose start is unknown
    is left out.
    """
    series = usage.get("series") or []
    if not series:
        return []
    unit = series[0].get("unit")
    if unit != "CM":
        raise ValueError(f"water unit is {unit!r}, expected CM (cubic metres)")
    ends = [_parse_date(label) for label in usage.get("categories") or []]
    volumes = series[0].get("data") or []
    costs = series[0].get("cost") or []
    if len(volumes) != len(ends):
        raise ValueError(f"{len(ends)} period dates for {len(volumes)} volumes")

    starts: dict[date, date] = {}
    for text in (chart or {}).get("dataPeriods") or []:
        try:
            first, _, last = text.partition(" - ")
            starts[_parse_date(last)] = _parse_date(first)
        except (AttributeError, ValueError):
            # The previous period's end date stands in for an unreadable entry.
            continue

    readings = sorted(
        (
            (end, volumes[index], costs[index] if index < len(costs) else None)
            for index, end in enumerate(ends)
        ),
        key=lambda reading: reading[0],
    )
    periods: list[BillingPeriod] = []
    for index, (end, volume, cost) in enumerate(readings):
        start = starts.get(end) or (readings[index - 1][0] if index else None)
        if start is None or start >= end or volume is None:
            continue
        periods.append(BillingPeriod(start, end, float(volume), float(cost or 0.0)))
    return periods


def spread_hourly(period: BillingPeriod, local_zone: tzinfo) -> list[HourlyRead]:
    """Share a period's volume and cost equally among its hours.

    The meter is read once per period, so the real use per hour is unknown.
    The period runs from local midnight of its start date to local midnight
    of its end date.
    """
    first = _local_midnight(period.start, local_zone)
    last = _local_midnight(period.end, local_zone)
    hours = int((last - first) / timedelta(hours=1))
    if hours <= 0:
        return []
    return [
        HourlyRead(
            first + timedelta(hours=index), period.volume / hours, period.cost / hours
        )
        for index in range(hours)
    ]


def with_stored_hours(
    reads: list[HourlyRead], stored: Iterable[HourlyRead]
) -> list[HourlyRead]:
    """Add the stored hours the portal left out of reads, oldest first.

    Every hour from the first read on gets a new running sum, so an hour that
    is already stored but missing from a later answer must keep its value.
    """
    by_start = {read.start: read for read in stored}
    by_start.update({read.start: read for read in reads})
    return [by_start[start] for start in sorted(by_start)]


def statistics_rows(
    reads: Iterable[HourlyRead], amount_base: float, cost_base: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build consumption and cost rows whose running sums continue from the bases."""
    consumption: list[dict[str, Any]] = []
    cost: list[dict[str, Any]] = []
    amount_sum = amount_base
    cost_sum = cost_base
    for read in reads:
        amount_sum += read.amount
        cost_sum += read.cost
        consumption.append(
            {"start": read.start, "state": read.amount, "sum": amount_sum}
        )
        cost.append({"start": read.start, "state": read.cost, "sum": cost_sum})
    return consumption, cost


def _parse_date(text: str) -> date:
    """Parse the portal's "Mar 10, 2025" dates without depending on the locale."""
    try:
        month, day, year = text.replace(",", " ").split()
        return date(int(year), _MONTHS[month], int(day))
    except (KeyError, ValueError) as err:
        raise ValueError(f"unexpected date: {text!r}") from err


def _local_midnight(day: date, local_zone: tzinfo) -> datetime:
    return (
        datetime.combine(day, time.min, local_zone)
        .astimezone(timezone.utc)
        .replace(minute=0, second=0, microsecond=0)
    )
