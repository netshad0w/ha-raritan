"""Event entities for Raritan PDU integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventEntity, EventExtraStoredData
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import RaritanConfigEntry
    from .coordinator import RaritanDataUpdateCoordinator
    from .models import OutletReading

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


@dataclass
class _RaritanEventExtraStoredData(EventExtraStoredData):
    """The last event EventEntity already stores, plus the diff baseline.

    The baseline rides in the same record so that a restart diffs its first
    payload against what was last actually known. Reseeding from the present
    instead folds whatever the PDU did while we were down into the new
    baseline, and none of it is ever reported.
    """

    baseline: Any = None


class _RaritanEventEntity(CoordinatorEntity["RaritanDataUpdateCoordinator"], EventEntity):
    """Shared availability and restore behaviour for the PDU's event entities.

    Both entities report "the last event received" and diff each payload
    against the one before it, so they need the diff baseline to outlive a
    restart and their state to outlive a failed poll.
    """

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Stay available for as long as the config entry is loaded.

        CoordinatorEntity ties availability to the last poll, which for an
        event entity asserts something untrue: a dropped request does not
        unmake the last event received, and the integration proves it still
        knows the value by rendering it again on recovery. The flip back is
        what consumers trip over. A state trigger cannot tell ``unavailable``
        followed by a timestamp apart from a fresh event.
        """
        return True

    async def _restored_baseline(self) -> Any:
        """The baseline the previous run left behind, or None on a first start."""
        restored = await self.async_get_last_extra_data()
        if restored is None:
            return None
        return restored.as_dict().get("baseline")


class RaritanAlertEvent(_RaritanEventEntity):
    """Pdu-level alarm event entity. Triggered when a new alert appears."""

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

    @property
    def extra_restore_state_data(self) -> _RaritanEventExtraStoredData:
        """Store the set of alerts that was active when we were last running."""
        stored = super().extra_restore_state_data
        baseline = sorted(self._previous_ids) if self._previous_ids is not None else None
        return _RaritanEventExtraStoredData(
            stored.last_event_type, stored.last_event_attributes, baseline
        )

    async def async_added_to_hass(self) -> None:
        """Pick up the baseline left by the previous run, or seed a new one."""
        await super().async_added_to_hass()
        baseline = await self._restored_baseline()
        if baseline is None:
            self._previous_ids = self._current_ids()
            return
        self._previous_ids = set(baseline)
        # Whatever the PDU did while we were down shows up as a diff between
        # the restored baseline and the payload waiting for us now.
        self._emit_diff()

    def _current_ids(self) -> set[str]:
        data = self.coordinator.data
        return {a.sensor_id for a in data.current_alerts} if data is not None else set()

    def _emit_diff(self) -> None:
        """Fire one event per alert that appeared or cleared since the baseline."""
        data = self.coordinator.data
        if data is None or self._previous_ids is None:
            return
        current_ids = {a.sensor_id for a in data.current_alerts}
        for sid in current_ids - self._previous_ids:
            alert = next(a for a in data.current_alerts if a.sensor_id == sid)
            self._trigger_event(
                "alert_active",
                {
                    "sensor_label": alert.sensor_label,
                    "parent_label": alert.parent_label,
                    "alert_state": alert.alert_state,
                    "sensor_id": alert.sensor_id,
                },
            )
        for sid in self._previous_ids - current_ids:
            self._trigger_event("alert_cleared", {"sensor_id": sid})
        self._previous_ids = current_ids

    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is None:
            return
        self._emit_diff()
        super()._handle_coordinator_update()


class RaritanOutletStateChangeEvent(_RaritanEventEntity):
    """Per-outlet state-change event entity."""

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

    @property
    def extra_restore_state_data(self) -> _RaritanEventExtraStoredData:
        """Store the position the outlet was in when we were last running."""
        stored = super().extra_restore_state_data
        return _RaritanEventExtraStoredData(
            stored.last_event_type, stored.last_event_attributes, self._previous_on
        )

    async def async_added_to_hass(self) -> None:
        """Pick up the baseline left by the previous run, or seed a new one."""
        await super().async_added_to_hass()
        baseline = await self._restored_baseline()
        if baseline is None:
            outlet = self._current_outlet()
            if outlet is not None:
                self._previous_on = outlet.on
            return
        self._previous_on = bool(baseline)
        # A flip that happened while we were down is the difference between
        # the restored baseline and the payload waiting for us now.
        self._emit_diff()

    def _current_outlet(self) -> OutletReading | None:
        data = self.coordinator.data
        return data.outlets_by_idx.get(self._outlet_idx) if data is not None else None

    def _emit_diff(self) -> None:
        """Fire a state-change event if the outlet moved since the baseline."""
        outlet = self._current_outlet()
        if outlet is None:
            return
        if self._previous_on is not None and self._previous_on != outlet.on:
            self._trigger_event(
                "turned_on" if outlet.on else "turned_off",
                {"outlet_idx": outlet.idx, "label": outlet.label},
            )
        self._previous_on = outlet.on

    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is None:
            return
        self._emit_diff()
        super()._handle_coordinator_update()
