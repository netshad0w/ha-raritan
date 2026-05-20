"""Error-path tests for the raritan service handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError

from custom_components.raritan.api import RaritanAPIError
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


def _coordinator(hass: HomeAssistant):
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinator


def _first_switch(hass: HomeAssistant) -> str:
    return next(s.entity_id for s in hass.states.async_all() if s.entity_id.startswith("switch."))


async def test_cycle_outlet_unresolvable_entity_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    with pytest.raises(HomeAssistantError, match="Cannot resolve"):
        await hass.services.async_call(
            DOMAIN, "cycle_outlet", {ATTR_ENTITY_ID: "switch.nonexistent"}, blocking=True
        )


async def test_cycle_outlet_non_outlet_entity_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """A raritan entity whose unique_id is not an outlet switch resolves to None."""
    await _setup(hass)
    voltage_sensor = next(
        s.entity_id
        for s in hass.states.async_all()
        if s.entity_id.startswith("sensor.") and "voltage" in s.entity_id
    )
    with pytest.raises(HomeAssistantError, match="Cannot resolve"):
        await hass.services.async_call(
            DOMAIN, "cycle_outlet", {ATTR_ENTITY_ID: voltage_sensor}, blocking=True
        )


async def test_cycle_outlet_api_error_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    _coordinator(hass).async_cycle_outlet = AsyncMock(side_effect=RaritanAPIError("boom"))
    with pytest.raises(HomeAssistantError, match="Failed to cycle outlet"):
        await hass.services.async_call(
            DOMAIN, "cycle_outlet", {ATTR_ENTITY_ID: _first_switch(hass)}, blocking=True
        )


async def test_set_outlet_state_unresolvable_entity_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    with pytest.raises(HomeAssistantError, match="Cannot resolve"):
        await hass.services.async_call(
            DOMAIN,
            "set_outlet_state",
            {ATTR_ENTITY_ID: "switch.nonexistent", "state": True},
            blocking=True,
        )


async def test_set_outlet_state_api_error_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    _coordinator(hass).async_set_outlet_state = AsyncMock(side_effect=RaritanAPIError("boom"))
    with pytest.raises(HomeAssistantError, match="Failed to set outlet"):
        await hass.services.async_call(
            DOMAIN,
            "set_outlet_state",
            {ATTR_ENTITY_ID: _first_switch(hass), "state": True},
            blocking=True,
        )


async def test_reset_energy_unresolvable_entity_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    with pytest.raises(HomeAssistantError, match="Cannot resolve"):
        await hass.services.async_call(
            DOMAIN,
            "reset_energy_counter",
            {ATTR_ENTITY_ID: "sensor.nonexistent"},
            blocking=True,
        )


async def test_reset_energy_non_energy_sensor_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    voltage_sensor = next(
        s.entity_id
        for s in hass.states.async_all()
        if s.entity_id.startswith("sensor.") and "voltage" in s.entity_id
    )
    with pytest.raises(HomeAssistantError, match="not an active_energy sensor"):
        await hass.services.async_call(
            DOMAIN,
            "reset_energy_counter",
            {ATTR_ENTITY_ID: voltage_sensor},
            blocking=True,
        )


async def test_reset_energy_outlet_api_error_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    outlet_energy = next(
        s.entity_id
        for s in hass.states.async_all()
        if s.entity_id.startswith("sensor.")
        and "outlet" in s.entity_id
        and "active_energy" in s.entity_id
    )
    _coordinator(hass).async_reset_outlet_energy = AsyncMock(side_effect=RaritanAPIError("boom"))
    with pytest.raises(HomeAssistantError, match="Reset failed"):
        await hass.services.async_call(
            DOMAIN, "reset_energy_counter", {ATTR_ENTITY_ID: outlet_energy}, blocking=True
        )


async def test_reset_energy_inlet_api_error_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    inlet_energy = next(
        s.entity_id
        for s in hass.states.async_all()
        if s.entity_id.startswith("sensor.")
        and "inlet" in s.entity_id
        and "active_energy" in s.entity_id
    )
    _coordinator(hass).async_reset_inlet_energy = AsyncMock(side_effect=RaritanAPIError("boom"))
    with pytest.raises(HomeAssistantError, match="Reset failed"):
        await hass.services.async_call(
            DOMAIN, "reset_energy_counter", {ATTR_ENTITY_ID: inlet_energy}, blocking=True
        )


def _first_outlet_energy(hass: HomeAssistant) -> str:
    return next(
        s.entity_id
        for s in hass.states.async_all()
        if s.entity_id.startswith("sensor.")
        and "outlet" in s.entity_id
        and "active_energy" in s.entity_id
    )


async def test_cycle_outlet_entry_not_loaded_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """Calling the service while the entry is not loaded surfaces a clean error."""
    await _setup(hass)
    switch = _first_switch(hass)
    del hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    with pytest.raises(HomeAssistantError, match="is not loaded"):
        await hass.services.async_call(
            DOMAIN, "cycle_outlet", {ATTR_ENTITY_ID: switch}, blocking=True
        )


async def test_set_outlet_state_entry_not_loaded_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    switch = _first_switch(hass)
    del hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    with pytest.raises(HomeAssistantError, match="is not loaded"):
        await hass.services.async_call(
            DOMAIN, "set_outlet_state", {ATTR_ENTITY_ID: switch, "state": True}, blocking=True
        )


async def test_reset_energy_entry_not_loaded_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    sensor = _first_outlet_energy(hass)
    del hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    with pytest.raises(HomeAssistantError, match="is not loaded"):
        await hass.services.async_call(
            DOMAIN, "reset_energy_counter", {ATTR_ENTITY_ID: sensor}, blocking=True
        )
