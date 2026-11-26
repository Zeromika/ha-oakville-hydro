"""Config flow for the Oakville Hydro integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    CannotConnect,
    ElectricityService,
    InvalidAuth,
    MfaRequired,
    OakvilleHydroClient,
    OakvilleHydroError,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_METER,
    CONF_SERVICE_ID,
    CONF_SERVICE_START,
    DOMAIN,
)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): str,
        probatio.Required(probatio.Secret(CONF_PASSWORD)): str,
    }
)
REAUTH_SCHEMA = probatio.Schema(
    {probatio.Required(probatio.Secret(CONF_PASSWORD)): str}
)


class OakvilleHydroConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the portal's email and password and pick the electricity meter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in and create the entry for the account's electricity meter."""
        errors: dict[str, str] = {}
        if user_input is not None:
            services, error = await self._async_sign_in(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            elif not services:
                errors["base"] = "no_service"
            else:
                service = services[0]
                await self.async_set_unique_id(service.service_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Oakville Hydro {service.meter}",
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCOUNT_ID: service.account_id,
                        CONF_SERVICE_ID: service.service_id,
                        CONF_METER: service.meter,
                        CONF_SERVICE_START: (
                            service.start.isoformat() if service.start else None
                        ),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA,
                {CONF_USERNAME: user_input[CONF_USERNAME]} if user_input else None,
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Ask for the password again after the portal rejected it."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the new password and store it."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            _, error = await self._async_sign_in(
                entry.data[CONF_USERNAME], user_input[CONF_PASSWORD], list_meters=False
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
        )

    async def _async_sign_in(
        self, username: str, password: str, list_meters: bool = True
    ) -> tuple[list[ElectricityService], str | None]:
        """Return the account's electricity meters, or the form's error key."""
        client = OakvilleHydroClient(
            async_get_clientsession(self.hass), username, password
        )
        try:
            await client.async_login()
            if not list_meters:
                return [], None
            return await client.async_get_services(), None
        except InvalidAuth:
            return [], "invalid_auth"
        except MfaRequired:
            return [], "mfa_required"
        except CannotConnect:
            return [], "cannot_connect"
        except (OakvilleHydroError, AttributeError, KeyError, TypeError):
            return [], "unknown"
