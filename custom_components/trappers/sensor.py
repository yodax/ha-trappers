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

from .const import DOMAIN, UNIT_TRAPPERS
from .coordinator import TrappersCoordinator

# Keys line up with the dict api.py's async_get_data() returns.
#
# Two of these are legitimately `unknown` on some accounts rather than broken:
# `commute_distance` when no commute is registered, and `balance_value_eur` on
# an account that has never placed an order (the points→euro ratio only exists
# on order records — see api.py, it is deliberately not defaulted to a
# constant).
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
        # Resets to zero on the first of the month; TOTAL_INCREASING is the
        # state class that understands that reset rather than reading it as a
        # spike back down.
        state_class=SensorStateClass.TOTAL_INCREASING,
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
        state_class=SensorStateClass.TOTAL_INCREASING,
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
