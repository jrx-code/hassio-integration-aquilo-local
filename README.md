# Aquilo Local: Home Assistant integration without the vendor cloud

Reads [Aquilo](https://aquilo.pl) liquid-level sensors (septic tank, rainwater
tank, well) from the gateway's own HTTP endpoint and, with the setup below,
takes the vendor cloud out of the data path altogether. Not affiliated with or
endorsed by the manufacturer.

This is the local-only sibling of
[`hassio-integration-aquilo`](https://github.com/jrx-code/hassio-integration-aquilo),
which can also read the vendor cloud. Here there is no cloud client and no
account: the integration polls `http://<gateway>/state`, and the repository
carries the small service and the instructions that make that endpoint
independent of the vendor.

The two have different domains (`aquilo` and `aquilo_local`) and can be
installed side by side.

## How the data flows

By default:

```
sensor --radio--> gateway --MQTT--> vendor cloud --(arithmetic)--> gateway --HTTP /state--> Home Assistant
```

The gateway is the radio receiver. It publishes each raw radar reading to the
vendor's broker, the cloud turns the reading into a level and a percentage and
sends that back down, and the gateway mirrors it on its own `/state` page.
Home Assistant only ever talks to the gateway, so the cloud is a calculator in
the middle, and it can be replaced:

```
sensor --radio--> gateway --MQTT--> your broker --> state service --MQTT--> gateway --HTTP /state--> Home Assistant
```

Three pieces make that work, all in this repository or in your network:

1. your DNS answers for the vendor's broker name with your broker's address;
2. your MQTT broker accepts the gateway;
3. `server/aquilo_state_local.py` does the arithmetic and publishes the state.

Tested on a gateway with firmware `1.7.1.5_en` and two sensor types (`e19` and
`g1`). Other firmware may behave differently; see "What you give up".

## Part 1: the Home Assistant integration

The integration is in the HACS default store:

1. HACS (or the Marketplace on Home Assistant 2026.11+), search for **Aquilo Local**,
   download it, restart Home Assistant
2. **Settings, Devices & services, Add integration, Aquilo Local**, and enter the
   gateway's address (the host or IP, no scheme)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jrx-code&repository=hassio-integration-aquilo-local&category=integration)

If the search finds nothing yet, add it by hand: HACS, three-dot menu, **Custom
repositories**, URL `https://github.com/jrx-code/hassio-integration-aquilo-local`,
type **Integration**.

Manual: copy `custom_components/aquilo_local` into your `config/custom_components/`.

The integration works with a stock gateway too, exactly as the original does,
so you can install it first and cut the cloud later.

Per tank you get a device with these entities. Names are Polish and set in
code, whatever language Home Assistant uses.

| Entity | Notes |
|---|---|
| Poziom (level, cm) | distance reading |
| Wypełnienie (fill, %) | |
| Poziom do pełna (level to full, cm) | disabled by default |
| Dni do pustego (days left) | only if the state carries it |
| Bateria (battery, %) | diagnostic, only if the state carries it (needs a battery curve) |
| Ostatni odczyt (last read) | diagnostic, timestamp; carries the whole raw block as attributes (the gateway's raw string, the echo fields, and a reading that was turned away) |
| Ostatnie opróżnienie (last emptied) | only if the state carries it |
| Jakość echa (echo quality) | diagnostic, only where the sensor reports an echo amplitude |
| Identyfikator, Adres bramki | diagnostic, identity |
| **Nieaktualne dane** (stale data) | on when the last read is older than the threshold (default 24 h) |
| **Ryzyko przepełnienia** (overflow risk) | on when fill reaches the threshold (default 90 %) |

Options (gear icon): the two thresholds, and a list of tanks to exclude.

**A reading no liquid can give is turned away.** A radar that gets no echo
reports a distance of zero, which a naive calculation turns into a full tank
(100 %). The integration refuses a level at or below zero, keeps the last good
level and shows the turned-away values under `rejected` on the raw-reading
entity. The last good level is saved to disk, so it survives a restart too. The
state service below applies the same rule at its end.

## Part 2: going fully local

Do this in order. Nothing in steps 1 to 4 touches the running system, so you can
prepare everything while the gateway still talks to the vendor.

### What you need

- the gateway's **id** and **MQTT password** (step 1)
- an MQTT broker you control that speaks plain MQTT on TCP 1883 (Mosquitto,
  EMQX, anything). The firmware has **no TLS**, so the listener the gateway uses
  must be a plain one
- a way to override one DNS name for the gateway (your router, Pi-hole,
  AdGuard, dnsmasq)
- a machine that can run a small Python script next to the broker, with
  `paho-mqtt` 2.x installed
- a network path from the gateway to the broker on TCP 1883. If the gateway sits
  on an isolated IoT network, allow exactly that

### Step 1: collect what only the cloud knows today

You need three things before you cut anything.

**The gateway id and password.** The gateway logs in to the broker with its id
as the user name and a password, over plain TCP. The same pair logs in to the
vendor's dashboard at `panel.aquilo.pl`. If you do not have them, the MQTT
`CONNECT` packet is readable on the wire, because there is no TLS:

```bash
# on a machine that sees the gateway's traffic (the router, a mirror port)
sudo tcpdump -i any -A -s0 'host <gateway-ip> and tcp port 1883' | grep -a -m1 -B2 -A2 CieczSensor
```

The client id is `CieczSensor<id>`; the user name and password are in the same
connect packet. The capture line above is a sketch to adapt to your network, not
a tested recipe. Capture once, then keep the password in your password manager.

**The id of each sensor and its tank geometry.** While the cloud still answers,
ask it once:

```bash
TOKEN=$(curl -s https://panel.aquilo.pl/api/v2/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<gateway-id>","password":"<password>"}' | jq -r .token)
curl -s https://panel.aquilo.pl/api/v2/sensors -H "Authorization: Bearer $TOKEN" | jq '.sensors[] |
  {sensor, settings_name, tank_depth_mode, tank_depth_manual_cm, tank_depth_auto_cm, max_level}'
```

For each sensor you need:

| Field | Meaning | Goes into `aquilo-sensors.json` as |
|---|---|---|
| `sensor` | the sensor's id | the key |
| `settings_name` | its name | `name` |
| `tank_depth_manual_cm`, or `tank_depth_auto_cm` when `tank_depth_mode` is `auto` | tank depth | `depth_cm` |
| `max_level` | the "full" offset | `full_cm` |

In auto mode the cloud rounds the depth it derived to whole centimetres. The
unrounded value is in the retained settings on the vendor's broker, as hundredths
of a centimetre; the rounded one is accurate to about half a centimetre of depth,
which is under one percentage point of fill. An auto depth also stops being
re-derived once the cloud is out of the path, so it freezes at this value. If
you change the tank, set `depth_cm` by hand.

**A battery curve, if you want a percentage.** The vendor's history endpoint
returns the voltage and its own percentage for every reading. Pull them once and
fit your own table (the repository's `e19` curve took the lowest voltage that
reaches each 5 % step and interpolates between):

```bash
curl -s "https://panel.aquilo.pl/api/v2/sensors/<sensor-id>/history?from=2025-01-01T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN" | jq '[.rows[] | {mv: (.battery|tonumber), pct: .battery_pct}] | unique'
```

A curve is only as good as the voltage range the history covers: a sensor that
has sat at one voltage for months gives you no slope, so leave it out.

**The last state, so the first poll is not empty.** The state service only
learns a level when a sensor next transmits, which can be hours away (the
sensors measure every few minutes but transmit in bursts, by default every seven
to eight hours, sooner when the level jumps). Seed your broker with what the
gateway shows now:

```bash
curl -s http://<gateway>/state > last-state.json
```

### Step 2: the broker

Create two users: the gateway (its id and password from step 1) and a service
account for the state service. Restrict both. A Mosquitto example:

```
# /etc/mosquitto/conf.d/aquilo.conf
listener 1883
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/aquilo.acl
allow_anonymous false
```

```
# /etc/mosquitto/aquilo.acl   (replace <id> with the gateway id)
user <id>
topic write /users/<id>/sensors/<id>/read
topic write /users/<id>/sensors/<id>/read_3
topic write /users/<id>/sensors/<id>/connection
topic write /users/<id>/sensors/<id>/log
topic write /version/czujnik_szamba/<id>
topic read  /users/<id>/#
topic read  /version/czujnik_szamba/<id>
topic read  /ping

user svc_aquilo
topic write /users/<id>/state
topic read  /users/<id>/sensors/+/read
topic read  /users/<id>/sensors/+/read_3
topic read  /users/<id>/state
```

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd <id>          # the gateway's password
sudo mosquitto_passwd    /etc/mosquitto/passwd svc_aquilo    # a new password
sudo systemctl restart mosquitto
```

The gateway may **subscribe** to its whole namespace (`/users/<id>/#`), and that
is deliberate: refusing a subscription would hand the firmware a failed SUBACK
on connect. It is also why the vendor's command channel (restart, reset,
sensor commands, parameter changes) does not need blocking: those messages come
from the vendor's broker, and with your broker in the way nothing ever publishes
them.

Seed the state (this is what the gateway and the service read on first start):

```bash
jq '. + {from: "seed"}' last-state.json | \
  mosquitto_pub -h <broker> -u svc_aquilo -P '<password>' -r -t /users/<id>/state -s
```

### Step 3: the state service

Files are in `server/`. Install:

```bash
sudo install -m 0755 server/aquilo_state_local.py server/aquilo_level.py /usr/local/bin/
sudo install -m 0644 server/aquilo-state-local.service /etc/systemd/system/
sudo install -m 0600 server/aquilo-state-local.env.example /etc/aquilo-state-local.env
sudo install -m 0644 server/aquilo-sensors.example.json /etc/aquilo-sensors.json
pip install paho-mqtt        # or your distribution's python3-paho-mqtt
```

Edit `/etc/aquilo-state-local.env` (broker, `svc_aquilo` password, the gateway id)
and `/etc/aquilo-sensors.json` (one entry per sensor, from step 1). Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now aquilo-state-local
journalctl -u aquilo-state-local -f
```

The unit runs with a dynamic user; if your distribution needs the script
readable by it, `/usr/local/bin` already is. It stores its state in
`/var/lib/aquilo-state-local/`.

The service checks itself against 166 recorded readings from the vendor's own
history (0 mismatches, worst error 0.05 percentage points, which is the vendor's
rounding). The arithmetic is:

```
lvl       = read1 / 100                                  # distance to the surface, cm
pct       = clamp((depth - lvl) / (depth - full) * 100, 0, 100)
lvlToFull = lvl - full
```

Tests: `pip install pytest paho-mqtt && pytest server`.

### Step 4: rehearse without touching the gateway

Publish a made-up reading as the gateway and watch the state appear:

```bash
mosquitto_pub -h <broker> -u <id> -P '<gateway-password>' \
  -t /users/<id>/sensors/<id>/read \
  -m '{"sensor":"<sensor-id>","read1":9000,"battery":"3600","crc":true,"amp1":1200}'
mosquitto_sub -h <broker> -u svc_aquilo -P '<password>' -t /users/<id>/state -C 1
```

Do not leave that test reading in place: the next real transmission replaces
it, but until then it is shown as the level. Re-publish your seed from step 2
afterwards if you care.

### Step 5: cut over

If you have an automation that power-cycles the gateway when its data goes
stale, switch it off or put it in maintenance first. The gateway loses its
connection for the moment between the DNS change and the new connection, and
that is exactly what such a watchdog fires on.

1. In your DNS, answer **`mqtt.aquilo.pl`** with the broker's address (A record,
   TTL 60 is convenient while you test).
2. Make sure the gateway can reach the broker on TCP 1883.
3. Power-cycle the gateway so it looks the name up again. A cold start took
   about twelve seconds to reconnect in testing.
4. Check that it arrived: your broker should list a client named
   `CieczSensor<id>`, and `http://<gateway>/state` should answer with
   `"from": "local"` once the service has published (or `"from": "seed"` until
   the first real reading, if you used the seed).

Then add the Home Assistant integration (part 1), or point an existing one at
the same gateway address.

If anything goes wrong, remove the DNS override and power-cycle the gateway.
It is back on the vendor's broker, which is what it did before.

### What you give up

Be clear about this before you cut:

- **The vendor app and web panel stop showing new readings.** The cloud no longer
  receives them. If you still want the vendor's view, relay the gateway's
  uplink topics (`read`, `connection`) to the vendor's broker from yours; the
  service does not do that for you.
- **No remote parameter changes from the vendor.** The panel's reading and
  sending intervals are set by publishing a command that your broker never
  forwards. Sensors keep the schedule they have now. If you want to change it,
  that will need the command topic, which this repository does not document.
- **No vendor firmware pushes.** The `version` topic no longer arrives.
- **Days to full and last-emptied are not computed.** The service carries them
  forward from the seed if there was one. If you want a days-left estimate,
  derive it from the level history in Home Assistant.
- **The battery percentage exists only if you give the service a curve.** The
  vendor derives it from the battery voltage alone, per hardware revision, so a
  table of `[mV, percent]` knots reproduces it. Without a curve a sensor
  publishes the raw voltage as `batMv` and no percentage at all, on purpose: a
  carried-over figure would freeze at its last cloud value and look live. A
  curve for the `e19` revision is in `server/aquilo-sensors.example.json`
  (fitted to 5150 vendor readings, 0.08 percentage points mean error, 3 worst);
  the `g1` revision had too narrow a voltage range in its history to fit one.
- **Readings arrive on the sensor's own schedule.** Local computation does not
  make a sleeping radio transmit sooner.

### Why a reading can look wrong

The gateway checks that a radio packet is intact (`crc`), not that the sensor is
looking at a liquid. A sensor lifted out of the tank or pointed at a wall still
sends a valid packet with a possible-looking distance, and no arithmetic can
tell. The **Jakość echa** entity (the echo amplitude, where the sensor has one)
is the number that does: over water it was about 1400 in testing, against 130 to
750 for a sensor that was being moved or lying on a desk. The service publishes
it and does not act on it, because a threshold would have to be derived per
installation.

## Troubleshooting

| Symptom | Look at |
|---|---|
| The gateway never appears on the broker | the DNS override (`dig mqtt.aquilo.pl` from the gateway's network), the firewall on TCP 1883, the user name and password, and that the listener is plain, not TLS |
| `/state` answers but has no sensors | the service has no state yet: seed it (step 2), or wait for a transmission |
| Entities read `unknown` | the last read was turned away and there is no earlier good one; see the raw-reading entity's `rejected` attribute |
| The level stays at the old value | the sensor has not transmitted; check **Ostatni odczyt** and the stale-data sensor |
| The service refuses to start | `AQUILO_GATEWAY_ID` unset, or `/etc/aquilo-sensors.json` unreadable (`journalctl -u aquilo-state-local`) |

## Moving from the original integration

Both can run at once, since the domains differ. To replace it, remove the
original entry first and then add this one with the same gateway address; entity
ids are built from the device and entity names, so if you keep the tank names
they come out the same, otherwise Home Assistant adds a numeric suffix and you
rename them. History does not carry over between integrations.

## Gateway `/state` reference

```json
{
  "sensors": [
    {"id": "000000", "lvl": 125.6, "pct": 34, "bat": 76,
     "lstRead": "2026-01-01T12:00:00+01:00",
     "lstEmpty": "2025-12-01T09:00:00+01:00",
     "daysLeft": 48, "name": "TANK", "lvlToFull": 121,
     "batMv": 3560, "amp1": 1357,
     "raw": {"raw": "_e19_000000_353_3559_1357_4_893_892_1_7", "read1": 3530}}
  ],
  "from": "local"
}
```

The vendor's own PDF documents only `id`, `lvl`, `pct` and `lstRead`; the rest
was captured from a live gateway.

## License

MIT
