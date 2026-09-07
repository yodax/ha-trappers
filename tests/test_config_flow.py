"""Tests for the trappers config flow, including the reauth flow."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.trappers.api import TrappersAuthError
from custom_components.trappers.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

EMAIL = "user@example.com"
PASSWORD = "hunter2"

LOGIN_PATCH_TARGET = "custom_components.trappers.api.TrappersApiClient.async_login"


async def test_user_step_success_creates_entry(hass: HomeAssistant) -> None:
    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value="Alex")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Alex"
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}


async def test_unique_id_is_the_lowercased_email(hass: HomeAssistant) -> None:
    """So the same account can't be added twice under a different capitalisation."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    ).add_to_hass(hass)

    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value=None)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "User@Example.COM", CONF_PASSWORD: PASSWORD}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_second_account_gets_its_own_entry(hass: HomeAssistant) -> None:
    """A household with two Trappers accounts adds both, as separate entries."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    ).add_to_hass(hass)

    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value="Sam")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "other@example.com", CONF_PASSWORD: "other-password"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sam"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_user_step_invalid_auth_shows_error(hass: HomeAssistant) -> None:
    with patch(
        LOGIN_PATCH_TARGET, new=AsyncMock(side_effect=TrappersAuthError("Invalid credentials"))
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_step_unexpected_error_shows_unknown(hass: HomeAssistant) -> None:
    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_flow_success_updates_password_and_reloads(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "old-password"},
    )
    entry.add_to_hass(hass)

    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value=None)):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data[CONF_EMAIL] == EMAIL


async def test_reauth_flow_invalid_auth_shows_error(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "old-password"},
    )
    entry.add_to_hass(hass)

    with patch(
        LOGIN_PATCH_TARGET, new=AsyncMock(side_effect=TrappersAuthError("Invalid credentials"))
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still-wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    # The password isn't updated on a failed reauth attempt.
    assert entry.data[CONF_PASSWORD] == "old-password"


async def test_entry_title_never_contains_the_email_address(hass: HomeAssistant) -> None:
    """The entry title is slugified into every entity_id, so it must not be the email.

    `title=email` puts the account holder's address into
    `sensor.<address>_puntensaldo` and into every friendly_name — which then
    travels into any dashboard YAML pasted into a forum thread or an issue on
    this repo, and into any screenshot. `unique_id` stays the lowercased email
    (it is never rendered); the title is a display name, and it is derived from
    the account's first name instead.
    """
    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value="Alex")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    title = result["title"]
    assert EMAIL not in title
    assert "@" not in title
    assert "example.com" not in title
    # The credentials are still stored on the entry — only the display name changes.
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}


async def test_entry_title_is_the_account_first_name(hass: HomeAssistant) -> None:
    """A household with two accounts gets 'Alex' and 'Sam', not two addresses."""
    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value="Alex")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )

    assert result["title"] == "Alex"


@pytest.mark.parametrize(
    "first_name", [None, "", "   "], ids=["missing", "empty", "whitespace"]
)
async def test_title_falls_back_to_the_local_part_not_the_full_address(
    hass: HomeAssistant, first_name: str | None
) -> None:
    """No usable first name still must not put the domain into entity_ids."""
    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value=first_name)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )

    assert result["title"] == "user"
    assert "@" not in result["title"]


async def test_unique_id_is_still_the_full_lowercased_email(hass: HomeAssistant) -> None:
    """Changing the title must not weaken the duplicate-account guard."""
    with patch(LOGIN_PATCH_TARGET, new=AsyncMock(return_value="Alex")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "User@Example.COM", CONF_PASSWORD: PASSWORD}
        )

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == EMAIL
