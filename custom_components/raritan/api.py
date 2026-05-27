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

from .device_info import slug_sensor_id
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
        # Outlet / OCP labels are nameplate metadata that don't change at
        # runtime, so we fetch getMetaData() once at proxy-load time and cache
        # the label strings. Per-tick we only read getState()/getReading().
        self._outlet_labels: list[str] | None = None
        self._ocp_labels: list[str] | None = None
        self._alerted_sensor_manager: Any | None = None
        # Env (peripheral) sensors. Discovered lazily on the first telemetry
        # tick (NOT at probe time) so async_setup_entry doesn't block on the
        # ~32-slot peripheral walk. ``None`` means "not yet discovered".
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
        self._outlet_labels = None
        self._ocp_labels = None
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

        Env (peripheral) discovery is intentionally deferred: the ~32-slot
        peripheral walk is slow (~17 s on a fully populated PX3) and would
        block ``async_setup_entry``. The first coordinator tick (or the
        periodic ``refresh_env_sensors`` rescan) populates env sensors instead,
        so ``env_sensor_ids`` is empty here.
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
            env_sensor_ids=(),
            outlet_switching=bool(getattr(metadata, "hasSwitchableOutlets", False)),
            outlet_metering=bool(getattr(metadata, "hasMeteredOutlets", False)),
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
        """Best-effort peripheral discovery.

        Populates ``self._env_sensors`` and returns the tuple of stable sensor
        IDs. Swallows any error: this surface is optional and often blocked by
        role permissions, so a failure degrades to "no env sensors" rather than
        aborting. Invoked lazily on the first telemetry tick (not at probe
        time) so setup never blocks on the peripheral walk.
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
        self._warn_on_env_type_change(self._env_sensors, walked)
        self._env_sensors = walked
        return tuple(s.sensor_id for s in walked)

    @staticmethod
    def _warn_on_env_type_change(
        previous: list[_EnvSensor] | None, current: list[_EnvSensor]
    ) -> None:
        """Warn when a slot reports a different reading type than it used to.

        The entity's device class, unit and name are fixed from the reading type
        the first time it is seen. When a serial-less slot is reused for a
        different SmartSensor it keeps its base id, so it either reuses the same
        entity (same kind) and freezes it on the stale class, or flips the
        ``:n0`` / ``:s0`` suffix (numeric<->state) and leaves the old entity
        orphaned and unavailable beside a fresh one. Both warrant a heads-up, so
        the comparison keys on the base id (without the suffix), not the full id.
        Rewriting a live entity's class/unit would break its long-term
        statistics, so this only logs and asks the user to remove and re-add it.
        The id may embed the peripheral serial, so it is kept to debug; the
        warning names only the (non-identifying) type transition.
        """
        if previous is None:
            return

        def _base(sensor_id: str) -> str:
            # Drop the trailing :n0 / :s0 kind suffix so a numeric<->state swap on
            # the same slot still compares against its prior reading type.
            return sensor_id.rsplit(":", 1)[0]

        prior_types = {_base(s.sensor_id): s.sensor_type for s in previous}
        for sensor in current:
            was = prior_types.get(_base(sensor.sensor_id))
            if was is not None and was != sensor.sensor_type:
                _LOGGER.warning(
                    "An env peripheral was reclassified from %s to %s; its Home "
                    "Assistant entity keeps the old device class and unit until "
                    "it is removed and re-added",
                    was,
                    sensor.sensor_type,
                )
                _LOGGER.debug("Reclassified env peripheral id: %s", sensor.sensor_id)

    def _walk_env_sensors(self, pdu: Any) -> list[_EnvSensor] | None:
        """Walk peripheral slots and classify sensors.

        Returns the discovered list (possibly empty if the PDU genuinely has no
        peripherals), or ``None`` if the peripheral manager itself could not be
        reached, letting callers tell "no peripherals" apart from "couldn't
        ask".
        """
        try:
            mgr = pdu.getPeripheralDeviceManager()
            slots = list(mgr.getDeviceSlots())
        except Exception as exc:
            # Peripheral discovery is best-effort and must never break a tick, so
            # this stays a broad catch (transport error, missing manager, or an
            # SDK shape change all degrade to "couldn't ask" -> return None). One
            # clause, always logged at debug, so nothing is swallowed silently.
            _LOGGER.debug("Peripheral discovery unavailable on %s: %s", self._host, str(exc)[:200])
            return None

        if not slots:
            return []

        # One bulk roundtrip for every slot's device handle instead of one RPC
        # per slot: the PDU closes keep-alive between requests, so each RPC pays
        # a fresh ~0.6s TLS handshake. A PX exposes a fixed slot count (~32)
        # regardless of occupancy, so a per-slot walk dominated discovery time.
        device_helper = BulkRequestHelper(self._agent)
        for slot in slots:
            device_helper.add_request(slot.getDevice)
        try:
            devices = device_helper.perform_bulk()
        except (HttpException, JsonRpcErrorException) as exc:
            # A whole-bulk transport failure means we couldn't ask: return None
            # (not []) so a first-tick walk degrades to empty and a rescan keeps
            # the prior set, rather than escaping and aborting the telemetry tick.
            _LOGGER.debug("Peripheral slot bulk failed on %s: %s", self._host, str(exc)[:200])
            return None

        # peripheral.Device is a ValueObject ['deviceID', 'position',
        # 'packageClass', 'device'] where `device` is a single Sensor proxy
        # (never a list). Classify numeric vs state by which read method it
        # exposes (NumericSensor.getReading vs StateSensor.getState).
        pending: list[tuple[str, str, Any, bool]] = []  # (base_id, label, proxy, is_state)
        seen_slugs: set[str] = set()
        for slot_idx, device in enumerate(devices):
            if isinstance(device, Exception) or device is None:
                continue
            sensor_proxy = getattr(device, "device", None)
            if sensor_proxy is None:
                continue
            if hasattr(sensor_proxy, "getReading"):
                is_state = False
            elif hasattr(sensor_proxy, "getState"):
                is_state = True
            else:
                continue
            try:
                serial = (
                    str(getattr(device.deviceID, "serial", ""))
                    if hasattr(device, "deviceID")
                    else ""
                )
            except Exception as exc:
                # deviceID shape is SDK-dependent; fall back to a slot-derived id.
                _LOGGER.debug("Peripheral serial unreadable on slot %d: %s", slot_idx, exc)
                serial = ""
            base_id = serial or f"slot_{slot_idx}"
            label = serial or f"Peripheral {slot_idx}"
            # Two peripherals can report the same serial (cloned hardware, or an
            # empty serial on both): a shared id would make one env sensor shadow
            # the other in env_by_id. Dedupe on the *slugged* id, not the raw one:
            # the id is slugified for the entity unique_id (slug_sensor_id maps
            # ':' and '/' to '_'), so distinct serials like "A:B" and "A_B" still
            # collapse to one unique_id. Fall back to the slot-derived id,
            # suffixing until the slug is unique -- so even a real serial that
            # collides post-slug (or equals our "slot_N" fallback) can't clash.
            if slug_sensor_id(base_id) in seen_slugs:
                base_id = f"slot_{slot_idx}"
                suffix = 0
                # Each pass appends a strictly larger suffix and ``seen_slugs`` is
                # finite, so a fresh slug is reached in at most len(seen_slugs)+1
                # steps -- the loop always terminates.
                while slug_sensor_id(base_id) in seen_slugs:
                    suffix += 1
                    base_id = f"slot_{slot_idx}_{suffix}"
            seen_slugs.add(slug_sensor_id(base_id))
            pending.append((base_id, label, sensor_proxy, is_state))

        # Second bulk roundtrip: classify every sensor's TypeSpec at once.
        specs: list[Any] = []
        if pending:
            spec_helper = BulkRequestHelper(self._agent)
            for _base_id, _label, sensor_proxy, _is_state in pending:
                spec_helper.add_request(sensor_proxy.getTypeSpec)
            try:
                specs = spec_helper.perform_bulk()
            except (HttpException, JsonRpcErrorException) as exc:
                # Couldn't classify -> couldn't ask (see device bulk above).
                _LOGGER.debug("Peripheral spec bulk failed on %s: %s", self._host, str(exc)[:200])
                return None

        env_sensors: list[_EnvSensor] = []
        for (base_id, label, sensor_proxy, is_state), spec in zip(pending, specs, strict=True):
            stype, unit = self._classify_spec(spec)
            if is_state:
                env_sensors.append(
                    _EnvSensor(f"{base_id}:s0", sensor_proxy, stype, None, True, label)
                )
            else:
                env_sensors.append(
                    _EnvSensor(f"{base_id}:n0", sensor_proxy, stype, unit, False, label)
                )

        return env_sensors

    @staticmethod
    def _classify_spec(spec: Any) -> tuple[str, str | None]:
        """Map a sensor's pre-fetched TypeSpec to (sensor_type_short, unit).

        ``spec`` is the result of a bulked ``getTypeSpec`` call, so it may be a
        TypeSpec, ``None``, or an Exception (bulk per-request failure). Never
        raises; falls back to ("UNKNOWN", None).
        """
        if spec is None or isinstance(spec, Exception):
            return ("UNKNOWN", None)
        try:
            rt_raw = getattr(spec, "readingtype", None)
            rt = int(rt_raw) if rt_raw is not None else None
            unit_raw = getattr(spec, "unit", None)
            unit_int = int(unit_raw) if unit_raw is not None else None
            stype = _READING_TYPE_NAMES.get(rt, "UNKNOWN") if rt is not None else "UNKNOWN"
            unit = _UNIT_NAMES.get(unit_int) if unit_int is not None else None
            return (stype, unit)
        except Exception as exc:
            # TypeSpec enum shape is SDK-dependent; degrade to UNKNOWN.
            _LOGGER.debug("Unrecognized TypeSpec, classifying as UNKNOWN: %s", exc)
            return ("UNKNOWN", None)

    def _refresh_proxies(self, cap: CapabilityMatrix) -> None:
        """Populate the cached inlet/outlet proxy lists if not yet loaded.

        Two bulk roundtrips on the first call (one for the lists, one for the
        Sensors structs + the one-shot outlet/OCP labels). Subsequent ticks
        reuse the cached proxies and labels; the only thing that changes per
        tick is the readings.
        """
        # Outlet sensor structs go stale on PX3 firmware 4.3.x after roughly
        # a minute (see _OUTLET_SENSORS_TTL). Evict the cached structs so the
        # block below re-fetches them from the live PDU. Labels are NOT evicted:
        # they're nameplate metadata and don't go stale.
        if (
            self._outlet_sensors_structs is not None
            and self._outlet_sensors_structs_ts is not None
            and (time.monotonic() - self._outlet_sensors_structs_ts) > _OUTLET_SENSORS_TTL
        ):
            self._outlet_sensors_structs = None

        pdu = self._ensure_connected()

        # Env (peripheral) sensors are discovered lazily here on the first tick
        # (probe() no longer walks them, to keep setup fast). Best-effort: a
        # failed walk leaves the set empty without breaking the tick.
        if self._env_sensors is None:
            self._discover_env_sensors(pdu)

        if self._inlets is None:
            self._inlets = list(pdu.getInlets())
        need_outlets = cap.outlet_metering or cap.outlet_switching
        if need_outlets and self._outlets is None:
            self._outlets = list(pdu.getOutlets())
        if not need_outlets:
            self._outlets = []
            self._outlet_sensors_structs = []
            self._outlet_labels = []

        # Inlet sensor structs (one Inlet.Sensors per inlet)
        if self._inlet_sensors_structs is None and self._inlets is not None:
            helper = BulkRequestHelper(self._agent)
            for inlet in self._inlets:
                helper.add_request(inlet.getSensors)
            self._inlet_sensors_structs = list(helper.perform_bulk())

        # Outlet labels: fetched once via getMetaData and cached. Labels are
        # nameplate metadata, so we read them only when the cache is empty (not
        # every tick, and not on TTL struct eviction).
        if need_outlets and self._outlet_labels is None and self._outlets is not None:
            helper = BulkRequestHelper(self._agent)
            for outlet in self._outlets:
                helper.add_request(outlet.getMetaData)
            md_results = list(helper.perform_bulk())
            self._outlet_labels = [
                _label_or_default(md, idx) for idx, md in enumerate(md_results, start=1)
            ]

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
            self._ocp_labels = []
        if need_ocp and self._ocp_sensors_structs is None and self._ocps is not None:
            helper = BulkRequestHelper(self._agent)
            for ocp in self._ocps:
                helper.add_request(ocp.getSensors)
            self._ocp_sensors_structs = list(helper.perform_bulk())

        # OCP labels: one-shot getMetaData, cached like outlet labels.
        if need_ocp and self._ocp_labels is None and self._ocps is not None:
            helper = BulkRequestHelper(self._agent)
            for ocp in self._ocps:
                helper.add_request(ocp.getMetaData)
            md_results = list(helper.perform_bulk())
            self._ocp_labels = [
                _label_or_default(md, idx) for idx, md in enumerate(md_results, start=1)
            ]

        # PSU state sensors come from Pdu.Sensors. Best-effort: ignore them if
        # the SKU/firmware doesn't expose them. probe() pre-populates this so
        # we usually find a non-None value here; the branch covers the
        # post-close() / cache-eviction recovery path.
        if self._psu_state_sensors is None:
            try:
                pdu_sensors = pdu.getSensors()
                self._psu_state_sensors = list(getattr(pdu_sensors, "powerSupplyStatus", []) or [])
            except Exception as exc:
                # Best-effort: SKUs/firmware without PSU state sensors raise here.
                # Log at debug so a genuine SDK-shape change is still observable.
                _LOGGER.debug("PSU state sensors unavailable on %s: %s", self._host, str(exc)[:200])
                self._psu_state_sensors = []

    def fetch_telemetry(self, cap: CapabilityMatrix) -> CoordinatorPayload:
        """Fetch a single telemetry tick using one or two bulk RPCs.

        The first call after connect or close() does the heavy proxy-loading
        bulks (list inlets/outlets/OCPs, fetch sensor structs, cache outlet/OCP
        labels via one-shot getMetaData). After that, every tick is a single
        bulk roundtrip that batches every sensor reading + outlet getState +
        OCP trip getState + the alerted-sensor poll into one HTTP request.
        Outlet/OCP labels are read from the per-load cache, NOT re-fetched each
        tick.
        """
        start = time.monotonic_ns()
        try:
            self._refresh_proxies(cap)

            inlet_sensors_structs = self._inlet_sensors_structs or []
            outlet_sensors_structs = self._outlet_sensors_structs or []
            outlets_proxies = self._outlets or []
            outlet_labels = self._outlet_labels or []
            ocp_sensors_structs = self._ocp_sensors_structs or []
            ocps_proxies = self._ocps or []
            ocp_labels = self._ocp_labels or []
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

            # Outlet getState (always when outlets present). Labels come from
            # the per-load cache, not re-fetched each tick.
            outlet_state_count = 0
            for outlet in outlets_proxies:
                helper.add_request(outlet.getState)
                outlet_state_count += 1

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

            # OCP trip getState + numeric sensor reads. Labels come from the
            # per-load cache, not re-fetched each tick.
            ocp_request_layout: list[list[bool]] = []
            for ocp_idx, _ocp in enumerate(ocps_proxies):
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
            for psu in psu_sensors:
                method = getattr(psu, "getState", None)
                if method is not None:
                    helper.add_request(method)
                    psu_request_layout.append(True)
                else:
                    psu_request_layout.append(False)

            # Alert poll folded into the SAME bulk: getAlertedSensors() is one
            # request, so the whole tick costs ONE roundtrip instead of a
            # second poll via fetch_alerts. Best-effort: a failure (e.g. role
            # lacks permission) decodes to no alerts without breaking the tick.
            alerts_queued = False
            try:
                mgr = self._ensure_alerted_sensor_manager()
                helper.add_request(mgr.getAlertedSensors)
                alerts_queued = True
            except (HttpException, JsonRpcErrorException) as exc:
                _LOGGER.debug("Alert poll skipped (manager unavailable): %s", exc)
                self._alerted_sensor_manager = None

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
            outlet_states = results[cursor : cursor + outlet_state_count]
            cursor += outlet_state_count
            for outlet_idx, _outlet in enumerate(outlets_proxies, start=1):
                state = outlet_states[outlet_idx - 1]
                label = (
                    outlet_labels[outlet_idx - 1]
                    if outlet_idx - 1 < len(outlet_labels)
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
                label = ocp_labels[ocp_idx - 1] if ocp_idx - 1 < len(ocp_labels) else str(ocp_idx)
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
            for psu_idx, present in enumerate(psu_request_layout, start=1):
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

            # Alert decode: the folded getAlertedSensors() result is the final
            # queued entry. Best-effort: a per-request failure (Exception in the
            # results list) decodes to no alerts and drops the cached manager so
            # the next tick rebuilds it.
            current_alerts: list[AlertSnapshot] = []
            if alerts_queued:
                alert_result = results[cursor]
                cursor += 1
                if isinstance(alert_result, Exception):
                    self._alerted_sensor_manager = None
                else:
                    current_alerts = self._build_alert_snapshots(alert_result)
        except (HttpException, JsonRpcErrorException) as exc:
            # Drop cached proxies; the next tick will re-fetch them after
            # whatever transient transport issue caused this.
            self._inlets = None
            self._outlets = None
            self._ocps = None
            self._inlet_sensors_structs = None
            self._outlet_sensors_structs = None
            self._outlet_sensors_structs_ts = None
            self._outlet_labels = None
            self._ocp_sensors_structs = None
            self._ocp_labels = None
            self._env_sensors = None
            self._psu_state_sensors = None
            self._alerted_sensor_manager = None
            raise self._remap(exc) from exc

        elapsed_ms = (time.monotonic_ns() - start) // 1_000_000
        return CoordinatorPayload(
            inlets=inlets,
            outlets=outlets,
            ocps=ocps,
            env=env,
            psus=psus,
            current_alerts=current_alerts,
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
        """Return the current alerted sensor snapshots in a standalone roundtrip.

        Telemetry ticks fold the alert poll into ``fetch_telemetry``'s bulk, so
        this method is no longer on the per-tick hot path. It remains available
        for callers (e.g. diagnostics) that want an explicit alert poll.

        Best-effort: returns `[]` on auth/unsupported errors so a missing role
        permission can't break the caller.
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
        return self._build_alert_snapshots(sensor_data_list)

    @staticmethod
    def _decode_alert_label(md: Any) -> str:
        """Decode a getMetaData() result to a sensor label, never raising.

        ``md`` is the bulked ``getMetaData`` result, so it may be metadata,
        ``None``, or an Exception (per-request bulk failure) -> falls back to
        "?" so a single failed sub-request keeps its fallback label rather than
        breaking the whole tick.
        """
        if md is None or isinstance(md, Exception):
            return "?"
        # Try a name attr first, then fall back to type/typeSpec representations.
        return str(getattr(md, "name", None) or getattr(md, "type", "?"))

    def _build_alert_snapshots(self, sensor_data_list: Any) -> list[AlertSnapshot]:
        """Decode an AlertedSensorManager.getAlertedSensors() result into snapshots.

        All per-sensor ``getMetaData`` lookups are batched into a single
        ``BulkRequestHelper`` roundtrip rather than issued sequentially. Bulk
        embeds per-request failures as Exception values in the result list, so a
        single sensor whose metadata can't be fetched degrades to its fallback
        label without breaking the others.
        """
        rows = list(sensor_data_list)
        # First pass: collect the per-row pieces and queue a getMetaData per
        # sensor that exposes one.
        helper = BulkRequestHelper(self._agent)
        md_index: list[int | None] = []  # row -> index into bulk results, or None
        queued = 0  # count of requests actually enqueued (skips None rows)
        for sd in rows:
            sensor = getattr(sd, "sensor", None)
            getter = getattr(sensor, "getMetaData", None) if sensor is not None else None
            if getter is not None:
                helper.add_request(getter)
                # Index into the bulk RESULTS, which only hold queued requests;
                # len(md_index) would also count the None rows and shift labels.
                md_index.append(queued)
                queued += 1
            else:
                md_index.append(None)
        try:
            md_results = helper.perform_bulk() if any(i is not None for i in md_index) else []
        except (HttpException, JsonRpcErrorException) as exc:
            # A whole-bulk transport failure must not break the tick: every
            # alert keeps its fallback label. Log at debug so a persistent
            # failure (revoked permission, firmware regression) is observable.
            _LOGGER.debug("Alert metadata bulk failed on %s: %s", self._host, str(exc)[:200])
            md_results = []

        snapshots: list[AlertSnapshot] = []
        for sd, idx in zip(rows, md_index, strict=True):
            alert_state = getattr(getattr(sd, "alertState", None), "name", "UNAVAILABLE")
            sensor = getattr(sd, "sensor", None)
            parent = getattr(sd, "parent", None)
            if idx is not None and idx < len(md_results):
                sensor_label = self._decode_alert_label(md_results[idx])
            else:
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

    def _reset_energy(self, *, proxy_list: list[Any], proxy_name: str, idx: int) -> None:
        """Reset the cumulative energy counter on ``proxy_list[idx-1]``.

        Shared by :meth:`reset_inlet_energy` and :meth:`reset_outlet_energy`;
        ``proxy_name`` ("Inlet"/"Outlet") only shapes the error messages. The
        caller is responsible for populating ``proxy_list`` first.
        """
        try:
            if idx < 1 or idx > len(proxy_list):
                raise RaritanConnectionError(
                    f"{proxy_name} index {idx} out of range (1..{len(proxy_list)})"
                )
            sensors = proxy_list[idx - 1].getSensors()
            energy = getattr(sensors, "activeEnergy", None)
            reset = getattr(energy, "resetValue", None) if energy is not None else None
            if reset is None:
                raise RaritanUnsupportedError(
                    f"{proxy_name} {idx} active energy sensor does not support resetValue"
                )
            reset()
        except (HttpException, JsonRpcErrorException) as exc:
            raise self._remap(exc) from exc

    def reset_inlet_energy(self, *, idx: int) -> None:
        """Reset the cumulative energy counter on inlet `idx` (1-based)."""
        self._ensure_inlets_proxy()
        self._reset_energy(proxy_list=self._inlets or [], proxy_name="Inlet", idx=idx)

    def reset_outlet_energy(self, *, idx: int) -> None:
        """Reset the cumulative energy counter on outlet `idx` (1-based)."""
        self._ensure_outlets_proxy()
        self._reset_energy(proxy_list=self._outlets or [], proxy_name="Outlet", idx=idx)


def _label_or_default(md: Any, idx: int) -> str:
    """Decode a getMetaData() result to its label, falling back to str(idx).

    Used to cache outlet/OCP labels once at proxy-load time. Tolerates an
    Exception value (per-request bulk failure) or a missing/empty label.
    """
    if isinstance(md, Exception):
        return str(idx)
    return str(getattr(md, "label", None) or idx)


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
