"""Hourly meter reads and the statistics rows built from them."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class HourlyRead:
    """Energy used, and its commodity cost, in the hour that begins at start (UTC)."""

    start: datetime
    kwh: float
    cost: float


def parse_usage(rows: Iterable[dict[str, Any]]) -> list[HourlyRead]:
    """Turn the portal's usage rows into one read per UTC hour, oldest first."""
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
        kwh, cost = totals.get(start, (0.0, 0.0))
        totals[start] = (
            kwh + float(row["value"]),
            cost + float(row.get("cost") or 0.0),
        )
    return [HourlyRead(start, *totals[start]) for start in sorted(totals)]


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
    reads: Iterable[HourlyRead], kwh_base: float, cost_base: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build consumption and cost rows whose running sums continue from the bases."""
    consumption: list[dict[str, Any]] = []
    cost: list[dict[str, Any]] = []
    kwh_sum = kwh_base
    cost_sum = cost_base
    for read in reads:
        kwh_sum += read.kwh
        cost_sum += read.cost
        consumption.append({"start": read.start, "state": read.kwh, "sum": kwh_sum})
        cost.append({"start": read.start, "state": read.cost, "sum": cost_sum})
    return consumption, cost
