"""The local Aquilo state service: what a rejected reading does to the state.

The gate that turns away an impossible distance is the interesting part, because
its first behaviour was to keep publishing the last accepted level, and that
published a held value that every consumer read as a current measurement.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# The service ships as a flat pair of files and imports its arithmetic as
# `aquilo_level`, so `server/` has to be importable as a directory.
os.environ.setdefault("AQUILO_GATEWAY_ID", "gw0001")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from aquilo_state_local import Service  # noqa: E402

TANKS: dict[str, Any] = {
    "sensors": {
        "aaaa01": {"name": "Rain", "depth_cm": 220, "full_cm": 25},
        "bbbb02": {"name": "Sewage", "depth_cm": 189.5, "full_cm": 20},
    }
}


def read(sensor: str = "aaaa01", read1: int = 6050, **extra: Any) -> dict[str, Any]:
    """One gateway `read` payload, shaped like the real ones."""
    payload: dict[str, Any] = {
        "raw": f"_e19_{sensor}_605_3557_756_6_1162_1160_1_6",
        "v": "e19",
        "sensor": sensor,
        "read1": read1,
        "battery": "3557",
        "crc": True,
        "amp1": 756,
        "temp": 6,
        "readNo": 1162,
        "lastSent": 1160,
        "rev": 1,
        "r": 6,
        "rssi": -87,
        "wifi": -81,
        "snr": 9,
        "salt": "do-not-republish",
    }
    payload.update(extra)
    return payload


@pytest.fixture
def svc(tmp_path: Path) -> Service:
    return Service(TANKS, tmp_path / "store.json")


def _entry(svc: Service, sensor: str = "aaaa01") -> dict[str, Any]:
    return svc.store[sensor]


# --- the accepted path ------------------------------------------------------


def test_an_accepted_reading_publishes_the_level(svc: Service):
    assert svc.apply_read(read(read1=6050)) is True
    entry = _entry(svc)
    assert entry["lvl"] == 60.5
    assert entry["pct"] == 82
    assert entry["lvlToFull"] == 36
    assert entry["name"] == "Rain"
    assert entry["amp1"] == 756
    assert entry["batMv"] == 3557
    assert entry["raw"]["read1"] == 6050
    assert "rejected" not in entry


def test_the_device_salt_is_never_republished(svc: Service):
    svc.apply_read(read())
    assert "salt" not in _entry(svc)["raw"]
    assert "salt" not in json.dumps(svc.state_payload())


def test_a_retained_replay_changes_nothing(svc: Service):
    assert svc.apply_read(read(), retained=True) is False
    assert svc.store == {}


def test_a_failed_crc_is_ignored(svc: Service):
    assert svc.apply_read(read(crc=False)) is False
    assert svc.store == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"read1": 6050},  # no sensor
        {"sensor": "aaaa01"},  # no read1
        {"sensor": "ffffff", "read1": 6050},  # no geometry configured
    ],
)
def test_a_reading_that_cannot_be_placed_is_ignored(svc: Service, payload: Any):
    assert svc.apply_read(payload) is False
    assert svc.store == {}


# --- the rejected path ------------------------------------------------------


# 393.0 cm for a 220 cm tank: the real failure from 2026-10-29, crc true.
IMPOSSIBLE = 39300


def test_a_rejected_reading_drops_the_level_instead_of_holding_it(svc: Service):
    svc.apply_read(read(read1=6050))
    assert svc.apply_read(read(read1=IMPOSSIBLE, amp1=150)) is True
    entry = _entry(svc)
    for gone in ("lvl", "pct", "lvlToFull"):
        assert gone not in entry, gone
    assert entry["rejected"]["lvl"] == 393.0
    assert entry["rejected"]["limit"] == 225.0
    assert entry["rejected"]["amp1"] == 150
    assert entry["rejected"]["at"]


def test_a_rejection_keeps_the_transmission_fields_live(svc: Service):
    """The echo amplitude is the reason one looks at all, so it must not freeze."""
    svc.apply_read(read(read1=6050, amp1=1375))
    svc.apply_read(read(read1=IMPOSSIBLE, amp1=150, rssi=-116, snr=3, battery="3601"))
    entry = _entry(svc)
    assert entry["amp1"] == 150
    assert entry["rssi"] == -116
    assert entry["snr"] == 3
    assert entry["batMv"] == 3601
    assert entry["raw"]["read1"] == IMPOSSIBLE
    assert entry["raw"]["amp1"] == 150


def test_a_rejection_leaves_lstread_on_the_last_accepted_reading(svc: Service):
    """The stale-data sensors measure that field, not the rejection."""
    svc.apply_read(read(read1=6050))
    accepted_at = _entry(svc)["lstRead"]
    svc.apply_read(read(read1=IMPOSSIBLE))
    assert _entry(svc)["lstRead"] == accepted_at
    assert _entry(svc)["rejected"]["at"] >= accepted_at


def test_the_carried_fields_survive_a_rejection(svc: Service):
    svc.seed_from_state(
        {"sensors": [{"id": "aaaa01", "bat": 54, "lstEmpty": "2026-08-31T20:13:54+02:00"}]}
    )
    svc.apply_read(read(read1=6050))
    svc.apply_read(read(read1=IMPOSSIBLE))
    entry = _entry(svc)
    assert entry["bat"] == 54
    assert entry["lstEmpty"] == "2026-08-31T20:13:54+02:00"
    assert entry["name"] == "Rain"
    assert entry["id"] == "aaaa01"


def test_a_good_reading_restores_the_level_and_clears_the_rejection(svc: Service):
    svc.apply_read(read(read1=6050))
    svc.apply_read(read(read1=IMPOSSIBLE))
    assert svc.apply_read(read(read1=7000, amp1=1375)) is True
    entry = _entry(svc)
    assert entry["lvl"] == 70.0
    assert entry["pct"] == 77
    assert "rejected" not in entry


def test_a_first_ever_reading_that_is_rejected_leaves_no_level(svc: Service):
    """Nothing to hold over, so the tank must not appear to have a level."""
    assert svc.apply_read(read(read1=IMPOSSIBLE)) is True
    entry = _entry(svc)
    assert "lvl" not in entry
    assert entry["id"] == "aaaa01"
    assert entry["rejected"]["read1"] == IMPOSSIBLE


# Zero is the no-echo case: a radar that sees no surface reports a distance of
# zero, which would otherwise be published as a full tank.
@pytest.mark.parametrize("read1", [0, -1, 22501, 100000])
def test_readings_outside_the_tank_are_rejected(svc: Service, read1: int):
    svc.apply_read(read(read1=6050))
    svc.apply_read(read(read1=read1))
    assert "lvl" not in _entry(svc)


@pytest.mark.parametrize("read1", [1, 22500])
def test_the_tolerance_band_is_inclusive(svc: Service, read1: int):
    """An empty tank reads its own depth; 5 cm of slack is instrument tolerance."""
    assert svc.apply_read(read(read1=read1)) is True
    assert "lvl" in _entry(svc)
    assert "rejected" not in _entry(svc)


# --- persistence and the published shape ------------------------------------


def test_the_state_survives_a_restart(svc: Service, tmp_path: Path):
    svc.apply_read(read(read1=6050))
    svc.apply_read(read(sensor="bbbb02", read1=16620))
    reloaded = Service(TANKS, tmp_path / "store.json")
    assert reloaded.store == svc.store


def test_the_payload_keeps_the_configured_tank_order(svc: Service):
    svc.apply_read(read(sensor="bbbb02", read1=16620))
    svc.apply_read(read(sensor="aaaa01", read1=6050))
    payload = svc.state_payload()
    assert [s["id"] for s in payload["sensors"]] == ["aaaa01", "bbbb02"]
    assert payload["from"] == "local"


def test_a_tank_with_no_reading_yet_is_absent_from_the_payload(svc: Service):
    svc.apply_read(read(read1=6050))
    assert [s["id"] for s in svc.state_payload()["sensors"]] == ["aaaa01"]


def test_seeding_never_overwrites_what_a_local_reading_produced(svc: Service):
    svc.apply_read(read(read1=6050))
    svc.seed_from_state({"sensors": [{"id": "aaaa01", "lvl": 999, "bat": 54}]})
    entry = _entry(svc)
    assert entry["lvl"] == 60.5
    assert entry["bat"] == 54
