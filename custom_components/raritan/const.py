"""Constants for the Raritan PDU integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "raritan"

# Config keys
CONF_HOST: Final = "host"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_VERIFY_TLS: Final = "verify_tls"
CONF_CA_BUNDLE: Final = "ca_bundle"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# Defaults
DEFAULT_SCAN_INTERVAL: Final = 5  # seconds
DEFAULT_VERIFY_TLS: Final = True
MIN_SCAN_INTERVAL: Final = 2
MAX_SCAN_INTERVAL: Final = 300

# Firmware support
MIN_FIRMWARE_VERSION: Final = (4, 0, 10)

# Coordinator behavior
TICK_OVERLAP_THRESHOLD: Final = 3  # consecutive skips before UpdateFailed
UNREACHABLE_REPAIR_THRESHOLD: Final = 30 * 60  # seconds before "extended unreachable" repair
# Ticks between hot-plug peripheral re-scans. The peripheral-slot walk is a
# heavy call (~17 s on a fully-populated 24-outlet PX3, measured E2E), so it
# runs infrequently: detecting a newly attached SmartSensor within a few
# minutes is plenty, and it keeps the slow tick off the common path.
ENV_RESCAN_EVERY: Final = 60

# Issue IDs (templated by serial)
ISSUE_TLS_DISABLED: Final = "tls_verification_disabled"
ISSUE_FIRMWARE_TOO_OLD: Final = "firmware_below_minimum"
ISSUE_UNREACHABLE_EXTENDED: Final = "pdu_unreachable_extended"

# Bus event types fired by the coordinator
EVENT_TYPE_ALERT: Final = "raritan_alert"
EVENT_TYPE_OUTLET_STATE_CHANGED: Final = "raritan_outlet_state_changed"
