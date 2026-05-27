"""Outlet on/off switch entities for Raritan PDU."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RaritanAPIError
from .const import DOMAIN
from .device_info import outlet_device_info

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import RaritanConfigEntry
    from .coordinator import RaritanDataUpdateCoordinator

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RaritanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up outlet switches when the PDU supports outlet switching."""
    runtime = entry.runtime_data
    cap = runtime.capabilities
    if not cap.outlet_switching:
        return
    entities = [
        RaritanOutletSwitch(coordinator=runtime.coordinator, outlet_idx=idx)
        for idx in cap.outlet_ids
    ]
    async_add_entities(entities)


class RaritanOutletSwitch(CoordinatorEntity["RaritanDataUpdateCoordinator"], SwitchEntity):
    """Outlet on/off switch entity, lives under a sub-device per outlet."""

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_has_entity_name = True
    # The outlet is the switch, so there's no entity-level name; the friendly
    # name is just the device name "Outlet {idx}". HA shows it as "Outlet 1".
    _attr_name = None

    def __init__(
        self,
        *,
        coordinator: RaritanDataUpdateCoordinator,
        outlet_idx: int,
    ) -> None:
        super().__init__(coordinator)
        self._outlet_idx = outlet_idx
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_outlet_{outlet_idx}_switch"
        self._attr_device_info = outlet_device_info(cap, outlet_idx)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        r = self.coordinator.data.outlets_by_idx.get(self._outlet_idx)
        return r.on if r is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_state(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_state(on=False)

    async def _set_state(self, *, on: bool) -> None:
        try:
            # Route through coordinator so the write shares the read-path lock
            # and the SDK's single HTTP connection isn't double-driven.
            await self.coordinator.async_set_outlet_state(idx=self._outlet_idx, on=on)
        except RaritanAPIError as exc:
            _LOGGER.warning("Outlet %d switch failed: %s", self._outlet_idx, exc)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="switch_failed",
                translation_placeholders={"idx": str(self._outlet_idx), "error": str(exc)},
            ) from exc
