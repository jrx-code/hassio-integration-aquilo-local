"""DataUpdateCoordinator for Aquilo."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AquiloApiError,
    AquiloLocalClient,
    apply_plausibility_gate,
)
from .const import (
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STORE_SAVE_DELAY_SECONDS = 30


class AquiloCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls one Aquilo gateway; data is keyed by sensor id."""

    def __init__(self, hass: HomeAssistant, client: AquiloLocalClient) -> None:
        """Set up polling.

        Args:
            hass: Home Assistant instance.
            client: The gateway client.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.client = client
        self._last_good: dict[str, dict[str, Any]] = {}
        # Kept across restarts: a gateway that keeps reporting an impossible
        # reading would otherwise leave the level unknown after every restart.
        self._store: Store[dict[str, dict[str, Any]]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.last_good_{client.host}"
        )
        self._store_loaded = False

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if not self._store_loaded:
            self._store_loaded = True
            saved = await self._store.async_load()
            if isinstance(saved, dict):
                self._last_good.update(saved)
        try:
            tanks = await self.client.async_get_sensors()
        except AquiloApiError as err:
            raise UpdateFailed(str(err)) from err
        before = {k: dict(v) for k, v in self._last_good.items()}
        apply_plausibility_gate(tanks, self._last_good)
        if self._last_good != before:
            self._store.async_delay_save(lambda: self._last_good, STORE_SAVE_DELAY_SECONDS)
        return tanks
