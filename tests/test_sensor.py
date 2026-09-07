"""Tests for the sensor platform: entity set, states, and the i18n wiring."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import UNDEFINED
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.trappers.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from custom_components.trappers.sensor import SENSOR_DESCRIPTIONS

EMAIL = "user@example.com"
ACCOUNT_NAME = "Alex"
COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "trappers"

SAMPLE_DATA = {
    "balance": 10000.0,
    "balance_value_eur": 95.24,
    "cycling_days_total": 131,
    "cycling_days_this_month": 1,
    "last_cycling_day": date(2026, 9, 1),
    "points_earned_this_month": 154.0,
    "commute_distance": 11.0,
}


async def _setup_entry(hass: HomeAssistant, data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=ACCOUNT_NAME,
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
        "sensor.alex_points_balance": "10000.0",
        "sensor.alex_points_balance_value": "95.24",
        "sensor.alex_cycling_days_total": "131",
        "sensor.alex_cycling_days_this_month": "1",
        "sensor.alex_last_cycling_day": "2026-09-01",
        "sensor.alex_points_earned_this_month": "154.0",
        "sensor.alex_commute_distance": "11.0",
    }


async def test_unknown_values_read_as_unknown_not_zero(hass: HomeAssistant) -> None:
    """An account with no orders has a genuinely unknown euro value."""
    await _setup_entry(
        hass, {**SAMPLE_DATA, "balance_value_eur": None, "commute_distance": None}
    )

    assert hass.states.get("sensor.alex_points_balance_value").state == "unknown"
    assert hass.states.get("sensor.alex_commute_distance").state == "unknown"


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


async def test_nothing_user_visible_carries_the_account_email(hass: HomeAssistant) -> None:
    """entity_ids, friendly_names and the device must all be free of the address.

    Entity IDs are fixed at creation and travel into dashboard YAML, forum
    posts, issues on this repo and screenshots. The email lives on the config
    entry's data and unique_id, which are never rendered; nothing that *is*
    rendered may repeat it. This pins the whole surface, not just the title —
    a DeviceInfo field quietly set to the address later would fail here.
    """
    entry = await _setup_entry(hass, SAMPLE_DATA)

    for state in hass.states.async_all("sensor"):
        assert EMAIL not in state.entity_id
        assert "@" not in state.entity_id
        for value in [state.entity_id, *map(str, state.attributes.values())]:
            assert EMAIL not in value
            assert "example.com" not in value

    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert devices
    for device in devices:
        rendered = [
            device.name,
            device.model,
            device.manufacturer,
            device.sw_version,
            device.configuration_url,
        ]
        for value in rendered:
            assert value is None or EMAIL not in str(value)


async def test_monthly_sensors_declare_an_explicit_reset(hass: HomeAssistant) -> None:
    """TOTAL + last_reset, not TOTAL_INCREASING.

    TOTAL_INCREASING *infers* a reset from a decrease it observes, and this
    integration deliberately does not poll between 20:00 and 08:00. A month
    ending on one cycling day followed by a first day that already has one is
    the sequence 1 -> 1: no decrease, so no reset recorded, and the new month's
    first day is swallowed from long-term statistics.
    """
    from homeassistant.components.sensor import SensorStateClass
    from homeassistant.util import dt as dt_util

    await _setup_entry(hass, SAMPLE_DATA)

    month_start = dt_util.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    for entity_id in (
        "sensor.alex_cycling_days_this_month",
        "sensor.alex_points_earned_this_month",
    ):
        state = hass.states.get(entity_id)
        assert state.attributes["state_class"] == SensorStateClass.TOTAL
        assert state.attributes["last_reset"] == month_start.isoformat()


async def test_non_monthly_sensors_have_no_last_reset(hass: HomeAssistant) -> None:
    """last_reset on a lifetime or gauge sensor would be meaningless."""
    await _setup_entry(hass, SAMPLE_DATA)

    for entity_id in (
        "sensor.alex_points_balance",
        "sensor.alex_cycling_days_total",
        "sensor.alex_commute_distance",
    ):
        assert "last_reset" not in hass.states.get(entity_id).attributes
