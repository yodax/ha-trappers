"""Config flow for the Trappers integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TrappersApiClient, TrappersAuthError
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


def _entry_title(first_name: str | None, email: str) -> str:
    """Pick the config entry's display name.

    Home Assistant slugifies the entry title into the device name and from
    there into every entity_id, so whatever goes here ends up in
    ``sensor.<title>_puntensaldo`` and in every friendly_name — and then in any
    dashboard YAML pasted into a forum thread or an issue on this repo, and in
    any screenshot. Titling the entry with the account's email address would
    make redacting that address a chore the user has to remember every single
    time; a first name is what the title is actually for, and it is what
    distinguishes two accounts in the same household.

    `unique_id` stays the full lowercased email — it is the duplicate-account
    guard and it is never rendered anywhere.
    """
    name = (first_name or "").strip()
    if name:
        return name
    # No first name on the account. Fall back to the address's local part
    # rather than the whole address, so at least the domain — which is what
    # identifies an employer — stays out of the entity_ids.
    local_part = email.split("@", 1)[0].strip()
    return local_part or "Trappers"


class TrappersConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Trappers — one entry per account."""

    VERSION = 1

    async def _async_validate_login(
        self, email: str, password: str
    ) -> tuple[dict[str, str], str | None]:
        """Try logging in.

        Returns a config-flow `errors` dict (empty on success) plus the
        account's first name, which is the only field taken out of the login
        response's `userDetails`.
        """
        session = async_get_clientsession(self.hass)
        client = TrappersApiClient(session, email, password)
        try:
            first_name = await client.async_login()
        except TrappersAuthError:
            return {"base": "invalid_auth"}, None
        except Exception:  # noqa: BLE001
            # No exc_info interpolation of the response — see api.py's privacy note.
            _LOGGER.exception("Unexpected error validating Trappers login")
            return {"base": "unknown"}, None
        return {}, first_name

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # Lowercased email as unique_id: the same account cannot be added
            # twice, while a household with two Trappers accounts can add both.
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()

            errors, first_name = await self._async_validate_login(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_create_entry(
                    title=_entry_title(first_name, user_input[CONF_EMAIL]),
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication after a password change."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            errors, _first_name = await self._async_validate_login(
                reauth_entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={**reauth_entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"email": reauth_entry.data[CONF_EMAIL]},
        )
