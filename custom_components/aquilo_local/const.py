"""Constants for the Aquilo Local integration."""
from __future__ import annotations

DOMAIN = "aquilo_local"

DEFAULT_SCAN_INTERVAL_SECONDS = 300  # 5 min — gateway just echoes the last reading it has
DEFAULT_STALE_HOURS = 24
DEFAULT_OVERFLOW_PCT = 90

CONF_STALE_HOURS = "stale_hours"
CONF_OVERFLOW_PCT = "overflow_pct"
CONF_EXCLUDED_TANKS = "excluded_tanks"

ATTR_LVL = "lvl"
ATTR_PCT = "pct"
ATTR_BAT = "bat"
ATTR_LST_READ = "lstRead"
ATTR_LST_EMPTY = "lstEmpty"
ATTR_DAYS_LEFT = "daysLeft"
ATTR_LVL_TO_FULL = "lvlToFull"
ATTR_NAME = "name"
ATTR_ID = "id"
# Raw measurement data the gateway sends with every reading. The echo amplitude
# is the one number that says whether the sensor is actually seeing a liquid
# surface, so it gets its own entity; the rest travels as attributes.
ATTR_AMP1 = "amp1"
ATTR_RAW = "raw"
ATTR_REJECTED = "rejected"
