"""Tests for the sensor platform: entity set, states, and the i18n wiring."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import UNDEFINED
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.trappers.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from custom_components.trappers.sensor import SENSOR_DESCRIPTIONS

EMAIL = "user@example.com"
COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "trappers"

SAMPLE_DATA = {
    "balance": 7574.0,
    "balance_value_eur": 75.74,
    "cycling_days_total": 131,
    "cycling_days_this_month": 1,
    "last_cycling_day": date(2026, 9, 1),
    "points_earned_this_month": 154.0,
    "commute_distance": 11.0,
}


async def _setup_entry(hass: HomeAssistant, data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.trappers.api.TrappersApiClient.async_get_data",
        new=AsyncMock(return_value=data),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_all_sensors_are_created_with_their_values(hass: HomeAssistant) -> None:
    await _setup_entry(hass, SAMPLE_DATA)

    states = {
        state.entity_id: state.state
        for state in hass.states.async_all("sensor")
    }
    assert states == {
        "sensor.user_example_com_points_balance": "7574.0",
        "sensor.user_example_com_points_balance_value": "75.74",
        "sensor.user_example_com_cycling_days_total": "131",
        "sensor.user_example_com_cycling_days_this_month": "1",
        "sensor.user_example_com_last_cycling_day": "2026-09-01",
        "sensor.user_example_com_points_earned_this_month": "154.0",
        "sensor.user_example_com_commute_distance": "11.0",
    }


async def test_unknown_values_read_as_unknown_not_zero(hass: HomeAssistant) -> None:
    """An account with no orders has a genuinely unknown euro value."""
    await _setup_entry(
        hass, {**SAMPLE_DATA, "balance_value_eur": None, "commute_distance": None}
    )

    assert hass.states.get("sensor.user_example_com_points_balance_value").state == "unknown"
    assert hass.states.get("sensor.user_example_com_commute_distance").state == "unknown"


async def test_unload_makes_the_entities_unavailable(hass: HomeAssistant) -> None:
    """HA keeps unloaded entities registered but marks them unavailable."""
    entry = await _setup_entry(hass, SAMPLE_DATA)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    states = hass.states.async_all("sensor")
    assert states
    assert {state.state for state in states} == {"unavailable"}


def test_every_sensor_has_a_translated_name_in_every_language() -> None:
    """Entity names come from translation files, never a hardcoded `name=`."""
    keys = {description.translation_key for description in SENSOR_DESCRIPTIONS}
    assert keys == {description.key for description in SENSOR_DESCRIPTIONS}
    # EntityDescription.name defaults to UNDEFINED; anything else would be a
    # hardcoded English name overriding the translation.
    assert all(description.name is UNDEFINED for description in SENSOR_DESCRIPTIONS)

    for filename in ("strings.json", "translations/en.json", "translations/nl.json"):
        payload = json.loads((COMPONENT_DIR / filename).read_text(encoding="utf-8"))
        translated = payload["entity"]["sensor"]
        assert set(translated) == keys, filename
        assert all(entry["name"] for entry in translated.values()), filename


def test_config_flow_strings_cover_every_step_and_error() -> None:
    expected_steps = {"user", "reauth_confirm"}
    expected_errors = {"invalid_auth", "unknown"}
    expected_aborts = {"already_configured", "reauth_successful"}

    for filename in ("strings.json", "translations/en.json", "translations/nl.json"):
        config = json.loads((COMPONENT_DIR / filename).read_text(encoding="utf-8"))["config"]
        assert set(config["step"]) == expected_steps, filename
        assert set(config["error"]) == expected_errors, filename
        assert set(config["abort"]) == expected_aborts, filename
