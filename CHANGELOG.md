# Changelog

## 0.4.3 (2026-11-02)

- The raw measurement behind a reading now travels as attributes of **Ostatni
  odczyt**, whose state is the time of that reading. The separate **Surowy
  odczyt** entity is gone: its state was the gateway's raw string, which does
  not fit a Home Assistant state (255 characters) and was unreadable in the UI.
  Nothing is truncated any more, and the rejected-reading attribute moved along
  with the rest. The old entity is removed from the registry on upgrade.

## 0.4.2 (2026-11-02)

- A reading the state service turned away (an echo too weak or too far to be a
  liquid surface) arrives without any level fields. The integration now shows the
  last accepted level in that case, as it already did for a zero reading, instead
  of leaving the level and fill unknown until the next good reading.

## 0.4.1 (2026-11-02)

- State service: the battery percentage is computed from the battery voltage along
  a per-sensor `battery_curve` in the sensor config, and a sensor without one
  publishes `batMv` and no `bat`. Before, `bat` was carried forward from the last
  state the vendor produced and froze there. A stored `bat` is reconciled with
  the config on start, and a seeded one is no longer adopted.
- Example config carries an `e19` curve, fitted to 5150 vendor readings (mean
  error 0.08 percentage points, worst 3). README: how to fit your own.

## 0.4.0 (2026-11-02)

First release of this repository. It continues the version line of
`hassio-integration-aquilo` (0.3.2) as its local-only sibling and takes the gateway client, the entities and the plausibility gate.

- Domain `aquilo_local`, one config-flow step (the gateway address). No vendor
  account, no cloud client.
- A level at or below zero (a radar with no echo) is turned away; the last good
  level is kept and saved across restarts; the turned-away values are shown under
  `rejected`.
- `server/`: the state service that replaces the vendor cloud's arithmetic once
  the gateway is pointed at your own broker, with its systemd unit, example
  configuration and 23 tests. It rejects a zero distance as well as one beyond
  the tank.
- README: the full procedure for taking the cloud out of the data path, and what
  that costs.
