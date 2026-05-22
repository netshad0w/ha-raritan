"""Wrapper around the official `raritan` SDK with HttpAgent lifecycle management."""

from __future__ import annotations

import logging
import ssl
import time
from typing import Any, NamedTuple

from raritan.rpc import (
    Agent,
    BulkRequestHelper,
    HttpException,
    JsonRpcErrorException,
    pdumodel,
)

from .models import (
    AlertSnapshot,
    CapabilityMatrix,
    CoordinatorPayload,
    EnvSensorReading,
    InletReading,
    OcpReading,
    OutletReading,
    PsuReading,
)

_LOGGER = logging.getLogger(__name__)


class _EnvSensor(NamedTuple):
    """A discovered peripheral sensor and how to read it each tick."""

    sensor_id: str
    proxy: Any
    sensor_type: str
    unit: str | None
    is_state: bool
    label: str


# Inlet.Sensors attribute names that we expose as InletReading fields.
# Order matters: must match _read_inlet_from_results() below.
_INLET_SENSOR_NAMES: tuple[str, ...] = (
    "voltage",
    "current",
    "activePower",
    "apparentPower",
    "powerFactor",
    "lineFrequency",
    "activeEnergy",
)

# Outlet.Sensors attribute names exposed as OutletReading fields.
_OUTLET_SENSOR_NAMES: tuple[str, ...] = (
    "voltage",
    "current",
    "activePower",
    "apparentPower",
    "activeEnergy",
)

# OCP.Sensors numeric attribute names exposed as OcpReading fields.
_OCP_NUMERIC_SENSOR_NAMES: tuple[str, ...] = (
    "current",
    "peakCurrent",
)

# Mapping of sensors.ReadingType integer enum values to our short string types.
# Only the env-sensor types we care about; anything else falls back to "UNKNOWN".
_READING_TYPE_NAMES: dict[int, str] = {
    8: "TEMPERATURE",
    9: "HUMIDITY",
    10: "AIR_FLOW",
    11: "AIR_PRESSURE",
    12: "CONTACT",  # CONTACT_CLOSURE
    13: "ON_OFF",  # ON_OFF_SENSOR
    14: "TRIP",  # TRIP_SENSOR
    16: "WATER_LEAK",
    17: "SMOKE",
    36: "MOTION",
    38: "TAMPER",
    39: "DRY_CONTACT",
    40: "POWERED_DRY_CONTACT",
    50: "DEW_POINT",
}

# Mapping of sensors.Unit integer enum values to display unit strings.
_UNIT_NAMES: dict[int, str] = {
    7: "°C",
    9: "%",
    10: "m/s",
    11: "Pa",
    29: "°F",
    30: "K",
}

# Outlet sensor proxies returned by `outlet.getSensors()` go stale after
# ~50 s on PX3 firmware 4.3.x: the cached structs start returning
# `Reading.valid=False` and `Outlet.State.available=False` for every outlet
# until close()/reconnect. Re-fetching the structs costs one extra bulk
# roundtrip per ~30 s tick, which is the cheapest known mitigation.
_OUTLET_SENSORS_TTL = 30.0


class RaritanAPIError(Exception):
    """Base class for all RaritanAPI errors."""


class RaritanAuthError(RaritanAPIError):
    """Authentication failed."""


class RaritanConnectionError(RaritanAPIError):
    """Network or transport-level failure."""


class RaritanTLSError(RaritanAPIError):
    """TLS certificate verification failure."""


class RaritanUnsupportedError(RaritanAPIError):
    """The PDU rejected a call as unsupported."""


class RaritanAPI:
    """Synchronous wrapper around the Raritan SDK with a long-lived HttpAgent.

    Telemetry reads are batched via BulkRequestHelper so a tick costs ~2 HTTP
    roundtrips total instead of one-per-sensor (which would be ~170 roundtrips
    on a 24-outlet PDU).
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_tls: bool,
        ca_bundle: str | None,
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._verify_tls = verify_tls
        self._ca_bundle = ca_bundle
        self._agent: Agent | None = None
        self._pdu: pdumodel.Pdu | None = None
        # Cached object proxies. These don't make RPC calls themselves; they're
        # client-side handles. The PDU's nameplate doesn't change at runtime, so
        # we cache them across ticks instead of re-listing every time.
        self._inlets: list[Any] | None = None
        self._outlets: list[Any] | None = None
        self._ocps: list[Any] | None = None
        self._inlet_sensors_structs: list[Any] | None = None
        self._outlet_sensors_structs: list[Any] | None = None
        # Monotonic timestamp of the last outlet-sensor-struct refresh; gates
        # the TTL eviction in `_refresh_proxies` (see _OUTLET_SENSORS_TTL).
        self._outlet_sensors_structs_ts: float | None = None
        self._ocp_sensors_structs: list[Any] | None = None
        self._alerted_sensor_manager: Any | None = None
        # Env (peripheral) sensors discovered at probe time.
        # Each entry: (sensor_id, sensor_proxy, sensor_type_short, unit_str|None,
        # is_state_sensor, label).
        self._env_sensors: list[_EnvSensor] | None = None
        # Per-PSU state sensors from Pdu.Sensors.powerSupplyStatus, cached at
        # probe time so each tick only batches getState() reads.
        self._psu_state_sensors: list[Any] | None = None

    @property
    def host(self) -> str:
        return self._host

    def _ensure_connected(self) -> pdumodel.Pdu:
        if self._pdu is not None:
            return self._pdu
        self._agent = Agent(
            "https",
            self._host,
            self._username,
            self._password,
            disable_certificate_verification=not self._verify_tls,
            timeout=15,
        )
        # Custom CA bundle support: replace the agent's _context with one we control.
        # The Agent class accepts no kwarg for a custom CA file, so injecting our
        # own SSLContext post-construction is the only supported path.
        if self._verify_tls and self._ca_bundle:
            ctx = ssl.create_default_context(cafile=self._ca_bundle)
            self._agent._context = ctx
        self._pdu = pdumodel.Pdu("/model/pdu/0", self._agent)
        _LOGGER.debug("Created HttpAgent for %s", self._host)
        return self._pdu

    def _ensure_outlets_proxy(self) -> None:
        """Lazily populate ``self._outlets`` (proxy list only).

        Used by write paths (set_outlet_state, cycle_outlet, reset_outlet_energy)
        that need an outlet handle without disturbing the sensor-struct caches
        owned by the read path. ``_refresh_proxies`` is intentionally NOT used
        here: it takes a capability matrix and will tear down caches that
        contradict it, which corrupts the next coordinator tick.
        """
        pdu = self._ensure_connected()
        if self._outlets is None:
            self._outlets = list(pdu.getOutlets())

    def _ensure_inlets_proxy(self) -> None:
        """Lazily populate ``self._inlets`` (proxy list only). See
        ``_ensure_outlets_proxy`` for why we don't use ``_refresh_proxies``."""
        pdu = self._ensure_connected()
        if self._inlets is None:
            self._inlets = list(pdu.getInlets())

    def close(self) -> None:
        """Drop the agent so a future call rebuilds it (e.g. after creds change)."""
        self._agent = None
        self._pdu = None
        self._inlets = None
        self._outlets = None
        self._ocps = None
        self._inlet_sensors_structs = None
        self._outlet_sensors_structs = None
        self._outlet_sensors_structs_ts = None
        self._ocp_sensors_structs = None
        self._alerted_sensor_manager = None
        self._env_sensors = None
        self._psu_state_sensors = None

    @staticmethod
    def _remap(exc: Exception) -> RaritanAPIError:
        # Truncate the stringified SDK exception before it flows into HA logs
        # via ConfigEntryNotReady/UpdateFailed: a raw PDU response body (or a
        # JSON-RPC error payload) can be large and may echo request internals.
        text = str(exc)[:200]
        msg = text.lower()
        if "certificate" in msg or "ssl" in msg or "tls" in msg:
            return RaritanTLSError(text)
        # "Insufficient privileges" comes from Raritan's JSON-RPC server when
        # the user role lacks the required permission for a call (e.g. Reset
        # Energy Counter, Switch Outlet). Classify as auth so HA surfaces a
        # clean message and can trigger reauth if the role was downgraded.
        if (
            "403" in msg
            or "401" in msg
            or "forbidden" in msg
            or "unauthorized" in msg
            or "insufficient privileges" in msg
        ):
            return RaritanAuthError(text)
        if "not supported" in msg or "no such method" in msg:
            return RaritanUnsupportedError(text)
        return RaritanConnectionError(text)

    def probe(self) -> CapabilityMatrix:
        """Probe the PDU and build a CapabilityMatrix.

        Bulk-batches the four nameplate RPCs (`getMetaData`, `getInlets`,
        `getOutlets`, `getOverCurrentProtectors`) into a single HTTP roundtrip.
        This was four sequential round-trips before, which dominated the
        reauth/setup latency on slow PDUs.
        """
        try:
            pdu = self._ensure_connected()
            helper = BulkRequestHelper(self._agent)
            helper.add_request(pdu.getMetaData)
            helper.add_request(pdu.getInlets)
            helper.add_request(pdu.getOutlets)
            helper.add_request(pdu.getOverCurrentProtectors)
            helper.add_request(pdu.getSensors)
            results = helper.perform_bulk()
        except (HttpException, JsonRpcErrorException) as exc:
            raise self._remap(exc) from exc

        # BulkRequestHelper embeds per-request failures as Exception values in
        # the result list rather than raising. The first four are required;
        # surface their failures. The fifth (pdu.getSensors) is best-effort
        # because some PDU SKUs / firmware variants don't expose a Pdu.Sensors
        # struct; degrading to "no PSU sensors" is fine.
        for r in results[:4]:
            if isinstance(r, Exception):
                raise self._remap(r) from r
        metadata, inlets, outlets, ocps, pdu_sensors = results

        if isinstance(pdu_sensors, Exception):
            self._psu_state_sensors = []
        else:
            self._psu_state_sensors = list(getattr(pdu_sensors, "powerSupplyStatus", []) or [])

        env_sensor_ids = self._discover_env_sensors(pdu)

        nameplate = metadata.nameplate
        mac = getattr(metadata, "macAddress", None) or getattr(nameplate, "macAddress", None)
        return CapabilityMatrix(
            model=nameplate.model,
            firmware=metadata.fwRevision,
            serial=nameplate.serialNumber,
            hw_revision=getattr(metadata, "hwRevision", None),
            mac=str(mac) if mac else None,
            nb_inlets=len(inlets),
            outlet_ids=tuple(range(1, len(outlets) + 1)),
            ocp_ids=tuple(range(1, len(ocps) + 1)),
            env_sensor_ids=env_sensor_ids,
            outlet_switching=bool(getattr(metadata, "hasSwitchableOutlets", False)),
            outlet_metering=bool(getattr(metadata, "hasMeteredOutlets", False)),
            has_alerts_engine=True,
            nb_psu=len(self._psu_state_sensors),
        )

    def probe_identity(self) -> tuple[str, str]:
        """Cheap probe used by reauth: verifies credentials and returns
        ``(serial, model)`` in a single HTTP roundtrip. Skips the full
        capability discovery (env-sensor walk, OCP/inlet/outlet list): the
        entry reload that follows a successful reauth will do that work
        anyway, so doing it twice in the reauth path is wasted latency.
        """
        try:
            pdu = self._ensure_connected()
            metadata = pdu.getMetaData()
        except (HttpException, JsonRpcErrorException) as exc:
            raise self._remap(exc) from exc
        nameplate = metadata.nameplate
        return (nameplate.serialNumber, nameplate.model)

    def _discover_env_sensors(self, pdu: Any) -> tuple[str, ...]:
        """Best-effort peripheral discovery used at probe time.

        Populates ``self._env_sensors`` (list of (sensor_id, proxy, type, unit,
        is_state, label)) and returns the tuple of stable sensor IDs for the
        CapabilityMatrix. Swallows any error: this surface is optional and
        often blocked by role permissions, so a failure degrades to "no env
        sensors" rather than aborting setup.
        """
        walked = self._walk_env_sensors(pdu)
        self._env_sensors = walked if walked is not None else []
        return tuple(s.sensor_id for s in self._env_sensors)

    def refresh_env_sensors(self) -> tuple[str, ...]:
        """Re-discover env peripherals for hot-plug support.

        Called periodically by the coordinator. Unlike the probe-time discovery,
        a hard failure (transport error / missing permission) preserves the
        previously known set instead of wiping it. Only a *successful* walk
        that genuinely returns fewer sensors shrinks the set, so a transient
        error never tears down live entities.
        """
        pdu = self._ensure_connected()
        walked = self._walk_env_sensors(pdu)
        if walked is None:
            return tuple(s.sensor_id for s in (self._env_sensors or []))
        self._env_sensors = walked
        return tuple(s.sensor_id for s in walked)

    def _walk_env_sensors(self, pdu: Any) -> list[_EnvSensor] | None:
        """Walk peripheral slots and classify sensors.

        Returns the discovered list (possibly empty if the PDU genuinely has no
        peripherals), or ``None`` if the peripheral manager itself could not be
        reached, letting callers tell "no peripherals" apart from "couldn't
        ask".
        """
        env_sensors: list[_EnvSensor] = []
        try:
            mgr = pdu.getPeripheralDeviceManager()
            slots = list(mgr.getDeviceSlots())
        except (HttpException, AttributeError):
            return None
        except Exception as exc:
            _LOGGER.debug("Peripheral discovery unavailable on %s: %s", self._host, exc)
            return None

        for slot_idx, slot in enumerate(slots):
            try:
                device = slot.getDevice()
            except Exception as exc:
                _LOGGER.debug("Skipping slot %d (getDevice failed): %s", slot_idx, exc)
                continue
            if device is None:
                continue
            # peripheral.Device is a ValueObject with shape
            # ['deviceID', 'position', 'packageClass', 'device'] where `device`
            # is typecheck.is_interface(..., raritan.rpc.sensors.Sensor): a
            # single Sensor proxy, never a list. So we always have at most one
            # sensor per slot; classify it as numeric or state by which read
            # method it exposes (NumericSensor.getReading vs StateSensor.getState).
            sensor_proxy = getattr(device, "device", None)
            if sensor_proxy is None:
                continue

            # Build a stable id and label from the device struct.
            try:
                serial = (
                    str(getattr(device.deviceID, "serial", ""))
                    if hasattr(device, "deviceID")
                    else ""
                )
            except Exception:
                serial = ""
            base_id = serial or f"slot_{slot_idx}"
            label = serial or f"Peripheral {slot_idx}"

            if hasattr(sensor_proxy, "getReading"):
                stype, unit = self._classify_sensor(sensor_proxy)
                env_sensors.append(
                    _EnvSensor(f"{base_id}:n0", sensor_proxy, stype, unit, False, label)
                )
            elif hasattr(sensor_proxy, "getState"):
                stype, _unit = self._classify_sensor(sensor_proxy)
                env_sensors.append(
                    _EnvSensor(f"{base_id}:s0", sensor_proxy, stype, None, True, label)
                )

        return env_sensors

    @staticmethod
    def _classify_sensor(sensor: Any) -> tuple[str, str | None]:
        """Best-effort: read the sensor's TypeSpec and map readingtype/unit.

        Uses getTypeSpec() which exists on both NumericSensor and StateSensor
        (NumericSensor.getMetaData() also wraps a TypeSpec but doesn't exist
        on StateSensor, which would silently classify all state peripherals
        as UNKNOWN).

        Returns (sensor_type_short, unit_str_or_none). Never raises; falls
        back to ("UNKNOWN", None).
        """
        try:
            spec = sensor.getTypeSpec()
            if spec is None:
                return ("UNKNOWN", None)
            rt_raw = getattr(spec, "readingtype", None)
            rt = int(rt_raw) if rt_raw is not None else None
            unit_raw = getattr(spec, "unit", None)
            unit_int = int(unit_raw) if unit_raw is not None else None
            stype = _READING_TYPE_NAMES.get(rt, "UNKNOWN") if rt is not None else "UNKNOWN"
            unit = _UNIT_NAMES.get(unit_int) if unit_int is not None else None
            return (stype, unit)
        except Exception:
            return ("UNKNOWN", None)

    def _refresh_proxies(self, cap: CapabilityMatrix) -> None:
        """Populate the cached inlet/outlet proxy lists if not yet loaded.

        Two bulk roundtrips on the first call (one for the lists, one for the
        Sensors structs). Subsequent ticks reuse the cached proxies; the only
        thing that changes per tick is the readings.
        """
        # Outlet sensor structs go stale on PX3 firmware 4.3.x after roughly
        # a minute (see _OUTLET_SENSORS_TTL). Evict the cached structs so the
        # block below re-fetches them from the live PDU.
        if (
            self._outlet_sensors_structs is not None
            and self._outlet_sensors_structs_ts is not None
            and (time.monotonic() - self._outlet_sensors_structs_ts) > _OUTLET_SENSORS_TTL
        ):
            self._outlet_sensors_structs = None

        pdu = self._ensure_connected()
        if self._inlets is None:
            self._inlets = list(pdu.getInlets())
        need_outlets = cap.outlet_metering or cap.outlet_switching
        if need_outlets and self._outlets is None:
            self._outlets = list(pdu.getOutlets())
        if not need_outlets:
            self._outlets = []
            self._outlet_sensors_structs = []

        # Inlet sensor structs (one Inlet.Sensors per inlet)
        if self._inlet_sensors_structs is None and self._inlets is not None:
            helper = BulkRequestHelper(self._agent)
            for inlet in self._inlets:
                helper.add_request(inlet.getSensors)
            self._inlet_sensors_structs = list(helper.perform_bulk())

        # Outlet sensor structs (only if metered)
        if (
            cap.outlet_metering
            and self._outlet_sensors_structs is None
            and self._outlets is not None
        ):
            helper = BulkRequestHelper(self._agent)
            for outlet in self._outlets:
                helper.add_request(outlet.getSensors)
            self._outlet_sensors_structs = list(helper.perform_bulk())
            self._outlet_sensors_structs_ts = time.monotonic()
        elif not cap.outlet_metering:
            self._outlet_sensors_structs = []
            self._outlet_sensors_structs_ts = None

        # OCP proxy + sensor struct caches.
        need_ocp = bool(cap.ocp_ids)
        if need_ocp and self._ocps is None:
            self._ocps = list(pdu.getOverCurrentProtectors())
        if not need_ocp:
            self._ocps = []
            self._ocp_sensors_structs = []
        if need_ocp and self._ocp_sensors_structs is None and self._ocps is not None:
            helper = BulkRequestHelper(self._agent)
            for ocp in self._ocps:
                helper.add_request(ocp.getSensors)
            self._ocp_sensors_structs = list(helper.perform_bulk())

        # PSU state sensors come from Pdu.Sensors. Best-effort: ignore them if
        # the SKU/firmware doesn't expose them. probe() pre-populates this so
        # we usually find a non-None value here; the branch covers the
        # post-close() / cache-eviction recovery path.
        if self._psu_state_sensors is None:
            try:
                pdu_sensors = pdu.getSensors()
                self._psu_state_sensors = list(getattr(pdu_sensors, "powerSupplyStatus", []) or [])
            except Exception:
                self._psu_state_sensors = []

    def fetch_telemetry(self, cap: CapabilityMatrix) -> CoordinatorPayload:
        """Fetch a single telemetry tick using one or two bulk RPCs.

        The very first call after connect or close() does up to 4 bulk
        roundtrips: list inlets, list outlets, get inlet sensor structs,
        get outlet sensor structs. After that, every tick is a single bulk
        roundtrip that batches every sensor reading + outlet getState +
        outlet getMetaData (label) into one HTTP request.
        """
        start = time.monotonic_ns()
        try:
            self._refresh_proxies(cap)

            inlet_sensors_structs = self._inlet_sensors_structs or []
            outlet_sensors_structs = self._outlet_sensors_structs or []
            outlets_proxies = self._outlets or []
            ocp_sensors_structs = self._ocp_sensors_structs or []
            ocps_proxies = self._ocps or []
            env_sensors_list = self._env_sensors or []
            psu_sensors = self._psu_state_sensors or []

            helper = BulkRequestHelper(self._agent)
            # Order matters: we consume responses in this exact order.

            # Inlet readings
            inlet_request_layout: list[list[bool]] = []
            for sensors in inlet_sensors_structs:
                row: list[bool] = []
                sensors_obj = None if isinstance(sensors, Exception) else sensors
                for name in _INLET_SENSOR_NAMES:
                    sensor = getattr(sensors_obj, name, None) if sensors_obj is not None else None
                    if sensor is not None:
                        helper.add_request(sensor.getReading)
                        row.append(True)
                    else:
                        row.append(False)
                inlet_request_layout.append(row)

            # Outlet getMetaData + getState (always when outlets present)
            outlet_meta_state_count = 0
            for outlet in outlets_proxies:
                helper.add_request(outlet.getMetaData)
                helper.add_request(outlet.getState)
                outlet_meta_state_count += 2

            # Outlet readings (only if metered)
            outlet_request_layout: list[list[bool]] = []
            if cap.outlet_metering:
                for sensors in outlet_sensors_structs:
                    row = []
                    sensors_obj = None if isinstance(sensors, Exception) else sensors
                    for name in _OUTLET_SENSOR_NAMES:
                        sensor = (
                            getattr(sensors_obj, name, None) if sensors_obj is not None else None
                        )
                        if sensor is not None:
                            helper.add_request(sensor.getReading)
                            row.append(True)
                        else:
                            row.append(False)
                    outlet_request_layout.append(row)

            # OCP getMetaData + trip getState + numeric sensor reads
            ocp_request_layout: list[list[bool]] = []
            for ocp_idx, ocp in enumerate(ocps_proxies):
                helper.add_request(ocp.getMetaData)
                sensors_struct = (
                    ocp_sensors_structs[ocp_idx] if ocp_idx < len(ocp_sensors_structs) else None
                )
                sensors_obj = None if isinstance(sensors_struct, Exception) else sensors_struct
                trip = getattr(sensors_obj, "trip", None) if sensors_obj is not None else None
                if trip is not None:
                    helper.add_request(trip.getState)
                    trip_present = True
                else:
                    trip_present = False
                row = [trip_present]
                for name in _OCP_NUMERIC_SENSOR_NAMES:
                    sensor = getattr(sensors_obj, name, None) if sensors_obj is not None else None
                    if sensor is not None:
                        helper.add_request(sensor.getReading)
                        row.append(True)
                    else:
                        row.append(False)
                ocp_request_layout.append(row)

            # Env (peripheral) sensor reads: best-effort, never break the tick
            env_request_layout: list[bool] = []
            for env_sensor in env_sensors_list:
                if env_sensor.is_state:
                    method = getattr(env_sensor.proxy, "getState", None)
                else:
                    method = getattr(env_sensor.proxy, "getReading", None)
                if method is not None:
                    helper.add_request(method)
                    env_request_layout.append(True)
                else:
                    env_request_layout.append(False)

            # PSU state reads: one getState() per controller PSU. Exercised only
            # on PDUs that expose internal power-supply state sensors (rare, mostly
            # large i7/4-pole models); the test PDU has none.
            psu_request_layout: list[bool] = []
            for psu in psu_sensors:  # pragma: no cover
                method = getattr(psu, "getState", None)
                if method is not None:
                    helper.add_request(method)
                    psu_request_layout.append(True)
                else:
                    psu_request_layout.append(False)

            results = list(helper.perform_bulk())

            # Decode in the order we queued.
            cursor = 0
            inlets: list[InletReading] = []
            for inlet_idx, row in enumerate(inlet_request_layout, start=1):
                values: dict[str, float | None] = {}
                for name, present in zip(_INLET_SENSOR_NAMES, row, strict=True):
                    if present:
                        reading = results[cursor]
                        cursor += 1
                        values[name] = _value_or_none(reading)
                    else:
                        values[name] = None
                inlets.append(
                    InletReading(
                        idx=inlet_idx,
                        voltage=values["voltage"],
                        current=values["current"],
                        active_power=values["activePower"],
                        apparent_power=values["apparentPower"],
                        power_factor=values["powerFactor"],
                        frequency=values["lineFrequency"],
                        active_energy_wh=values["activeEnergy"],
                    )
                )

            outlets: list[OutletReading] = []
            outlet_md_state = results[cursor : cursor + outlet_meta_state_count]
            cursor += outlet_meta_state_count
            for outlet_idx, _outlet in enumerate(outlets_proxies, start=1):
                md = outlet_md_state[(outlet_idx - 1) * 2]
                state = outlet_md_state[(outlet_idx - 1) * 2 + 1]
                label = (
                    str(getattr(md, "label", None) or outlet_idx)
                    if not isinstance(md, Exception)
                    else str(outlet_idx)
                )
                # Outlet.getState() returns Outlet.State STRUCT (not the enum).
                # The enum lives at state.powerState; state.available gates validity.
                on = (
                    not isinstance(state, Exception)
                    and getattr(state, "available", False)
                    and getattr(state, "powerState", None) == pdumodel.Outlet.PowerState.PS_ON
                )

                if cap.outlet_metering and outlet_idx - 1 < len(outlet_request_layout):
                    row = outlet_request_layout[outlet_idx - 1]
                    values = {}
                    for name, present in zip(_OUTLET_SENSOR_NAMES, row, strict=True):
                        if present:
                            reading = results[cursor]
                            cursor += 1
                            values[name] = _value_or_none(reading)
                        else:
                            values[name] = None
                    outlets.append(
                        OutletReading(
                            idx=outlet_idx,
                            on=on,
                            label=label,
                            voltage=values["voltage"],
                            current=values["current"],
                            active_power=values["activePower"],
                            apparent_power=values["apparentPower"],
                            active_energy_wh=values["activeEnergy"],
                        )
                    )
                else:
                    outlets.append(
                        OutletReading(
                            idx=outlet_idx,
                            on=on,
                            label=label,
                            voltage=None,
                            current=None,
                            active_power=None,
                            apparent_power=None,
                            active_energy_wh=None,
                        )
                    )

            # OCP decode
            ocps: list[OcpReading] = []
            for ocp_idx, row in enumerate(ocp_request_layout, start=1):
                md_result = results[cursor]
                cursor += 1
                if isinstance(md_result, Exception):
                    label = str(ocp_idx)
                else:
                    label = str(getattr(md_result, "label", "") or ocp_idx)
                trip_present = row[0]
                if trip_present:
                    state_result = results[cursor]
                    cursor += 1
                    tripped = _state_or_false(state_result)
                else:
                    tripped = False
                ocp_values: dict[str, float | None] = {}
                for name, present in zip(_OCP_NUMERIC_SENSOR_NAMES, row[1:], strict=True):
                    if present:
                        reading = results[cursor]
                        cursor += 1
                        ocp_values[name] = _value_or_none(reading)
                    else:
                        ocp_values[name] = None
                ocps.append(
                    OcpReading(
                        idx=ocp_idx,
                        label=label,
                        tripped=tripped,
                        current=ocp_values["current"],
                        peak_current=ocp_values["peakCurrent"],
                    )
                )

            # Env sensor decode
            env: list[EnvSensorReading] = []
            for env_sensor, present in zip(env_sensors_list, env_request_layout, strict=True):
                env_value: float | None = None
                env_state: bool | None = None
                if present:
                    raw = results[cursor]
                    cursor += 1
                    if env_sensor.is_state:
                        env_state = _state_or_false(raw) if not isinstance(raw, Exception) else None
                    else:
                        env_value = _value_or_none(raw)
                env.append(
                    EnvSensorReading(
                        sensor_id=env_sensor.sensor_id,
                        label=env_sensor.label,
                        sensor_type=env_sensor.sensor_type,
                        value=env_value,
                        state=env_state,
                        unit=env_sensor.unit,
                    )
                )

            # PSU decode: convert StateSensor.State to a tri-state ok/None.
            # Raritan firmware uses value=0 for normal/OK; any non-zero or
            # available=False is treated as a problem or unknown.
            psus: list[PsuReading] = []
            for psu_idx, present in enumerate(psu_request_layout, start=1):  # pragma: no cover
                if not present:
                    psus.append(PsuReading(idx=psu_idx, ok=None))
                    continue
                raw = results[cursor]
                cursor += 1
                if isinstance(raw, Exception):
                    psus.append(PsuReading(idx=psu_idx, ok=None))
                    continue
                if not getattr(raw, "available", False):
                    psus.append(PsuReading(idx=psu_idx, ok=None))
                    continue
                psus.append(PsuReading(idx=psu_idx, ok=int(getattr(raw, "value", 0)) == 0))
        except (HttpException, JsonRpcErrorException) as exc:
            # Drop cached proxies; the next tick will re-fetch them after
            # whatever transient transport issue caused this.
            self._inlets = None
            self._outlets = None
            self._ocps = None
            self._inlet_sensors_structs = None
            self._outlet_sensors_structs = None
            self._outlet_sensors_structs_ts = None
            self._ocp_sensors_structs = None
            self._env_sensors = None
            self._psu_state_sensors = None
            raise self._remap(exc) from exc

        elapsed_ms = (time.monotonic_ns() - start) // 1_000_000
        return CoordinatorPayload(
            inlets=inlets,
            outlets=outlets,
            ocps=ocps,
            env=env,
            psus=psus,
            current_alerts=[],
            last_tick_duration_ms=int(elapsed_ms),
            consecutive_skips=0,
        )

    def set_outlet_state(self, *, idx: int, on: bool) -> None:
        """Set outlet to ON or OFF. idx is 1-based."""
        try:
            self._ensure_outlets_proxy()
            outlets = self._outlets or []
            if idx < 1 or idx > len(outlets):
                raise RaritanConnectionError(f"Outlet index {idx} out of range (1..{len(outlets)})")
            ps = pdumodel.Outlet.PowerState
            outlets[idx - 1].setPowerState(ps.PS_ON if on else ps.PS_OFF)
        except (HttpException, JsonRpcErrorException) as exc:
            raise self._remap(exc) from exc

    def cycle_outlet(self, *, idx: int) -> None:
        """Power-cycle the outlet (off then on, with PDU's configured delay)."""
        try:
            self._ensure_outlets_proxy()
            outlets = self._outlets or []
            if idx < 1 or idx > len(outlets):
                raise RaritanConnectionError(f"Outlet index {idx} out of range (1..{len(outlets)})")
            outlets[idx - 1].cyclePowerState()
        except (HttpException, JsonRpcErrorException) as exc:
            raise self._remap(exc) from exc

    def _ensure_alerted_sensor_manager(self) -> Any:
        """Lazily fetch the AlertedSensorManager proxy.

        The proxy itself is a client-side handle (one RPC roundtrip on the very
        first call to populate it). Subsequent calls are cached.
        """
        if self._alerted_sensor_manager is not None:
            return self._alerted_sensor_manager
        pdu = self._ensure_connected()
        self._alerted_sensor_manager = pdu.getAlertedSensorManager()
        return self._alerted_sensor_manager

    def fetch_alerts(self, _cap: CapabilityMatrix) -> list[AlertSnapshot]:
        """Return the current alerted sensor snapshots.

        Best-effort: returns `[]` on auth/unsupported errors so a missing role
        permission can't break the whole telemetry tick.
        """
        try:
            mgr = self._ensure_alerted_sensor_manager()
            sensor_data_list = mgr.getAlertedSensors()
        except (HttpException, JsonRpcErrorException) as exc:
            mapped = self._remap(exc)
            if isinstance(mapped, RaritanAuthError | RaritanUnsupportedError):
                _LOGGER.debug("fetch_alerts skipped (role lacks permission): %s", exc)
                return []
            # Drop the cached manager so the next call retries.
            self._alerted_sensor_manager = None
            raise mapped from exc

        snapshots: list[AlertSnapshot] = []
        for sd in sensor_data_list:
            alert_state = getattr(getattr(sd, "alertState", None), "name", "UNAVAILABLE")
            sensor = getattr(sd, "sensor", None)
            parent = getattr(sd, "parent", None)
            sensor_label = "?"
            try:
                md = sensor.getMetaData() if sensor is not None else None
                if md is not None:
                    # Try a name attr first, then fall back to type/typeSpec representations.
                    sensor_label = str(getattr(md, "name", None) or getattr(md, "type", "?"))
            except Exception:  # best-effort label, never break tick
                sensor_label = "?"
            # Use the public `target` attr set by raritan.rpc.Interface.__init__
            # (the RID string). The leading-underscore `_target` form is not part
            # of the SDK API and would silently break on a future SDK refactor.
            sensor_id = str(getattr(sensor, "target", "")) if sensor is not None else ""
            parent_label = str(getattr(parent, "target", "")) if parent is not None else "?"
            snapshots.append(
                AlertSnapshot(
                    sensor_label=sensor_label,
                    parent_label=parent_label,
                    alert_state=str(alert_state),
                    sensor_id=sensor_id,
                )
            )
        return snapshots

    def reset_inlet_energy(self, *, idx: int) -> None:
        """Reset the cumulative energy counter on inlet `idx` (1-based)."""
        try:
            self._ensure_inlets_proxy()
            inlets = self._inlets or []
            if idx < 1 or idx > len(inlets):
                raise RaritanConnectionError(f"Inlet index {idx} out of range (1..{len(inlets)})")
            sensors = inlets[idx - 1].getSensors()
            energy = getattr(sensors, "activeEnergy", None)
            reset = getattr(energy, "resetValue", None) if energy is not None else None
            if reset is None:
                raise RaritanUnsupportedError(
                    f"Inlet {idx} active energy sensor does not support resetValue"
                )
            reset()
        except (HttpException, JsonRpcErrorException) as exc:
            raise self._remap(exc) from exc

    def reset_outlet_energy(self, *, idx: int) -> None:
        """Reset the cumulative energy counter on outlet `idx` (1-based)."""
        try:
            self._ensure_outlets_proxy()
            outlets = self._outlets or []
            if idx < 1 or idx > len(outlets):
                raise RaritanConnectionError(f"Outlet index {idx} out of range (1..{len(outlets)})")
            sensors = outlets[idx - 1].getSensors()
            energy = getattr(sensors, "activeEnergy", None)
            reset = getattr(energy, "resetValue", None) if energy is not None else None
            if reset is None:
                raise RaritanUnsupportedError(
                    f"Outlet {idx} active energy sensor does not support resetValue"
                )
            reset()
        except (HttpException, JsonRpcErrorException) as exc:
            raise self._remap(exc) from exc


def _value_or_none(reading: Any) -> float | None:
    """Decode a getReading() result to a float, or None on invalid/exception."""
    if isinstance(reading, Exception):
        return None
    if not getattr(reading, "valid", False):
        return None
    return float(reading.value)


def _state_or_false(state: Any) -> bool:
    """Decode a StateSensor.getState() result to a bool, or False on exception/unavailable.

    StateSensor.State has shape ``(timestamp, available, value)`` per the SDK.
    A non-zero value with available=True is "active" (e.g. trip occurred).
    """
    if isinstance(state, Exception):
        return False
    available = bool(getattr(state, "available", False))
    if not available:
        return False
    return bool(getattr(state, "value", 0))
