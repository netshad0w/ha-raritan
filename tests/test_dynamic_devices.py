"""Tests for runtime add (dynamic-devices) and removal (stale-devices) of env peripherals."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)
from custom_components.raritan.models import CoordinatorPayload, EnvSensorReading

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


def _payload_with_env(base: CoordinatorPayload, env: list[EnvSensorReading]) -> CoordinatorPayload:
    return replace(base, env=env)


_TEMP = EnvSensorReading(
    sensor_id="DEV1:n0",
    label="Rack temp",
    sensor_type="TEMPERATURE",
    value=22.5,
    state=None,
    unit="°C",
)
_CONTACT = EnvSensorReading(
    sensor_id="DEV2:s0",
    label="Door",
    sensor_type="CONTACT",
    value=None,
    state=False,
    unit=None,
)


async def test_numeric_env_added_at_runtime(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    await _setup(hass)
    coord = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinator
    before = [
        s for s in hass.states.async_all() if "env" in s.entity_id or "temperature" in s.entity_id
    ]

    coord.async_set_updated_data(_payload_with_env(coord.data, [_TEMP]))
    await hass.async_block_till_done()

    after = [
        s for s in hass.states.async_all() if s.attributes.get("device_class") == "temperature"
    ]
    assert len(after) > len(before)


async def test_state_env_added_at_runtime(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    await _setup(hass)
    coord = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinator

    coord.async_set_updated_data(_payload_with_env(coord.data, [_CONTACT]))
    await hass.async_block_till_done()

    opening = [s for s in hass.states.async_all() if s.attributes.get("device_class") == "opening"]
    assert len(opening) >= 1


async def test_stale_env_device_removed(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    coord = entry.runtime_data.coordinator
    dev_reg = dr.async_get(hass)

    # Plug in a peripheral -> its device appears.
    coord.async_set_updated_data(_payload_with_env(coord.data, [_TEMP]))
    await hass.async_block_till_done()
    env_devices = [
        d
        for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        if any("_env_" in ident for _domain, ident in d.identifiers)
    ]
    assert len(env_devices) == 1

    # Unplug it -> the stale device is removed on the next update.
    coord.async_set_updated_data(_payload_with_env(coord.data, []))
    await hass.async_block_till_done()
    env_devices = [
        d
        for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        if any("_env_" in ident for _domain, ident in d.identifiers)
    ]
    assert env_devices == []
