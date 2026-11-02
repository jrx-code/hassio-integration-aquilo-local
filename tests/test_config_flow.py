"""The config flow: one step, the gateway address."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquilo_local.api import AquiloApiError
from custom_components.aquilo_local.const import DOMAIN

from .conftest import HOST

LOCAL_GET = "custom_components.aquilo_local.api.AquiloLocalClient.async_get_sensors"
SETUP = "custom_components.aquilo_local.async_setup_entry"
ONE_TANK = {"000001": {"id": "000001", "pct": 34}}


async def _form(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


@pytest.mark.parametrize(
    "typed", [HOST, f"http://{HOST}", f"https://{HOST}/", f"  {HOST}/  "]
)
async def test_creates_entry_and_cleans_host(hass: HomeAssistant, typed: str):
    result = await _form(hass)
    with (
        patch(LOCAL_GET, return_value=ONE_TANK),
        patch(SETUP, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: typed}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Aquilo Local ({HOST})"
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == HOST


async def test_cannot_connect(hass: HomeAssistant):
    result = await _form(hass)
    with patch(LOCAL_GET, side_effect=AquiloApiError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_no_sensors_found(hass: HomeAssistant):
    result = await _form(hass)
    with patch(LOCAL_GET, return_value={}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_sensors_found"}


async def test_already_configured(hass: HomeAssistant):
    MockConfigEntry(
        domain=DOMAIN, unique_id=HOST, data={CONF_HOST: HOST}
    ).add_to_hass(hass)
    result = await _form(hass)
    with patch(LOCAL_GET, return_value=ONE_TANK):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: f"http://{HOST}/"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
