"""Client for the Aquilo gateway's own HTTP endpoint.

The gateway is a radio receiver. It hands its raw radar readings to whoever
holds the MQTT broker it talks to, and it mirrors whatever state arrives back on
a plain HTTP endpoint of its own. This client polls ``http://<host>/state`` and
nothing leaves the network. What the endpoint returns is only as good as whoever
computes that state: with the broker substituted (see the README) it is the
state service in ``server/``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)


class AquiloApiError(Exception):
    """Raised when the gateway can't be reached or returns garbage."""


# Fields that describe the level. A rejected reading keeps the previous
# accepted values of these and leaves everything else (battery, signal) fresh.
LEVEL_FIELDS = ("lvl", "pct", "lvlToFull", "daysLeft", "lstRead")


def is_implausible(tank: dict[str, Any]) -> bool:
    """Whether a tank's level comes from a reading no liquid surface can give.

    A radar that gets no echo reports a distance of zero, which the gateway
    turns into a full tank. Zero is physically impossible: even a brim-full
    tank sits at the vendor's "full" offset (20 cm by default) from the sensor.

    Args:
        tank: One sensor dict in the gateway's field names.

    Returns:
        True when the level is present and not above zero.
    """
    lvl = tank.get("lvl")
    return isinstance(lvl, (int, float)) and lvl <= 0


def apply_plausibility_gate(
    tanks: dict[str, dict[str, Any]], last_good: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Hold back readings that cannot be real and remember the ones that can.

    Mutates ``tanks`` and ``last_good``. A turned-away reading is kept under
    ``rejected`` so the diagnostic entity can show it, and the level fields
    fall back to the last accepted reading, or to nothing when there has been
    none yet (an unknown level is better than a wrong full tank).

    Args:
        tanks: Freshly fetched ``{sensor_id: sensor_dict}``.
        last_good: Per-sensor level fields of the last accepted reading.

    Returns:
        ``tanks``, for chaining.
    """
    for sensor_id, tank in tanks.items():
        if isinstance(tank.get("rejected"), dict) and "lvl" not in tank:
            # The state service already turned the reading away and dropped
            # the level fields; show the last accepted ones instead of nothing.
            tank.update(last_good.get(sensor_id, {}))
        elif is_implausible(tank):
            tank["rejected"] = {k: tank.get(k) for k in LEVEL_FIELDS if k in tank}
            for key in LEVEL_FIELDS:
                tank.pop(key, None)
            tank.update(last_good.get(sensor_id, {}))
            _LOGGER.warning(
                "%s: reading turned away (level %s cm is not physically possible)",
                sensor_id,
                tank["rejected"].get("lvl"),
            )
        elif "lvl" in tank:
            last_good[sensor_id] = {k: tank[k] for k in LEVEL_FIELDS if k in tank}
    return tanks


class AquiloLocalClient:
    """Reads the tanks from the gateway's own HTTP endpoint.

    One gateway can report several tank sensors in a single response; each
    ``sensors[]`` entry becomes its own Home Assistant device.
    """

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        """Store the session and the gateway address.

        Args:
            session: Shared aiohttp session.
            host: Gateway host or IP, without a scheme.
        """
        self._session = session
        self._host = host

    @property
    def host(self) -> str:
        """Stable identity label for this entry, used in every unique id."""
        return self._host

    @property
    def configuration_url(self) -> str:
        """Where a user should click through to."""
        return f"http://{self._host}/state"

    async def async_get_sensors(self) -> dict[str, dict[str, Any]]:
        """Fetch the tanks.

        Returns:
            ``{sensor_id: sensor_dict}``.

        Raises:
            AquiloApiError: On any transport or payload problem.
        """
        url = f"http://{self._host}/state"
        try:
            async with self._session.get(url, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    raise AquiloApiError(f"HTTP {resp.status} from {url}")
                data = await resp.json(content_type=None)
        except TimeoutError as err:
            raise AquiloApiError(f"Timeout talking to {url}") from err
        except aiohttp.ClientError as err:
            raise AquiloApiError(f"Connection error talking to {url}: {err}") from err
        except ValueError as err:
            # A 200 carrying something that is not JSON. Without this it would
            # leave the integration as an unhandled exception instead of a
            # failed update.
            raise AquiloApiError(f"Malformed JSON from {url}: {err}") from err

        if not isinstance(data, dict):
            raise AquiloApiError(f"Unexpected payload from {url}: {data!r}")
        sensors = data.get("sensors")
        if not isinstance(sensors, list):
            raise AquiloApiError(f"Unexpected payload from {url}: {data!r}")

        result: dict[str, dict[str, Any]] = {}
        for entry in sensors:
            if not isinstance(entry, dict):
                _LOGGER.warning("Aquilo sensor entry not an object, skipping: %r", entry)
                continue
            sensor_id = entry.get("id")
            if not sensor_id:
                _LOGGER.warning("Aquilo sensor entry without id, skipping: %s", entry)
                continue
            result[sensor_id] = entry
        return result
