"""Pulls hourly usage from the portal and stores it as long-term statistics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import InvalidAuth, MfaRequired, OakvilleHydroClient, OakvilleHydroError
from .const import (
    CONF_ACCOUNT_ID,
    CONF_METER,
    CONF_SERVICE_ID,
    CONF_SERVICE_START,
    CONSUMPTION_STATISTIC_ID,
    COST_STATISTIC_ID,
    DOMAIN,
)
from .model import HourlyRead, statistics_rows, with_stored_hours

_LOGGER = logging.getLogger(__name__)

# The portal publishes the previous day's readings once a night.
UPDATE_INTERVAL = timedelta(hours=6)
# Largest date range the portal was seen to answer in one request.
CHUNK_DAYS = 14
# Readings this far behind the newest stored hour are read again, so that
# values the utility corrects after the fact replace the stored ones.
REFETCH_DAYS = 7
# How far back the first import goes when the service start date is unknown.
DEFAULT_BACKFILL_DAYS = 400
# Wait after a failed update, instead of the full update interval.
RETRY_SECONDS = 600

type OakvilleHydroConfigEntry = ConfigEntry[OakvilleHydroCoordinator]


class OakvilleHydroCoordinator(DataUpdateCoordinator[datetime | None]):
    """Its data is the start of the newest hour stored, or None before the first."""

    config_entry: OakvilleHydroConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: OakvilleHydroConfigEntry) -> None:
        """Create the portal client from the entry's credentials."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Oakville Hydro",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = OakvilleHydroClient(
            async_get_clientsession(hass),
            config_entry.data[CONF_USERNAME],
            config_entry.data[CONF_PASSWORD],
        )

        @callback
        def _keep_polling() -> None:
            pass

        # A coordinator polls only while it has a listener, and this
        # integration has no entities to provide one.
        self.async_add_listener(_keep_polling)

    async def _async_update_data(self) -> datetime | None:
        try:
            # The session token's lifetime is not documented, so sign in every time.
            await self.client.async_login()
        except (InvalidAuth, MfaRequired) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OakvilleHydroError as err:
            raise UpdateFailed(str(err), retry_after=RETRY_SECONDS) from err
        try:
            return await self._async_import()
        except OakvilleHydroError as err:
            # A rejected token here is not a rejected password: the sign-in above
            # succeeded. Try again later instead of asking for the password.
            raise UpdateFailed(str(err), retry_after=RETRY_SECONDS) from err

    async def _async_import(self) -> datetime | None:
        recorder = get_instance(self.hass)
        last = await recorder.async_add_executor_job(
            get_last_statistics, self.hass, 1, CONSUMPTION_STATISTIC_ID, True, set()
        )
        today = dt_util.now().date()
        if last:
            last_start = dt_util.utc_from_timestamp(
                last[CONSUMPTION_STATISTIC_ID][0]["start"]
            )
            fetch_from = dt_util.as_local(last_start).date() - timedelta(
                days=REFETCH_DAYS
            )
        else:
            last_start = None
            service_start = self.config_entry.data.get(CONF_SERVICE_START)
            fetch_from = (
                date.fromisoformat(service_start)
                if service_start
                else today - timedelta(days=DEFAULT_BACKFILL_DAYS)
            )

        reads = await self._async_fetch(fetch_from, today)
        if not reads:
            _LOGGER.debug("The portal returned no readings since %s", fetch_from)
            return last_start

        reads = with_stored_hours(
            reads, await self._async_stored_hours(reads[0].start, reads[-1].start)
        )
        kwh_base, cost_base = await self._async_sums_before(reads[0].start)
        consumption, cost = statistics_rows(reads, kwh_base, cost_base)
        async_add_external_statistics(
            self.hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name="Oakville Hydro electricity",
                source=DOMAIN,
                statistic_id=CONSUMPTION_STATISTIC_ID,
                unit_class=EnergyConverter.UNIT_CLASS,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
            consumption,
        )
        async_add_external_statistics(
            self.hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name="Oakville Hydro electricity cost (commodity)",
                source=DOMAIN,
                statistic_id=COST_STATISTIC_ID,
                unit_class=None,
                unit_of_measurement=self.hass.config.currency,
            ),
            cost,
        )
        _LOGGER.debug(
            "Stored %s hourly readings from %s to %s",
            len(reads),
            reads[0].start,
            reads[-1].start,
        )
        return reads[-1].start

    async def _async_fetch(self, first: date, last: date) -> list[HourlyRead]:
        data = self.config_entry.data
        by_start: dict[datetime, HourlyRead] = {}
        chunk_start = first
        while chunk_start <= last:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), last)
            for read in await self.client.async_get_hourly(
                data[CONF_ACCOUNT_ID],
                data[CONF_SERVICE_ID],
                data[CONF_METER],
                chunk_start,
                chunk_end,
            ):
                by_start[read.start] = read
            chunk_start = chunk_end + timedelta(days=1)
        return [by_start[start] for start in sorted(by_start)]

    async def _async_stored_hours(
        self, first: datetime, last: datetime
    ) -> list[HourlyRead]:
        """Return what is stored for the hours from first to last, both included."""
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            first,
            last + timedelta(hours=1),
            {CONSUMPTION_STATISTIC_ID, COST_STATISTIC_ID},
            "hour",
            None,
            {"state"},
        )
        cost_by_start = {
            row["start"]: row.get("state")
            for row in stats.get(COST_STATISTIC_ID, [])
        }
        return [
            HourlyRead(
                dt_util.utc_from_timestamp(row["start"]),
                float(row["state"]),
                float(cost_by_start.get(row["start"]) or 0.0),
            )
            for row in stats.get(CONSUMPTION_STATISTIC_ID, [])
            if row.get("state") is not None
        ]

    async def _async_sums_before(self, start: datetime) -> tuple[float, float]:
        """Return the running sums stored for the newest hour before start."""
        # The hour before start is normally stored. When it is not, look through
        # everything older for the newest hour that is.
        for window_start in (start - timedelta(days=2), dt_util.utc_from_timestamp(0)):
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                window_start,
                start,
                {CONSUMPTION_STATISTIC_ID, COST_STATISTIC_ID},
                "hour",
                None,
                {"sum"},
            )
            if all(
                _older_rows(stats.get(statistic_id, []), start)
                for statistic_id in (CONSUMPTION_STATISTIC_ID, COST_STATISTIC_ID)
            ):
                break

        def _last_sum(statistic_id: str) -> float:
            rows = _older_rows(stats.get(statistic_id, []), start)
            return float(rows[-1]["sum"]) if rows else 0.0

        return _last_sum(CONSUMPTION_STATISTIC_ID), _last_sum(COST_STATISTIC_ID)


def _older_rows(rows: list[Any], start: datetime) -> list[Any]:
    """Keep the statistics rows that begin before start and carry a sum."""
    return [
        row
        for row in rows
        if row["start"] < start.timestamp() and row.get("sum") is not None
    ]
