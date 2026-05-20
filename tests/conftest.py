"""Shared fixtures for raritan tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

# Real SDK imports, used as `spec=` to enforce method/attribute shape so that
# attribute typos surface in tests instead of in production. Note: the Raritan
# SDK builds methods dynamically on instances, so we must spec against an actual
# instance (not the class). We construct throwaway instances with a dummy agent.
from raritan.rpc import pdumodel  # type: ignore[import-not-found]
from raritan.rpc.sensors import (  # type: ignore[import-not-found]
    AccumulatingNumericSensor,
    NumericSensor,
)

from tests.helpers import make_fake_bulk_helper_class

if TYPE_CHECKING:
    from collections.abc import Generator

pytest_plugins = ["pytest_homeassistant_custom_component"]

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Real-SDK spec templates
#
# The SDK adds methods (`getMetaData`, `getInlets`, `getSensors`, ...) at
# instance construction time. Class-level `spec=pdumodel.Pdu` would not see
# them. We build one throwaway instance per type and reuse it as the spec.
# ---------------------------------------------------------------------------

_DUMMY_AGENT = MagicMock()
_PDU_SPEC = pdumodel.Pdu("/model/pdu/0", _DUMMY_AGENT)
_INLET_SPEC = pdumodel.Inlet("/x", _DUMMY_AGENT)
_OUTLET_SPEC = pdumodel.Outlet("/x", _DUMMY_AGENT)
_OCP_SPEC = pdumodel.OverCurrentProtector("/x", _DUMMY_AGENT)
_PDU_METADATA_SPEC = pdumodel.Pdu.MetaData()
_INLET_SENSORS_SPEC = pdumodel.Inlet.Sensors
_OUTLET_SENSORS_SPEC = pdumodel.Outlet.Sensors
_NUMERIC_SENSOR_SPEC = NumericSensor("/x", _DUMMY_AGENT)
_ACCUMULATING_NUMERIC_SENSOR_SPEC = AccumulatingNumericSensor("/x", _DUMMY_AGENT)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable loading of the raritan custom integration in tests."""


@pytest.fixture
def snapshot_4_3_11() -> dict[str, Any]:
    """Anonymized PX3-5487V-N2 snapshot on firmware 4.3.11."""
    with (FIXTURES_DIR / "4.3.11" / "snapshot.json").open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _make_numeric_sensor(value: float = 0.0, valid: bool = True) -> MagicMock:
    """Build a NumericSensor mock that returns a Reading-like object on getReading()."""
    sensor = MagicMock(spec=_NUMERIC_SENSOR_SPEC)
    reading = MagicMock()
    reading.value = value
    reading.valid = valid
    sensor.getReading.return_value = reading
    return sensor


def _make_accumulating_numeric_sensor(value: float = 0.0, valid: bool = True) -> MagicMock:
    """Build an AccumulatingNumericSensor mock with getReading + resetValue."""
    sensor = MagicMock(spec=_ACCUMULATING_NUMERIC_SENSOR_SPEC)
    reading = MagicMock()
    reading.value = value
    reading.valid = valid
    sensor.getReading.return_value = reading
    return sensor


def _make_outlet_state(on: bool, available: bool = True) -> MagicMock:
    """Build an Outlet.State struct mock matching SDK shape (powerState enum)."""
    state = MagicMock()
    state.available = available
    state.powerState = pdumodel.Outlet.PowerState.PS_ON if on else pdumodel.Outlet.PowerState.PS_OFF
    return state


def _make_inlet_sensors(snapshot_props: dict[str, Any]) -> MagicMock:
    """Build a mock of Inlet.Sensors struct with all relevant numeric sensors.

    Maps snapshot keys (legacy names) to real SDK Inlet.Sensors attribute names.
    The snapshot file uses "frequency" but the real SDK exposes "lineFrequency";
    production code reads `lineFrequency` per the real SDK.
    """
    sensors = MagicMock(spec=_INLET_SENSORS_SPEC)
    real_names = {
        "voltage": "voltage",
        "current": "current",
        "activePower": "activePower",
        "apparentPower": "apparentPower",
        "powerFactor": "powerFactor",
        "frequency": "lineFrequency",
        "activeEnergy": "activeEnergy",
    }
    for snap_key, real_attr in real_names.items():
        if snap_key in snapshot_props:
            if real_attr == "activeEnergy":
                setattr(
                    sensors,
                    real_attr,
                    _make_accumulating_numeric_sensor(value=0.0, valid=True),
                )
            else:
                setattr(sensors, real_attr, _make_numeric_sensor(value=0.0, valid=True))
    return sensors


def _make_inlet(snapshot: dict[str, Any]) -> MagicMock:
    """Build a mock of pdumodel.Inlet with getMetaData and getSensors methods."""
    inlet = MagicMock(spec=_INLET_SPEC)
    metadata = MagicMock()
    metadata.label = snapshot["label"]
    inlet.getMetaData.return_value = metadata
    inlet.getSensors.return_value = _make_inlet_sensors(snapshot["sensor_logical_properties"])
    return inlet


def _make_pdu_metadata(snap: dict[str, Any]) -> MagicMock:
    """Build a Pdu.MetaData mock with the real Pdu.MetaData fields."""
    md = MagicMock(spec=_PDU_METADATA_SPEC)
    nameplate = MagicMock()
    nameplate.manufacturer = snap["nameplate"]["manufacturer"]
    nameplate.model = snap["nameplate"]["model"]
    nameplate.serialNumber = snap["nameplate"]["serialNumber"]
    nameplate.partNumber = snap["nameplate"]["partNumber"]
    nameplate.macAddress = snap["nameplate"]["macAddress"]
    md.nameplate = nameplate
    md.fwRevision = snap["fwRevision"]
    md.hwRevision = snap["hwRevision"]
    md.macAddress = snap["nameplate"]["macAddress"]
    md.hasSwitchableOutlets = False
    md.hasMeteredOutlets = False
    md.hasLatchingOutletRelays = False
    md.isInlineMeter = False
    md.isEnergyPulseSupported = False
    md.hasDCInlets = False
    md.ctrlBoardSerial = ""
    md.pduOrientation = None
    return md


@pytest.fixture
def mock_raritan(snapshot_4_3_11: dict[str, Any]) -> Generator[MagicMock]:
    """Mock the raritan SDK Pdu object using the captured snapshot, with strict spec=."""
    pdu = MagicMock(spec=_PDU_SPEC)
    pdu.getMetaData.return_value = _make_pdu_metadata(snapshot_4_3_11["metadata"])
    pdu.getInlets.return_value = [_make_inlet(i) for i in snapshot_4_3_11["inlets"]]
    pdu.getOutlets.return_value = [
        MagicMock(spec=_OUTLET_SPEC) for _ in range(snapshot_4_3_11["outlet_count"])
    ]
    pdu.getOverCurrentProtectors.return_value = [
        MagicMock(spec=_OCP_SPEC) for _ in range(snapshot_4_3_11["ocp_count"])
    ]

    _alerted_mgr = MagicMock()
    _alerted_mgr.getAlertedSensors.return_value = []
    pdu.getAlertedSensorManager.return_value = _alerted_mgr

    with (
        patch("custom_components.raritan.api.Agent") as agent_cls,
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=make_fake_bulk_helper_class(),
        ),
    ):
        agent_cls.return_value = MagicMock()
        yield pdu


@pytest.fixture
def mock_raritan_with_outlets(
    snapshot_4_3_11: dict[str, Any],
) -> Generator[MagicMock]:
    """Variant that simulates an outlet-metered+switchable PDU with 2 outlets."""
    pdu = MagicMock(spec=_PDU_SPEC)

    # Patch metadata to enable outlet flags
    md = _make_pdu_metadata(snapshot_4_3_11["metadata"])
    md.hasSwitchableOutlets = True
    md.hasMeteredOutlets = True
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = [_make_inlet(i) for i in snapshot_4_3_11["inlets"]]

    # 2 outlets with sensors + state
    def _make_outlet(
        idx: int,
        label: str,
        on: bool,
        voltage: float = 230.0,
        current: float = 0.5,
        power: float = 115.0,
        apparent: float = 120.0,
        energy: float = 12345,
    ) -> MagicMock:
        outlet = MagicMock(spec=_OUTLET_SPEC)
        metadata = MagicMock()
        metadata.label = label
        metadata.isSwitchable = True
        outlet.getMetaData.return_value = metadata
        outlet.getState.return_value = _make_outlet_state(on)
        sensors = MagicMock(spec=_OUTLET_SENSORS_SPEC)
        for name, val in [
            ("voltage", voltage),
            ("current", current),
            ("activePower", power),
            ("apparentPower", apparent),
            ("activeEnergy", energy),
        ]:
            if name == "activeEnergy":
                ns = _make_accumulating_numeric_sensor(value=val, valid=True)
            else:
                ns = _make_numeric_sensor(value=val, valid=True)
            setattr(sensors, name, ns)
        outlet.getSensors.return_value = sensors
        return outlet

    pdu.getOutlets.return_value = [
        _make_outlet(1, "1", True),
        _make_outlet(2, "2", False, voltage=0.0, current=0.0, power=0.0, apparent=0.0, energy=0),
    ]
    pdu.getOverCurrentProtectors.return_value = []

    # Default: AlertedSensorManager returns an empty alert list. Tests can override
    # via `set_alerts(pdu_mock, [...])`.
    _alerted_mgr = MagicMock()
    _alerted_mgr.getAlertedSensors.return_value = []
    pdu.getAlertedSensorManager.return_value = _alerted_mgr

    with (
        patch("custom_components.raritan.api.Agent") as agent_cls,
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=make_fake_bulk_helper_class(),
        ),
    ):
        agent_cls.return_value = MagicMock()
        yield pdu


def _make_alert_snapshot(
    label: str,
    parent_label: str = "Inlet I1",
    state: str = "CRITICAL",
    sensor_id: str = "/test/sensor/0",
) -> Any:
    """Build an AlertSnapshot for tests (uses the dataclass directly, no SDK)."""
    from custom_components.raritan.models import AlertSnapshot

    return AlertSnapshot(
        sensor_label=label,
        parent_label=parent_label,
        alert_state=state,
        sensor_id=sensor_id,
    )


def set_alerts(pdu_mock: MagicMock, alerts: list[Any]) -> None:
    """Configure the AlertedSensorManager mock to return the given SensorData mocks."""
    mgr = pdu_mock.getAlertedSensorManager.return_value
    mgr.getAlertedSensors.return_value = list(alerts)


@pytest.fixture
def alert_snapshot_factory() -> Any:
    """Return the _make_alert_snapshot helper as a fixture."""
    return _make_alert_snapshot
