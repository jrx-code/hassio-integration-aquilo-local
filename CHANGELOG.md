# Changelog

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
