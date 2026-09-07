"""Tests for TrappersCoordinator's exception-to-HA-behaviour mapping."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Importing config_flow registers TrappersConfigFlow in HA's HANDLERS registry —
# required for async_start_reauth_if_available() to actually do anything (it
# silently no-ops for domains with no known config flow).
from custom_components.trappers import config_flow  # noqa: F401
from custom_components.trappers.api import (
    TrappersApiClient,
    TrappersApiError,
    TrappersAuthError,
)
from custom_components.trappers.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from custom_components.trappers.coordinator import TrappersCoordinator

EMAIL = "user@example.com"


def _setup_coordinator(
    hass: HomeAssistant,
    *,
    side_effect: Exception | None = None,
    result: dict | None = None,
) -> tuple[TrappersCoordinator, MockConfigEntry, AsyncMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)

    client = AsyncMock(spec=TrappersApiClient)
    if side_effect is not None:
        client.async_get_data.side_effect = side_effect
    else:
        client.async_get_data.return_value = result

    return TrappersCoordinator(hass, entry, client), entry, client


def _active_reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("source") == "reauth"
    ]


async def test_successful_update_stores_data(hass: HomeAssistant) -> None:
    data = {"balance": 10000.0}
    coordinator, _entry, _client = _setup_coordinator(hass, result=data)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data == data
    assert not _active_reauth_flows(hass)


async def test_month_boundary_follows_home_assistant_time(hass: HomeAssistant) -> None:
    """The 'this month' sensors roll over on the user's clock, not the container's."""
    coordinator, _entry, client = _setup_coordinator(hass, result={"balance": 1.0})

    await coordinator.async_refresh()

    passed_today = client.async_get_data.call_args.kwargs["today"]
    passed_now = client.async_get_data.call_args.kwargs["now"]
    assert isinstance(passed_today, date)
    # dt_util.now() is timezone-aware in HA's configured zone.
    assert passed_now.tzinfo is not None
    assert passed_now.date() == passed_today


async def test_auth_error_marks_failed_and_starts_reauth(hass: HomeAssistant) -> None:
    """A rejected password must open HA's reauth flow, not just fail the poll."""
    coordinator, entry, _client = _setup_coordinator(
        hass, side_effect=TrappersAuthError("Invalid credentials")
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    reauth_flows = _active_reauth_flows(hass)
    assert len(reauth_flows) == 1
    assert reauth_flows[0]["context"]["entry_id"] == entry.entry_id


async def test_api_error_marks_failed_without_reauth(hass: HomeAssistant) -> None:
    """A renamed field isn't a credentials problem — reauth can't fix it."""
    coordinator, _entry, _client = _setup_coordinator(
        hass, side_effect=TrappersApiError("Unexpected events response: no 'items' list")
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert not _active_reauth_flows(hass)


def _response_error(status: int) -> aiohttp.ClientResponseError:
    """A ClientResponseError that can actually be formatted.

    Constructing one with `request_info=None` raises AttributeError on
    `real_url` the moment anything renders it — so the previous fixture was
    exercising HA's catch-all handler, not this integration's aiohttp branch,
    and passed for the wrong reason.
    """
    request_info = aiohttp.RequestInfo(
        url=URL("https://api.trappers.net/api/status"),
        method="GET",
        headers=CIMultiDictProxy(CIMultiDict()),
        real_url=URL("https://api.trappers.net/api/status"),
    )
    return aiohttp.ClientResponseError(request_info=request_info, history=(), status=status)


def test_the_response_error_fixture_is_renderable() -> None:
    """Guards the trap above: a fixture that explodes when formatted is not a fixture."""
    assert "api.trappers.net" in str(_response_error(500))


@pytest.mark.parametrize(
    "exc",
    [
        aiohttp.ClientError("boom"),
        _response_error(500),
        TimeoutError("timed out"),
    ],
)
async def test_transport_error_marks_failed_without_reauth(
    hass: HomeAssistant, exc: Exception
) -> None:
    coordinator, _entry, _client = _setup_coordinator(hass, side_effect=exc)

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert not _active_reauth_flows(hass)
