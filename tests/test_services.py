"""Tests for raritan services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

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


async def test_services_registered(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    assert hass.services.has_service(DOMAIN, "cycle_outlet")
    assert hass.services.has_service(DOMAIN, "set_outlet_state")


async def test_cycle_outlet_service_calls_sdk(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    switches = [s for s in hass.states.async_all() if s.entity_id.startswith("switch.")]
    target = switches[0]
    outlet_0 = mock_raritan_with_outlets.getOutlets.return_value[0]
    initial = outlet_0.cyclePowerState.call_count

    await hass.services.async_call(
        DOMAIN,
        "cycle_outlet",
        {ATTR_ENTITY_ID: target.entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert outlet_0.cyclePowerState.call_count > initial


async def test_set_outlet_state_service_calls_sdk(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    from raritan.rpc import pdumodel

    await _setup(hass)
    switches = [s for s in hass.states.async_all() if s.entity_id.startswith("switch.")]
    # outlet 2 (idx 2) is OFF in fixture; turning it ON
    target = next(s for s in switches if s.state == "off")
    outlet_1 = mock_raritan_with_outlets.getOutlets.return_value[1]
    initial = outlet_1.setPowerState.call_count

    await hass.services.async_call(
        DOMAIN,
        "set_outlet_state",
        {ATTR_ENTITY_ID: target.entity_id, "state": True},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert outlet_1.setPowerState.call_count > initial
    outlet_1.setPowerState.assert_called_with(pdumodel.Outlet.PowerState.PS_ON)


async def test_reset_energy_counter_service_registered(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    assert hass.services.has_service(DOMAIN, "reset_energy_counter")


async def test_reset_energy_counter_service_calls_outlet_resetValue(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """Targeting an outlet active_energy sensor calls outlet's resetValue()."""
    await _setup(hass)
    energy_states = [
        s
        for s in hass.states.async_all()
        if s.entity_id.startswith("sensor.") and "active_energy" in s.entity_id
    ]
    # Find outlet active_energy sensor (not inlet)
    outlet_energy = next(
        s
        for s in energy_states
        if "outlet" in s.entity_id or "outlet" in s.attributes.get("friendly_name", "").lower()
    )
    outlet_1 = mock_raritan_with_outlets.getOutlets.return_value[0]
    sensors = outlet_1.getSensors.return_value
    sensors.activeEnergy.resetValue.reset_mock()

    await hass.services.async_call(
        DOMAIN,
        "reset_energy_counter",
        {ATTR_ENTITY_ID: outlet_energy.entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert sensors.activeEnergy.resetValue.call_count >= 1


async def test_reset_energy_counter_service_calls_inlet_resetValue(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """Targeting an inlet active_energy sensor calls inlet's resetValue()."""
    await _setup(hass)
    inlet_energy = next(
        s
        for s in hass.states.async_all()
        if s.entity_id.startswith("sensor.")
        and "inlet" in s.entity_id
        and "active_energy" in s.entity_id
    )
    inlet = mock_raritan_with_outlets.getInlets.return_value[0]
    sensors = inlet.getSensors.return_value
    # Reset value mock for tracking
    sensors.activeEnergy.resetValue.reset_mock()

    await hass.services.async_call(
        DOMAIN,
        "reset_energy_counter",
        {ATTR_ENTITY_ID: inlet_energy.entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert sensors.activeEnergy.resetValue.call_count >= 1
