"""Diagnostics: what a download contains."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquilo_local.const import DOMAIN
from custom_components.aquilo_local.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)

from .conftest import HOST

LOCAL_GET = "custom_components.aquilo_local.api.AquiloLocalClient.async_get_sensors"
TANKS = {"000001": {"id": "000001", "pct": 34}}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        title=f"Aquilo Local ({HOST})",
        data={CONF_HOST: HOST},
    )
    entry.add_to_hass(hass)
    with patch(LOCAL_GET, return_value=TANKS):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_entry_diagnostics_report_the_gateway(hass: HomeAssistant):
    entry = await _setup(hass)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["host"] == HOST
    assert result["tank_ids"] == ["000001"]
    assert result["last_update_success"] is True
    assert result["sensors"]["000001"]["pct"] == 34


async def test_device_diagnostics_name_the_tank(hass: HomeAssistant):
    entry = await _setup(hass)
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert (DOMAIN, f"{HOST}_000001") in device.identifiers
    result = await async_get_device_diagnostics(hass, entry, device)
    assert result["tank_ids"] == ["000001"]
    assert result["sensors"]["000001"]["pct"] == 34
