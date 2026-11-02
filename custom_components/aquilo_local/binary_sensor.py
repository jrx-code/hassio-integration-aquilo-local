"""Binary sensors for Aquilo: stale-data watchdog + overflow-risk alert."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_LST_READ,
    ATTR_PCT,
    CONF_EXCLUDED_TANKS,
    CONF_OVERFLOW_PCT,
    CONF_STALE_HOURS,
    DEFAULT_OVERFLOW_PCT,
    DEFAULT_STALE_HOURS,
    DOMAIN,
)
from .coordinator import AquiloCoordinator
from .entity import AquiloEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AquiloCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_tank_ids: set[str] = set()

    def _add_new_tanks() -> None:
        excluded = set(entry.options.get(CONF_EXCLUDED_TANKS, []))
        new_entities: list[BinarySensorEntity] = []
        for tank_id in coordinator.data:
            if tank_id in known_tank_ids or tank_id in excluded:
                continue
            known_tank_ids.add(tank_id)
            new_entities.append(AquiloStaleDataSensor(coordinator, tank_id, entry))
            new_entities.append(AquiloOverflowRiskSensor(coordinator, tank_id, entry))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_tanks()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_tanks))


class AquiloStaleDataSensor(AquiloEntity, BinarySensorEntity):
    """ON when the sensor hasn't phoned home in a while (dead battery, comms loss).

    This is exactly the kind of thing that's invisible until you go looking —
    a rain-tank sensor can go quiet for months and nothing tells you.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Nieaktualne dane"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquiloCoordinator, tank_id: str, entry: ConfigEntry) -> None:
        super().__init__(coordinator, tank_id)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.client.host}_{tank_id}_stale"

    @property
    def is_on(self) -> bool | None:
        raw = self._tank_data.get(ATTR_LST_READ)
        if not raw:
            return None
        last_read: datetime | None = dt_util.parse_datetime(raw)
        if last_read is None:
            return None
        threshold_hours = self._entry.options.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS)
        age = dt_util.utcnow() - dt_util.as_utc(last_read)
        return age.total_seconds() > threshold_hours * 3600

    @property
    def extra_state_attributes(self) -> dict:
        raw = self._tank_data.get(ATTR_LST_READ)
        last_read = dt_util.parse_datetime(raw) if raw else None
        if last_read is None:
            return {}
        age_hours = (dt_util.utcnow() - dt_util.as_utc(last_read)).total_seconds() / 3600
        return {"godzin_od_ostatniego_odczytu": round(age_hours, 1)}


class AquiloOverflowRiskSensor(AquiloEntity, BinarySensorEntity):
    """ON when fill % crosses a configurable threshold (default 90%) —
    e.g. time to call the pump-out truck for a septic tank."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Ryzyko przepełnienia"
    _attr_icon = "mdi:alert-octagon-outline"

    def __init__(self, coordinator: AquiloCoordinator, tank_id: str, entry: ConfigEntry) -> None:
        super().__init__(coordinator, tank_id)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.client.host}_{tank_id}_overflow_risk"

    @property
    def is_on(self) -> bool | None:
        pct = self._tank_data.get(ATTR_PCT)
        if pct is None:
            return None
        threshold = self._entry.options.get(CONF_OVERFLOW_PCT, DEFAULT_OVERFLOW_PCT)
        return pct >= threshold
