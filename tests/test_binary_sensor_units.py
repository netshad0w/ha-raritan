"""Unit tests for PSU health and env binary sensor entities."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.raritan.binary_sensor import (
    RaritanEnvBinarySensor,
    RaritanPsuHealthSensor,
)
from custom_components.raritan.models import (
    CapabilityMatrix,
    CoordinatorPayload,
    EnvSensorReading,
    PsuReading,
)


def _caps(*, nb_psu: int = 1, env_ids: tuple[str, ...] = ()) -> CapabilityMatrix:
    return CapabilityMatrix(
        model="PX3",
        firmware="4.3.11",
        serial="TEST00000001",
        hw_revision=None,
        nb_inlets=1,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=env_ids,
        outlet_switching=False,
        outlet_metering=False,
        has_alerts_engine=False,
        nb_psu=nb_psu,
    )


def _payload(*, psus: list[PsuReading] | None = None, env: list[EnvSensorReading] | None = None):
    return CoordinatorPayload(
        inlets=[],
        outlets=[],
        ocps=[],
        env=env or [],
        current_alerts=[],
        last_tick_duration_ms=0,
        consecutive_skips=0,
        psus=psus or [],
    )


def _coord(caps: CapabilityMatrix, data: CoordinatorPayload | None) -> MagicMock:
    coord = MagicMock()
    coord.capabilities = caps
    coord.data = data
    return coord


def test_psu_multi_uses_subdevice() -> None:
    coord = _coord(_caps(nb_psu=2), _payload(psus=[PsuReading(idx=1, ok=False)]))
    ent = RaritanPsuHealthSensor(coordinator=coord, psu_idx=1)
    assert ent.device_info["identifiers"] == {("raritan", "TEST00000001_psu_1")}
    assert ent.is_on is True  # ok=False -> problem


def test_psu_single_flat_on_pdu_device() -> None:
    coord = _coord(_caps(nb_psu=1), _payload(psus=[PsuReading(idx=1, ok=True)]))
    ent = RaritanPsuHealthSensor(coordinator=coord, psu_idx=1)
    assert ent.device_info["identifiers"] == {("raritan", "TEST00000001")}
    assert ent.is_on is False  # ok=True -> no problem


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (None, None),
        (_payload(psus=[]), None),  # idx missing
        (_payload(psus=[PsuReading(idx=1, ok=None)]), None),  # reading unavailable
    ],
)
def test_psu_is_on_none_paths(data: CoordinatorPayload | None, expected: None) -> None:
    ent = RaritanPsuHealthSensor(coordinator=_coord(_caps(nb_psu=1), data), psu_idx=1)
    assert ent.is_on is expected


def test_env_binary_known_type_sets_device_class() -> None:
    env = [
        EnvSensorReading(
            sensor_id="abc:s0",
            label="Door",
            sensor_type="CONTACT",
            value=None,
            state=True,
            unit=None,
        )
    ]
    coord = _coord(_caps(env_ids=("abc:s0",)), _payload(env=env))
    ent = RaritanEnvBinarySensor(coordinator=coord, sensor_id="abc:s0")
    assert ent.device_class == "opening"
    assert ent.is_on is True


def test_env_binary_unknown_type_no_device_class() -> None:
    env = [
        EnvSensorReading(
            sensor_id="xyz:s0",
            label="",
            sensor_type="MYSTERY",
            value=None,
            state=False,
            unit=None,
        )
    ]
    coord = _coord(_caps(env_ids=("xyz:s0",)), _payload(env=env))
    ent = RaritanEnvBinarySensor(coordinator=coord, sensor_id="xyz:s0")
    assert getattr(ent, "_attr_device_class", None) is None
    assert ent.is_on is False


def test_env_binary_is_on_none_when_no_data() -> None:
    ent = RaritanEnvBinarySensor(coordinator=_coord(_caps(), None), sensor_id="abc:s0")
    assert ent.is_on is None
