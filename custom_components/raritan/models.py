"""Dataclasses for the Raritan PDU integration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import RaritanAPI
    from .coordinator import RaritanDataUpdateCoordinator


_FW_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class CapabilityMatrix:
    """Auto-detected capabilities of a Raritan PDU."""

    model: str
    firmware: str
    serial: str
    hw_revision: str | None
    nb_inlets: int
    outlet_ids: tuple[int, ...]
    ocp_ids: tuple[int, ...]
    env_sensor_ids: tuple[str, ...]
    outlet_switching: bool
    outlet_metering: bool
    nb_psu: int = 0  # internal controller power supplies exposed via Pdu.Sensors
    mac: str | None = None  # MAC address for DHCP discovery + host-change tracking

    @property
    def firmware_tuple(self) -> tuple[int, int, int]:
        """Parse firmware to (major, minor, patch). Returns (0,0,0) on parse failure."""
        match = _FW_RE.match(self.firmware)
        if not match:
            return (0, 0, 0)
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


@dataclass(slots=True)
class InletReading:
    """A single tick's reading from one inlet."""

    idx: int
    voltage: float | None
    current: float | None
    active_power: float | None
    apparent_power: float | None
    power_factor: float | None
    frequency: float | None
    active_energy_wh: float | None


@dataclass(slots=True)
class OutletReading:
    """A single tick's reading from one outlet."""

    idx: int
    on: bool
    label: str
    voltage: float | None
    current: float | None
    active_power: float | None
    apparent_power: float | None
    active_energy_wh: float | None


@dataclass(slots=True)
class OcpReading:
    """A single tick's reading from one over-current protector."""

    idx: int
    label: str
    tripped: bool  # True if breaker is currently tripped
    current: float | None
    peak_current: float | None


@dataclass(slots=True)
class EnvSensorReading:
    """A single tick's reading from an env (peripheral) sensor."""

    sensor_id: str  # stable peripheral ID
    label: str  # user-set label or device serial
    sensor_type: str  # "TEMPERATURE", "HUMIDITY", "AIR_PRESSURE", "AIR_FLOW", "CONTACT", "ON_OFF"
    value: float | None  # for numeric sensors
    state: bool | None  # for state sensors
    unit: str | None  # "°C", "%", "hPa", "m/s"; None for state sensors


@dataclass(frozen=True, slots=True)
class AlertSnapshot:
    """A single alerted-sensor snapshot of one alarm state."""

    sensor_label: str  # e.g. "RMS Current" or "Temperature"
    parent_label: str  # e.g. "Inlet 1" or "Outlet 3", else the parent's RID
    alert_state: str  # "CRITICAL" | "WARNED" | "UNAVAILABLE" | "NORMAL"
    sensor_id: str  # stable identifier we can compare across ticks
    # (typically the sensor proxy's RPC target string)


@dataclass(slots=True)
class PsuReading:
    """A single tick's reading of one internal controller PSU.

    Raritan exposes per-PSU health as a state sensor (typically OK / FAILURE
    / UNAVAILABLE). We surface ``ok`` (True if explicitly healthy) so HA can
    bind it to a ``BinarySensorDeviceClass.PROBLEM`` (off=ok, on=problem).
    """

    idx: int
    ok: bool | None  # None when the sensor reading is unavailable


@dataclass(slots=True)
class CoordinatorPayload:
    """The data structure produced by every coordinator tick.

    ``fetch_telemetry`` (one executor job, one bulk roundtrip) builds the whole
    payload, including ``current_alerts`` from the alert poll folded into the
    same bulk. The coordinator reads the alerts straight off the payload, so a
    tick costs a single roundtrip.
    """

    inlets: list[InletReading]
    outlets: list[OutletReading]
    ocps: list[OcpReading]
    env: list[EnvSensorReading]
    current_alerts: list[AlertSnapshot]
    last_tick_duration_ms: int
    consecutive_skips: int
    # Per-PSU health from Pdu.Sensors.powerSupplyStatus. Defaulted to an empty
    # list so test fixtures and the placeholder payload in
    # coordinator._async_update_data don't need to pass it explicitly.
    psus: list[PsuReading] = field(default_factory=list)
    # Index dicts built once per tick. Entity `native_value` is called by
    # the HA state machine on every state read and on a 36-outlet PDU with
    # 6 metrics each that adds up to 216 linear scans of a 36-item list per
    # poll. Building the indexes once at construction lets every entity do
    # a single dict lookup.
    inlets_by_idx: dict[int, InletReading] = field(init=False)
    outlets_by_idx: dict[int, OutletReading] = field(init=False)
    ocps_by_idx: dict[int, OcpReading] = field(init=False)
    env_by_id: dict[str, EnvSensorReading] = field(init=False)
    psus_by_idx: dict[int, PsuReading] = field(init=False)

    def __post_init__(self) -> None:
        self.inlets_by_idx = {r.idx: r for r in self.inlets}
        self.outlets_by_idx = {r.idx: r for r in self.outlets}
        self.ocps_by_idx = {r.idx: r for r in self.ocps}
        self.env_by_id = {r.sensor_id: r for r in self.env}
        self.psus_by_idx = {r.idx: r for r in self.psus}


@dataclass(slots=True)
class RaritanRuntimeData:
    """Runtime data attached to a ConfigEntry."""

    api: RaritanAPI
    capabilities: CapabilityMatrix
    coordinator: RaritanDataUpdateCoordinator
