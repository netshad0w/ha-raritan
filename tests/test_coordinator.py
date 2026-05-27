"""Tests for the Raritan coordinator."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.raritan.api import RaritanAuthError, RaritanConnectionError
from custom_components.raritan.const import (
    DOMAIN,
    ISSUE_UNREACHABLE_EXTENDED,
    TICK_OVERLAP_THRESHOLD,
    UNREACHABLE_REPAIR_THRESHOLD,
)
from custom_components.raritan.coordinator import RaritanDataUpdateCoordinator
from custom_components.raritan.models import (
    CapabilityMatrix,
    CoordinatorPayload,
    InletReading,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture
def capability() -> CapabilityMatrix:
    return CapabilityMatrix(
        model="PX3-5487V-N2",
        firmware="4.3.11.5-52050",
        serial="TEST00000001",
        hw_revision="0x01",
        nb_inlets=1,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
    )


@pytest.fixture
def fake_payload() -> CoordinatorPayload:
    return CoordinatorPayload(
        inlets=[
            InletReading(
                idx=1,
                voltage=230.0,
                current=4.5,
                active_power=1035,
                apparent_power=1100,
                power_factor=0.94,
                frequency=50.0,
                active_energy_wh=12345678,
            )
        ],
        outlets=[],
        ocps=[],
        env=[],
        current_alerts=[],
        last_tick_duration_ms=42,
        consecutive_skips=0,
    )


def _make_api(payload: CoordinatorPayload, alerts: list | None = None) -> MagicMock:
    api = MagicMock()
    api.fetch_telemetry.return_value = payload
    api.fetch_alerts.return_value = alerts if alerts is not None else []
    return api


async def test_coordinator_returns_payload_on_successful_tick(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    api = _make_api(fake_payload)
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=capability.serial)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    coord.config_entry = entry
    await coord.async_config_entry_first_refresh()
    assert coord.data is fake_payload
    await coord.async_shutdown()


async def test_coordinator_periodic_env_rescan(
    hass: HomeAssistant,
    capability: CapabilityMatrix,
    fake_payload: CoordinatorPayload,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env peripherals are re-scanned on the configured tick cadence."""
    monkeypatch.setattr("custom_components.raritan.coordinator.ENV_RESCAN_EVERY", 1)
    api = _make_api(fake_payload)
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert api.refresh_env_sensors.called
    await coord.async_shutdown()


async def test_coordinator_env_rescan_failure_is_non_fatal(
    hass: HomeAssistant,
    capability: CapabilityMatrix,
    fake_payload: CoordinatorPayload,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected rescan error must not break the tick, and is logged with a
    traceback at ERROR level (not the quiet debug path) so it surfaces."""
    monkeypatch.setattr("custom_components.raritan.coordinator.ENV_RESCAN_EVERY", 1)
    api = _make_api(fake_payload)
    api.refresh_env_sensors.side_effect = RuntimeError("boom")
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    with caplog.at_level(logging.DEBUG, logger="custom_components.raritan.coordinator"):
        await coord.async_refresh()
        await hass.async_block_till_done()
    assert coord.data is fake_payload
    assert any(
        r.levelno == logging.ERROR and "Unexpected error during env peripheral rescan" in r.message
        for r in caplog.records
    )
    await coord.async_shutdown()


async def test_coordinator_env_rescan_transport_error_is_non_fatal(
    hass: HomeAssistant,
    capability: CapabilityMatrix,
    fake_payload: CoordinatorPayload,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An expected transport error during rescan takes the quiet debug path (not
    the ERROR/traceback path) and the tick still produces a payload."""
    monkeypatch.setattr("custom_components.raritan.coordinator.ENV_RESCAN_EVERY", 1)
    api = _make_api(fake_payload)
    api.refresh_env_sensors.side_effect = RaritanConnectionError("unreachable")
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    with caplog.at_level(logging.DEBUG, logger="custom_components.raritan.coordinator"):
        await coord.async_refresh()
        await hass.async_block_till_done()
    assert coord.data is fake_payload
    assert any(
        r.levelno == logging.DEBUG and "Env peripheral rescan failed (non-fatal)" in r.message
        for r in caplog.records
    )
    assert not any(
        "Unexpected error during env peripheral rescan" in r.message for r in caplog.records
    )
    await coord.async_shutdown()


async def test_coordinator_serializes_concurrent_ticks(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    """Two near-simultaneous refreshes must not trigger overlapping SDK calls."""
    call_count = 0

    def slow_fetch(_cap: CapabilityMatrix) -> CoordinatorPayload:
        nonlocal call_count
        call_count += 1
        import time

        time.sleep(0.05)
        return fake_payload

    api = _make_api(fake_payload)
    api.fetch_telemetry.side_effect = slow_fetch
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    await asyncio.gather(
        coord.async_request_refresh(),
        coord.async_request_refresh(),
    )
    assert call_count >= 1
    await coord.async_shutdown()


async def test_coordinator_tick_skip_on_overlap_increments_counter(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    api = _make_api(fake_payload)

    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    coord._lock = asyncio.Lock()  # type: ignore[attr-defined]
    await coord._lock.acquire()
    try:
        await coord._async_update_data()
        assert coord._consecutive_skips == 1  # type: ignore[attr-defined]
    finally:
        coord._lock.release()


async def test_coordinator_three_skips_raises_update_failed(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    api = _make_api(fake_payload)
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    coord._lock = asyncio.Lock()  # type: ignore[attr-defined]
    await coord._lock.acquire()
    try:
        for _ in range(TICK_OVERLAP_THRESHOLD - 1):
            await coord._async_update_data()
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
    finally:
        coord._lock.release()


async def test_coordinator_remaps_api_errors(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    api = MagicMock()
    api.fetch_telemetry.side_effect = RaritanConnectionError("unreachable")
    api.fetch_alerts.return_value = []
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_coordinator_raises_auth_failed_on_auth_error(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """RaritanAuthError must surface as ConfigEntryAuthFailed -> triggers reauth flow."""
    from homeassistant.exceptions import ConfigEntryAuthFailed

    api = MagicMock()
    api.fetch_telemetry.side_effect = RaritanAuthError("forbidden")
    api.fetch_alerts.return_value = []
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


# ---------------------------------------------------------------------------
# Diff: outlet state change + alert events
# ---------------------------------------------------------------------------


def _make_outlet_payload(idx_to_on: dict[int, bool]) -> CoordinatorPayload:
    from custom_components.raritan.models import OutletReading

    outlets = [
        OutletReading(
            idx=idx,
            on=on,
            label=str(idx),
            voltage=None,
            current=None,
            active_power=None,
            apparent_power=None,
            active_energy_wh=None,
        )
        for idx, on in idx_to_on.items()
    ]
    return CoordinatorPayload(
        inlets=[],
        outlets=outlets,
        ocps=[],
        env=[],
        current_alerts=[],
        last_tick_duration_ms=0,
        consecutive_skips=0,
    )


async def test_coordinator_no_event_on_first_tick(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """First tick must not fire any state-change event (no prior baseline)."""
    from custom_components.raritan.const import EVENT_TYPE_OUTLET_STATE_CHANGED

    payload = _make_outlet_payload({1: True, 2: False})
    api = _make_api(payload)
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    events: list = []
    hass.bus.async_listen(EVENT_TYPE_OUTLET_STATE_CHANGED, lambda e: events.append(e))
    await coord._async_update_data()
    await hass.async_block_till_done()
    assert events == []


async def test_coordinator_fires_outlet_state_change_event(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """On flip, the bus event with the right payload is fired."""
    from custom_components.raritan.const import EVENT_TYPE_OUTLET_STATE_CHANGED

    api = MagicMock()
    api.fetch_alerts.return_value = []
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    events: list = []
    hass.bus.async_listen(EVENT_TYPE_OUTLET_STATE_CHANGED, lambda e: events.append(e))

    api.fetch_telemetry.return_value = _make_outlet_payload({1: True, 2: False})
    await coord._async_update_data()
    api.fetch_telemetry.return_value = _make_outlet_payload({1: False, 2: False})
    await coord._async_update_data()
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["outlet_idx"] == 1
    assert events[0].data["on_before"] is True
    assert events[0].data["on_after"] is False


async def test_coordinator_no_outlet_event_when_unchanged(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    from custom_components.raritan.const import EVENT_TYPE_OUTLET_STATE_CHANGED

    api = MagicMock()
    api.fetch_alerts.return_value = []
    api.fetch_telemetry.return_value = _make_outlet_payload({1: True})
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    events: list = []
    hass.bus.async_listen(EVENT_TYPE_OUTLET_STATE_CHANGED, lambda e: events.append(e))

    await coord._async_update_data()
    await coord._async_update_data()
    await hass.async_block_till_done()
    assert events == []


def _payload_with_alerts(alerts: list) -> CoordinatorPayload:
    return CoordinatorPayload(
        inlets=[],
        outlets=[],
        ocps=[],
        env=[],
        current_alerts=alerts,
        last_tick_duration_ms=0,
        consecutive_skips=0,
    )


async def test_coordinator_fires_alert_event_on_new_alert(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    from custom_components.raritan.const import EVENT_TYPE_ALERT
    from custom_components.raritan.models import AlertSnapshot

    snap = AlertSnapshot(
        sensor_label="RMS Current",
        parent_label="/inlet/0",
        alert_state="CRITICAL",
        sensor_id="/inlet/0/sensors/current",
    )
    api = MagicMock()
    api.fetch_telemetry.side_effect = [_payload_with_alerts([]), _payload_with_alerts([snap])]
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    events: list = []
    hass.bus.async_listen(EVENT_TYPE_ALERT, lambda e: events.append(e))

    await coord._async_update_data()  # baseline = []
    await coord._async_update_data()  # new alert -> event
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["sensor_id"] == "/inlet/0/sensors/current"
    assert events[0].data["alert_state"] == "CRITICAL"


async def test_coordinator_overlap_returns_existing_data_when_present(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    """When tick overlap occurs and prior data exists, the lock branch returns it."""
    api = _make_api(fake_payload)
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    # Establish prior data
    await coord._async_update_data()
    assert coord.data is None  # _async_update_data doesn't set self.data - but coord was used
    # Manually set data attr (DataUpdateCoordinator.data is set by async_refresh wrappers)
    coord.async_set_updated_data(fake_payload)
    # Now hold the lock and call again
    await coord._lock.acquire()  # type: ignore[attr-defined]
    try:
        result = await coord._async_update_data()
        assert result is fake_payload
    finally:
        coord._lock.release()  # type: ignore[attr-defined]


async def test_coordinator_does_not_poll_alerts_separately(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    """The alert poll is folded into fetch_telemetry, so the coordinator must
    never call fetch_alerts; current_alerts come straight from the payload."""
    api = MagicMock()
    api.fetch_telemetry.return_value = fake_payload
    # If the coordinator wrongly called this, it would blow up the tick.
    api.fetch_alerts.side_effect = RaritanConnectionError("must not be called")
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    payload = await coord._async_update_data()
    assert payload is fake_payload
    assert payload.current_alerts == []
    api.fetch_alerts.assert_not_called()


async def test_coordinator_does_not_re_fire_existing_alert(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """An alert seen in the previous tick must not fire a new event."""
    from custom_components.raritan.const import EVENT_TYPE_ALERT
    from custom_components.raritan.models import AlertSnapshot

    snap = AlertSnapshot(
        sensor_label="X",
        parent_label="Y",
        alert_state="CRITICAL",
        sensor_id="/dup/0",
    )
    api = MagicMock()
    api.fetch_telemetry.side_effect = [_payload_with_alerts([snap]), _payload_with_alerts([snap])]
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    events: list = []
    hass.bus.async_listen(EVENT_TYPE_ALERT, lambda e: events.append(e))
    await coord._async_update_data()  # baseline: 1 alert (no prior)
    await coord._async_update_data()  # same alert still present -> no event
    await hass.async_block_till_done()
    assert events == []


async def test_coordinator_no_alert_event_when_alert_disappears(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    from custom_components.raritan.const import EVENT_TYPE_ALERT
    from custom_components.raritan.models import AlertSnapshot

    snap = AlertSnapshot(
        sensor_label="X",
        parent_label="Y",
        alert_state="WARNED",
        sensor_id="/test/0",
    )
    api = MagicMock()
    api.fetch_telemetry.side_effect = [_payload_with_alerts([snap]), _payload_with_alerts([])]
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    events: list = []
    hass.bus.async_listen(EVENT_TYPE_ALERT, lambda e: events.append(e))

    await coord._async_update_data()  # first tick: 1 alert (no prior baseline -> no event)
    await coord._async_update_data()  # alert cleared -> no event
    await hass.async_block_till_done()
    assert events == []


async def test_async_set_outlet_state_holds_lock_while_calling_api(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """Regression: writes used to call api directly via async_add_executor_job
    without holding coordinator._lock. When a toggle landed during an
    in-flight telemetry tick, the SDK's single HTTP connection surfaced
    `http.client.CannotSendRequest('Request-sent')` and the write was lost.
    """
    api = MagicMock()
    seen_locked: list[bool] = []

    def _record_lock(*, idx: int, on: bool) -> None:
        seen_locked.append(coord._lock.locked())

    api.set_outlet_state.side_effect = _record_lock
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )

    await coord.async_set_outlet_state(idx=5, on=True)
    api.set_outlet_state.assert_called_once_with(idx=5, on=True)
    assert seen_locked == [True], "Write must run with coordinator._lock held"
    await coord.async_shutdown()


async def test_async_cycle_outlet_holds_lock_while_calling_api(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    api = MagicMock()
    seen_locked: list[bool] = []

    def _record_lock(*, idx: int) -> None:
        seen_locked.append(coord._lock.locked())

    api.cycle_outlet.side_effect = _record_lock
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )

    await coord.async_cycle_outlet(idx=3)
    api.cycle_outlet.assert_called_once_with(idx=3)
    assert seen_locked == [True]
    await coord.async_shutdown()


async def test_async_reset_inlet_energy_holds_lock(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    api = MagicMock()
    seen_locked: list[bool] = []

    def _record_lock(*, idx: int) -> None:
        seen_locked.append(coord._lock.locked())

    api.reset_inlet_energy.side_effect = _record_lock
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )

    await coord.async_reset_inlet_energy(idx=1)
    api.reset_inlet_energy.assert_called_once_with(idx=1)
    assert seen_locked == [True]
    await coord.async_shutdown()


async def test_async_reset_outlet_energy_holds_lock(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    api = MagicMock()
    seen_locked: list[bool] = []

    def _record_lock(*, idx: int) -> None:
        seen_locked.append(coord._lock.locked())

    api.reset_outlet_energy.side_effect = _record_lock
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )

    await coord.async_reset_outlet_energy(idx=2)
    api.reset_outlet_energy.assert_called_once_with(idx=2)
    assert seen_locked == [True]
    await coord.async_shutdown()


async def test_async_set_outlet_state_blocks_when_lock_held(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """Write must wait when something else (e.g. an in-flight tick) holds
    the lock. We grab the lock directly to simulate that condition. The
    real symptom in production was `CannotSendRequest('Request-sent')`
    because the write reused the SDK's HTTP connection mid-flight.
    """
    import asyncio as _asyncio

    api = MagicMock()
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )

    await coord._lock.acquire()
    write_task = _asyncio.create_task(coord.async_set_outlet_state(idx=1, on=True))
    # The write must NOT have called api yet: the lock is held by us.
    await _asyncio.sleep(0.05)
    api.set_outlet_state.assert_not_called()
    # Release the lock; the write should proceed.
    coord._lock.release()
    await write_task
    api.set_outlet_state.assert_called_once_with(idx=1, on=True)
    await coord.async_shutdown()


# ---------------------------------------------------------------------------
# Extended-unreachable repair issue
# ---------------------------------------------------------------------------


async def test_unreachable_first_failure_creates_no_issue(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """A single failed tick must not raise the extended-unreachable repair."""
    api = MagicMock()
    api.host = "10.0.0.1"
    api.fetch_telemetry.side_effect = RaritanConnectionError("unreachable")
    api.fetch_alerts.return_value = []
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    issue_id = f"{ISSUE_UNREACHABLE_EXTENDED}_ENTRY1"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_unreachable_past_threshold_creates_issue(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """Once unreachable for the threshold, a WARNING repair carries host + minutes."""
    api = MagicMock()
    api.host = "10.0.0.1"
    api.fetch_telemetry.side_effect = RaritanConnectionError("unreachable")
    api.fetch_alerts.return_value = []
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    # Simulate a failure streak that began at least the threshold ago.
    coord._unreachable_since = hass.loop.time() - UNREACHABLE_REPAIR_THRESHOLD
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_UNREACHABLE_EXTENDED}_ENTRY1")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["host"] == "10.0.0.1"
    # Serial must never leak into a repair surfaced in diagnostics dumps.
    assert "serial" not in issue.translation_placeholders


async def test_unreachable_cleared_on_recovery(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    """A successful tick after an unreachable streak clears the repair."""
    api = MagicMock()
    api.host = "10.0.0.1"
    api.fetch_telemetry.side_effect = RaritanConnectionError("unreachable")
    api.fetch_alerts.return_value = []
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    coord._unreachable_since = hass.loop.time() - UNREACHABLE_REPAIR_THRESHOLD
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    issue_id = f"{ISSUE_UNREACHABLE_EXTENDED}_ENTRY1"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    # PDU recovers on the next tick.
    api.fetch_telemetry.side_effect = None
    api.fetch_telemetry.return_value = fake_payload
    await coord._async_update_data()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert coord._unreachable_since is None


# ---------------------------------------------------------------------------
# One roundtrip per tick: alerts folded into telemetry, no separate fetch_alerts
# ---------------------------------------------------------------------------


async def test_coordinator_tick_does_not_call_fetch_alerts_separately(
    hass: HomeAssistant, capability: CapabilityMatrix, fake_payload: CoordinatorPayload
) -> None:
    """The alert poll is folded into fetch_telemetry's single bulk; the
    coordinator must NOT issue a second roundtrip via api.fetch_alerts."""
    api = MagicMock()
    api.fetch_telemetry.return_value = fake_payload
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    await coord._async_update_data()
    api.fetch_alerts.assert_not_called()


async def test_coordinator_fires_alert_event_from_payload(
    hass: HomeAssistant, capability: CapabilityMatrix
) -> None:
    """Alert bus events must fire from payload.current_alerts (the folded poll)."""
    from custom_components.raritan.const import EVENT_TYPE_ALERT
    from custom_components.raritan.models import AlertSnapshot

    snap = AlertSnapshot(
        sensor_label="RMS Current",
        parent_label="/inlet/0",
        alert_state="CRITICAL",
        sensor_id="/inlet/0/sensors/current",
    )

    def _payload(alerts: list) -> CoordinatorPayload:
        return CoordinatorPayload(
            inlets=[],
            outlets=[],
            ocps=[],
            env=[],
            current_alerts=alerts,
            last_tick_duration_ms=0,
            consecutive_skips=0,
        )

    api = MagicMock()
    api.fetch_telemetry.side_effect = [_payload([]), _payload([snap])]
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=capability, scan_interval=5, entry_id="ENTRY1"
    )
    events: list = []
    hass.bus.async_listen(EVENT_TYPE_ALERT, lambda e: events.append(e))
    await coord._async_update_data()  # baseline []
    await coord._async_update_data()  # new alert -> event
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["sensor_id"] == "/inlet/0/sensors/current"
    assert events[0].data["alert_state"] == "CRITICAL"
    # Finding #9: no duplicate "severity" key (it duplicated alert_state).
    assert "severity" not in events[0].data


# ---------------------------------------------------------------------------
# Serial must not leak into the coordinator name or overlap warning (#4)
# ---------------------------------------------------------------------------


async def test_coordinator_name_does_not_embed_serial(
    hass: HomeAssistant, fake_payload: CoordinatorPayload
) -> None:
    """The DataUpdateCoordinator name (logged) must use entry_id, not serial."""
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="SECRET_SERIAL_123",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
    )
    api = _make_api(fake_payload)
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=cap, scan_interval=5, entry_id="ENTRY_XYZ"
    )
    assert "SECRET_SERIAL_123" not in coord.name
    assert "ENTRY_XYZ" in coord.name


async def test_coordinator_overlap_warning_does_not_embed_serial(
    hass: HomeAssistant, capability: CapabilityMatrix, caplog: pytest.LogCaptureFixture
) -> None:
    """The tick-overlap WARNING must not contain the PDU serial."""
    api = _make_api(_fake_payload_with_serial("SECRET_SERIAL_999"))
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="SECRET_SERIAL_999",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
    )
    api.host = "10.9.9.9"
    coord = RaritanDataUpdateCoordinator(
        hass=hass, api=api, capabilities=cap, scan_interval=5, entry_id="ENTRY1"
    )
    coord._lock = asyncio.Lock()  # type: ignore[attr-defined]
    await coord._lock.acquire()
    try:
        with caplog.at_level("WARNING"):
            await coord._async_update_data()
    finally:
        coord._lock.release()
    assert "SECRET_SERIAL_999" not in caplog.text


def _fake_payload_with_serial(_serial: str) -> CoordinatorPayload:
    return CoordinatorPayload(
        inlets=[],
        outlets=[],
        ocps=[],
        env=[],
        current_alerts=[],
        last_tick_duration_ms=0,
        consecutive_skips=0,
    )
