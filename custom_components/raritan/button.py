"""Diagnostic buttons for Raritan PDU."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .api import RaritanAPIError
from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import RaritanConfigEntry

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RaritanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostic + outlet cycle buttons."""
    runtime = entry.runtime_data
    cap = runtime.capabilities
    entities: list[ButtonEntity] = [RefreshCapabilitiesButton(entry)]
    if cap.outlet_switching:
        entities.extend(RaritanOutletCycleButton(entry, outlet_idx=idx) for idx in cap.outlet_ids)
    async_add_entities(entities)


class RefreshCapabilitiesButton(ButtonEntity):
    """Force re-probe of the PDU capabilities."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_capabilities"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: RaritanConfigEntry) -> None:
        self._entry = entry
        cap = entry.runtime_data.capabilities
        self._attr_unique_id = f"{cap.serial}_refresh_capabilities"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cap.serial)},
            manufacturer="Raritan",
            model=cap.model,
            name=f"Raritan {cap.model} ({cap.serial})",
            sw_version=cap.firmware,
            hw_version=cap.hw_revision,
        )

    async def async_press(self) -> None:
        """Re-probe and reload the entry to apply new capabilities."""
        coordinator = self._entry.runtime_data.coordinator
        try:
            await coordinator.async_refresh_capabilities()
        except RaritanAPIError as exc:
            _LOGGER.warning("Refresh capabilities failed: %s", exc)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
                translation_placeholders={"error": str(exc)},
            ) from exc
        await self.hass.config_entries.async_reload(self._entry.entry_id)


class RaritanOutletCycleButton(ButtonEntity):
    """Power-cycle a Raritan PDU outlet (off then on with PDU's configured delay)."""

    _attr_has_entity_name = True
    # Sub-device "Outlet {idx}" carries the index, so the entity name is just "Cycle".
    _attr_translation_key = "cycle"

    def __init__(self, entry: RaritanConfigEntry, *, outlet_idx: int) -> None:
        self._entry = entry
        self._outlet_idx = outlet_idx
        cap = entry.runtime_data.capabilities
        self._attr_unique_id = f"{cap.serial}_outlet_{outlet_idx}_cycle"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{cap.serial}_outlet_{outlet_idx}")},
            name=f"Outlet {outlet_idx}",
            manufacturer="Raritan",
            model=f"{cap.model} outlet",
            via_device=(DOMAIN, cap.serial),
        )

    async def async_press(self) -> None:
        """Power-cycle the outlet."""
        coordinator = self._entry.runtime_data.coordinator
        try:
            # Coordinator wrapper acquires _lock so the cycle write doesn't
            # collide with an in-flight telemetry tick on the SDK's single
            # HTTP connection.
            await coordinator.async_cycle_outlet(idx=self._outlet_idx)
        except RaritanAPIError as exc:
            _LOGGER.warning("Outlet %d cycle failed: %s", self._outlet_idx, exc)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cycle_failed",
                translation_placeholders={"idx": str(self._outlet_idx), "error": str(exc)},
            ) from exc
