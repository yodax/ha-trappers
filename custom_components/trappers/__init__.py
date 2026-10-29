"""The Trappers integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TrappersApiClient
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN, SERVICE_REFRESH
from .coordinator import TrappersCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the `trappers.refresh` action (once, not per entry)."""

    async def _refresh(call: ServiceCall) -> None:
        # async_refresh, not async_request_refresh: the latter is debounced
        # (10 s cooldown, trailing edge), and a caller that follows the action
        # with a state check needs the poll finished when the call returns.
        for coordinator in list(hass.data.get(DOMAIN, {}).values()):
            await coordinator.async_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Trappers from a config entry (one per account)."""
    session = async_get_clientsession(hass)
    client = TrappersApiClient(
        session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
    )
    coordinator = TrappersCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
