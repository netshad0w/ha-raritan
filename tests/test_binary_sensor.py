"""Tests for binary sensors (OCP tripped + env state sensors)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from homeassistant import config_entries
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_ocp_tripped_binary_sensors_created(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """One OCP tripped binary_sensor is created per OCP in cap.ocp_ids."""
    # Mock OCP sensors so they all return non-tripped (available=True, value=0)
    for ocp in mock_raritan.getOverCurrentProtectors.return_value:
        sensors = MagicMock()
        trip_state = MagicMock(available=True, value=0)
        sensors.trip.getState.return_value = trip_state
        sensors.current.getReading.return_value = MagicMock(value=0.0, valid=True)
        sensors.peakCurrent.getReading.return_value = MagicMock(value=0.0, valid=True)
        ocp.getSensors.return_value = sensors
        md = MagicMock(label="C")
        ocp.getMetaData.return_value = md

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
    tripped_entries = [
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN and e.unique_id.endswith("_tripped")
    ]
    # Snapshot has 6 OCPs
    assert len(tripped_entries) == 6
    # Verify state: should be off (not tripped)
    states = hass.states.async_all("binary_sensor")
    assert any(
        s.state == "off" and s.attributes.get("device_class") == BinarySensorDeviceClass.PROBLEM
        for s in states
    )


async def test_ocp_tripped_state_reflects_tripped_breaker(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """When trip.getState returns available=True, value=1, is_on is True."""
    ocps = mock_raritan.getOverCurrentProtectors.return_value
    # Set first OCP as tripped, rest as healthy
    for i, ocp in enumerate(ocps):
        sensors = MagicMock()
        trip_state = MagicMock(available=True, value=1 if i == 0 else 0)
        sensors.trip.getState.return_value = trip_state
        sensors.current.getReading.return_value = MagicMock(value=0.0, valid=True)
        sensors.peakCurrent.getReading.return_value = MagicMock(value=0.0, valid=True)
        ocp.getSensors.return_value = sensors
        ocp.getMetaData.return_value = MagicMock(label=f"C{i + 1}")

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
    ocp_1 = next(
        e for e in registry.entities.values() if e.unique_id == "TEST00000001_ocp_1_tripped"
    )
    state = hass.states.get(ocp_1.entity_id)
    assert state is not None
    assert state.state == "on"


async def test_ocp_sub_device_hierarchy(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    """Each OCP must be a sub-device of the PDU (linked via via_device)."""
    from homeassistant.helpers import device_registry as dr

    # Set up basic OCP sensor mocks
    for ocp in mock_raritan.getOverCurrentProtectors.return_value:
        sensors = MagicMock()
        trip_state = MagicMock(available=True, value=0)
        sensors.trip.getState.return_value = trip_state
        sensors.current.getReading.return_value = MagicMock(value=0.0, valid=True)
        sensors.peakCurrent.getReading.return_value = MagicMock(value=0.0, valid=True)
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

    devreg = dr.async_get(hass)
    pdu_dev = next(d for d in devreg.devices.values() if (DOMAIN, "TEST00000001") in d.identifiers)
    ocp_devs = [
        d
        for d in devreg.devices.values()
        if d.via_device_id == pdu_dev.id and any("_ocp_" in i for _, i in d.identifiers)
    ]
    assert len(ocp_devs) == 6


async def test_no_binary_sensor_when_no_ocp_or_env(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """If cap has no OCPs and no env state sensors, no binary_sensor entities are added.

    The default snapshot has 6 OCPs, so we patch to remove them.
    """
    mock_raritan.getOverCurrentProtectors.return_value = []
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
    states = hass.states.async_all("binary_sensor")
    assert states == []


async def test_env_state_binary_sensor_created_for_contact(
    hass: HomeAssistant,
) -> None:
    """A peripheral CONTACT_CLOSURE state sensor -> binary_sensor with OPENING device class."""
    from raritan.rpc import pdumodel

    _DUMMY = MagicMock()
    pdu_spec = pdumodel.Pdu("/x", _DUMMY)
    pdu = MagicMock(spec=pdu_spec)

    # Minimal pdu metadata
    md = MagicMock()
    nameplate = MagicMock(
        manufacturer="Raritan",
        model="X",
        serialNumber="ENVTEST1",
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

    # Build the peripheral device with one CONTACT state sensor
    mgr = MagicMock()
    slot = MagicMock()
    device = MagicMock()
    device.deviceID.serial = "DEV-CONTACT-1"
    # peripheral.Device.device is a single sensors.Sensor proxy. State sensors
    # expose getState (and not getReading); the heuristic uses presence of
    # getReading vs getState to classify.
    inner = MagicMock()
    spec = MagicMock(readingtype=12, unit=0)  # CONTACT_CLOSURE
    inner.getTypeSpec.return_value = spec
    inner.getState.return_value = MagicMock(available=True, value=1)
    del inner.getReading  # force the heuristic to classify as state
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

    states = hass.states.async_all("binary_sensor")
    # No OCPs, just the 1 env state sensor.
    contact_states = [
        s for s in states if s.attributes.get("device_class") == BinarySensorDeviceClass.OPENING
    ]
    assert len(contact_states) == 1
    assert contact_states[0].state == "on"


def test_is_state_env_classification() -> None:
    """_is_state_env: state peripherals qualify, numeric do not, and a fully
    unclassified reading (no value, no state) is dropped, not turned into a
    permanently-unavailable ghost binary sensor."""
    from custom_components.raritan.binary_sensor import _is_state_env
    from custom_components.raritan.models import EnvSensorReading

    def reading(sensor_type: str, value: float | None, state: bool | None) -> EnvSensorReading:
        return EnvSensorReading(
            sensor_id="DEV:s0",
            label="x",
            sensor_type=sensor_type,
            value=value,
            state=state,
            unit=None,
        )

    # Mapped state type that reports a state -> binary sensor.
    assert _is_state_env(reading("CONTACT", None, True)) is True
    # Numeric reading (value, no state) -> not a binary sensor.
    assert _is_state_env(reading("TEMPERATURE", 23.4, None)) is False
    # Unmapped type but actually reports a state -> still a binary sensor.
    assert _is_state_env(reading("UNKNOWN", None, False)) is True
    # Fully unclassified (no value AND no state) -> dropped.
    assert _is_state_env(reading("UNKNOWN", None, None)) is False
