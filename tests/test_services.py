"""The `trappers.refresh` action goes through the real service registry."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.trappers.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    SERVICE_REFRESH,
)


async def test_refresh_action_polls_every_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id="user@example.com",
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.trappers.api.TrappersApiClient.async_get_data",
        AsyncMock(return_value={}),
    ) as get_data, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)
        calls = get_data.await_count  # first refresh at setup
        await hass.services.async_call(DOMAIN, SERVICE_REFRESH, blocking=True)
        assert get_data.await_count == calls + 1
