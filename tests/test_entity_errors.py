"""Error and no-data paths for button, switch, and diagnostics entities."""

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
from custom_components.raritan.diagnostics import async_get_config_entry_diagnostics

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


def _entry(hass: HomeAssistant):
    return hass.config_entries.async_entries(DOMAIN)[0]


def _find(hass: HomeAssistant, prefix: str, needle: str) -> str:
    return next(
        s.entity_id
        for s in hass.states.async_all()
        if s.entity_id.startswith(prefix) and needle in s.entity_id
    )


async def test_refresh_button_api_error_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    coord = _entry(hass).runtime_data.coordinator
    coord.async_refresh_capabilities = AsyncMock(side_effect=RaritanAPIError("boom"))
    button = _find(hass, "button.", "refresh_capabilities")
    with pytest.raises(HomeAssistantError, match="Refresh failed"):
        await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: button}, blocking=True)


async def test_cycle_button_api_error_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    coord = _entry(hass).runtime_data.coordinator
    coord.async_cycle_outlet = AsyncMock(side_effect=RaritanAPIError("boom"))
    button = _find(hass, "button.", "cycle")
    with pytest.raises(HomeAssistantError, match="Failed to cycle outlet"):
        await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: button}, blocking=True)


async def test_switch_turn_on_api_error_raises(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    coord = _entry(hass).runtime_data.coordinator
    coord.async_set_outlet_state = AsyncMock(side_effect=RaritanAPIError("boom"))
    switch = next(s.entity_id for s in hass.states.async_all() if s.entity_id.startswith("switch."))
    with pytest.raises(HomeAssistantError, match="Failed to switch outlet"):
        await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: switch}, blocking=True)


async def test_switch_is_on_none_when_no_data(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    coord = _entry(hass).runtime_data.coordinator
    coord.data = None
    switch = next(s.entity_id for s in hass.states.async_all() if s.entity_id.startswith("switch."))
    coord.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "unknown"


async def test_diagnostics_with_no_coordinator_data(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    await _setup(hass)
    entry = _entry(hass)
    entry.runtime_data.coordinator.data = None
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["coordinator"]["ocps_count"] == 0
    assert diag["coordinator"]["env_count"] == 0
    assert diag["coordinator"]["last_tick_duration_ms"] is None
