"""Tests for diagnostics dump."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant import config_entries

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)
from custom_components.raritan.diagnostics import async_get_config_entry_diagnostics

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from homeassistant.core import HomeAssistant


async def test_diagnostics_redacts_sensitive_fields(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Anything that uniquely identifies user, network, or hardware is redacted.

    A diagnostics dump must be safe to paste into a public GitHub issue
    without leaking credentials, internal hostnames, or hardware serials.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "pdu.lab.example.internal",
            CONF_USERNAME: "home-assistant",
            CONF_PASSWORD: "supersecret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    dump = await async_get_config_entry_diagnostics(hass, entry)

    raw = str(dump)
    # Credentials never appear in plaintext anywhere in the dump.
    assert "supersecret" not in raw
    assert "home-assistant" not in raw
    # Internal hostname leaks the user's network, so it is redacted.
    assert "pdu.lab.example.internal" not in raw
    # Hardware serial uniquely identifies the user's PDU, so it is redacted.
    assert "TEST00000001" not in raw

    assert dump["entry"]["data"]["password"] == "**REDACTED**"
    assert dump["entry"]["data"]["host"] == "**REDACTED**"
    assert dump["entry"]["data"]["username"] == "**REDACTED**"
    # Model/firmware are NOT secret; they're the whole point of the dump.
    assert dump["capabilities"]["model"] == "PX3-5487V-N2"
    assert dump["capabilities"]["firmware"] == "4.3.11.5-52050"
    # Serial + hw_revision identify the device -> redacted.
    assert dump["capabilities"]["serial"] == "**REDACTED**"
    assert dump["capabilities"]["hw_revision"] == "**REDACTED**"


async def test_diagnostics_redacts_alert_sensor_id(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """The per-alert sensor_id is an internal RID that can embed a peripheral
    serial, so it is redacted like every other hardware identifier (default-deny).
    """
    from custom_components.raritan.models import AlertSnapshot

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    entry.runtime_data.coordinator._previous_alerts = [
        AlertSnapshot(
            sensor_label="Temperature",
            parent_label="Peripheral 1",
            alert_state="CRITICAL",
            sensor_id="/model/peripheraldevicemanager/PERIPHSERIAL42/sensors/temp",
        )
    ]
    dump = await async_get_config_entry_diagnostics(hass, entry)

    assert "PERIPHSERIAL42" not in str(dump)
    assert dump["last_5_alerts"][0]["sensor_id"] == "**REDACTED**"
    # The human-readable labels stay (they carry no hardware identity).
    assert dump["last_5_alerts"][0]["sensor_label"] == "Temperature"


async def test_diagnostics_includes_entity_breakdown_and_counts(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Diagnostics surface entity counts by domain plus OCP/env/alert counts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    dump = await async_get_config_entry_diagnostics(hass, entry)

    assert "entities_by_domain" in dump
    assert isinstance(dump["entities_by_domain"], dict)
    # Inlet sensors at minimum
    assert dump["entities_by_domain"].get("sensor", 0) >= 1
    # 6 OCPs in the snapshot -> binary_sensor count >= 6
    assert dump["entities_by_domain"].get("binary_sensor", 0) >= 6

    assert "ocps_count" in dump["coordinator"]
    assert dump["coordinator"]["ocps_count"] == 6
    assert dump["coordinator"]["env_count"] == 0
    assert "last_5_alerts" in dump
    assert dump["last_5_alerts"] == []
