"""Tests for event entities."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant import config_entries

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def _setup(hass: HomeAssistant) -> None:
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


async def test_alert_event_entity_created(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    await _setup(hass)
    events = [s for s in hass.states.async_all() if s.entity_id.startswith("event.")]
    assert any(s.entity_id.endswith("_alert") for s in events) or any(
        "alert" in s.entity_id for s in events
    )


async def test_outlet_state_change_event_entities_created_with_outlets(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    events = [s for s in hass.states.async_all() if s.entity_id.startswith("event.")]
    state_change_events = [s for s in events if "state_change" in s.entity_id]
    assert len(state_change_events) == 2  # 2 outlets in fixture


async def test_alert_event_initial_state_no_event(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """First tick must not trigger any event_type: no baseline."""
    await _setup(hass)
    events = [
        s
        for s in hass.states.async_all()
        if s.entity_id.startswith("event.") and "alert" in s.entity_id
    ]
    assert len(events) >= 1
    # No alerts in fixture -> state is "unknown" (no event ever triggered).
    for e in events:
        assert e.state in ("unknown",)


async def test_outlet_state_change_initial_state_no_event(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """First tick must not fire turned_on/turned_off: no baseline."""
    await _setup(hass)
    events = [
        s
        for s in hass.states.async_all()
        if s.entity_id.startswith("event.") and "state_change" in s.entity_id
    ]
    # No prior, so state is "unknown" since no flip has been observed.
    for e in events:
        assert e.state in ("unknown",)


async def test_outlet_state_change_fires_after_flip(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """After we flip the underlying mock state and refresh, entity records the event."""
    from custom_components.raritan.const import DOMAIN as _DOMAIN

    await _setup(hass)
    # Flip outlet 2 from off to on
    from raritan.rpc import pdumodel

    pdu = mock_raritan_with_outlets
    outlet_2 = pdu.getOutlets.return_value[1]
    state = MagicMock()
    state.available = True
    state.powerState = pdumodel.Outlet.PowerState.PS_ON
    outlet_2.getState.return_value = state

    entries = hass.config_entries.async_entries(_DOMAIN)
    coord = entries[0].runtime_data.coordinator
    await coord.async_request_refresh()
    await hass.async_block_till_done()

    state_change_states = [
        s
        for s in hass.states.async_all()
        if s.entity_id.startswith("event.") and "state_change" in s.entity_id
    ]
    triggered = [s for s in state_change_states if s.attributes.get("event_type") == "turned_on"]
    assert len(triggered) >= 1


async def test_alert_event_fires_when_alert_appears(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Inject an alert via the AlertedSensorManager mock and trigger a refresh."""
    from custom_components.raritan.const import DOMAIN as _DOMAIN

    await _setup(hass)

    sd = MagicMock()
    sensor = MagicMock()
    sensor.target = "/model/pdu/0/inlet/0/sensors/current"
    md = MagicMock()
    md.name = "RMS Current"
    sensor.getMetaData.return_value = md
    parent = MagicMock()
    parent.target = "/model/pdu/0/inlet/0"
    state = MagicMock()
    state.name = "CRITICAL"
    sd.sensor = sensor
    sd.parent = parent
    sd.alertState = state
    mock_raritan.getAlertedSensorManager.return_value.getAlertedSensors.return_value = [sd]

    entries = hass.config_entries.async_entries(_DOMAIN)
    coord = entries[0].runtime_data.coordinator
    await coord.async_refresh()
    await hass.async_block_till_done()

    states = [
        s
        for s in hass.states.async_all()
        if s.entity_id.startswith("event.") and "alert" in s.entity_id
    ]
    triggered = [s for s in states if s.attributes.get("event_type") == "alert_active"]
    assert len(triggered) >= 1


async def test_alert_event_fires_cleared_when_alert_disappears(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """An alert that appears then disappears emits an alert_cleared event."""
    from custom_components.raritan.const import DOMAIN as _DOMAIN

    await _setup(hass)

    sd = MagicMock()
    sensor = MagicMock()
    sensor.target = "/model/pdu/0/inlet/0/sensors/current"
    md = MagicMock()
    md.name = "RMS Current"
    sensor.getMetaData.return_value = md
    parent = MagicMock()
    parent.target = "/model/pdu/0/inlet/0"
    state = MagicMock()
    state.name = "CRITICAL"
    sd.sensor = sensor
    sd.parent = parent
    sd.alertState = state
    mgr = mock_raritan.getAlertedSensorManager.return_value
    mgr.getAlertedSensors.return_value = [sd]

    coord = hass.config_entries.async_entries(_DOMAIN)[0].runtime_data.coordinator
    await coord.async_refresh()
    await hass.async_block_till_done()

    # Now clear the alert and refresh again.
    mgr.getAlertedSensors.return_value = []
    await coord.async_refresh()
    await hass.async_block_till_done()

    states = [
        s
        for s in hass.states.async_all()
        if s.entity_id.startswith("event.") and "alert" in s.entity_id
    ]
    cleared = [s for s in states if s.attributes.get("event_type") == "alert_cleared"]
    assert len(cleared) >= 1
