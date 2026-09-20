"""Tests for event entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from homeassistant import config_entries

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
    TRANSIENT_FAILURE_GRACE,
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


def _alerted_sensor(target: str = "/model/pdu/0/inlet/0/sensors/current") -> MagicMock:
    """Build an AlertedSensorManager entry the api layer can map to a snapshot."""
    sd = MagicMock()
    sensor = MagicMock()
    sensor.target = target
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
    return sd


async def _fail_past_the_grace(hass: HomeAssistant, coordinator: Any) -> None:
    """Poll twice with the streak aged past the grace, so the failure surfaces."""
    await coordinator.async_refresh()
    coordinator._unreachable_since = hass.loop.time() - TRANSIENT_FAILURE_GRACE
    await coordinator.async_refresh()
    await hass.async_block_till_done()


def _event_states(hass: HomeAssistant, needle: str) -> dict[str, str]:
    return {
        s.entity_id: s.state
        for s in hass.states.async_all()
        if s.entity_id.startswith("event.") and needle in s.entity_id
    }


async def test_event_entities_stay_available_when_a_poll_fails(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """A failed poll must leave the event entities' state and availability alone.

    Following the coordinator makes them flip to unavailable and back, and the
    flip back re-renders the retained timestamp, which a state trigger cannot
    tell apart from a fresh event.
    """
    from custom_components.raritan.api import RaritanConnectionError

    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    before = _event_states(hass, "")
    assert before

    coordinator = entry.runtime_data.coordinator
    entry.runtime_data.api.fetch_telemetry = MagicMock(
        side_effect=RaritanConnectionError("unreachable")
    )
    await _fail_past_the_grace(hass, coordinator)

    assert coordinator.last_update_success is False
    assert _event_states(hass, "") == before


async def test_sensors_still_go_unavailable_when_a_poll_fails(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """The availability override is scoped to event: a stale reading is unknown."""
    from custom_components.raritan.api import RaritanConnectionError

    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    entry.runtime_data.api.fetch_telemetry = MagicMock(
        side_effect=RaritanConnectionError("unreachable")
    )
    await _fail_past_the_grace(hass, entry.runtime_data.coordinator)

    sensors = [s for s in hass.states.async_all() if s.entity_id.startswith("sensor.")]
    assert sensors
    assert all(s.state == "unavailable" for s in sensors)


async def test_alert_raised_while_down_surfaces_after_a_reload(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """An alert that appears while HA is down must not be folded into the baseline."""
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    mgr = mock_raritan.getAlertedSensorManager.return_value

    mgr.getAlertedSensors.return_value = [_alerted_sensor()]
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    states = _event_states(hass, "alert")
    assert states
    entity_id = next(iter(states))
    assert hass.states.get(entity_id).attributes.get("event_type") == "alert_active"


async def test_alert_cleared_while_down_surfaces_after_a_reload(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """An alert that clears while HA is down must still produce alert_cleared."""
    mgr = mock_raritan.getAlertedSensorManager.return_value
    mgr.getAlertedSensors.return_value = [_alerted_sensor()]
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    mgr.getAlertedSensors.return_value = []
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = next(iter(_event_states(hass, "alert")))
    assert hass.states.get(entity_id).attributes.get("event_type") == "alert_cleared"


async def test_alert_that_came_and_went_while_down_is_not_reported(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """The restored baseline reports what changed between two known points.

    An alert that both appeared and cleared during the outage leaves those
    two points identical, so nothing fires. That suppression is deliberate:
    replaying an alert the user can no longer act on would be noise.
    """
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = next(iter(_event_states(hass, "alert")))
    assert hass.states.get(entity_id).state == "unknown"


async def test_outlet_flipped_while_down_surfaces_after_a_reload(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """An outlet switched while HA is down must produce its state-change event."""
    from raritan.rpc import pdumodel

    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    outlet_2 = mock_raritan_with_outlets.getOutlets.return_value[1]
    state = MagicMock()
    state.available = True
    state.powerState = pdumodel.Outlet.PowerState.PS_ON
    outlet_2.getState.return_value = state

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    triggered = [
        s
        for s in hass.states.async_all()
        if "state_change" in s.entity_id and s.attributes.get("event_type") == "turned_on"
    ]
    assert len(triggered) == 1
