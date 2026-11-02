"""Aquilo Local: a liquid-level gateway read over the LAN only."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from .api import AquiloLocalClient
from .const import CONF_EXCLUDED_TANKS, DOMAIN
from .coordinator import AquiloCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = AquiloLocalClient(async_get_clientsession(hass), entry.data[CONF_HOST])
    coordinator = AquiloCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    _async_purge_excluded_tanks(hass, entry, client.host)
    _async_purge_raw_entities(hass, entry, client.host)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_purge_excluded_tanks(hass: HomeAssistant, entry: ConfigEntry, host: str) -> None:
    """Remove the device (and its entities) for any tank the user excluded.

    Excluding a tank in options only stops new entities from being created —
    a device that already exists from before the exclusion would otherwise
    stick around as a permanently-unavailable leftover.
    """
    excluded = entry.options.get(CONF_EXCLUDED_TANKS, [])
    if not excluded:
        return
    device_registry = dr.async_get(hass)
    for tank_id in excluded:
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, f"{host}_{tank_id}")}
        )
        if device is not None:
            device_registry.async_remove_device(device.id)


def _async_purge_raw_entities(hass: HomeAssistant, entry: ConfigEntry, host: str) -> None:
    """Remove the "Surowy odczyt" entity that 0.4.2 and earlier created.

    Its state was the gateway's raw string, truncated to the 255 characters a
    state may hold and unreadable in the UI. The whole raw block now travels as
    attributes of "Ostatni odczyt", so the old entity has no state left to show
    and would stick around as a permanently-unknown leftover.
    """
    entity_registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if registry_entry.unique_id.startswith(f"{host}_") and (
            registry_entry.unique_id.endswith("_raw")
        ):
            entity_registry.async_remove(registry_entry.entity_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> bool:
    """Allow a tank device to be deleted from the device page.

    Without this, HA keeps the "Delete" button disabled for every device of
    the integration. A tank the gateway no longer reports can just be removed.
    A tank the gateway still reports would be recreated on the next refresh,
    so we also add it to the excluded list (same effect as unchecking it in
    the options flow) and let the update listener reload the entry.
    """
    coordinator: AquiloCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return True

    host = coordinator.client.host
    still_reported = {
        tank_id
        for tank_id in coordinator.data
        if (DOMAIN, f"{host}_{tank_id}") in device.identifiers
    }
    if still_reported:
        excluded = set(entry.options.get(CONF_EXCLUDED_TANKS, []))
        excluded |= still_reported
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_EXCLUDED_TANKS: sorted(excluded)},
        )
    return True
