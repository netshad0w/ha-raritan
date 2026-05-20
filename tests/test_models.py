"""Tests for raritan dataclasses."""

from __future__ import annotations

import pytest

from custom_components.raritan.models import (
    CapabilityMatrix,
    InletReading,
    RaritanRuntimeData,
)


def test_capability_matrix_is_frozen() -> None:
    cap = CapabilityMatrix(
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
        has_alerts_engine=True,
    )
    with pytest.raises(AttributeError):
        cap.serial = "OTHER"  # type: ignore[misc]


def test_capability_matrix_firmware_tuple() -> None:
    cap = CapabilityMatrix(
        model="PX3-5487V-N2",
        firmware="4.3.11.5-52050",
        serial="TEST00000001",
        hw_revision=None,
        nb_inlets=1,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
        has_alerts_engine=True,
    )
    assert cap.firmware_tuple == (4, 3, 11)


def test_capability_matrix_firmware_tuple_invalid_returns_zero() -> None:
    cap = CapabilityMatrix(
        model="X",
        firmware="garbage",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
        has_alerts_engine=False,
    )
    assert cap.firmware_tuple == (0, 0, 0)


def test_inlet_reading_holds_all_metrics() -> None:
    reading = InletReading(
        idx=1,
        voltage=230.1,
        current=4.500,
        active_power=1035,
        apparent_power=1100,
        power_factor=0.94,
        frequency=50.0,
        active_energy_wh=12345678,
    )
    assert reading.idx == 1
    assert reading.active_energy_wh == 12345678


def test_runtime_data_holds_api_capabilities_coordinator() -> None:
    api = object()
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
        has_alerts_engine=False,
    )
    coord = object()
    rt = RaritanRuntimeData(api=api, capabilities=cap, coordinator=coord)  # type: ignore[arg-type]
    assert rt.api is api
    assert rt.capabilities is cap
    assert rt.coordinator is coord


def test_capability_matrix_slots_prevents_new_attributes() -> None:
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
        has_alerts_engine=False,
    )
    with pytest.raises((AttributeError, TypeError)):
        cap.bogus = "x"  # type: ignore[attr-defined]


def test_inlet_reading_slots_prevents_new_attributes() -> None:
    reading = InletReading(
        idx=1,
        voltage=None,
        current=None,
        active_power=None,
        apparent_power=None,
        power_factor=None,
        frequency=None,
        active_energy_wh=None,
    )
    with pytest.raises(AttributeError):
        reading.bogus = "x"  # type: ignore[attr-defined]


def test_coordinator_payload_holds_inlets_and_timing() -> None:
    from custom_components.raritan.models import CoordinatorPayload

    payload = CoordinatorPayload(
        inlets=[],
        outlets=[],
        ocps=[],
        env=[],
        current_alerts=[],
        last_tick_duration_ms=42,
        consecutive_skips=0,
    )
    assert payload.inlets == []
    assert payload.last_tick_duration_ms == 42
    assert payload.consecutive_skips == 0


def test_coordinator_payload_slots_prevents_new_attributes() -> None:
    from custom_components.raritan.models import CoordinatorPayload

    payload = CoordinatorPayload(
        inlets=[],
        outlets=[],
        ocps=[],
        env=[],
        current_alerts=[],
        last_tick_duration_ms=0,
        consecutive_skips=0,
    )
    with pytest.raises(AttributeError):
        payload.bogus = "x"  # type: ignore[attr-defined]


def test_outlet_reading_holds_state_and_metrics() -> None:
    from custom_components.raritan.models import OutletReading

    reading = OutletReading(
        idx=1,
        on=True,
        label="1",
        voltage=230.0,
        current=0.5,
        active_power=115.0,
        apparent_power=120.0,
        active_energy_wh=12345,
    )
    assert reading.idx == 1
    assert reading.on is True
    assert reading.label == "1"
    assert reading.voltage == 230.0
    assert reading.active_energy_wh == 12345


def test_outlet_reading_slots_prevents_new_attributes() -> None:
    from custom_components.raritan.models import OutletReading

    r = OutletReading(
        idx=1,
        on=False,
        label="x",
        voltage=None,
        current=None,
        active_power=None,
        apparent_power=None,
        active_energy_wh=None,
    )
    with pytest.raises(AttributeError):
        r.bogus = 1  # type: ignore[attr-defined]


def test_coordinator_payload_includes_outlets_list() -> None:
    from custom_components.raritan.models import CoordinatorPayload

    p = CoordinatorPayload(
        inlets=[],
        outlets=[],
        ocps=[],
        env=[],
        current_alerts=[],
        last_tick_duration_ms=0,
        consecutive_skips=0,
    )
    assert p.outlets == []


def test_alert_snapshot_holds_fields() -> None:
    from custom_components.raritan.models import AlertSnapshot

    snap = AlertSnapshot(
        sensor_label="RMS Current",
        parent_label="Inlet I1",
        alert_state="CRITICAL",
        sensor_id="/model/pdu/0/inlet/0/sensors/current",
    )
    assert snap.sensor_label == "RMS Current"
    assert snap.parent_label == "Inlet I1"
    assert snap.alert_state == "CRITICAL"
    assert snap.sensor_id == "/model/pdu/0/inlet/0/sensors/current"


def test_alert_snapshot_is_frozen() -> None:
    from custom_components.raritan.models import AlertSnapshot

    snap = AlertSnapshot(
        sensor_label="X",
        parent_label="Y",
        alert_state="WARNED",
        sensor_id="/test/0",
    )
    with pytest.raises(AttributeError):
        snap.alert_state = "CRITICAL"  # type: ignore[misc]


def test_alert_snapshot_slots_prevents_new_attributes() -> None:
    from custom_components.raritan.models import AlertSnapshot

    snap = AlertSnapshot(
        sensor_label="X",
        parent_label="Y",
        alert_state="NORMAL",
        sensor_id="/test/0",
    )
    # frozen+slots: assigning unknown attr raises FrozenInstanceError (AttributeError subclass)
    # or TypeError depending on cpython version.
    with pytest.raises((AttributeError, TypeError)):
        snap.bogus = "v"  # type: ignore[attr-defined]


def test_coordinator_payload_carries_current_alerts() -> None:
    from custom_components.raritan.models import AlertSnapshot, CoordinatorPayload

    snap = AlertSnapshot(
        sensor_label="X",
        parent_label="Y",
        alert_state="CRITICAL",
        sensor_id="/test/0",
    )
    p = CoordinatorPayload(
        inlets=[],
        outlets=[],
        ocps=[],
        env=[],
        current_alerts=[snap],
        last_tick_duration_ms=0,
        consecutive_skips=0,
    )
    assert p.current_alerts == [snap]
