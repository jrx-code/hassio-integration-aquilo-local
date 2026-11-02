"""Sensor entities for Aquilo."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_AMP1,
    ATTR_BAT,
    ATTR_DAYS_LEFT,
    ATTR_ID,
    ATTR_LST_EMPTY,
    ATTR_LST_READ,
    ATTR_LVL,
    ATTR_LVL_TO_FULL,
    ATTR_PCT,
    ATTR_RAW,
    ATTR_REJECTED,
    CONF_EXCLUDED_TANKS,
    DOMAIN,
)
from .coordinator import AquiloCoordinator
from .entity import AquiloEntity


@dataclass(frozen=True, kw_only=True)
class AquiloSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any] = lambda d: None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    return dt_util.as_utc(parsed) if parsed else None


SENSOR_DESCRIPTIONS: tuple[AquiloSensorDescription, ...] = (
    AquiloSensorDescription(
        key=ATTR_LVL,
        name="Poziom",
        icon="mdi:waves-arrow-up",
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get(ATTR_LVL),
    ),
    AquiloSensorDescription(
        key=ATTR_PCT,
        name="Wypełnienie",
        icon="mdi:cup-water",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get(ATTR_PCT),
    ),
    AquiloSensorDescription(
        key=ATTR_BAT,
        name="Bateria",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get(ATTR_BAT),
    ),
    AquiloSensorDescription(
        key=ATTR_DAYS_LEFT,
        name="Dni do pustego",
        icon="mdi:calendar-alert",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get(ATTR_DAYS_LEFT),
    ),
    AquiloSensorDescription(
        key=ATTR_LVL_TO_FULL,
        name="Poziom do pełna",
        icon="mdi:arrow-collapse-up",
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get(ATTR_LVL_TO_FULL),
    ),
    AquiloSensorDescription(
        key=ATTR_LST_READ,
        name="Ostatni odczyt",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _parse_ts(d.get(ATTR_LST_READ)),
    ),
    AquiloSensorDescription(
        key=ATTR_LST_EMPTY,
        name="Ostatnie opróżnienie",
        icon="mdi:tanker-truck",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _parse_ts(d.get(ATTR_LST_EMPTY)),
    ),
    AquiloSensorDescription(
        key=ATTR_AMP1,
        name="Jakość echa",
        icon="mdi:waveform",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get(ATTR_AMP1),
    ),
)


def _descriptions_for_tank(tank_data: dict[str, Any]) -> list[AquiloSensorDescription]:
    """Descriptions that apply to one tank's payload (skips fields the tank type doesn't report)."""
    result: list[AquiloSensorDescription] = []
    for description in SENSOR_DESCRIPTIONS:
        # daysLeft/lvlToFull/lstEmpty/amp1 aren't reported by every tank type
        if description.value_fn(tank_data) is None and description.key in (
            ATTR_DAYS_LEFT,
            ATTR_LVL_TO_FULL,
            ATTR_LST_EMPTY,
            ATTR_AMP1,
        ):
            continue
        result.append(description)
    return result


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquiloCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_tank_ids: set[str] = set()

    def _add_new_tanks() -> None:
        excluded = set(entry.options.get(CONF_EXCLUDED_TANKS, []))
        new_entities: list[SensorEntity] = []
        for tank_id, tank_data in coordinator.data.items():
            if tank_id in known_tank_ids or tank_id in excluded:
                continue
            known_tank_ids.add(tank_id)
            new_entities.append(AquiloIdSensor(coordinator, tank_id))
            new_entities.append(AquiloHostSensor(coordinator, tank_id))
            if isinstance(tank_data.get(ATTR_RAW), dict):
                new_entities.append(AquiloRawSensor(coordinator, tank_id))
            for description in _descriptions_for_tank(tank_data):
                new_entities.append(AquiloSensor(coordinator, tank_id, description))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_tanks()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_tanks))


class AquiloIdSensor(AquiloEntity, SensorEntity):
    """Diagnostic entity exposing the sensor's Aquilo id (the device identity).

    Always available: the id never changes and is known even while the gateway
    is unreachable, so it must not go unavailable with the rest of the tank.
    """

    _attr_name = "Identyfikator"
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquiloCoordinator, tank_id: str) -> None:
        super().__init__(coordinator, tank_id)
        self._attr_unique_id = f"{coordinator.client.host}_{tank_id}_id"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        # tank_id is the gateway's `id` field; fall back to it when the live
        # payload is momentarily empty.
        return self._tank_data.get(ATTR_ID) or self._tank_id


class AquiloHostSensor(AquiloEntity, SensorEntity):
    """Diagnostic entity exposing where this tank is read from.

    The state is the gateway address the entry polls; the URL behind it is an
    attribute.
    """

    _attr_name = "Adres bramki"
    _attr_icon = "mdi:ip-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquiloCoordinator, tank_id: str) -> None:
        super().__init__(coordinator, tank_id)
        self._attr_unique_id = f"{coordinator.client.host}_{tank_id}_host"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        return self.coordinator.client.host

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "configuration_url": self.coordinator.client.configuration_url,
        }


class AquiloRawSensor(AquiloEntity, SensorEntity):
    """Diagnostic entity carrying the measurement behind the computed values.

    The state is the gateway's own raw string, which names the hardware
    revision, the sensor and the reading in one line. Everything else it sent
    travels as attributes, including a reading the plausibility gate turned
    away, so a level that looks wrong can be traced to its source without
    opening a broker or a log.
    """

    _attr_name = "Surowy odczyt"
    _attr_icon = "mdi:code-braces"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquiloCoordinator, tank_id: str) -> None:
        """Build the unique id from the entry's identity label."""
        super().__init__(coordinator, tank_id)
        self._attr_unique_id = f"{coordinator.client.host}_{tank_id}_raw"

    @property
    def available(self) -> bool:
        """Stay available: a rejected reading is still worth showing."""
        return True

    @property
    def native_value(self) -> str | None:
        """The gateway's raw string for the last reading it reported."""
        raw = self._tank_data.get(ATTR_RAW) or {}
        value = raw.get("raw") if isinstance(raw, dict) else None
        # Home Assistant refuses a state longer than 255 characters.
        return value[:255] if isinstance(value, str) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Every raw field, plus the last reading that was turned away."""
        attrs: dict[str, Any] = {}
        raw = self._tank_data.get(ATTR_RAW)
        if isinstance(raw, dict):
            attrs.update({k: v for k, v in raw.items() if k != "raw"})
        rejected = self._tank_data.get(ATTR_REJECTED)
        if isinstance(rejected, dict):
            attrs["rejected"] = rejected
        return attrs


class AquiloSensor(AquiloEntity, SensorEntity):
    entity_description: AquiloSensorDescription

    def __init__(
        self,
        coordinator: AquiloCoordinator,
        tank_id: str,
        description: AquiloSensorDescription,
    ) -> None:
        super().__init__(coordinator, tank_id)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.client.host}_{tank_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._tank_data)
