"""Binary sensor entities for Raritan PDU (OCP trip + env state sensors)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import RaritanConfigEntry
    from .coordinator import RaritanDataUpdateCoordinator
    from .models import EnvSensorReading

PARALLEL_UPDATES = 0


# Map env sensor types we care about to a binary_sensor device class.
_ENV_STATE_DEVICE_CLASS: dict[str, BinarySensorDeviceClass | None] = {
    "CONTACT": BinarySensorDeviceClass.OPENING,
    "DRY_CONTACT": BinarySensorDeviceClass.OPENING,
    "POWERED_DRY_CONTACT": BinarySensorDeviceClass.OPENING,
    "ON_OFF": BinarySensorDeviceClass.POWER,
    "WATER_LEAK": BinarySensorDeviceClass.MOISTURE,
    "SMOKE": BinarySensorDeviceClass.SMOKE,
    "MOTION": BinarySensorDeviceClass.MOTION,
    "TAMPER": BinarySensorDeviceClass.TAMPER,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RaritanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OCP tripped + env state + PSU health binary sensors."""
    runtime = entry.runtime_data
    cap = runtime.capabilities
    coord = runtime.coordinator
    entities: list[CoordinatorEntity[RaritanDataUpdateCoordinator]] = [
        RaritanOcpTrippedSensor(coordinator=coord, ocp_idx=idx) for idx in cap.ocp_ids
    ]
    entities.extend(
        RaritanPsuHealthSensor(coordinator=coord, psu_idx=idx) for idx in range(1, cap.nb_psu + 1)
    )
    if entities:
        async_add_entities(entities)

    # Env state peripherals are hot-pluggable: add them now and whenever the
    # coordinator's periodic rescan surfaces a new one (dynamic-devices).
    known_env: set[str] = set()

    def _is_state_env(reading: EnvSensorReading) -> bool:
        if reading.state is None and reading.value is not None:
            return False
        return reading.sensor_type in _ENV_STATE_DEVICE_CLASS or (
            reading.unit is None and reading.value is None
        )

    @callback
    def _add_new_env_sensors() -> None:
        if coord.data is None:
            return
        new = [
            RaritanEnvBinarySensor(coordinator=coord, sensor_id=r.sensor_id)
            for r in coord.data.env
            if r.sensor_id not in known_env and _is_state_env(r)
        ]
        known_env.update(r.sensor_id for r in coord.data.env if _is_state_env(r))
        if new:
            async_add_entities(new)

    _add_new_env_sensors()
    entry.async_on_unload(coord.async_add_listener(_add_new_env_sensors))


class RaritanOcpTrippedSensor(
    CoordinatorEntity["RaritanDataUpdateCoordinator"], BinarySensorEntity
):
    """Boolean sensor; True when the OCP (breaker) has tripped."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_has_entity_name = True
    _attr_translation_key = "tripped"

    def __init__(self, *, coordinator: RaritanDataUpdateCoordinator, ocp_idx: int) -> None:
        super().__init__(coordinator)
        self._ocp_idx = ocp_idx
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_ocp_{ocp_idx}_tripped"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{cap.serial}_ocp_{ocp_idx}")},
            name=f"OCP {ocp_idx}",
            manufacturer="Raritan",
            model=f"{cap.model} OCP",
            via_device=(DOMAIN, cap.serial),
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        o = self.coordinator.data.ocps_by_idx.get(self._ocp_idx)
        return o.tripped if o is not None else None


class RaritanPsuHealthSensor(CoordinatorEntity["RaritanDataUpdateCoordinator"], BinarySensorEntity):
    """Boolean health sensor for one of the controller's internal PSUs.

    Raritan firmware reports each PSU as a state sensor; ``ok=True`` maps to
    ``is_on=False`` (no problem) and ``ok=False`` to ``is_on=True`` (problem)
    so the entity reads naturally with ``BinarySensorDeviceClass.PROBLEM``.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "psu_health"

    def __init__(self, *, coordinator: RaritanDataUpdateCoordinator, psu_idx: int) -> None:
        super().__init__(coordinator)
        self._psu_idx = psu_idx
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_psu_{psu_idx}_health"
        self._attr_translation_placeholders = {"idx": str(psu_idx)}
        # Single-PSU PDUs (the common case) keep the entity flat on the PDU
        # device. Multi-PSU PDUs (rare, mostly large i7 / 4-pole models) get
        # a sub-device per PSU so the user can see which one failed without
        # parsing the entity ID.
        if cap.nb_psu > 1:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{cap.serial}_psu_{psu_idx}")},
                name=f"PSU {psu_idx}",
                manufacturer="Raritan",
                model=f"{cap.model} PSU",
                via_device=(DOMAIN, cap.serial),
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, cap.serial)},
            )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        p = self.coordinator.data.psus_by_idx.get(self._psu_idx)
        if p is None or p.ok is None:
            return None
        return not p.ok


class RaritanEnvBinarySensor(CoordinatorEntity["RaritanDataUpdateCoordinator"], BinarySensorEntity):
    """Boolean state from an env (peripheral) state sensor."""

    _attr_has_entity_name = True

    def __init__(self, *, coordinator: RaritanDataUpdateCoordinator, sensor_id: str) -> None:
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        cap = coordinator.capabilities
        # Slugify the sensor_id since it may contain colons not safe for unique_id.
        safe = sensor_id.replace(":", "_").replace("/", "_")
        self._attr_unique_id = f"{cap.serial}_env_{safe}_state"
        # Resolve label and device_class from current data if available
        label = sensor_id
        device_class: BinarySensorDeviceClass | None = None
        sensor_type = "UNKNOWN"
        if coordinator.data is not None:
            r = coordinator.data.env_by_id.get(sensor_id)
            if r is not None:
                label = r.label or sensor_id
                sensor_type = r.sensor_type
        device_class = _ENV_STATE_DEVICE_CLASS.get(sensor_type)
        if device_class is not None:
            self._attr_device_class = device_class
        self._attr_name = sensor_type.replace("_", " ").title() if sensor_type else "State"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{cap.serial}_env_{safe}")},
            name=f"Sensor {label}",
            manufacturer="Raritan",
            model=f"{cap.model} env",
            via_device=(DOMAIN, cap.serial),
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        r = self.coordinator.data.env_by_id.get(self._sensor_id)
        return r.state if r is not None else None
