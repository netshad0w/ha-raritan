"""Event entities for Raritan PDU integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import RaritanConfigEntry
    from .coordinator import RaritanDataUpdateCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: RaritanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up alert + outlet state-change event entities."""
    runtime = entry.runtime_data
    cap = runtime.capabilities
    entities: list[EventEntity] = [RaritanAlertEvent(coordinator=runtime.coordinator)]
    if cap.outlet_switching:
        entities.extend(
            RaritanOutletStateChangeEvent(coordinator=runtime.coordinator, outlet_idx=idx)
            for idx in cap.outlet_ids
        )
    async_add_entities(entities)


class RaritanAlertEvent(CoordinatorEntity["RaritanDataUpdateCoordinator"], EventEntity):
    """Pdu-level alarm event entity. Triggered when a new alert appears."""

    _attr_has_entity_name = True
    _attr_translation_key = "alert"
    # EventEntity declares _attr_event_types as a plain instance var, so the
    # ClassVar annotation RUF012 asks for is rejected by mypy (override error).
    _attr_event_types = ["alert_active", "alert_cleared"]  # noqa: RUF012

    def __init__(self, *, coordinator: RaritanDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_alert"
        self._previous_ids: set[str] | None = None
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, cap.serial)})

    async def async_added_to_hass(self) -> None:
        """Seed previous-ids from current data so the first real update can diff."""
        await super().async_added_to_hass()
        if self.coordinator.data is not None:
            self._previous_ids = {a.sensor_id for a in self.coordinator.data.current_alerts}

    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is None:
            return
        if self._previous_ids is None:
            self._previous_ids = {a.sensor_id for a in self.coordinator.data.current_alerts}
            super()._handle_coordinator_update()
            return
        current_ids = {a.sensor_id for a in self.coordinator.data.current_alerts}
        new_ids = current_ids - self._previous_ids
        cleared_ids = self._previous_ids - current_ids
        for sid in new_ids:
            alert = next(a for a in self.coordinator.data.current_alerts if a.sensor_id == sid)
            self._trigger_event(
                "alert_active",
                {
                    "sensor_label": alert.sensor_label,
                    "parent_label": alert.parent_label,
                    "alert_state": alert.alert_state,
                    "sensor_id": alert.sensor_id,
                },
            )
        for sid in cleared_ids:
            self._trigger_event("alert_cleared", {"sensor_id": sid})
        self._previous_ids = current_ids
        super()._handle_coordinator_update()


class RaritanOutletStateChangeEvent(CoordinatorEntity["RaritanDataUpdateCoordinator"], EventEntity):
    """Per-outlet state-change event entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "state_change"
    _attr_event_types = ["turned_on", "turned_off"]  # noqa: RUF012

    def __init__(self, *, coordinator: RaritanDataUpdateCoordinator, outlet_idx: int) -> None:
        super().__init__(coordinator)
        self._outlet_idx = outlet_idx
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_outlet_{outlet_idx}_state_change"
        self._previous_on: bool | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{cap.serial}_outlet_{outlet_idx}")},
        )

    async def async_added_to_hass(self) -> None:
        """Seed _previous_on from current data so the next update can diff."""
        await super().async_added_to_hass()
        if self.coordinator.data is None:
            return
        outlet = self.coordinator.data.outlets_by_idx.get(self._outlet_idx)
        if outlet is not None:
            self._previous_on = outlet.on

    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is None:
            return
        outlet = self.coordinator.data.outlets_by_idx.get(self._outlet_idx)
        if outlet is not None:
            if self._previous_on is not None and self._previous_on != outlet.on:
                self._trigger_event(
                    "turned_on" if outlet.on else "turned_off",
                    {"outlet_idx": outlet.idx, "label": outlet.label},
                )
            self._previous_on = outlet.on
        super()._handle_coordinator_update()
