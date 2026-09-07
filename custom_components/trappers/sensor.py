"""Sensor platform for the Trappers integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MONTHLY_SENSOR_KEYS, UNIT_TRAPPERS
from .coordinator import TrappersCoordinator

# Keys line up with the dict api.py's async_get_data() returns.
#
# Two of these are legitimately `unknown` on some accounts rather than broken:
# `commute_distance` when no commute registration is in force today, and
# `balance_value_eur` when the catalogue yields no single consistent rate (see
# api.py — the rate is never assumed).
SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="balance",
        translation_key="balance",
        native_unit_of_measurement=UNIT_TRAPPERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wallet-bifold",
    ),
    SensorEntityDescription(
        key="balance_value_eur",
        translation_key="balance_value_eur",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:cash",
    ),
    SensorEntityDescription(
        key="cycling_days_total",
        translation_key="cycling_days_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:bike",
    ),
    SensorEntityDescription(
        key="cycling_days_this_month",
        translation_key="cycling_days_this_month",
        # TOTAL with an explicit last_reset, not TOTAL_INCREASING. See
        # TrappersSensor.last_reset — TOTAL_INCREASING *infers* a reset from a
        # decrease it observes, and this integration does not poll overnight,
        # so the decrease at midnight on the 1st is never seen.
        state_class=SensorStateClass.TOTAL,
        icon="mdi:calendar-month",
    ),
    SensorEntityDescription(
        key="last_cycling_day",
        translation_key="last_cycling_day",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-check",
    ),
    SensorEntityDescription(
        key="points_earned_this_month",
        translation_key="points_earned_this_month",
        native_unit_of_measurement=UNIT_TRAPPERS,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:trending-up",
    ),
    SensorEntityDescription(
        key="commute_distance",
        translation_key="commute_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:map-marker-distance",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Trappers sensors for one account (config entry)."""
    coordinator: TrappersCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TrappersSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class TrappersSensor(CoordinatorEntity[TrappersCoordinator], SensorEntity):
    """A single balance/activity value for one Trappers account."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TrappersCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Trappers",
            configuration_url="https://trappersshop.fiscfree.nl",
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def last_reset(self):
        """Start of the month **these figures were computed for**.

        Two separate points here.

        Why `TOTAL` + `last_reset` rather than `TOTAL_INCREASING`:
        TOTAL_INCREASING *infers* a reset from a decrease it actually observes,
        and this integration deliberately does not poll between 20:00 and
        08:00. A month whose last day ends on one cycling day, followed by a
        first day that already has one, is the sequence 1 -> 1 — no decrease,
        so no reset is recorded and the new month's first day is swallowed.
        Declaring the reset states it instead of hoping to witness it.

        Why it comes from the coordinator's data and not from `dt_util.now()`:
        the timestamp has to describe the *snapshot*, not the moment someone
        reads the property. A poll starting at 23:59:59 on the 31st computes
        that month's counts and may finish a second into the next month —
        reading the clock here would publish the new month as those figures'
        reset point and file them under the wrong cycle. Reading the clock also
        means the value can change without the state changing, which is not
        something a reset timestamp should do.
        """
        if self.entity_description.key not in MONTHLY_SENSOR_KEYS:
            return None
        data = self.coordinator.data
        month_start = data.get("month_start") if data else None
        if month_start is None:
            return None
        return dt_util.start_of_local_day(month_start)
