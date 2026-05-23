"""Diagnostics dump for Raritan PDU integration."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import entity_registry as er

from .const import CONF_CA_BUNDLE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import RaritanConfigEntry

# Anything in this set is replaced with "**REDACTED**" before the dump leaves
# the integration. Default-deny posture: anything that uniquely identifies the
# user, their network, or their hardware is redacted so a diagnostics file is
# safe to paste into a public GitHub issue.
REDACTED_KEYS = {
    # Credentials
    CONF_PASSWORD,
    CONF_USERNAME,
    # Network identity
    CONF_HOST,
    "host",  # in case future capability fields surface it
    "ip",
    "ip_address",
    # Filesystem path to a user-supplied CA bundle can encode hostnames or
    # internal directory structure, so redact it like any other identifier.
    CONF_CA_BUNDLE,
    # Hardware identity
    "serial",
    "serialNumber",
    "serial_number",
    "mac",
    "macAddress",
    "mac_address",
    "hw_revision",
    "hwRevision",
    # Attached SmartSensor/SmartLock peripheral IDs embed the peripheral's own
    # serial, a unique hardware identifier, so redact them like the PDU serial.
    "env_sensor_ids",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RaritanConfigEntry
) -> dict[str, Any]:
    """Return a redacted diagnostics dump for a Raritan config entry."""
    runtime = entry.runtime_data
    coord = runtime.coordinator

    # Entity registry breakdown by domain, useful for triage.
    registry = er.async_get(hass)
    entity_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    entities_by_domain = Counter(e.domain for e in entity_entries)

    # OCP + env counts from the latest tick.
    if coord.data is not None:
        ocps_count = len(coord.data.ocps)
        env_count = len(coord.data.env)
    else:
        ocps_count = 0
        env_count = 0

    # Last seen alerts (the previous tick's snapshot).
    last_5_alerts = [asdict(a) for a in coord.previous_alerts[-5:]]

    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), REDACTED_KEYS),
            "options": dict(entry.options),
        },
        "capabilities": async_redact_data(asdict(runtime.capabilities), REDACTED_KEYS),
        "coordinator": {
            "last_update_success": coord.last_update_success,
            "update_interval": str(coord.update_interval),
            "last_tick_duration_ms": (coord.data.last_tick_duration_ms if coord.data else None),
            "consecutive_skips": (coord.data.consecutive_skips if coord.data else 0),
            "ocps_count": ocps_count,
            "env_count": env_count,
        },
        "entities_by_domain": dict(entities_by_domain),
        "last_5_alerts": async_redact_data(last_5_alerts, REDACTED_KEYS),
    }
