"""Tests for async_migrate_entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.raritan import _migrate_inlet_idx_entity_ids, async_migrate_entry
from custom_components.raritan.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_migrate_entry_v1_is_noop(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"host": "10.0.0.1"},
    )
    entry.add_to_hass(hass)
    result = await async_migrate_entry(hass, entry)
    assert result is True
    assert entry.version == 1


async def test_migrate_entry_unknown_version_returns_false(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=99,
        data={"host": "10.0.0.1"},
    )
    entry.add_to_hass(hass)
    result = await async_migrate_entry(hass, entry)
    assert result is False


async def test_migrate_inlet_idx_renames_broken_slug(hass: HomeAssistant) -> None:
    """A `..._inlet_idx_<metric>` slug is renamed to `..._inlet_<n>_<metric>`."""
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.1"})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ent = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="TEST00000001_inlet_2_voltage",
        config_entry=entry,
        suggested_object_id="raritan_pdu_inlet_idx_voltage",
    )
    assert "_inlet_idx_" in ent.entity_id

    _migrate_inlet_idx_entity_ids(hass, entry_id=entry.entry_id)

    renamed = registry.async_get(ent.entity_id)
    assert renamed is None
    assert registry.async_get("sensor.raritan_pdu_inlet_2_voltage") is not None


async def test_migrate_inlet_idx_skips_clean_entities(hass: HomeAssistant) -> None:
    """Entities without the broken slug are left untouched."""
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "10.0.0.1"})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    ent = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="TEST00000001_inlet_1_voltage",
        config_entry=entry,
        suggested_object_id="raritan_pdu_inlet_1_voltage",
    )
    before = ent.entity_id

    _migrate_inlet_idx_entity_ids(hass, entry_id=entry.entry_id)

    assert registry.async_get(before) is not None
