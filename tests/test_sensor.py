"""_parse_ts and the two binary sensors, through a real config entry setup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquilo_local.const import (
    CONF_OVERFLOW_PCT,
    CONF_STALE_HOURS,
    DOMAIN,
)
from custom_components.aquilo_local.sensor import _parse_ts

from .conftest import HOST


def test_parse_ts_with_offset():
    assert _parse_ts("2026-01-01T12:00:00+01:00") == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)


def test_parse_ts_utc():
    assert _parse_ts("2026-01-01T12:00:00Z") == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, "", 0])
def test_parse_ts_empty(value: Any):
    assert _parse_ts(value) is None


def test_parse_ts_garbage():
    assert _parse_ts("not a date") is None


def _iso(delta: timedelta) -> str:
    return (dt_util.utcnow() - delta).isoformat()


async def _setup(hass: HomeAssistant, sensors: list[dict[str, Any]], options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        title=f"Aquilo ({HOST})",
        data={CONF_HOST: HOST},
        options=options or {},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.aquilo_local.api.AquiloLocalClient.async_get_sensors",
        return_value={s["id"]: s for s in sensors},
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant, tank_id: str, suffix: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{HOST}_{tank_id}_{suffix}"
    )
    assert entity_id, f"no binary_sensor for {tank_id}_{suffix}"
    return hass.states.get(entity_id).state


async def test_stale_data(hass: HomeAssistant):
    await _setup(
        hass,
        [
            {"id": "fresh", "pct": 10, "lstRead": _iso(timedelta(hours=1))},
            {"id": "stale", "pct": 10, "lstRead": _iso(timedelta(hours=30))},
            {"id": "never", "pct": 10},
            {"id": "broken", "pct": 10, "lstRead": "yesterday"},
        ],
    )
    assert _state(hass, "fresh", "stale") == STATE_OFF
    assert _state(hass, "stale", "stale") == STATE_ON
    assert _state(hass, "never", "stale") == STATE_UNKNOWN
    assert _state(hass, "broken", "stale") == STATE_UNKNOWN


async def test_stale_threshold_from_options(hass: HomeAssistant):
    await _setup(
        hass,
        [{"id": "t", "pct": 10, "lstRead": _iso(timedelta(hours=3))}],
        options={CONF_STALE_HOURS: 2},
    )
    assert _state(hass, "t", "stale") == STATE_ON


async def test_overflow_risk(hass: HomeAssistant):
    await _setup(
        hass,
        [
            {"id": "low", "pct": 34},
            {"id": "edge", "pct": 90},
            {"id": "high", "pct": 95},
            {"id": "nopct", "lvl": 12.0},
        ],
    )
    assert _state(hass, "low", "overflow_risk") == STATE_OFF
    assert _state(hass, "edge", "overflow_risk") == STATE_ON  # >= default 90
    assert _state(hass, "high", "overflow_risk") == STATE_ON
    assert _state(hass, "nopct", "overflow_risk") == STATE_UNKNOWN


async def test_overflow_threshold_from_options(hass: HomeAssistant):
    await _setup(hass, [{"id": "t", "pct": 60}], options={CONF_OVERFLOW_PCT: 50})
    assert _state(hass, "t", "overflow_risk") == STATE_ON


def _sensor_state(hass: HomeAssistant, tank_id: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{HOST}_{tank_id}_{key}")
    assert entity_id, f"no sensor for {tank_id}_{key}"
    return hass.states.get(entity_id).state


async def test_impossible_reading_keeps_the_last_good_level_across_a_restart(hass, hass_storage):
    hass_storage[f"{DOMAIN}.last_good_{HOST}"] = {
        "version": 1,
        "key": f"{DOMAIN}.last_good_{HOST}",
        "data": {"t": {"lvl": 160.9, "pct": 17, "lvlToFull": 141, "lstRead": "2026-11-01T21:00:08+01:00"}},
    }
    await _setup(hass, [{"id": "t", "lvl": 0.0, "pct": 100, "lvlToFull": -20, "bat": 37,
                         "lstRead": "2026-11-02T04:45:28+01:00"}])
    assert _sensor_state(hass, "t", "pct") == "17"
    assert _sensor_state(hass, "t", "lvl") == "160.9"


async def test_impossible_reading_without_history_is_unknown_not_full(hass, hass_storage):
    await _setup(hass, [{"id": "t", "lvl": 0.0, "pct": 100, "bat": 37}])
    assert _sensor_state(hass, "t", "pct") == STATE_UNKNOWN
