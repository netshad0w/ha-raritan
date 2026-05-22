"""Inlet sensor entities for Raritan PDU."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .device_info import (
    env_device_info,
    env_display_name,
    inlet_device_info,
    ocp_device_info,
    outlet_device_info,
    slug_sensor_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import RaritanConfigEntry
    from .coordinator import RaritanDataUpdateCoordinator
    from .models import EnvSensorReading, InletReading, OcpReading, OutletReading

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class RaritanInletSensorDescription(SensorEntityDescription):
    """Describes a Raritan inlet sensor."""

    value_fn: Callable[[InletReading], float | None]
    scale: float = 1.0


INLET_SENSORS: tuple[RaritanInletSensorDescription, ...] = (
    RaritanInletSensorDescription(
        key="voltage",
        translation_key="inlet_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda r: r.voltage,
    ),
    RaritanInletSensorDescription(
        key="current",
        translation_key="inlet_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=3,
        value_fn=lambda r: r.current,
    ),
    RaritanInletSensorDescription(
        key="active_power",
        translation_key="inlet_active_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda r: r.active_power,
    ),
    RaritanInletSensorDescription(
        key="apparent_power",
        translation_key="inlet_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        suggested_display_precision=0,
        value_fn=lambda r: r.apparent_power,
    ),
    RaritanInletSensorDescription(
        key="power_factor",
        translation_key="inlet_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=2,
        # Derived quantity, rarely actioned on; off by default to reduce clutter.
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.power_factor,
        scale=100.0,
    ),
    RaritanInletSensorDescription(
        key="frequency",
        translation_key="inlet_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=1,
        value_fn=lambda r: r.frequency,
    ),
    RaritanInletSensorDescription(
        key="active_energy",
        translation_key="inlet_active_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda r: r.active_energy_wh,
        scale=0.001,  # Wh -> kWh
    ),
)


@dataclass(frozen=True, kw_only=True)
class RaritanOutletSensorDescription(SensorEntityDescription):
    """Describes a Raritan outlet sensor."""

    value_fn: Callable[[OutletReading], float | None]
    scale: float = 1.0


# Outlet sensors live under a sub-device named "Outlet {idx}", so we don't
# duplicate "outlet" in the entity name; translation_key is just the metric
# (HA composes friendly_name as "Outlet {idx} Voltage" automatically).
OUTLET_SENSORS: tuple[RaritanOutletSensorDescription, ...] = (
    RaritanOutletSensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda r: r.voltage,
    ),
    RaritanOutletSensorDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=3,
        value_fn=lambda r: r.current,
    ),
    RaritanOutletSensorDescription(
        key="active_power",
        translation_key="active_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda r: r.active_power,
    ),
    RaritanOutletSensorDescription(
        key="apparent_power",
        translation_key="apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        suggested_display_precision=0,
        value_fn=lambda r: r.apparent_power,
    ),
    RaritanOutletSensorDescription(
        key="active_energy",
        translation_key="active_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda r: r.active_energy_wh,
        scale=0.001,  # Wh -> kWh
    ),
)


@dataclass(frozen=True, kw_only=True)
class RaritanOcpSensorDescription(SensorEntityDescription):
    """Describes a Raritan OCP numeric sensor (current, peak current)."""

    value_fn: Callable[[OcpReading], float | None]


OCP_SENSORS: tuple[RaritanOcpSensorDescription, ...] = (
    RaritanOcpSensorDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=3,
        value_fn=lambda r: r.current,
    ),
    RaritanOcpSensorDescription(
        key="peak_current",
        translation_key="peak_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=3,
        # Diagnostic high-water mark; off by default to reduce clutter.
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.peak_current,
    ),
)


# Map peripheral sensor types -> (device_class, native_unit) for HA. Unknown
# types fall through to None so the sensor still appears but without classes.
_ENV_NUMERIC_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    "TEMPERATURE": SensorDeviceClass.TEMPERATURE,
    "HUMIDITY": SensorDeviceClass.HUMIDITY,
    "AIR_PRESSURE": SensorDeviceClass.ATMOSPHERIC_PRESSURE,
    "DEW_POINT": SensorDeviceClass.TEMPERATURE,
}

_ENV_NUMERIC_DEFAULT_UNIT: dict[str, str] = {
    "TEMPERATURE": UnitOfTemperature.CELSIUS,
    "HUMIDITY": PERCENTAGE,
    "AIR_PRESSURE": UnitOfPressure.PA,
    "AIR_FLOW": UnitOfSpeed.METERS_PER_SECOND,
    "DEW_POINT": UnitOfTemperature.CELSIUS,
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: RaritanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up inlet, outlet, OCP, and env sensors."""
    runtime = entry.runtime_data
    cap = runtime.capabilities
    coord = runtime.coordinator
    entities: list[CoordinatorEntity[RaritanDataUpdateCoordinator]] = []
    for inlet_idx in range(1, cap.nb_inlets + 1):
        entities.extend(
            RaritanInletSensor(coordinator=coord, description=desc, inlet_idx=inlet_idx)
            for desc in INLET_SENSORS
        )
    if cap.outlet_metering:
        for outlet_idx in cap.outlet_ids:
            entities.extend(
                RaritanOutletSensor(
                    coordinator=coord, description=outlet_desc, outlet_idx=outlet_idx
                )
                for outlet_desc in OUTLET_SENSORS
            )
    for ocp_idx in cap.ocp_ids:
        entities.extend(
            RaritanOcpSensor(coordinator=coord, description=ocp_desc, ocp_idx=ocp_idx)
            for ocp_desc in OCP_SENSORS
        )
    async_add_entities(entities)

    # Env peripherals are hot-pluggable: add numeric ones now and whenever the
    # coordinator's periodic rescan surfaces a new one (dynamic-devices).
    known_env: set[str] = set()

    def _is_numeric_env(reading: EnvSensorReading) -> bool:
        # Intentional gap: a sensor with a numeric value but no unit, an
        # UNKNOWN/unrecognised sensor_type (not in _ENV_NUMERIC_DEFAULT_UNIT),
        # and no state falls through here AND through binary_sensor's
        # _is_state_env, so it is silently dropped. That is deliberate -- HA
        # cannot classify such a reading (no unit -> not a numeric sensor; no
        # state -> not a binary sensor), so there is nothing meaningful to
        # surface. Do not "fix" by emitting a unitless numeric entity.
        if reading.value is None and reading.state is not None:
            return False
        return reading.sensor_type in _ENV_NUMERIC_DEFAULT_UNIT or reading.unit is not None

    @callback
    def _add_new_env_sensors() -> None:
        if coord.data is None:
            return
        new = [
            RaritanEnvSensor(coordinator=coord, sensor_id=r.sensor_id)
            for r in coord.data.env
            if r.sensor_id not in known_env and _is_numeric_env(r)
        ]
        known_env.update(r.sensor_id for r in coord.data.env if _is_numeric_env(r))
        if new:
            async_add_entities(new)

    _add_new_env_sensors()
    entry.async_on_unload(coord.async_add_listener(_add_new_env_sensors))


class RaritanInletSensor(CoordinatorEntity["RaritanDataUpdateCoordinator"], SensorEntity):
    """Inlet sensor entity."""

    entity_description: RaritanInletSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: RaritanDataUpdateCoordinator,
        description: RaritanInletSensorDescription,
        inlet_idx: int,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._inlet_idx = inlet_idx
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_inlet_{inlet_idx}_{description.key}"
        self._attr_translation_placeholders = {"idx": str(inlet_idx)}
        # HA derives the auto-generated entity_id slug from the translation
        # template BEFORE resolving placeholders, so "Inlet {idx} voltage"
        # becomes "..._inlet_idx_voltage" (literal "idx"). Explicitly
        # pre-resolve the slug so the entity_id reads as "..._inlet_1_voltage".
        self._attr_suggested_object_id = f"inlet_{inlet_idx}_{description.key}"
        self._attr_device_info = inlet_device_info(cap, inlet_idx, coordinator.host)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        reading = self.coordinator.data.inlets_by_idx.get(self._inlet_idx)
        if reading is None:
            return None
        raw = self.entity_description.value_fn(reading)
        if raw is None:
            return None
        return raw * self.entity_description.scale


class RaritanOutletSensor(CoordinatorEntity["RaritanDataUpdateCoordinator"], SensorEntity):
    """Outlet sensor entity, lives under a sub-device per outlet."""

    entity_description: RaritanOutletSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: RaritanDataUpdateCoordinator,
        description: RaritanOutletSensorDescription,
        outlet_idx: int,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._outlet_idx = outlet_idx
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_outlet_{outlet_idx}_{description.key}"
        # No translation_placeholders: the parent device name "Outlet {idx}"
        # already carries the index, so the entity name is just the metric.
        self._attr_device_info = outlet_device_info(cap, outlet_idx)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        r = self.coordinator.data.outlets_by_idx.get(self._outlet_idx)
        if r is None:
            return None
        raw = self.entity_description.value_fn(r)
        if raw is None:
            return None
        return raw * self.entity_description.scale


class RaritanOcpSensor(CoordinatorEntity["RaritanDataUpdateCoordinator"], SensorEntity):
    """OCP (over-current protector) numeric sensor: current, peak current."""

    entity_description: RaritanOcpSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: RaritanDataUpdateCoordinator,
        description: RaritanOcpSensorDescription,
        ocp_idx: int,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._ocp_idx = ocp_idx
        cap = coordinator.capabilities
        self._attr_unique_id = f"{cap.serial}_ocp_{ocp_idx}_{description.key}"
        self._attr_device_info = ocp_device_info(cap, ocp_idx)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        r = self.coordinator.data.ocps_by_idx.get(self._ocp_idx)
        if r is None:
            return None
        return self.entity_description.value_fn(r)


class RaritanEnvSensor(CoordinatorEntity["RaritanDataUpdateCoordinator"], SensorEntity):
    """Numeric env (peripheral) sensor: temperature, humidity, pressure, etc."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, *, coordinator: RaritanDataUpdateCoordinator, sensor_id: str) -> None:
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        cap = coordinator.capabilities
        safe = slug_sensor_id(sensor_id)
        self._attr_unique_id = f"{cap.serial}_env_{safe}_value"
        # Resolve label/type from current data if available
        label = sensor_id
        sensor_type = "UNKNOWN"
        unit: str | None = None
        if coordinator.data is not None:
            r = coordinator.data.env_by_id.get(sensor_id)
            if r is not None:
                label = r.label or sensor_id
                sensor_type = r.sensor_type
                unit = r.unit
        device_class = _ENV_NUMERIC_DEVICE_CLASS.get(sensor_type)
        if device_class is not None:
            self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit or _ENV_NUMERIC_DEFAULT_UNIT.get(sensor_type)
        self._attr_name = env_display_name(sensor_type)
        self._attr_device_info = env_device_info(cap, safe, label)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        r = self.coordinator.data.env_by_id.get(self._sensor_id)
        return r.value if r is not None else None
