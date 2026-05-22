"""Tests for the Raritan API wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.raritan.api import RaritanAPI
from custom_components.raritan.models import CapabilityMatrix
from tests.helpers import make_fake_bulk_helper_class


@pytest.fixture
def api() -> RaritanAPI:
    return RaritanAPI(
        host="10.0.0.1",
        username="admin",
        password="secret",
        verify_tls=False,
        ca_bundle=None,
    )


def test_probe_returns_capability_matrix(api: RaritanAPI, mock_raritan: MagicMock) -> None:
    cap = api.probe()
    assert isinstance(cap, CapabilityMatrix)
    assert cap.model == "PX3-5487V-N2"
    assert cap.firmware == "4.3.11.5-52050"
    assert cap.serial == "TEST00000001"
    assert cap.nb_inlets == 1
    assert len(cap.outlet_ids) == 36
    assert len(cap.ocp_ids) == 6
    assert cap.env_sensor_ids == ()


def test_probe_identity_returns_serial_and_model(api: RaritanAPI, mock_raritan: MagicMock) -> None:
    """Lightweight reauth probe returns (serial, model) without full discovery."""
    serial, model = api.probe_identity()
    assert serial == "TEST00000001"
    assert model == "PX3-5487V-N2"
    # Critical: probe_identity must NOT walk peripheral slots; that's the
    # whole point. Confirm the env-discovery surface was never touched.
    mock_raritan.getPeripheralDeviceManager.assert_not_called()
    mock_raritan.getInlets.assert_not_called()
    mock_raritan.getOutlets.assert_not_called()
    mock_raritan.getOverCurrentProtectors.assert_not_called()


def test_probe_identity_raises_auth_error_on_401() -> None:
    """probe_identity() remaps HTTP 401 to RaritanAuthError, like probe()."""
    from raritan.rpc import HttpException

    from custom_components.raritan.api import RaritanAuthError

    api = RaritanAPI(
        host="10.0.0.1",
        username="admin",
        password="bad",
        verify_tls=False,
        ca_bundle=None,
    )
    pdu = MagicMock()
    pdu.getMetaData.side_effect = HttpException("HTTP Error 401")
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanAuthError),
    ):
        api.probe_identity()


def test_probe_constructs_agent_with_verify_tls_false(
    api: RaritanAPI, mock_raritan: MagicMock
) -> None:
    with (
        patch("custom_components.raritan.api.Agent") as agent_cls,
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=mock_raritan),
    ):
        api.probe()
        agent_cls.assert_called_once()
        # First positional arg is the scheme; verify it's "https"
        assert agent_cls.call_args.args[0] == "https"
        kwargs = agent_cls.call_args.kwargs
        assert kwargs.get("disable_certificate_verification") is True


def test_probe_constructs_agent_with_ca_bundle() -> None:
    """When verify_tls=True and a ca_bundle path is given, the Agent is
    constructed with disable_certificate_verification=False and the SDK
    pipeline runs without exception. The custom CA injection itself is
    handled by ssl.create_default_context after Agent construction; we
    stub that out to avoid a real filesystem read.
    """
    import ssl as _ssl

    api = RaritanAPI(
        host="10.0.0.1",
        username="admin",
        password="secret",
        verify_tls=True,
        ca_bundle="/etc/ssl/custom-ca.pem",
    )
    # Build a minimal spec'd Pdu mock matching the real SDK shape.
    from raritan.rpc import pdumodel as _pdumodel  # type: ignore[import-not-found]

    _agent = MagicMock()
    _pdu_spec_instance = _pdumodel.Pdu("/model/pdu/0", _agent)
    _md_spec_instance = _pdumodel.Pdu.MetaData()

    pdu = MagicMock(spec=_pdu_spec_instance)
    md = MagicMock(spec=_md_spec_instance)
    md.nameplate = MagicMock(
        manufacturer="Raritan",
        model="X",
        serialNumber="S",
        partNumber="X",
        macAddress="00:11:22:33:44:55",
    )
    md.fwRevision = "4.0.10"
    md.hwRevision = None
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = []

    with (
        patch("custom_components.raritan.api.Agent") as agent_cls,
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        patch.object(_ssl, "create_default_context", return_value=MagicMock()),
    ):
        api.probe()
        agent_cls.assert_called_once()
        kwargs = agent_cls.call_args.kwargs
        # verify_tls=True -> disable_certificate_verification=False
        assert kwargs.get("disable_certificate_verification") is False


def test_probe_reuses_http_agent_across_calls(api: RaritanAPI, mock_raritan: MagicMock) -> None:
    with (
        patch("custom_components.raritan.api.Agent") as agent_cls,
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=mock_raritan),
    ):
        api.probe()
        api.probe()
        assert agent_cls.call_count == 1


def test_fetch_telemetry_returns_inlet_readings(api: RaritanAPI, mock_raritan: MagicMock) -> None:
    # Arrange: set readings on the mocked sensors. Real SDK exposes sensors via
    # `inlet.getSensors()` returning an Inlet.Sensors struct; the SDK frequency
    # field is `lineFrequency`.
    inlet = mock_raritan.getInlets.return_value[0]
    sensors = inlet.getSensors.return_value
    sensors.voltage.getReading.return_value = MagicMock(value=230.1, valid=True)
    sensors.current.getReading.return_value = MagicMock(value=4.5, valid=True)
    sensors.activePower.getReading.return_value = MagicMock(value=1035, valid=True)
    sensors.apparentPower.getReading.return_value = MagicMock(value=1100, valid=True)
    sensors.powerFactor.getReading.return_value = MagicMock(value=0.94, valid=True)
    sensors.lineFrequency.getReading.return_value = MagicMock(value=50.0, valid=True)
    sensors.activeEnergy.getReading.return_value = MagicMock(value=12345678, valid=True)

    cap = api.probe()
    payload = api.fetch_telemetry(cap)

    assert len(payload.inlets) == 1
    reading = payload.inlets[0]
    assert reading.idx == 1
    assert reading.voltage == 230.1
    assert reading.current == 4.5
    assert reading.active_power == 1035
    assert reading.apparent_power == 1100
    assert reading.power_factor == 0.94
    assert reading.frequency == 50.0
    assert reading.active_energy_wh == 12345678


def test_fetch_telemetry_invalid_reading_yields_none(
    api: RaritanAPI, mock_raritan: MagicMock
) -> None:
    inlet = mock_raritan.getInlets.return_value[0]
    inlet.getSensors.return_value.voltage.getReading.return_value = MagicMock(
        value=230.1, valid=False
    )

    cap = api.probe()
    payload = api.fetch_telemetry(cap)

    assert payload.inlets[0].voltage is None


def test_probe_raises_auth_error_on_403() -> None:
    from raritan.rpc import HttpException  # type: ignore[import-not-found]

    from custom_components.raritan.api import RaritanAuthError

    api = RaritanAPI(
        host="10.0.0.1",
        username="admin",
        password="bad",
        verify_tls=False,
        ca_bundle=None,
    )
    pdu = MagicMock()
    pdu.getMetaData.side_effect = HttpException("HTTP 403 Forbidden")
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanAuthError),
    ):
        api.probe()


def test_probe_raises_connection_error_on_unreachable() -> None:
    from raritan.rpc import HttpException  # type: ignore[import-not-found]

    from custom_components.raritan.api import RaritanConnectionError

    api = RaritanAPI(
        host="10.0.0.1",
        username="admin",
        password="secret",
        verify_tls=False,
        ca_bundle=None,
    )
    pdu = MagicMock()
    pdu.getMetaData.side_effect = HttpException("Connection refused")
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanConnectionError),
    ):
        api.probe()


def test_probe_raises_tls_error_on_certificate_failure() -> None:
    from raritan.rpc import HttpException  # type: ignore[import-not-found]

    from custom_components.raritan.api import RaritanTLSError

    api = RaritanAPI(
        host="10.0.0.1",
        username="admin",
        password="secret",
        verify_tls=True,
        ca_bundle=None,
    )
    pdu = MagicMock()
    pdu.getMetaData.side_effect = HttpException("certificate verify failed")
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanTLSError),
    ):
        api.probe()


def test_probe_raises_unsupported_error_on_not_supported() -> None:
    from raritan.rpc import HttpException  # type: ignore[import-not-found]

    from custom_components.raritan.api import RaritanUnsupportedError

    api = RaritanAPI(
        host="10.0.0.1",
        username="admin",
        password="secret",
        verify_tls=False,
        ca_bundle=None,
    )
    pdu = MagicMock()
    pdu.getMetaData.side_effect = HttpException("Method not supported")
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanUnsupportedError),
    ):
        api.probe()


def test_fetch_telemetry_remaps_http_exception(api: RaritanAPI) -> None:
    """fetch_telemetry must catch HttpException from getInlets and remap."""
    from raritan.rpc import HttpException  # type: ignore[import-not-found]

    from custom_components.raritan.api import RaritanConnectionError

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
    )
    pdu = MagicMock()
    pdu.getInlets.side_effect = HttpException("Connection refused")
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanConnectionError),
    ):
        api.fetch_telemetry(cap)


def test_fetch_telemetry_handles_missing_inlet_sensor_attribute(api: RaritanAPI) -> None:
    """If a sensor attribute is missing on the Inlet.Sensors struct, the value is None.

    Replaces the deleted `_read_inlet` unit test. The same branch is now
    reachable through `fetch_telemetry` because the helper's per-sensor
    `getattr(sensors, name, None)` handles missing attributes uniformly.
    """
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=1,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
    )
    pdu = MagicMock()
    inlet = MagicMock()
    sensors = MagicMock(spec=["voltage"])  # only `voltage` exists; `current` etc. are absent
    voltage = MagicMock()
    voltage.getReading.return_value = MagicMock(value=230.0, valid=True)
    sensors.voltage = voltage
    inlet.getSensors.return_value = sensors
    pdu.getInlets.return_value = [inlet]
    pdu.getOutlets.return_value = []

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        payload = api.fetch_telemetry(cap)

    assert payload.inlets[0].voltage == 230.0
    assert payload.inlets[0].current is None
    assert payload.inlets[0].active_power is None


def _make_outlet_mock_v2(
    idx: int,
    label: str,
    on: bool,
    *,
    voltage: float = 230.0,
    current: float = 0.5,
    power: float = 115.0,
    apparent: float = 120.0,
    energy: float = 12345,
) -> MagicMock:
    """Build an outlet mock with metadata + state + sensors."""
    from raritan.rpc import pdumodel

    outlet = MagicMock()
    metadata = MagicMock()
    metadata.label = label
    outlet.getMetaData.return_value = metadata
    state = MagicMock()
    state.available = True
    state.powerState = pdumodel.Outlet.PowerState.PS_ON if on else pdumodel.Outlet.PowerState.PS_OFF
    outlet.getState.return_value = state
    sensors = MagicMock()
    sensors.voltage.getReading.return_value = MagicMock(value=voltage, valid=True)
    sensors.current.getReading.return_value = MagicMock(value=current, valid=True)
    sensors.activePower.getReading.return_value = MagicMock(value=power, valid=True)
    sensors.apparentPower.getReading.return_value = MagicMock(value=apparent, valid=True)
    sensors.activeEnergy.getReading.return_value = MagicMock(value=energy, valid=True)
    outlet.getSensors.return_value = sensors
    return outlet


def test_fetch_telemetry_reads_outlets_when_metered(api: RaritanAPI) -> None:
    """When capabilities.outlet_metering is True, fetch_telemetry reads outlets."""
    from custom_components.raritan.models import CapabilityMatrix

    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(1, 2),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
    )
    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [
        _make_outlet_mock_v2(
            1, "A1", True, voltage=230.0, current=0.5, power=115.0, apparent=120.0, energy=12345
        ),
        _make_outlet_mock_v2(
            2, "A2", False, voltage=0.0, current=0.0, power=0.0, apparent=0.0, energy=0
        ),
    ]

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        payload = api.fetch_telemetry(cap)

    assert len(payload.outlets) == 2
    assert payload.outlets[0].idx == 1
    assert payload.outlets[0].on is True
    assert payload.outlets[0].label == "A1"
    assert payload.outlets[0].voltage == 230.0
    assert payload.outlets[0].active_energy_wh == 12345
    assert payload.outlets[1].on is False
    assert payload.outlets[1].voltage == 0.0


def test_fetch_telemetry_refreshes_outlet_sensors_after_ttl(api: RaritanAPI) -> None:
    """Cached outlet sensor structs must be re-fetched once `_OUTLET_SENSORS_TTL`
    elapses. PX3 firmware 4.3.x silently invalidates the cached
    `outlet.getSensors()` Sensor proxies after ~50 s; re-issuing `getSensors()`
    is the only known way to recover without a full close()/reconnect.
    """
    from custom_components.raritan import api as api_mod
    from custom_components.raritan.models import CapabilityMatrix

    cap = CapabilityMatrix(
        model="X",
        firmware="4.3.11",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(1,),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
    )
    outlet = _make_outlet_mock_v2(1, "A1", True)
    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [outlet]

    # Drive `time.monotonic()` through three discrete steps so we can assert the
    # exact tick on which the TTL evicts the cache.
    fake_now = {"t": 1000.0}

    def _fake_monotonic() -> float:
        return fake_now["t"]

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        patch.object(api_mod.time, "monotonic", side_effect=_fake_monotonic),
    ):
        # First tick populates the cache and stamps it at t=1000.
        api.fetch_telemetry(cap)
        assert outlet.getSensors.call_count == 1

        # Tick within TTL must reuse the cache, no extra getSensors() call.
        fake_now["t"] = 1000.0 + api_mod._OUTLET_SENSORS_TTL - 0.1
        api.fetch_telemetry(cap)
        assert outlet.getSensors.call_count == 1

        # Tick past TTL must evict and re-fetch. This is the regression guard.
        fake_now["t"] = 1000.0 + api_mod._OUTLET_SENSORS_TTL + 0.1
        api.fetch_telemetry(cap)
        assert outlet.getSensors.call_count == 2


def test_set_outlet_state_does_not_wipe_outlet_sensor_struct_cache(
    api: RaritanAPI,
) -> None:
    """A write path (set_outlet_state) must not invalidate the outlet sensor
    struct cache populated by fetch_telemetry. Regression for the bug where
    the write path called _refresh_proxies(_minimal_cap_for_outlets()) with
    outlet_metering=False, which wiped `_outlet_sensors_structs` to `[]`.
    The next coordinator tick saw `[] is not None`, skipped re-fetch, and
    every outlet metering sensor reported "unknown" until container restart.
    """
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(1, 2),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
    )
    outlet1 = _make_outlet_mock_v2(1, "A1", True, voltage=230.0, power=115.0, energy=12345)
    outlet2 = _make_outlet_mock_v2(2, "A2", True, voltage=230.0, power=42.0, energy=999)
    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [outlet1, outlet2]

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        # 1. First tick populates the outlet sensor struct cache.
        first = api.fetch_telemetry(cap)
        assert first.outlets[0].active_power == 115.0
        assert first.outlets[1].active_power == 42.0
        assert outlet1.getSensors.call_count == 1

        # 2. Toggle outlet 1. This used to wipe the cache via the minimal-cap path.
        api.set_outlet_state(idx=1, on=False)

        # 3. Next coordinator tick MUST still return valid metering readings.
        second = api.fetch_telemetry(cap)
        assert second.outlets[0].active_power == 115.0, (
            "Outlet metering reads stale-None after set_outlet_state - cache was wiped"
        )
        assert second.outlets[1].active_power == 42.0
        # And we should NOT have re-fetched the sensor structs (cache must
        # survive the write path untouched).
        assert outlet1.getSensors.call_count == 1


def test_cycle_outlet_does_not_wipe_outlet_sensor_struct_cache(api: RaritanAPI) -> None:
    """Same invariant as set_outlet_state: cycle_outlet must not break the
    outlet metering cache for the next coordinator tick.
    """
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(1,),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
    )
    outlet = _make_outlet_mock_v2(1, "A1", True, voltage=230.0, power=115.0)
    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [outlet]

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        api.fetch_telemetry(cap)
        api.cycle_outlet(idx=1)
        payload = api.fetch_telemetry(cap)
        assert payload.outlets[0].active_power == 115.0
        assert outlet.getSensors.call_count == 1


def test_reset_inlet_energy_does_not_wipe_outlet_cache(api: RaritanAPI) -> None:
    """reset_inlet_energy used to call _refresh_proxies with a minimal cap
    whose outlet_metering=False AND outlet_switching=False, causing
    `not need_outlets` to fire and wipe `_outlets = []` AND
    `_outlet_sensors_structs = []`. The next coordinator tick then saw
    `_outlets = []` (not None) and skipped re-fetch, surfacing every outlet
    sensor as None.
    """
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=1,
        outlet_ids=(1,),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
    )
    pdu = MagicMock()
    inlet = MagicMock()
    inlet_sensors = MagicMock()
    energy = MagicMock()
    energy.getReading.return_value = MagicMock(value=1000.0, valid=True)
    energy.resetValue = MagicMock()
    inlet_sensors.activeEnergy = energy
    inlet_sensors.voltage.getReading.return_value = MagicMock(value=230.0, valid=True)
    inlet.getSensors.return_value = inlet_sensors
    pdu.getInlets.return_value = [inlet]
    outlet = _make_outlet_mock_v2(1, "A1", True, power=88.0)
    pdu.getOutlets.return_value = [outlet]

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        api.fetch_telemetry(cap)
        api.reset_inlet_energy(idx=1)
        energy.resetValue.assert_called_once()
        payload = api.fetch_telemetry(cap)
        assert payload.outlets[0].active_power == 88.0, (
            "Outlet readings vanished after reset_inlet_energy "
            "- inlet write path wiped outlet cache"
        )


def test_fetch_telemetry_reads_outlet_state_only_when_switching_only(api: RaritanAPI) -> None:
    """Switching but not metering: state read, sensor metrics are None."""
    from custom_components.raritan.models import CapabilityMatrix

    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(1,),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=False,
    )
    pdu = MagicMock()
    from raritan.rpc import pdumodel

    outlet = MagicMock()
    metadata = MagicMock()
    metadata.label = "A1"
    outlet.getMetaData.return_value = metadata
    state = MagicMock()
    state.available = True
    state.powerState = pdumodel.Outlet.PowerState.PS_ON
    outlet.getState.return_value = state
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [outlet]

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        payload = api.fetch_telemetry(cap)

    assert len(payload.outlets) == 1
    assert payload.outlets[0].on is True
    assert payload.outlets[0].voltage is None  # not metered


def test_fetch_telemetry_handles_outlet_label_state_and_invalid_sensor(api: RaritanAPI) -> None:
    """fetch_telemetry must tolerate getMetaData/getState exceptions and invalid sensor reads.

    Replaces the deleted `_read_outlet` unit test. Each per-outlet call is queued
    on the bulk helper, so an exception in any of them surfaces as an Exception
    object in the results list, and the api decoder then falls back to defaults
    (label = str(idx), on = False) and propagates None for invalid readings.
    """
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(1,),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
    )
    pdu = MagicMock()
    outlet = MagicMock()
    outlet.getMetaData.side_effect = RuntimeError("boom")
    outlet.getState.side_effect = RuntimeError("boom")
    sensors = MagicMock(spec=["current", "activePower", "apparentPower", "activeEnergy"])
    # voltage is absent (covers the `getattr(..., None)` branch)
    sensors.current.getReading.return_value = MagicMock(value=1.0, valid=False)  # invalid branch
    sensors.activePower.getReading.return_value = MagicMock(value=2.0, valid=True)
    sensors.apparentPower.getReading.return_value = MagicMock(value=3.0, valid=True)
    sensors.activeEnergy.getReading.return_value = MagicMock(value=4.0, valid=True)
    outlet.getSensors.return_value = sensors
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [outlet]

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        payload = api.fetch_telemetry(cap)

    assert len(payload.outlets) == 1
    reading = payload.outlets[0]
    assert reading.idx == 1
    assert reading.label == "1"  # fallback after getMetaData raised
    assert reading.on is False  # fallback after getState raised
    assert reading.voltage is None  # absent attribute on Sensors struct
    assert reading.current is None  # invalid reading
    assert reading.active_power == 2.0
    assert reading.apparent_power == 3.0
    assert reading.active_energy_wh == 4.0


def test_fetch_telemetry_skips_outlets_when_neither_switching_nor_metering(api: RaritanAPI) -> None:
    """Capability flags both False: no outlet reads at all."""
    from custom_components.raritan.models import CapabilityMatrix

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
    )
    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        payload = api.fetch_telemetry(cap)
    assert payload.outlets == []


def test_set_outlet_state_on_calls_setPowerState_with_PS_ON(api: RaritanAPI) -> None:
    from raritan.rpc import pdumodel

    pdu = MagicMock()
    outlet1, outlet2 = MagicMock(), MagicMock()
    pdu.getOutlets.return_value = [outlet1, outlet2]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        api.set_outlet_state(idx=2, on=True)
    outlet1.setPowerState.assert_not_called()
    outlet2.setPowerState.assert_called_once_with(pdumodel.Outlet.PowerState.PS_ON)


def test_set_outlet_state_off_calls_setPowerState_with_PS_OFF(api: RaritanAPI) -> None:
    from raritan.rpc import pdumodel

    pdu = MagicMock()
    outlet = MagicMock()
    pdu.getOutlets.return_value = [outlet]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        api.set_outlet_state(idx=1, on=False)
    outlet.setPowerState.assert_called_once_with(pdumodel.Outlet.PowerState.PS_OFF)


def test_set_outlet_state_invalid_idx_raises_value_error(api: RaritanAPI) -> None:
    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    pdu.getOutlets.return_value = [MagicMock(), MagicMock()]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanConnectionError, match="out of range"),
    ):
        api.set_outlet_state(idx=99, on=True)


def test_cycle_outlet_calls_cyclePowerState(api: RaritanAPI) -> None:
    pdu = MagicMock()
    outlet = MagicMock()
    pdu.getOutlets.return_value = [outlet]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        api.cycle_outlet(idx=1)
    outlet.cyclePowerState.assert_called_once()


def test_cycle_outlet_invalid_idx_raises_value_error(api: RaritanAPI) -> None:
    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    pdu.getOutlets.return_value = [MagicMock()]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanConnectionError, match="out of range"),
    ):
        api.cycle_outlet(idx=2)


def test_set_outlet_state_remaps_http_exception(api: RaritanAPI) -> None:
    from raritan.rpc import HttpException

    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    outlet = MagicMock()
    outlet.setPowerState.side_effect = HttpException("Connection refused")
    pdu.getOutlets.return_value = [outlet]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanConnectionError),
    ):
        api.set_outlet_state(idx=1, on=True)


def test_cycle_outlet_remaps_http_exception(api: RaritanAPI) -> None:
    from raritan.rpc import HttpException

    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    outlet = MagicMock()
    outlet.cyclePowerState.side_effect = HttpException("Connection refused")
    pdu.getOutlets.return_value = [outlet]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanConnectionError),
    ):
        api.cycle_outlet(idx=1)


# ---------------------------------------------------------------------------
# fetch_alerts
# ---------------------------------------------------------------------------


def _make_sensor_data(
    sensor_target: str = "/model/pdu/0/inlet/0/sensors/current",
    parent_target: str = "/model/pdu/0/inlet/0",
    state_name: str = "CRITICAL",
    sensor_label: str = "RMS Current",
) -> MagicMock:
    """Build a SensorData-shaped mock matching the AlertedSensorManager output."""
    sd = MagicMock()
    sensor = MagicMock()
    # raritan.rpc.Interface.__init__ exposes the RID via the public `target` attr.
    sensor.target = sensor_target
    md = MagicMock()
    md.name = sensor_label
    sensor.getMetaData.return_value = md
    parent = MagicMock()
    parent.target = parent_target
    state = MagicMock()
    state.name = state_name
    sd.sensor = sensor
    sd.parent = parent
    sd.alertState = state
    return sd


def test_fetch_alerts_returns_empty_when_no_alerts(api: RaritanAPI) -> None:
    pdu = MagicMock()
    mgr = MagicMock()
    mgr.getAlertedSensors.return_value = []
    pdu.getAlertedSensorManager.return_value = mgr
    cap = _basic_cap()
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        result = api.fetch_alerts(cap)
    assert result == []


def test_fetch_alerts_returns_snapshot_with_label_and_state(api: RaritanAPI) -> None:
    pdu = MagicMock()
    mgr = MagicMock()
    mgr.getAlertedSensors.return_value = [
        _make_sensor_data(
            sensor_target="/model/pdu/0/inlet/0/sensors/current",
            parent_target="/model/pdu/0/inlet/0",
            state_name="CRITICAL",
            sensor_label="RMS Current",
        )
    ]
    pdu.getAlertedSensorManager.return_value = mgr
    cap = _basic_cap()
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        snapshots = api.fetch_alerts(cap)
    assert len(snapshots) == 1
    s = snapshots[0]
    assert s.sensor_label == "RMS Current"
    assert s.parent_label == "/model/pdu/0/inlet/0"
    assert s.alert_state == "CRITICAL"
    assert s.sensor_id == "/model/pdu/0/inlet/0/sensors/current"


def test_fetch_alerts_returns_empty_on_auth_error(api: RaritanAPI) -> None:
    """When the role lacks permission, fetch_alerts must swallow the error."""
    from raritan.rpc import HttpException

    pdu = MagicMock()
    mgr = MagicMock()
    mgr.getAlertedSensors.side_effect = HttpException("HTTP 403 Forbidden")
    pdu.getAlertedSensorManager.return_value = mgr
    cap = _basic_cap()
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        result = api.fetch_alerts(cap)
    assert result == []


def test_fetch_alerts_returns_empty_on_unsupported_error(api: RaritanAPI) -> None:
    from raritan.rpc import HttpException

    pdu = MagicMock()
    mgr = MagicMock()
    mgr.getAlertedSensors.side_effect = HttpException("Method not supported")
    pdu.getAlertedSensorManager.return_value = mgr
    cap = _basic_cap()
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        result = api.fetch_alerts(cap)
    assert result == []


def test_fetch_alerts_propagates_connection_error(api: RaritanAPI) -> None:
    """Connection errors are NOT swallowed; they should bubble up."""
    from raritan.rpc import HttpException

    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    mgr = MagicMock()
    mgr.getAlertedSensors.side_effect = HttpException("Connection refused")
    pdu.getAlertedSensorManager.return_value = mgr
    cap = _basic_cap()
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanConnectionError),
    ):
        api.fetch_alerts(cap)


def test_fetch_alerts_caches_alerted_sensor_manager(api: RaritanAPI) -> None:
    pdu = MagicMock()
    mgr = MagicMock()
    mgr.getAlertedSensors.return_value = []
    pdu.getAlertedSensorManager.return_value = mgr
    cap = _basic_cap()
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        api.fetch_alerts(cap)
        api.fetch_alerts(cap)
    assert pdu.getAlertedSensorManager.call_count == 1


# ---------------------------------------------------------------------------
# reset_inlet_energy / reset_outlet_energy
# ---------------------------------------------------------------------------


def _basic_cap() -> CapabilityMatrix:
    return CapabilityMatrix(
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
    )


def test_reset_inlet_energy_calls_resetValue(api: RaritanAPI) -> None:
    pdu = MagicMock()
    inlet = MagicMock()
    sensors = MagicMock()
    energy = MagicMock()
    energy.resetValue = MagicMock()
    sensors.activeEnergy = energy
    inlet.getSensors.return_value = sensors
    pdu.getInlets.return_value = [inlet]
    pdu.getOutlets.return_value = []
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        api.reset_inlet_energy(idx=1)
    energy.resetValue.assert_called_once_with()


def test_reset_inlet_energy_invalid_idx_raises(api: RaritanAPI) -> None:
    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    inlet = MagicMock()
    pdu.getInlets.return_value = [inlet]
    pdu.getOutlets.return_value = []
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanConnectionError, match="out of range"),
    ):
        api.reset_inlet_energy(idx=99)


def test_reset_inlet_energy_unsupported_when_resetValue_missing(api: RaritanAPI) -> None:
    from custom_components.raritan.api import RaritanUnsupportedError

    pdu = MagicMock()
    inlet = MagicMock()
    sensors = MagicMock(spec=[])  # no activeEnergy attribute
    inlet.getSensors.return_value = sensors
    pdu.getInlets.return_value = [inlet]
    pdu.getOutlets.return_value = []
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanUnsupportedError),
    ):
        api.reset_inlet_energy(idx=1)


def test_reset_outlet_energy_calls_resetValue(api: RaritanAPI) -> None:
    pdu = MagicMock()
    outlet = MagicMock()
    sensors = MagicMock()
    energy = MagicMock()
    energy.resetValue = MagicMock()
    sensors.activeEnergy = energy
    outlet.getSensors.return_value = sensors
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [outlet]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        api.reset_outlet_energy(idx=1)
    energy.resetValue.assert_called_once_with()


def test_reset_outlet_energy_invalid_idx_raises(api: RaritanAPI) -> None:
    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [MagicMock()]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanConnectionError, match="out of range"),
    ):
        api.reset_outlet_energy(idx=99)


# ---------------------------------------------------------------------------
# OCP (over-current protector) telemetry
# ---------------------------------------------------------------------------


def _make_ocp_mock(
    label: str = "C1",
    tripped_state: tuple[bool, int] = (True, 0),
    current: float = 1.5,
    peak_current: float = 5.0,
) -> MagicMock:
    """Build a pdumodel.OverCurrentProtector mock with sensors struct.

    `tripped_state` is (available, value) for the `trip` StateSensor.
    """
    ocp = MagicMock()
    md = MagicMock()
    md.label = label
    ocp.getMetaData.return_value = md

    sensors = MagicMock()
    trip = MagicMock()
    trip_state = MagicMock()
    trip_state.available = tripped_state[0]
    trip_state.value = tripped_state[1]
    trip.getState.return_value = trip_state
    sensors.trip = trip
    sensors.current.getReading.return_value = MagicMock(value=current, valid=True)
    sensors.peakCurrent.getReading.return_value = MagicMock(value=peak_current, valid=True)
    ocp.getSensors.return_value = sensors
    return ocp


def test_fetch_telemetry_reads_ocps(api: RaritanAPI) -> None:
    """OCPs in cap.ocp_ids -> fetch_telemetry yields OcpReading rows."""
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(1, 2),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
    )
    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = [
        _make_ocp_mock(label="C1", tripped_state=(True, 1), current=1.2, peak_current=4.5),
        _make_ocp_mock(label="C2", tripped_state=(True, 0), current=0.5, peak_current=2.0),
    ]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=make_fake_bulk_helper_class(),
        ),
    ):
        payload = api.fetch_telemetry(cap)

    assert len(payload.ocps) == 2
    assert payload.ocps[0].idx == 1
    assert payload.ocps[0].label == "C1"
    assert payload.ocps[0].tripped is True
    assert payload.ocps[0].current == 1.2
    assert payload.ocps[0].peak_current == 4.5
    assert payload.ocps[1].tripped is False  # available True but value=0
    assert payload.ocps[1].current == 0.5


def test_fetch_telemetry_ocp_handles_missing_trip_sensor(api: RaritanAPI) -> None:
    """Missing trip -> tripped defaults to False."""
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(1,),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
    )
    pdu = MagicMock()
    ocp = MagicMock()
    md = MagicMock()
    md.label = "C1"
    ocp.getMetaData.return_value = md
    sensors = MagicMock(spec=["current", "peakCurrent"])  # no trip
    sensors.current.getReading.return_value = MagicMock(value=2.0, valid=True)
    sensors.peakCurrent.getReading.return_value = MagicMock(value=3.0, valid=True)
    ocp.getSensors.return_value = sensors
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = [ocp]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=make_fake_bulk_helper_class(),
        ),
    ):
        payload = api.fetch_telemetry(cap)

    assert payload.ocps[0].tripped is False
    assert payload.ocps[0].current == 2.0


def test_fetch_telemetry_ocp_handles_metadata_exception(api: RaritanAPI) -> None:
    """getMetaData raising -> label falls back to str(idx)."""
    cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="S",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(1,),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
    )
    pdu = MagicMock()
    ocp = MagicMock()
    ocp.getMetaData.side_effect = RuntimeError("boom")
    sensors = MagicMock(spec=["trip", "current", "peakCurrent"])
    trip_state = MagicMock(available=True, value=0)
    sensors.trip.getState.return_value = trip_state
    sensors.current.getReading.return_value = MagicMock(value=1.0, valid=True)
    sensors.peakCurrent.getReading.return_value = MagicMock(value=2.0, valid=True)
    ocp.getSensors.return_value = sensors
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = [ocp]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=make_fake_bulk_helper_class(),
        ),
    ):
        payload = api.fetch_telemetry(cap)

    assert payload.ocps[0].label == "1"
    assert payload.ocps[0].tripped is False


# ---------------------------------------------------------------------------
# Env (peripheral) sensor discovery + telemetry
# ---------------------------------------------------------------------------


def _make_peripheral_device(
    serial: str,
    *,
    numeric: tuple[int, int, float] | None = None,
    state: tuple[int, int, bool] | None = None,
) -> MagicMock:
    """Build a peripheral.Device mock.

    Per the SDK, peripheral.Device.device is a single sensors.Sensor proxy
    (never a list). Provide exactly one of `numeric=(readingtype, unit, value)`
    or `state=(readingtype, unit, state_value_int)`.
    """
    if (numeric is None) == (state is None):
        raise ValueError("Provide exactly one of `numeric` or `state`")
    device = MagicMock()
    device.deviceID.serial = serial

    sensor = MagicMock()
    if numeric is not None:
        rt, unit, val = numeric
        sensor.getTypeSpec.return_value = MagicMock(readingtype=rt, unit=unit)
        sensor.getReading.return_value = MagicMock(value=val, valid=True)
        del sensor.getState  # heuristic uses presence of getReading vs getState
    else:
        assert state is not None
        rt, unit, val = state
        sensor.getTypeSpec.return_value = MagicMock(readingtype=rt, unit=unit)
        sensor.getState.return_value = MagicMock(available=True, value=int(val))
        del sensor.getReading

    device.device = sensor
    return device


def _make_peripheral_slot(device: MagicMock) -> MagicMock:
    slot = MagicMock()
    slot.getDevice.return_value = device
    return slot


def test_probe_discovers_env_sensors(api: RaritanAPI) -> None:
    """Env sensor IDs are populated from peripheral DeviceManager."""
    pdu = MagicMock()
    md = MagicMock()
    nameplate = MagicMock(model="X", serialNumber="S", manufacturer="Raritan")
    md.nameplate = nameplate
    md.fwRevision = "4.0.10"
    md.hwRevision = None
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = []

    # Mock the peripheral device manager. Each peripheral.Device wraps a
    # single sensor; multi-sensor peripherals expose one slot per sensor.
    mgr = MagicMock()
    num_slot = _make_peripheral_slot(
        _make_peripheral_device(serial="DEV001", numeric=(8, 7, 23.4)),  # TEMP °C
    )
    state_slot = _make_peripheral_slot(
        _make_peripheral_device(serial="DEV002", state=(12, 0, True)),  # CONTACT
    )
    mgr.getDeviceSlots.return_value = [num_slot, state_slot]
    pdu.getPeripheralDeviceManager.return_value = mgr

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        cap = api.probe()

    # 1 numeric + 1 state sensor (one per slot)
    assert len(cap.env_sensor_ids) == 2
    # Stable ID format: "<serial>:n0" for numeric, "<serial>:s0" for state
    assert "DEV001:n0" in cap.env_sensor_ids
    assert "DEV002:s0" in cap.env_sensor_ids


def test_probe_env_discovery_swallows_errors(api: RaritanAPI) -> None:
    """If peripheral discovery raises, env_sensor_ids = ()."""
    pdu = MagicMock()
    md = MagicMock()
    nameplate = MagicMock(model="X", serialNumber="S", manufacturer="Raritan")
    md.nameplate = nameplate
    md.fwRevision = "4.0.10"
    md.hwRevision = None
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = []
    pdu.getPeripheralDeviceManager.side_effect = RuntimeError("forbidden")

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        cap = api.probe()

    assert cap.env_sensor_ids == ()


def _pdu_with_peripherals(slots: list[MagicMock]) -> MagicMock:
    pdu = MagicMock()
    mgr = MagicMock()
    mgr.getDeviceSlots.return_value = slots
    pdu.getPeripheralDeviceManager.return_value = mgr
    return pdu


def test_refresh_env_sensors_detects_peripherals(api: RaritanAPI) -> None:
    """refresh_env_sensors returns the freshly discovered peripheral IDs."""
    pdu = _pdu_with_peripherals(
        [_make_peripheral_slot(_make_peripheral_device(serial="DEV9", numeric=(8, 7, 21.0)))]
    )
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        ids = api.refresh_env_sensors()
    assert "DEV9:n0" in ids


def test_refresh_env_sensors_preserves_on_failure(api: RaritanAPI) -> None:
    """A failed rescan keeps the previously known set instead of wiping it."""
    pdu = _pdu_with_peripherals(
        [_make_peripheral_slot(_make_peripheral_device(serial="DEV9", numeric=(8, 7, 21.0)))]
    )
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        first = api.refresh_env_sensors()
    assert "DEV9:n0" in first
    # The connection is cached, so the next call hits the same pdu mock; make
    # its peripheral manager raise and confirm the prior set survives.
    pdu.getPeripheralDeviceManager.side_effect = RuntimeError("transient")
    second = api.refresh_env_sensors()
    assert second == first


def test_fetch_telemetry_reads_env_sensors(api: RaritanAPI) -> None:
    """Env numeric + state sensors are read via the bulk helper."""
    pdu = MagicMock()
    md = MagicMock()
    nameplate = MagicMock(model="X", serialNumber="S", manufacturer="Raritan")
    md.nameplate = nameplate
    md.fwRevision = "4.0.10"
    md.hwRevision = None
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = []

    # Three single-sensor peripherals: TEMP, HUMIDITY, CONTACT.
    mgr = MagicMock()
    mgr.getDeviceSlots.return_value = [
        _make_peripheral_slot(_make_peripheral_device(serial="TEMP1", numeric=(8, 7, 21.5))),
        _make_peripheral_slot(_make_peripheral_device(serial="HUM1", numeric=(9, 9, 55.0))),
        _make_peripheral_slot(_make_peripheral_device(serial="CON1", state=(12, 0, True))),
    ]
    pdu.getPeripheralDeviceManager.return_value = mgr

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=make_fake_bulk_helper_class(),
        ),
    ):
        cap = api.probe()
        payload = api.fetch_telemetry(cap)

    assert len(payload.env) == 3
    temps = [r for r in payload.env if r.sensor_type == "TEMPERATURE"]
    assert len(temps) == 1
    assert temps[0].value == 21.5
    assert temps[0].unit == "°C"
    hum = [r for r in payload.env if r.sensor_type == "HUMIDITY"]
    assert hum[0].value == 55.0
    contacts = [r for r in payload.env if r.sensor_type == "CONTACT"]
    assert contacts[0].state is True
    assert contacts[0].value is None


def test_probe_env_discovery_handles_http_exception(api: RaritanAPI) -> None:
    """HttpException from getPeripheralDeviceManager -> env_sensor_ids = ()."""
    from raritan.rpc import HttpException

    pdu = MagicMock()
    md = MagicMock()
    nameplate = MagicMock(model="X", serialNumber="S", manufacturer="Raritan")
    md.nameplate = nameplate
    md.fwRevision = "4.0.10"
    md.hwRevision = None
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = []
    pdu.getPeripheralDeviceManager.side_effect = HttpException("HTTP 403 Forbidden")

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        cap = api.probe()
    assert cap.env_sensor_ids == ()


def test_probe_env_discovery_skips_empty_or_failing_slots(api: RaritanAPI) -> None:
    """Slots with getDevice raising or returning None are skipped."""
    pdu = MagicMock()
    md = MagicMock()
    nameplate = MagicMock(model="X", serialNumber="S", manufacturer="Raritan")
    md.nameplate = nameplate
    md.fwRevision = "4.0.10"
    md.hwRevision = None
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = []

    mgr = MagicMock()
    bad_slot = MagicMock()
    bad_slot.getDevice.side_effect = RuntimeError("boom")
    empty_slot = MagicMock()
    empty_slot.getDevice.return_value = None
    good_slot = MagicMock()
    good_slot.getDevice.return_value = _make_peripheral_device(
        serial="DEV-OK", numeric=(8, 7, 21.0)
    )
    mgr.getDeviceSlots.return_value = [bad_slot, empty_slot, good_slot]
    pdu.getPeripheralDeviceManager.return_value = mgr

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
    ):
        cap = api.probe()

    # Only the good slot contributed
    assert len(cap.env_sensor_ids) == 1
    assert "DEV-OK:n0" in cap.env_sensor_ids


def test_fetch_telemetry_env_empty_when_no_peripherals(api: RaritanAPI) -> None:
    """No peripherals -> env list is empty."""
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
    )
    pdu = MagicMock()
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
    ):
        payload = api.fetch_telemetry(cap)
    assert payload.env == []


def test_reset_outlet_energy_unsupported_when_resetValue_missing(api: RaritanAPI) -> None:
    """Outlet activeEnergy missing resetValue -> RaritanUnsupportedError."""
    from custom_components.raritan.api import RaritanUnsupportedError

    pdu = MagicMock()
    outlet = MagicMock()
    sensors = MagicMock(spec=[])  # no activeEnergy
    outlet.getSensors.return_value = sensors
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = [outlet]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=make_fake_bulk_helper_class(),
        ),
        pytest.raises(RaritanUnsupportedError),
    ):
        api.reset_outlet_energy(idx=1)


def test_state_or_false_on_exception_returns_false() -> None:
    """The decoder returns False when handed an Exception object."""
    from custom_components.raritan.api import _state_or_false

    assert _state_or_false(RuntimeError("boom")) is False


def test_state_or_false_unavailable_returns_false() -> None:
    """available=False -> False even if value=1."""
    from custom_components.raritan.api import _state_or_false

    state = MagicMock(available=False, value=1)
    assert _state_or_false(state) is False


def test_classify_sensor_handles_metadata_exception() -> None:
    """If getMetaData raises, classification falls back to (UNKNOWN, None)."""
    from custom_components.raritan.api import RaritanAPI as _API

    sensor = MagicMock()
    sensor.getMetaData.side_effect = RuntimeError("boom")
    assert _API._classify_sensor(sensor) == ("UNKNOWN", None)


def test_classify_sensor_handles_missing_type_spec() -> None:
    """MetaData without `type` attribute -> (UNKNOWN, None)."""
    from custom_components.raritan.api import RaritanAPI as _API

    sensor = MagicMock()
    md = MagicMock(spec=["foo"])
    sensor.getMetaData.return_value = md
    # spec=['foo'] means accessing .type returns from spec; we want None
    md.type = None
    assert _API._classify_sensor(sensor) == ("UNKNOWN", None)


def test_reset_inlet_energy_remaps_http_exception(api: RaritanAPI) -> None:
    from raritan.rpc import HttpException

    from custom_components.raritan.api import RaritanConnectionError

    pdu = MagicMock()
    inlet = MagicMock()
    sensors = MagicMock()
    energy = MagicMock()
    energy.resetValue = MagicMock(side_effect=HttpException("Connection refused"))
    sensors.activeEnergy = energy
    inlet.getSensors.return_value = sensors
    pdu.getInlets.return_value = [inlet]
    pdu.getOutlets.return_value = []
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch("custom_components.raritan.api.BulkRequestHelper", new=make_fake_bulk_helper_class()),
        pytest.raises(RaritanConnectionError),
    ):
        api.reset_inlet_energy(idx=1)


def test_reset_outlet_energy_remaps_insufficient_privileges_to_auth_error(
    api: RaritanAPI,
) -> None:
    """When the PDU role lacks "Reset Energy Counters" perm, the SDK raises
    JsonRpcErrorException, not HttpException. It must still remap cleanly to
    RaritanAuthError so HA surfaces a sensible error.
    """
    from raritan.rpc import JsonRpcErrorException

    from custom_components.raritan.api import RaritanAuthError

    pdu = MagicMock()
    outlet = MagicMock()
    energy = MagicMock()
    energy.resetValue.side_effect = JsonRpcErrorException(
        "JSON RPC returned error: code = -32001, msg = Insufficient privileges to perform request."
    )
    sensors = MagicMock()
    sensors.activeEnergy = energy
    outlet.getSensors.return_value = sensors
    pdu.getOutlets.return_value = [outlet]
    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        pytest.raises(RaritanAuthError, match="Insufficient privileges"),
    ):
        api.reset_outlet_energy(idx=1)
