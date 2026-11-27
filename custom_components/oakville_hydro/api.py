"""Client for the Oakville Hydro MyAccount portal API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from typing import Any

import aiohttp

from .model import BillingPeriod, HourlyRead, parse_usage, parse_water_usage

IAM_URL = "https://iamapi-dot-oakvillehydro-myaccount-prod.appspot.com/iamapi"
ACCOUNT_URL = (
    "https://myaccountapi-dot-oakvillehydro-myaccount-prod.appspot.com/api/v1"
)
USAGE_URL = "https://usageapi-dot-oakvillehydro-myaccount-prod.appspot.com/energy"
PORTAL_TIMEZONE = "America/New_York"
TIMEOUT = aiohttp.ClientTimeout(total=60)


class OakvilleHydroError(Exception):
    """The portal returned something this client cannot use."""


class CannotConnect(OakvilleHydroError):
    """The portal could not be reached, or answered with a server error."""


class InvalidAuth(OakvilleHydroError):
    """The portal rejected the credentials or the session token."""


class MfaRequired(OakvilleHydroError):
    """The account asks for a one-time code, which this client does not send."""


@dataclass(frozen=True)
class ElectricityService:
    """One electricity meter on a billing account."""

    account_id: str
    service_id: str
    meter: str
    start: date | None


class OakvilleHydroClient:
    """Signs in with the portal's email and password and reads hourly usage."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        """Keep the session and credentials; nothing is sent until async_login."""
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None

    async def async_login(self) -> None:
        """Exchange the credentials for a session token."""
        try:
            async with self._session.post(
                f"{IAM_URL}/login",
                data={
                    "username": self._username,
                    "password": self._password,
                    "client_id": "iam",
                },
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
            ) as response:
                status = response.status
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"login request failed: {err}") from err
        if status >= 500:
            raise CannotConnect(f"login returned HTTP {status}")
        body = _json_object(text)
        reason = str(body.get("statusText") or "")
        # A wrong email or password comes back as HTTP 404 with a "login." reason.
        if status in (401, 403) or (status >= 400 and reason.startswith("login.")):
            raise InvalidAuth(reason or f"login returned HTTP {status}")
        if status == 429:
            raise CannotConnect("login returned HTTP 429")
        if status >= 400:
            raise OakvilleHydroError(f"login returned HTTP {status} {reason}".strip())
        if body.get("mfa"):
            raise MfaRequired("the account requires a one-time code")
        token = body.get("access_token")
        if not token:
            raise OakvilleHydroError("login response has no access_token")
        self._token = token

    async def async_get_services(self) -> list[ElectricityService]:
        """List the electricity meters the signed-in user can see."""
        whoami = await self._get(f"{IAM_URL}/management/whoami")
        customer_id = whoami["customerId"]
        accounts = await self._get(
            f"{ACCOUNT_URL}/customers/{customer_id}/accounts", {"searchParam": ""}
        )
        services: list[ElectricityService] = []
        for account in accounts or []:
            billing = await self._async_billing(str(account["id"]))
            for service in billing.get("electricityServices") or []:
                if not service.get("meter"):
                    continue
                eff = service.get("effDate")
                services.append(
                    ElectricityService(
                        account_id=str(account["id"]),
                        service_id=str(service["id"]),
                        meter=str(service["meter"]),
                        start=(
                            datetime.fromtimestamp(eff / 1000, timezone.utc).date()
                            if eff
                            else None
                        ),
                    )
                )
        return services

    async def async_get_hourly(
        self, account_id: str, service_id: str, meter: str, start: date, end: date
    ) -> list[HourlyRead]:
        """Read hourly usage for the local days start to end, both included."""
        rows = await self._get(
            f"{USAGE_URL}/account/{account_id}/service/{service_id}/usage",
            {
                "start-date": start.isoformat(),
                "end-date": end.isoformat(),
                "usage-view": "HOURLY",
                "meter": meter,
                "bestEffort": "Y",
                "tz": PORTAL_TIMEZONE,
            },
        )
        try:
            return parse_usage(rows or [])
        except (KeyError, TypeError, ValueError) as err:
            raise OakvilleHydroError(f"unexpected usage row: {err}") from err

    async def async_get_water_service_id(self, account_id: str) -> str | None:
        """Return the billing account's water service, or None when it has none."""
        billing = await self._async_billing(account_id)
        for service in billing.get("waterServices") or []:
            if isinstance(service, dict) and service.get("id"):
                return str(service["id"])
        return None

    async def async_get_water_periods(
        self, account_id: str, service_id: str
    ) -> list[BillingPeriod]:
        """Read the water billing periods the portal shows for the service.

        The portal returns its whole list whatever dates are asked for.
        """
        now_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        params = {
            "accountId": account_id,
            "view": "billing",
            "startDate": "0",
            "endDate": now_ms,
        }
        usage = await self._get(
            f"{ACCOUNT_URL}/services/{service_id}/waterUsage", params
        )
        chart = await self._get(
            f"{ACCOUNT_URL}/services/{service_id}/billingChartData", params
        )
        try:
            return parse_water_usage(usage or {}, chart)
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            raise OakvilleHydroError(f"unexpected water usage: {err}") from err

    async def _async_billing(self, account_id: str) -> dict[str, Any]:
        billing = await self._get(
            f"{ACCOUNT_URL}/dashboard/me/billing", {"billingId": account_id}
        )
        if not isinstance(billing, dict):
            raise OakvilleHydroError("billing answer is not a JSON object")
        return billing

    async def _get(self, url: str, params: dict[str, str] | None = None) -> Any:
        if self._token is None:
            raise InvalidAuth("not signed in")
        try:
            async with self._session.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
                timeout=TIMEOUT,
            ) as response:
                status = response.status
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"request failed: {err}") from err
        if status in (401, 403):
            raise InvalidAuth(f"HTTP {status}")
        if status >= 500:
            raise CannotConnect(f"HTTP {status}")
        if status >= 400:
            raise OakvilleHydroError(f"HTTP {status}: {text[:200]}")
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as err:
            raise OakvilleHydroError(f"response is not JSON: {text[:200]}") from err


def _json_object(text: str) -> dict[str, Any]:
    try:
        body = json.loads(text)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
