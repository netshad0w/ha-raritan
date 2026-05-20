"""Tests for outlet switch entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON

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


async def _setup_entry_with_outlets(hass: HomeAssistant) -> None:
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


async def test_switches_created_when_outlet_switching_enabled(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup_entry_with_outlets(hass)
    switches = [s for s in hass.states.async_all() if s.entity_id.startswith("switch.")]
    assert len(switches) == 2  # 2 outlets in fixture


async def test_switch_device_class_is_outlet(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup_entry_with_outlets(hass)
    switches = [s for s in hass.states.async_all() if s.entity_id.startswith("switch.")]
    for s in switches:
        assert s.attributes.get("device_class") == SwitchDeviceClass.OUTLET


async def test_switch_state_reflects_coordinator_data(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup_entry_with_outlets(hass)
    # Outlet 1 fixture is ON, outlet 2 is OFF
    switches = sorted(
        [s for s in hass.states.async_all() if s.entity_id.startswith("switch.")],
        key=lambda s: s.entity_id,
    )
    states = [s.state for s in switches]
    assert "on" in states
    assert "off" in states


async def test_turn_on_calls_setPowerState_with_1(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    from raritan.rpc import pdumodel

    await _setup_entry_with_outlets(hass)
    switches = [s for s in hass.states.async_all() if s.entity_id.startswith("switch.")]
    # Find a switch that is currently OFF (outlet 2 in our fixture)
    target = next(s for s in switches if s.state == "off")
    outlet_2 = mock_raritan_with_outlets.getOutlets.return_value[1]
    initial_calls = outlet_2.setPowerState.call_count

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: target.entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    # SDK called with PowerState.PS_ON enum
    assert outlet_2.setPowerState.call_count > initial_calls
    outlet_2.setPowerState.assert_called_with(pdumodel.Outlet.PowerState.PS_ON)


async def test_turn_off_calls_setPowerState_with_0(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    from raritan.rpc import pdumodel

    await _setup_entry_with_outlets(hass)
    switches = [s for s in hass.states.async_all() if s.entity_id.startswith("switch.")]
    target = next(s for s in switches if s.state == "on")
    outlet_1 = mock_raritan_with_outlets.getOutlets.return_value[0]
    initial_calls = outlet_1.setPowerState.call_count

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: target.entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert outlet_1.setPowerState.call_count > initial_calls
    outlet_1.setPowerState.assert_called_with(pdumodel.Outlet.PowerState.PS_OFF)
