"""Tests for inlet sensor entities."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfEnergy,
)

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_inlet_sensors_created_with_energy_dashboard_classes(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    inlet = mock_raritan.getInlets.return_value[0]
    sensors = inlet.getSensors.return_value
    sensors.voltage.getReading.return_value = MagicMock(value=230.1, valid=True)
    sensors.current.getReading.return_value = MagicMock(value=4.5, valid=True)
    sensors.activePower.getReading.return_value = MagicMock(value=1035, valid=True)
    sensors.apparentPower.getReading.return_value = MagicMock(value=1100, valid=True)
    sensors.powerFactor.getReading.return_value = MagicMock(value=0.94, valid=True)
    sensors.lineFrequency.getReading.return_value = MagicMock(value=50.0, valid=True)
    sensors.activeEnergy.getReading.return_value = MagicMock(value=12345678, valid=True)

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

    # Find any voltage sensor
    voltage_states = [s for s in hass.states.async_all() if "voltage" in s.entity_id]
    assert len(voltage_states) >= 1
    voltage_state = voltage_states[0]
    assert voltage_state.attributes["device_class"] == SensorDeviceClass.VOLTAGE
    assert voltage_state.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert voltage_state.attributes["unit_of_measurement"] == UnitOfElectricPotential.VOLT

    # Active energy must be total_increasing kWh
    energy_states = [s for s in hass.states.async_all() if "active_energy" in s.entity_id]
    assert len(energy_states) >= 1
    energy_state = energy_states[0]
    assert energy_state.attributes["device_class"] == SensorDeviceClass.ENERGY
    assert energy_state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING
    assert energy_state.attributes["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
    # Wh -> kWh conversion
    assert float(energy_state.state) == 12345.678


async def test_inlet_sensor_unique_id_uses_serial_namespace(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
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
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    # Find any voltage entity, then check unique_id format
    voltage_entries = [
        e for e in registry.entities.values() if e.platform == DOMAIN and "voltage" in e.unique_id
    ]
    assert len(voltage_entries) >= 1
    assert voltage_entries[0].unique_id.startswith("TEST00000001_inlet_")
    assert voltage_entries[0].unique_id.endswith("_voltage")


async def test_inlet_sensor_invalid_reading_yields_none_state(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    inlet = mock_raritan.getInlets.return_value[0]
    inlet.getSensors.return_value.voltage.getReading.return_value = MagicMock(value=0, valid=False)
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

    voltage_states = [s for s in hass.states.async_all() if "voltage" in s.entity_id]
    assert len(voltage_states) >= 1
    # Invalid reading -> state in {"unknown", "unavailable", "None"}
    assert voltage_states[0].state in ("unknown", "unavailable", "None")


async def test_outlet_sensors_created_when_metering_enabled(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """When the PDU is metered, outlet sensors appear with sub-device hierarchy."""
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

    # Each outlet should have 5 sensors -> 2 outlets x 5 = 10 outlet sensor entities
    outlet_sensors = [
        s
        for s in hass.states.async_all()
        if "outlet" in s.entity_id and s.entity_id.startswith("sensor.")
    ]
    assert len(outlet_sensors) == 10

    # Inlet sensors still present
    inlet_voltage = [
        s for s in hass.states.async_all() if "inlet" in s.entity_id and "voltage" in s.entity_id
    ]
    assert len(inlet_voltage) >= 1


async def test_inlet_single_feed_attaches_to_pdu_device(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Single-inlet PDU: inlet sensors stay flat on the PDU device.

    The 99% case: adding a sub-device for the only feed would just deepen
    navigation without conveying useful information.
    """
    from homeassistant.helpers import device_registry as dr

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

    devreg = dr.async_get(hass)
    # No inlet sub-device should exist on a single-inlet PDU.
    inlet_devs = [
        d for d in devreg.devices.values() if any("_inlet_" in i for _, i in d.identifiers)
    ]
    assert inlet_devs == []

    # Flat on the PDU device, so the entity name keeps the "Inlet 1" qualifier
    # itself (otherwise it'd be a bare "Active power" on the shared PDU device).
    fnames = {s.entity_id: s.attributes.get("friendly_name") for s in hass.states.async_all()}
    assert (
        fnames.get("sensor.raritan_px3_5487v_n2_test00000001_inlet_1_active_power")
        == "Raritan PX3-5487V-N2 (TEST00000001) Inlet 1 active power"
    )


async def test_inlet_multi_feed_creates_sub_device_per_inlet(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Multi-inlet PDU (e.g. ATS, dual-feed): one sub-device per inlet.

    Each feed becomes addressable as its own HA device so users can assign
    Source A vs Source B to different Areas, alert per-source, etc.
    """
    from homeassistant.helpers import device_registry as dr

    # Add a second inlet to the mock, same shape as the existing one so the
    # bulk readings still work. The integration treats len(getInlets()) as
    # nb_inlets and generates one DeviceInfo per inlet. The Raritan SDK
    # attaches methods at instance construction time, so we use the same
    # spec instance the conftest does (a real pdumodel.Inlet) to keep the
    # MagicMock attributes aligned with the SDK shape.
    from raritan.rpc import pdumodel  # type: ignore[import-not-found]

    inlet_proto = mock_raritan.getInlets.return_value[0]
    inlet_spec = pdumodel.Inlet("/x", MagicMock())
    second_inlet = MagicMock(spec=inlet_spec)
    second_metadata = MagicMock()
    second_metadata.label = "I2"
    second_inlet.getMetaData.return_value = second_metadata
    second_inlet.getSensors.return_value = inlet_proto.getSensors.return_value
    mock_raritan.getInlets.return_value = [inlet_proto, second_inlet]

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

    # Confirm probe saw 2 inlets. If this fails, the cap discovery is wrong
    # rather than the device-hierarchy logic.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.runtime_data.capabilities.nb_inlets == 2

    from homeassistant.helpers import entity_registry as er

    er_reg = er.async_get(hass)
    inlet_entities = [
        e
        for e in er_reg.entities.values()
        if e.platform == DOMAIN and "_inlet_" in (e.unique_id or "")
    ]
    # 2 inlets * 7 inlet sensors = 14 entities, exactly (a phantom inlet or a
    # unique_id collision would leak extras, so pin the count).
    assert len(inlet_entities) == 14, (
        f"expected 14 inlet entities, got {len(inlet_entities)}: "
        f"{[(e.entity_id, e.unique_id) for e in inlet_entities]}"
    )

    devreg = dr.async_get(hass)
    pdu_dev = next(d for d in devreg.devices.values() if (DOMAIN, "TEST00000001") in d.identifiers)
    inlet_devs = [
        d
        for d in devreg.devices.values()
        if d.via_device_id == pdu_dev.id and any("_inlet_" in i for _, i in d.identifiers)
    ]
    assert len(inlet_devs) == 2
    # Bare names; the PDU is carried by via_device + serial_number, not a prefix.
    inlet_names = sorted(d.name or "" for d in inlet_devs)
    assert inlet_names == ["Inlet 1", "Inlet 2"]
    assert all(d.serial_number == "TEST00000001" for d in inlet_devs)

    # The "Inlet N" sub-device carries the qualifier, so the entity name is bare
    # and HA composes "Inlet 2 Active power" -- never the doubled
    # "Inlet 2 Inlet 2 active power".
    fnames = {s.entity_id: s.attributes.get("friendly_name") for s in hass.states.async_all()}
    assert fnames.get("sensor.inlet_2_active_power") == "Inlet 2 Active power"
    assert fnames.get("sensor.inlet_1_active_power") == "Inlet 1 Active power"


async def test_outlet_sensors_have_sub_device_hierarchy(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """Each outlet should be a sub-device of the PDU (linked via via_device)."""
    from homeassistant.helpers import device_registry as dr

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

    devreg = dr.async_get(hass)
    pdu_dev = next(d for d in devreg.devices.values() if (DOMAIN, "TEST00000001") in d.identifiers)
    outlet_devs = [d for d in devreg.devices.values() if d.via_device_id == pdu_dev.id]
    assert len(outlet_devs) == 2
    # Bare names ("Outlet 1") so entities read "Outlet 1 Active power" rather
    # than repeating the PDU model+serial; serial_number disambiguates PDUs.
    outlet_names = sorted(d.name for d in outlet_devs)
    assert outlet_names == ["Outlet 1", "Outlet 2"]
    assert all(d.serial_number == "TEST00000001" for d in outlet_devs)


async def test_ocp_current_sensors_created(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    """OCP current + peak_current sensors are created (one pair per OCP)."""
    for ocp in mock_raritan.getOverCurrentProtectors.return_value:
        sensors = MagicMock()
        sensors.trip.getState.return_value = MagicMock(available=True, value=0)
        sensors.current.getReading.return_value = MagicMock(value=1.5, valid=True)
        sensors.peakCurrent.getReading.return_value = MagicMock(value=4.5, valid=True)
        ocp.getSensors.return_value = sensors
        ocp.getMetaData.return_value = MagicMock(label="C")

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

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    ocp_current = [
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN
        and e.unique_id.startswith("TEST00000001_ocp_")
        and e.unique_id.endswith("_current")
        and not e.unique_id.endswith("_peak_current")
    ]
    ocp_peak = [
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN
        and e.unique_id.startswith("TEST00000001_ocp_")
        and e.unique_id.endswith("_peak_current")
    ]
    # 6 OCPs in snapshot
    assert len(ocp_current) == 6
    assert len(ocp_peak) == 6

    state = hass.states.get(ocp_current[0].entity_id)
    assert state is not None
    assert float(state.state) == 1.5
    assert state.attributes["device_class"] == SensorDeviceClass.CURRENT


async def test_env_temperature_numeric_sensor_created(hass: HomeAssistant) -> None:
    """A peripheral TEMPERATURE numeric sensor -> sensor entity with TEMPERATURE class."""
    from unittest.mock import patch

    from raritan.rpc import pdumodel

    _DUMMY = MagicMock()
    pdu_spec = pdumodel.Pdu("/x", _DUMMY)
    pdu = MagicMock(spec=pdu_spec)

    md = MagicMock()
    nameplate = MagicMock(
        manufacturer="Raritan",
        model="X",
        serialNumber="ENVTEMP1",
        partNumber="P",
        macAddress="00:11:22:33:44:55",
    )
    md.nameplate = nameplate
    md.fwRevision = "4.0.10"
    md.hwRevision = None
    md.hasSwitchableOutlets = False
    md.hasMeteredOutlets = False
    pdu.getMetaData.return_value = md
    pdu.getInlets.return_value = []
    pdu.getOutlets.return_value = []
    pdu.getOverCurrentProtectors.return_value = []
    pdu.getAlertedSensorManager.return_value = MagicMock(
        getAlertedSensors=MagicMock(return_value=[])
    )

    mgr = MagicMock()
    slot = MagicMock()
    device = MagicMock()
    device.deviceID.serial = "DEV-TEMP-1"
    # peripheral.Device.device is a single sensors.Sensor proxy. Numeric
    # sensors expose getReading; classification reads getTypeSpec.
    inner = MagicMock()
    spec = MagicMock(readingtype=8, unit=7)  # TEMPERATURE °C
    inner.getTypeSpec.return_value = spec
    inner.getReading.return_value = MagicMock(value=22.5, valid=True)
    del inner.getState  # force the heuristic to classify as numeric
    device.device = inner
    slot.getDevice.return_value = device
    mgr.getDeviceSlots.return_value = [slot]
    pdu.getPeripheralDeviceManager.return_value = mgr

    def _bulk_factory(_agent: object) -> MagicMock:
        instance = MagicMock()
        queued: list = []

        def _add_request(method: object, *args: object) -> None:
            queued.append((method, args))

        def _perform_bulk() -> list:
            results = []
            for m, a in queued:
                try:
                    results.append(m(*a))
                except Exception as exc:
                    results.append(exc)
            queued.clear()
            return results

        instance.add_request.side_effect = _add_request
        instance.perform_bulk.side_effect = _perform_bulk
        return instance

    with (
        patch("custom_components.raritan.api.Agent"),
        patch("custom_components.raritan.api.pdumodel.Pdu", return_value=pdu),
        patch(
            "custom_components.raritan.api.BulkRequestHelper",
            new=MagicMock(side_effect=_bulk_factory),
        ),
    ):
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

    states = hass.states.async_all("sensor")
    temp_states = [
        s for s in states if s.attributes.get("device_class") == SensorDeviceClass.TEMPERATURE
    ]
    assert len(temp_states) == 1
    assert float(temp_states[0].state) == 22.5
    assert temp_states[0].attributes["unit_of_measurement"] == "°C"


async def test_outlet_active_energy_sensor_total_increasing(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """Outlet active_energy sensor must be TOTAL_INCREASING + kWh for Energy Dashboard."""
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

    energy_states = [
        s
        for s in hass.states.async_all()
        if "outlet" in s.entity_id and "active_energy" in s.entity_id
    ]
    assert len(energy_states) == 2
    for s in energy_states:
        assert s.attributes["device_class"] == SensorDeviceClass.ENERGY
        assert s.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING
        assert s.attributes["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
