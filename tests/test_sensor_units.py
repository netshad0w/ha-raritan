"""Unit tests for sensor native_value edge paths and env numeric sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.raritan.models import (
    CapabilityMatrix,
    CoordinatorPayload,
    EnvSensorReading,
    InletReading,
    OutletReading,
)
from custom_components.raritan.sensor import (
    INLET_SENSORS,
    OCP_SENSORS,
    OUTLET_SENSORS,
    RaritanEnvSensor,
    RaritanInletSensor,
    RaritanOcpSensor,
    RaritanOutletSensor,
)


def _caps(*, nb_inlets: int = 1, env_ids: tuple[str, ...] = ()) -> CapabilityMatrix:
    return CapabilityMatrix(
        model="PX3",
        firmware="4.3.11",
        serial="TEST00000001",
        hw_revision=None,
        nb_inlets=nb_inlets,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=env_ids,
        outlet_switching=False,
        outlet_metering=False,
    )


def _payload(**kw) -> CoordinatorPayload:
    base = {
        "inlets": [],
        "outlets": [],
        "ocps": [],
        "env": [],
        "current_alerts": [],
        "last_tick_duration_ms": 0,
        "consecutive_skips": 0,
    }
    base.update(kw)
    return CoordinatorPayload(**base)


def _coord(caps: CapabilityMatrix, data: CoordinatorPayload | None) -> MagicMock:
    coord = MagicMock()
    coord.capabilities = caps
    coord.data = data
    coord.host = "10.0.0.1"
    return coord


def _voltage(descriptions):
    return next(d for d in descriptions if d.key == "voltage")


def test_inlet_native_value_none_when_no_data() -> None:
    ent = RaritanInletSensor(
        coordinator=_coord(_caps(), None), description=_voltage(INLET_SENSORS), inlet_idx=1
    )
    assert ent.native_value is None


def test_inlet_native_value_none_when_idx_missing() -> None:
    ent = RaritanInletSensor(
        coordinator=_coord(_caps(), _payload(inlets=[])),
        description=_voltage(INLET_SENSORS),
        inlet_idx=1,
    )
    assert ent.native_value is None


def test_inlet_native_value_none_when_reading_value_none() -> None:
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
    ent = RaritanInletSensor(
        coordinator=_coord(_caps(nb_inlets=2), _payload(inlets=[reading])),
        description=_voltage(INLET_SENSORS),
        inlet_idx=1,
    )
    assert ent.native_value is None


def test_outlet_native_value_none_paths() -> None:
    desc = _voltage(OUTLET_SENSORS)
    assert (
        RaritanOutletSensor(
            coordinator=_coord(_caps(), None), description=desc, outlet_idx=1
        ).native_value
        is None
    )
    assert (
        RaritanOutletSensor(
            coordinator=_coord(_caps(), _payload(outlets=[])), description=desc, outlet_idx=1
        ).native_value
        is None
    )
    reading = OutletReading(
        idx=1,
        on=True,
        label="1",
        voltage=None,
        current=None,
        active_power=None,
        apparent_power=None,
        active_energy_wh=None,
    )
    assert (
        RaritanOutletSensor(
            coordinator=_coord(_caps(), _payload(outlets=[reading])),
            description=desc,
            outlet_idx=1,
        ).native_value
        is None
    )


def test_ocp_native_value_none_paths() -> None:
    desc = OCP_SENSORS[0]
    assert (
        RaritanOcpSensor(
            coordinator=_coord(_caps(), None), description=desc, ocp_idx=1
        ).native_value
        is None
    )
    assert (
        RaritanOcpSensor(
            coordinator=_coord(_caps(), _payload(ocps=[])), description=desc, ocp_idx=1
        ).native_value
        is None
    )


def test_env_numeric_sensor_resolves_type_and_value() -> None:
    env = [
        EnvSensorReading(
            sensor_id="t:n0",
            label="Rack top",
            sensor_type="TEMPERATURE",
            value=22.5,
            state=None,
            unit="°C",
        )
    ]
    coord = _coord(_caps(env_ids=("t:n0",)), _payload(env=env))
    ent = RaritanEnvSensor(coordinator=coord, sensor_id="t:n0")
    assert ent.device_class == "temperature"
    assert ent.native_value == 22.5


def test_env_numeric_sensor_none_when_no_data() -> None:
    ent = RaritanEnvSensor(coordinator=_coord(_caps(), None), sensor_id="t:n0")
    assert ent.native_value is None
