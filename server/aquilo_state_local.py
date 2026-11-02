#!/usr/bin/env python3
"""Compute the Aquilo state locally and keep it on your own MQTT broker.

Why this exists. The Aquilo gateway is the radio receiver: when a sensor
transmits, the gateway publishes the raw reading, and the vendor cloud turns
that reading into levels and pushes the result back down, which the gateway
then mirrors on its local HTTP endpoint, which is what Home Assistant polls.
With the gateway pointed at your own broker (see the README), this service
takes over the only thing the vendor still did, so no part of the data path
leaves the house.

    sensor --radio--> gateway --MQTT--> [this service] --MQTT--> gateway --HTTP--> HA

The arithmetic is the vendor's own, read off the retained settings and checked
against 166 readings from the vendor's recorded history (0 mismatched, worst
error 0.050 pp, which is the vendor's rounding to one decimal).

What this service does NOT change is how often readings arrive. That is the
sensor's own firmware schedule (by default ``sendInterval`` 28800 s), the vendor charges
for changing it, and no amount of local computation makes a sleeping radio
transmit sooner.

The published state carries the raw reading beside the computed one. Everything
the gateway sent, minus the device's ``salt``, goes into a ``raw`` object per
sensor, and a reading the gate turned away is kept under ``rejected`` instead of
vanishing. The gateway mirrors whatever it is handed, so this is what makes
``http://<host>/state`` show the whole picture rather than only the result. The
fields are nested so they cannot collide with the nine keys the Home Assistant
integration reads, and that integration validates no schema, so additions are
inert to it.

A reading is only accepted when it is physically possible for the tank it claims
to describe. The radio CRC in the payload validates the packet, not the
measurement: in one recorded case a rainwater sensor's echo amplitude collapsed from
about 1375 to about 150 and it began reporting distances of 276 to 393 cm for a
220 cm tank, every one of them with ``crc: true``. Those are rejected here, and
the echo amplitude is published so the quality can be seen in Home Assistant
rather than inferred.

A rejected reading takes the level with it: ``lvl``, ``pct`` and ``lvlToFull``
are dropped rather than held at the last accepted reading, so the Home
Assistant entities read ``unknown`` instead of a number that looks measured and
is not. Holding the old value was the first behaviour here and it was wrong in
the way that matters: nothing downstream carries a "this is held over" state,
so a held level and a measured one are the same value to every consumer, and an
automation or a person reads it as current. What the radio actually sent stays
live across a rejection -- the raw block, the echo amplitude, the signal
figures -- because those are what one goes looking for when a level has gone
unknown. ``lstRead`` stays on the last accepted reading, which is what the
stale-data sensors in Home Assistant are measuring; the rejection has its own
timestamp under ``rejected``.

Fields the service cannot derive from a single reading -- ``lstEmpty`` and
``daysLeft`` -- are carried forward from the last state it knows, so the Home
Assistant entities that read them keep their values instead of going
unavailable. ``bat`` is different: it is computed from the battery voltage where
a sensor's config carries a ``battery_curve`` (knots recovered from the vendor's
own recorded history for that hardware revision), and a sensor without one has
no ``bat`` at all. A carried value would freeze at whatever it was when the
vendor left the path and read as a live measurement, and a curve fitted to a
voltage range the sensor has never left would be invention. The true millivolts
are published alongside as ``batMv``.

``lstRead`` is stamped on arrival, because the raw read carries no timestamp
of its own, which is also how the cloud produced it.

Env (from /etc/aquilo-state-local.env):
    MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, AQUILO_GATEWAY_ID,
    AQUILO_CONFIG, AQUILO_STORE
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from aquilo_level import battery_pct, levels

logger = logging.getLogger("aquilo-state-local")

# The device's own salt is the one field not republished: it is a credential-
# shaped value and nothing downstream needs it.
RAW_SKIP = frozenset({"salt"})

# The gateway's id is the account name it logs in to the broker with, and every
# topic it uses hangs off it.
GATEWAY_ID = os.environ.get("AQUILO_GATEWAY_ID", "")
READ_TOPIC = f"/users/{GATEWAY_ID}/sensors/+/read"
STATE_TOPIC = f"/users/{GATEWAY_ID}/state"

# An empty tank reads its own depth, so a little slack above that is instrument
# tolerance rather than a bad measurement. Anything past it is not a level.
DEPTH_TOLERANCE_CM = 5.0


def load_json(path: Path, default: Any) -> Any:
    """Read a JSON file, returning a default when it is absent or unreadable.

    Args:
        path: File to read.
        default: Value to return when the file cannot be used.

    Returns:
        The parsed contents, or ``default``.
    """
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cannot read %s (%s), using default", path, exc)
        return default


def compute(read1: int, tank: dict[str, Any]) -> dict[str, Any]:
    """Turn a raw radar reading into the level fields the vendor would publish.

    Args:
        read1: Raw reading, in hundredths of a centimetre, as the gateway sends it.
        tank: Per-sensor geometry: ``depth_cm`` and ``full_cm``.

    Returns:
        Dict with ``lvl``, ``pct`` and ``lvlToFull``.

    Raises:
        ValueError: Propagated from the shared arithmetic when the tank geometry
            makes a percentage meaningless.
    """
    return levels(read1, float(tank["depth_cm"]), float(tank["full_cm"]))


class Service:
    """Keeps per-sensor state and republishes the combined payload."""

    def __init__(self, config: dict[str, Any], store_path: Path) -> None:
        """Initialise from the tank geometry and the persisted last state.

        Args:
            config: ``{"sensors": {"<id>": {"name","depth_cm","full_cm"}}}``.
            store_path: Where the last published per-sensor fields live.
        """
        self.tanks: dict[str, Any] = config["sensors"]
        self.store_path = store_path
        self.store: dict[str, Any] = load_json(store_path, {})
        self._reconcile_battery()

    def _reconcile_battery(self) -> None:
        """Make every stored ``bat`` agree with the config as it is now.

        A figure carried over from the time the vendor computed it must not
        outlive that: without a curve it is dropped, with one it is recomputed
        from the stored voltage.
        """
        for sensor_id, entry in self.store.items():
            curve = self.tanks.get(sensor_id, {}).get("battery_curve")
            if curve and entry.get("batMv") is not None:
                entry["bat"] = battery_pct(int(entry["batMv"]), curve)
            else:
                entry.pop("bat", None)


    def persist(self) -> None:
        """Write the current per-sensor fields so a restart keeps them."""
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.store, ensure_ascii=False, indent=2))
        tmp.replace(self.store_path)

    @staticmethod
    def _apply_transmission(
        entry: dict[str, Any],
        payload: dict[str, Any],
        curve: list[list[float]] | None = None,
    ) -> None:
        """Copy the fields that describe the transmission itself onto an entry.

        These say what the radio sent and how well it was heard, which is true
        of a reading whether or not its distance is usable, so they are applied
        on both paths. The echo amplitude is the clearest case: it is the one
        number that distinguishes a sensor looking at a liquid surface from one
        looking at a desk, so holding it at its last accepted value would hide
        exactly the state one is trying to read.

        Args:
            entry: The per-sensor store entry, modified in place.
            payload: The gateway's ``read`` payload.
            curve: The sensor's battery curve, if its config has one.
        """
        if payload.get("battery") is not None:
            entry["batMv"] = int(payload["battery"])
            if curve:
                entry["bat"] = battery_pct(int(payload["battery"]), curve)
        if not curve:
            entry.pop("bat", None)
        for extra in ("rssi", "temp", "snr", "amp1"):
            if payload.get(extra) is not None:
                entry[extra] = payload[extra]
        entry["raw"] = {k: v for k, v in payload.items() if k not in RAW_SKIP}

    def apply_read(self, payload: dict[str, Any], retained: bool = False) -> bool:
        """Fold one raw reading into the stored state.

        Args:
            payload: The gateway's ``read`` payload.
            retained: Whether the broker replayed this from its retained store
                rather than the gateway publishing it now.

        Returns:
            True when the state changed and should be republished.
        """
        if retained:
            # The gateway publishes its reads retained, so every reconnect
            # replays the last one. Stamping that with the current time would
            # report a measurement from hours ago as fresh, and the stale-data
            # sensors in Home Assistant depend on that timestamp.
            logger.info("ignoring the retained replay of %s", payload.get("sensor"))
            return False
        sensor = payload.get("sensor")
        read1 = payload.get("read1")
        if sensor is None or read1 is None:
            logger.info("reading without sensor/read1, ignored: %s", payload)
            return False
        if payload.get("crc") is False:
            logger.warning("%s: reading failed its crc, ignored", sensor)
            return False
        tank = self.tanks.get(str(sensor))
        if tank is None:
            logger.warning("%s: no tank geometry configured, ignored", sensor)
            return False

        lvl = int(read1) / 100
        limit = float(tank["depth_cm"]) + DEPTH_TOLERANCE_CM
        if not 0 < lvl <= limit:
            logger.warning(
                "%s %s: %.1f cm is outside the tank (above 0 to %.1f cm), reading rejected"
                " (read1=%s, amp1=%s, crc=%s)",
                sensor, tank["name"], lvl, limit, read1,
                payload.get("amp1"), payload.get("crc"),
            )
            # A rejected reading drops the level rather than leaving the last
            # good one in place. Carrying it forward published a number that
            # read as the current level of the tank while the sensor was
            # provably not measuring it, and nothing downstream could tell the
            # two apart: the integration has no "this is held over" state, so
            # a held value is indistinguishable from a measured one. Absent
            # fields make the level entities read `unknown`, which is what is
            # true. What the sensor DID send stays live below, because the raw
            # reading and the echo amplitude are the whole reason one looks at
            # a tank whose level has gone unknown.
            entry = dict(self.store.get(str(sensor), {}))
            for field in ("lvl", "pct", "lvlToFull"):
                entry.pop(field, None)
            self._apply_transmission(entry, payload, tank.get("battery_curve"))
            entry["id"] = str(sensor)
            entry["name"] = tank["name"]
            entry["rejected"] = {
                "read1": int(read1),
                "lvl": round(lvl, 1),
                "limit": round(limit, 1),
                "amp1": payload.get("amp1"),
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            self.store[str(sensor)] = entry
            self.persist()
            return True

        entry = dict(self.store.get(str(sensor), {}))
        entry.update(compute(int(read1), tank))
        entry["id"] = str(sensor)
        entry["name"] = tank["name"]
        self._apply_transmission(entry, payload, tank.get("battery_curve"))
        # A reading that passes clears the record of the last one that did not.
        entry.pop("rejected", None)
        # The raw read carries no timestamp of its own: the cloud stamped it on
        # arrival, so we do the same, in the local-offset format its state
        # payloads used.
        entry["lstRead"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.store[str(sensor)] = entry
        self.persist()
        logger.info("%s %s: lvl=%s pct=%s lvlToFull=%s (read1=%s)", sensor,
                    tank["name"], entry["lvl"], entry["pct"], entry["lvlToFull"], read1)
        return True

    def seed_from_state(self, payload: dict[str, Any]) -> None:
        """Take the carried fields from a state payload we did not produce.

        Used once at start-up so ``lstEmpty`` and ``daysLeft`` are
        known before the first local reading arrives.

        Args:
            payload: A ``state`` payload in the vendor's shape.
        """
        changed = False
        for s in payload.get("sensors", []):
            sid = str(s.get("id", ""))
            if not sid or sid not in self.tanks:
                continue
            entry = dict(self.store.get(sid, {}))
            for field in ("lstEmpty", "daysLeft", "lstRead", "lvl", "pct",
                          "lvlToFull", "name"):
                if field in s and field not in entry:
                    entry[field] = s[field]
            entry["id"] = sid
            if self.store.get(sid) != entry:
                self.store[sid] = entry
                changed = True
        if changed:
            self.persist()
            logger.info("seeded carried fields for %d sensors", len(self.store))

    def state_payload(self) -> dict[str, Any]:
        """Build the combined state payload in the shape the gateway mirrors.

        Returns:
            ``{"sensors": [...], "from": "local"}``.
        """
        order = list(self.tanks)
        sensors = [self.store[s] for s in order if s in self.store]
        return {"sensors": sensors, "from": "local"}


def main() -> int:
    """Run the service until signalled.

    Returns:
        Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s",
                        stream=sys.stdout)
    host = os.environ.get("MQTT_HOST", "127.0.0.1")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    user = os.environ["MQTT_USER"]
    password = os.environ["MQTT_PASS"]
    if not GATEWAY_ID:
        logger.error("AQUILO_GATEWAY_ID is not set, refusing to start")
        return 1
    config = load_json(Path(os.environ["AQUILO_CONFIG"]), None)
    if not config:
        logger.error("no tank configuration, refusing to start")
        return 1
    svc = Service(config, Path(os.environ.get("AQUILO_STORE",
                                              "/var/lib/aquilo-state-local/store.json")))
    svc.store_path.parent.mkdir(parents=True, exist_ok=True)

    seeded = {"done": bool(svc.store)}

    def on_connect(client: mqtt.Client, _u: Any, _f: Any, reason: Any, _p: Any = None) -> None:
        logger.info("connected to %s:%s (%s)", host, port, reason)
        client.subscribe(READ_TOPIC, qos=0)
        if svc.store:
            # Republish what is already stored, so a change to the published
            # field set appears without waiting hours for the next reading.
            # Stored values are reused verbatim, lstRead included, so this
            # cannot present an old measurement as a fresh one.
            body = json.dumps(svc.state_payload(), ensure_ascii=False)
            client.publish(STATE_TOPIC, body, qos=0, retain=True)
            logger.info("republished %s on connect (%d bytes)", STATE_TOPIC, len(body))
        if not seeded["done"]:
            # One-off: adopt whatever state is currently retained, so the carried
            # fields are not empty before the first local reading arrives.
            client.subscribe(STATE_TOPIC, qos=0)

    def on_message(client: mqtt.Client, _u: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.info("%s: payload is not JSON, ignored", msg.topic)
            return
        if msg.topic == STATE_TOPIC:
            if payload.get("from") == "local" or seeded["done"]:
                return
            svc.seed_from_state(payload)
            seeded["done"] = True
            client.unsubscribe(STATE_TOPIC)
            return
        if svc.apply_read(payload, retained=bool(msg.retain)):
            body = json.dumps(svc.state_payload(), ensure_ascii=False)
            client.publish(STATE_TOPIC, body, qos=0, retain=True)
            logger.info("published %s (%d bytes)", STATE_TOPIC, len(body))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aquilo-state-local")
    client.username_pw_set(user, password)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(host, port, keepalive=60)

    stop = {"now": False}

    def handle(_sig: int, _frm: Any) -> None:
        stop["now"] = True
        client.disconnect()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    client.loop_forever(retry_first_connection=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
