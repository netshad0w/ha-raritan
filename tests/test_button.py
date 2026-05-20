"""Tests for diagnostic buttons."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.helpers.entity import EntityCategory

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


async def test_refresh_capabilities_button_exists_and_is_diagnostic(
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
    button_entries = [
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN and "refresh_capabilities" in e.unique_id
    ]
    assert len(button_entries) == 1
    entity = button_entries[0]
    assert entity.unique_id == "TEST00000001_refresh_capabilities"
    assert entity.entity_category == EntityCategory.DIAGNOSTIC


async def test_refresh_capabilities_press_calls_probe_again(
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

    initial_calls = mock_raritan.getMetaData.call_count

    # Find the button entity_id
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    button_entry = next(
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN and "refresh_capabilities" in e.unique_id
    )

    await hass.services.async_call(
        "button",
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: button_entry.entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert mock_raritan.getMetaData.call_count > initial_calls


async def test_cycle_buttons_created_when_outlet_switching(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """When the PDU supports switching, one cycle button per outlet appears."""
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
    cycle_buttons = [
        e for e in registry.entities.values() if e.platform == DOMAIN and "_cycle" in e.unique_id
    ]
    # 2 outlets in fixture -> 2 cycle buttons
    assert len(cycle_buttons) == 2


async def test_cycle_button_press_calls_cyclePowerState(
    hass: HomeAssistant, mock_raritan_with_outlets: MagicMock
) -> None:
    """Pressing a cycle button calls the SDK cyclePowerState on the right outlet."""
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
    # Find outlet 1 cycle button
    cycle_buttons = [
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN and "_cycle" in e.unique_id and "outlet_1" in e.unique_id
    ]
    assert len(cycle_buttons) == 1
    button = cycle_buttons[0]

    outlet_1 = mock_raritan_with_outlets.getOutlets.return_value[0]
    initial = outlet_1.cyclePowerState.call_count

    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: button.entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert outlet_1.cyclePowerState.call_count > initial
