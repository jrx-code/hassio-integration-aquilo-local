"""The sensor platform: which entities a tank gets, and the diagnostic ones.

Not every tank type reports every field, so the interesting behaviour here is
what is *not* created, and which entities stay available when the readings do
not.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquilo_local.const import DOMAIN

from .conftest import HOST, raw_block

LOCAL_GET = "custom_components.aquilo_local.api.AquiloLocalClient.async_get_sensors"


async def _setup(
    hass: HomeAssistant,
    sensors: list[dict[str, Any]],
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        title=f"Aquilo Local ({HOST})",
        data={CONF_HOST: HOST},
    )
    entry.add_to_hass(hass)
    with patch(LOCAL_GET, return_value={s["id"]: s for s in sensors}):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant, tank_id: str, suffix: str) -> State | None:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{HOST}_{tank_id}_{suffix}"
    )
    return hass.states.get(entity_id) if entity_id else None


def _exists(hass: HomeAssistant, tank_id: str, suffix: str) -> bool:
    return (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{HOST}_{tank_id}_{suffix}"
        )
        is not None
    )


async def test_tank_gets_the_core_readings(hass: HomeAssistant):
    await _setup(hass, [{"id": "t", "lvl": 125.6, "pct": 34, "bat": 76}])
    assert _state(hass, "t", "lvl").state == "125.6"
    assert _state(hass, "t", "pct").state == "34"
    assert _state(hass, "t", "bat").state == "76"


async def test_fields_a_tank_does_not_report_get_no_entity(hass: HomeAssistant):
    """The g1 sewage sensor sends no amplitude, so it must not grow the entity."""
    await _setup(hass, [{"id": "g1", "lvl": 125.6, "pct": 34}])
    for absent in ("amp1", "daysLeft", "lvlToFull", "lstEmpty"):
        assert not _exists(hass, "g1", absent), absent


async def test_echo_quality_entity_only_where_the_hardware_reports_it(
    hass: HomeAssistant,
):
    await _setup(
        hass,
        [
            {"id": "e19", "pct": 34, "amp1": 760},
            {"id": "g1", "pct": 34},
        ],
    )
    assert _state(hass, "e19", "amp1").state == "760"
    assert not _exists(hass, "g1", "amp1")


async def test_last_reading_carries_the_raw_string_and_every_field(hass: HomeAssistant):
    """The raw block is attributes of "Ostatni odczyt", whose state is the time."""
    await _setup(
        hass,
        [{"id": "t", "pct": 34, "lstRead": "2026-01-01T12:00:00+01:00", "raw": raw_block()}],
    )
    state = _state(hass, "t", "lstRead")
    assert state.state == "2026-01-01T11:00:00+00:00"
    assert state.attributes["raw"] == "_e19_aaaa01_601_3556_760_6_1161_1160_1_6"
    assert state.attributes["read1"] == 3556
    assert state.attributes["amp1"] == 760
    assert state.attributes["rssi"] == -98
    assert state.attributes["rev"] == "e19"


async def test_a_raw_string_too_long_for_a_state_still_arrives_whole(hass: HomeAssistant):
    """An attribute has no 255-character limit, so nothing is truncated."""
    await _setup(hass, [{"id": "t", "pct": 34, "raw": raw_block(raw="x" * 400)}])
    assert _state(hass, "t", "lstRead").attributes["raw"] == "x" * 400


async def test_the_old_raw_entity_is_gone(hass: HomeAssistant):
    await _setup(hass, [{"id": "t", "pct": 34, "raw": raw_block()}])
    assert not _exists(hass, "t", "raw")


async def test_a_rejected_reading_is_visible(hass: HomeAssistant):
    rejected = {"read1": 39300, "limit": 22500, "amp1": 1375, "at": "2026-01-01T12:00:00Z"}
    await _setup(hass, [{"id": "t", "pct": 34, "raw": raw_block(), "rejected": rejected}])
    assert _state(hass, "t", "lstRead").attributes["rejected"] == rejected


async def test_identity_entities_stay_available_without_a_reading(
    hass: HomeAssistant,
):
    """Id, gateway address and the last reading are exactly what one wants to
    see when the tank's own readings have gone."""
    await _setup(hass, [{"id": "t", "raw": raw_block()}])
    assert _state(hass, "t", "id").state == "t"
    assert _state(hass, "t", "host").state == HOST
    last_read = _state(hass, "t", "lstRead")
    assert last_read.state != "unavailable"
    assert last_read.attributes["read1"] == 3556


async def test_host_entity_names_the_gateway(hass: HomeAssistant):
    await _setup(hass, [{"id": "t", "pct": 34}])
    attrs = _state(hass, "t", "host").attributes
    assert attrs["configuration_url"] == f"http://{HOST}/state"


async def test_excluded_tank_gets_no_entities(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        data={CONF_HOST: HOST},
        options={"excluded_tanks": ["skip"]},
    )
    entry.add_to_hass(hass)
    with patch(
        LOCAL_GET,
        return_value={"keep": {"id": "keep", "pct": 34}, "skip": {"id": "skip", "pct": 50}},
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert _exists(hass, "keep", "pct")
    assert not _exists(hass, "skip", "pct")
