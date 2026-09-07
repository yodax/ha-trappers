"""DataUpdateCoordinator for the Trappers integration."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TrappersApiClient, TrappersApiError, TrappersAuthError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class TrappersCoordinator(DataUpdateCoordinator[dict]):
    """Polls the Trappers API for one account's balance and cycling activity."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TrappersApiClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        # dt_util.now() is Home Assistant's configured timezone, which is the
        # clock the "this month" sensors should roll over on — not the
        # container's, which may well be UTC.
        now = dt_util.now()
        try:
            return await self.client.async_get_data(today=now.date(), now=now)
        except TrappersAuthError as err:
            # Not UpdateFailed: a rejected password has to start HA's reauth
            # flow, or the sensors sit unavailable forever with no prompt.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except TrappersApiError as err:
            raise UpdateFailed(str(err)) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with Trappers: {err}") from err
