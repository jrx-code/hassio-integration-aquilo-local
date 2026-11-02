"""Shared fixtures. Payloads follow the `/state` example in README.md (and the
vendor PDF it links); nothing here comes from a real gateway."""

from __future__ import annotations

from typing import Any

import pytest

HOST = "aquilo.example"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def state_payload(**overrides: Any) -> dict[str, Any]:
    """Two tanks, shaped like the README example."""
    tanks = [
        {
            "id": "000001",
            "name": "SZAMBO",
            "lvl": 125.6,
            "pct": 34,
            "bat": 76,
            "lstRead": "2026-01-01T12:00:00+01:00",
        },
        {
            "id": "000002",
            "name": "DESZCZÓWKA",
            "lvl": 40.0,
            "pct": 95,
            "bat": 81,
            "lstRead": "2026-01-01T12:00:00+01:00",
        },
    ]
    payload: dict[str, Any] = {"sensors": tanks}
    payload.update(overrides)
    return payload


def raw_block(**overrides: Any) -> dict[str, Any]:
    """The `raw` object the gateway sends alongside the computed fields."""
    block: dict[str, Any] = {
        "raw": "_e19_aaaa01_601_3556_760_6_1161_1160_1_6",
        "read1": 3556,
        "amp1": 760,
        "readNo": 1161,
        "lastSent": 1160,
        "crc": True,
        "rssi": -98,
        "wifi": -61,
        "snr": 9,
        "v": 6,
        "rev": "e19",
        "r": 1,
    }
    block.update(overrides)
    return block
