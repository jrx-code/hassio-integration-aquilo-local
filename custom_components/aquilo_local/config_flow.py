"""Config flow for Aquilo Local: one step, the gateway address."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AquiloApiError, AquiloLocalClient
from .const import (
    ATTR_NAME,
    CONF_EXCLUDED_TANKS,
    CONF_OVERFLOW_PCT,
    CONF_STALE_HOURS,
    DEFAULT_OVERFLOW_PCT,
    DEFAULT_STALE_HOURS,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


def _clean_host(raw: str) -> str:
    """Strip a scheme, whitespace and a trailing slash off a typed-in host.

    Args:
        raw: What the user typed.

    Returns:
        A bare host or IP.
    """
    return raw.strip().removeprefix("http://").removeprefix("https://").rstrip("/")


class AquiloConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Aquilo Local."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Ask for the gateway address and check that it answers."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _clean_host(user_input[CONF_HOST])
            session = async_get_clientsession(self.hass)
            try:
                sensors = await AquiloLocalClient(session, host).async_get_sensors()
            except AquiloApiError:
                errors["base"] = "cannot_connect"
            else:
                if not sensors:
                    errors["base"] = "no_sensors_found"
                else:
                    await self.async_set_unique_id(host)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Aquilo Local ({host})", data={CONF_HOST: host}
                    )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return AquiloOptionsFlow(config_entry)


class AquiloOptionsFlow(OptionsFlow):
    """Options: stale-data threshold, overflow-risk threshold, excluded sensors."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._data: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_sensors()

        current = self._config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STALE_HOURS,
                    default=current.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=24 * 30)),
                vol.Required(
                    CONF_OVERFLOW_PCT,
                    default=current.get(CONF_OVERFLOW_PCT, DEFAULT_OVERFLOW_PCT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        current_excluded: list[str] = list(
            self._config_entry.options.get(CONF_EXCLUDED_TANKS, [])
        )

        if user_input is not None:
            self._data[CONF_EXCLUDED_TANKS] = user_input.get(CONF_EXCLUDED_TANKS, [])
            return self.async_create_entry(title="", data=self._data)

        coordinator = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        tank_choices: dict[str, str] = {}
        if coordinator is not None:
            for tank_id, tank_data in coordinator.data.items():
                label = tank_data.get(ATTR_NAME) or tank_id
                tank_choices[tank_id] = label.title()
        # keep already-excluded ids selectable even if the source no longer
        # reports them (offline tank, coordinator not yet refreshed)
        for tank_id in current_excluded:
            tank_choices.setdefault(tank_id, tank_id)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_EXCLUDED_TANKS, default=current_excluded
                ): cv.multi_select(tank_choices),
            }
        )
        return self.async_show_form(step_id="sensors", data_schema=schema)
