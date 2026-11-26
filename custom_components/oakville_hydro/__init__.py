"""The Oakville Hydro integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .coordinator import OakvilleHydroConfigEntry, OakvilleHydroCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: OakvilleHydroConfigEntry) -> None:
    """Start pulling usage for a config entry."""
    coordinator = OakvilleHydroCoordinator(hass, entry)
    entry.runtime_data = coordinator
    # The first import can take minutes, so it runs after setup has returned.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), "oakville_hydro first import"
    )


async def async_unload_entry(hass: HomeAssistant, entry: OakvilleHydroConfigEntry) -> None:
    """Stop pulling usage for a config entry."""
    await entry.runtime_data.async_shutdown()
