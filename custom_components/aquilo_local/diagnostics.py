"""Diagnostics support for Aquilo.

Exposes the "Download diagnostics" button on the integration and on each tank
device: the config entry, coordinator state and the raw payload keyed by sensor
id. The entry holds only the gateway address.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import AquiloCoordinator


def _entry_info(entry: ConfigEntry) -> dict[str, Any]:
    """Summarise the config entry.

    Args:
        entry: The config entry.

    Returns:
        The entry's title, unique id, data and options.
    """
    return {
        "title": entry.title,
        "unique_id": entry.unique_id,
        "data": dict(entry.data),
        "options": dict(entry.options),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: AquiloCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": _entry_info(entry),
        "host": coordinator.client.host,
        "configuration_url": coordinator.client.configuration_url,
        "last_update_success": coordinator.last_update_success,
        "tank_count": len(coordinator.data),
        "tank_ids": sorted(coordinator.data),
        "sensors": coordinator.data,
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    coordinator: AquiloCoordinator = hass.data[DOMAIN][entry.entry_id]
    host = coordinator.client.host
    tank_ids = [
        tank_id
        for tank_id in coordinator.data
        if (DOMAIN, f"{host}_{tank_id}") in device.identifiers
    ]
    return {
        "entry": _entry_info(entry),
        "host": host,
        "device": {
            "name": device.name,
            "identifiers": sorted(f"{d}:{i}" for d, i in device.identifiers),
            "serial_number": device.serial_number,
        },
        "tank_ids": tank_ids,
        "sensors": {tank_id: coordinator.data.get(tank_id) for tank_id in tank_ids},
    }
