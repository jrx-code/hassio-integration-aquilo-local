"""AquiloLocalClient.async_get_sensors against a fake aiohttp session."""

from __future__ import annotations

from typing import Any, Self

import aiohttp
import pytest

from custom_components.aquilo_local.api import (
    AquiloApiError,
    AquiloLocalClient,
    apply_plausibility_gate,
    is_implausible,
)

from .conftest import HOST, state_payload


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: str | None = "application/json") -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Session:
    def __init__(self, status: int = 200, payload: Any = None, error: Exception | None = None):
        self.status, self.payload, self.error = status, payload, error
        self.urls: list[str] = []

    def get(self, url: str, timeout: Any = None) -> _Response:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return _Response(self.status, self.payload)


async def _get(session: _Session) -> dict[str, dict[str, Any]]:
    return await AquiloLocalClient(session, HOST).async_get_sensors()  # type: ignore[arg-type]


async def test_returns_sensors_by_id():
    session = _Session(payload=state_payload())
    result = await _get(session)
    assert session.urls == [f"http://{HOST}/state"]
    assert set(result) == {"000001", "000002"}
    assert result["000001"]["pct"] == 34


@pytest.mark.parametrize("status", [404, 500, 503])
async def test_http_error(status: int):
    with pytest.raises(AquiloApiError, match=f"HTTP {status}"):
        await _get(_Session(status=status, payload=state_payload()))


async def test_timeout():
    with pytest.raises(AquiloApiError, match="Timeout"):
        await _get(_Session(error=TimeoutError()))


async def test_connection_error():
    with pytest.raises(AquiloApiError, match="Connection error"):
        await _get(_Session(error=aiohttp.ClientConnectionError("refused")))


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "not json object",
        None,
        {},
        {"sensors": None},
        {"sensors": {"id": "000001"}},
        {"devices": []},
    ],
)
async def test_payload_without_sensors_list(payload: Any):
    with pytest.raises(AquiloApiError, match="Unexpected payload"):
        await _get(_Session(payload=payload))


async def test_entries_without_id_or_not_objects_are_skipped():
    payload = {
        "sensors": [
            {"id": "000001", "pct": 10},
            {"pct": 20},
            {"id": "", "pct": 30},
            {"id": None, "pct": 40},
            "garbage",
            42,
        ]
    }
    assert list(await _get(_Session(payload=payload))) == ["000001"]


async def test_empty_sensor_list_is_not_an_error():
    assert await _get(_Session(payload={"sensors": []})) == {}


async def test_malformed_json_is_an_api_error():
    session = _Session(payload=ValueError("not json"))
    with pytest.raises(AquiloApiError, match="Malformed JSON"):
        await _get(session)


def test_identity_and_configuration_url():
    client = AquiloLocalClient(None, HOST)  # type: ignore[arg-type]
    assert client.host == HOST
    assert client.configuration_url == f"http://{HOST}/state"


# --- plausibility gate ---------------------------------------------------

def _tank(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "t1", "lvl": 160.9, "pct": 17, "lvlToFull": 141,
        "daysLeft": 67, "lstRead": "2026-11-01T21:01:26+01:00", "bat": 43,
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    ("lvl", "expected"), [(0, True), (0.0, True), (-1.0, True), (0.4, False), (160.9, False), (None, False)]
)
def test_is_implausible(lvl: Any, expected: bool):
    tank = {"id": "t1"} if lvl is None else {"id": "t1", "lvl": lvl}
    assert is_implausible(tank) is expected


def test_gate_replaces_a_zero_reading_with_the_last_good_one():
    last_good: dict[str, dict[str, Any]] = {}
    apply_plausibility_gate({"t1": _tank()}, last_good)

    bad = _tank(lvl=0.0, pct=100, lvlToFull=-20, lstRead="2026-11-02T04:45:28+01:00", bat=37)
    del bad["daysLeft"]
    out = apply_plausibility_gate({"t1": bad}, last_good)["t1"]

    assert (out["lvl"], out["pct"], out["lvlToFull"]) == (160.9, 17, 141)
    assert out["lstRead"] == "2026-11-01T21:01:26+01:00"  # stale watchdog keeps counting
    assert out["bat"] == 37  # non-level fields stay fresh
    assert out["rejected"]["lvl"] == 0.0 and out["rejected"]["pct"] == 100


def test_gate_without_history_leaves_the_level_unknown():
    out = apply_plausibility_gate({"t1": _tank(lvl=0.0, pct=100)}, {})["t1"]
    assert "lvl" not in out and "pct" not in out
    assert out["rejected"]["pct"] == 100


def test_gate_passes_a_good_reading_untouched():
    out = apply_plausibility_gate({"t1": _tank()}, {})["t1"]
    assert out["pct"] == 17 and "rejected" not in out


def test_gate_restores_the_last_good_level_when_the_service_dropped_it():
    last_good = {"t1": {"lvl": 60.5, "pct": 82, "lvlToFull": 36, "lstRead": "x"}}
    tank = {"id": "t1", "bat": 20, "rejected": {"read1": 34330, "limit": 225.0}}
    out = apply_plausibility_gate({"t1": tank}, last_good)["t1"]
    assert out["pct"] == 82 and out["lvl"] == 60.5
    assert out["rejected"]["read1"] == 34330


def test_gate_leaves_the_level_unknown_when_the_service_dropped_it_without_history():
    tank = {"id": "t1", "rejected": {"read1": 34330}}
    out = apply_plausibility_gate({"t1": tank}, {})["t1"]
    assert "pct" not in out and "lvl" not in out
