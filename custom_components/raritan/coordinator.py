"""Single DataUpdateCoordinator for the Raritan PDU integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RaritanAPIError, RaritanAuthError
from .const import (
    DOMAIN,
    ENV_RESCAN_EVERY,
    EVENT_TYPE_ALERT,
    EVENT_TYPE_OUTLET_STATE_CHANGED,
    TICK_OVERLAP_THRESHOLD,
    UNREACHABLE_REPAIR_THRESHOLD,
)
from .models import AlertSnapshot, CoordinatorPayload
from .repairs import clear_unreachable_issue, create_unreachable_issue

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .api import RaritanAPI
    from .models import CapabilityMatrix, OutletReading

_LOGGER = logging.getLogger(__name__)


class RaritanDataUpdateCoordinator(DataUpdateCoordinator[CoordinatorPayload]):
    """Polls one PDU at a fixed interval, serializing executor jobs via a lock."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        api: RaritanAPI,
        capabilities: CapabilityMatrix,
        scan_interval: int,
        entry_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            # Use entry_id (not the PDU serial) for the coordinator name: the
            # name is written to public log files and the serial must not leak.
            name=f"{DOMAIN}_{entry_id}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._api = api
        self._capabilities = capabilities
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._consecutive_skips = 0
        self._previous_outlets: list[OutletReading] | None = None
        self._previous_alerts: list[AlertSnapshot] | None = None
        # Monotonic timestamp of the first failure in the current unreachable
        # streak, or None while the PDU is reachable. Drives the
        # "extended unreachable" repair once the streak crosses the threshold.
        self._unreachable_since: float | None = None
        # Hot-pluggable env peripherals are re-scanned every N ticks so newly
        # attached SmartSensors surface (and removed ones disappear) without a
        # full entry reload. Keeps the cost off the hot path: one extra
        # roundtrip every ENV_RESCAN_EVERY ticks, not every tick.
        self._ticks_since_env_scan = 0

    @property
    def capabilities(self) -> CapabilityMatrix:
        return self._capabilities

    @property
    def host(self) -> str:
        return self._api.host

    @property
    def previous_alerts(self) -> list[AlertSnapshot]:
        """Last seen alerts from the previous tick (for diagnostics)."""
        return list(self._previous_alerts) if self._previous_alerts is not None else []

    async def async_set_outlet_state(self, *, idx: int, on: bool) -> None:
        """Drive ``api.set_outlet_state`` under the shared coordinator lock.

        The Raritan SDK's ``Agent`` owns a single underlying HTTP connection
        which is not reentrant: issuing a write while a telemetry tick is
        in flight surfaces as ``http.client.CannotSendRequest('Request-sent')``
        and the write is lost. Holding ``_lock`` serializes writes against
        ticks. After the write returns, request a refresh so HA reflects the
        new state without waiting for the next scheduled tick. The refresh runs
        after the lock is released, so two writes fired back-to-back may have
        their refreshes coalesced by the coordinator's debouncer; the next tick
        still converges on the true state.
        """
        async with self._lock:
            await self.hass.async_add_executor_job(
                lambda: self._api.set_outlet_state(idx=idx, on=on)
            )
        await self.async_request_refresh()

    async def async_cycle_outlet(self, *, idx: int) -> None:
        """Drive ``api.cycle_outlet`` under the shared coordinator lock. See
        :meth:`async_set_outlet_state` for rationale.
        """
        async with self._lock:
            await self.hass.async_add_executor_job(lambda: self._api.cycle_outlet(idx=idx))
        await self.async_request_refresh()

    async def async_reset_inlet_energy(self, *, idx: int) -> None:
        """Drive ``api.reset_inlet_energy`` under the shared coordinator lock."""
        async with self._lock:
            await self.hass.async_add_executor_job(lambda: self._api.reset_inlet_energy(idx=idx))
        await self.async_request_refresh()

    async def async_reset_outlet_energy(self, *, idx: int) -> None:
        """Drive ``api.reset_outlet_energy`` under the shared coordinator lock."""
        async with self._lock:
            await self.hass.async_add_executor_job(lambda: self._api.reset_outlet_energy(idx=idx))
        await self.async_request_refresh()

    async def async_refresh_capabilities(self) -> CapabilityMatrix:
        """Re-probe under the same lock that serializes coordinator ticks.

        The Raritan SDK's HttpAgent owns a single underlying HTTP connection
        and is not thread-safe. Without holding ``_lock``, a refresh-button
        probe can collide with an in-flight tick and surface as
        ``http.client.CannotSendRequest('Request-sent')``. Closing the agent
        first forces ``probe()`` to rebuild a clean connection.
        """
        async with self._lock:
            await self.hass.async_add_executor_job(self._api.close)
            cap = await self.hass.async_add_executor_job(self._api.probe)
        self._capabilities = cap
        return cap

    async def _async_update_data(self) -> CoordinatorPayload:
        # No ``await`` may be inserted between this check and the ``async with``
        # below: asyncio is single-threaded, so the locked() probe is only
        # race-free as long as nothing yields the event loop in between.
        if self._lock.locked():
            self._consecutive_skips += 1
            _LOGGER.warning(
                # Identify by the opaque entry_id: neither the serial nor the
                # host should leak into log files that land in public bug reports.
                "Tick overlap on entry %s (skip %d/%d)",
                self._entry_id,
                self._consecutive_skips,
                TICK_OVERLAP_THRESHOLD,
            )
            if self._consecutive_skips >= TICK_OVERLAP_THRESHOLD:
                raise UpdateFailed(
                    f"Tick overlap exceeded {TICK_OVERLAP_THRESHOLD} consecutive skips"
                )
            if self.data is not None:
                return self.data
            return CoordinatorPayload(
                inlets=[],
                outlets=[],
                ocps=[],
                env=[],
                current_alerts=[],
                last_tick_duration_ms=0,
                consecutive_skips=self._consecutive_skips,
            )
        async with self._lock:
            self._ticks_since_env_scan += 1
            if self._ticks_since_env_scan >= ENV_RESCAN_EVERY:
                self._ticks_since_env_scan = 0
                try:
                    await self.hass.async_add_executor_job(self._api.refresh_env_sensors)
                except RaritanAPIError as exc:
                    # Expected transport/SDK failure: keep the prior env set and
                    # carry on with the tick. Quiet by design (hot-plug rescan).
                    _LOGGER.debug("Env peripheral rescan failed (non-fatal): %s", str(exc)[:200])
                except Exception:
                    # Anything else is unexpected (programming error / SDK shape
                    # change). Still non-fatal for the tick, but log a traceback
                    # so it surfaces instead of being silently swallowed.
                    _LOGGER.exception("Unexpected error during env peripheral rescan (non-fatal)")
            try:
                payload = await self.hass.async_add_executor_job(
                    self._api.fetch_telemetry, self._capabilities
                )
            except RaritanAuthError as exc:
                raise ConfigEntryAuthFailed(str(exc)) from exc
            except RaritanAPIError as exc:
                self._note_unreachable()
                raise UpdateFailed(str(exc)) from exc
            self._consecutive_skips = 0
            self._note_reachable()

            # Diff outlets against the previous tick, firing bus events for any flips.
            self._fire_outlet_state_change_events(payload.outlets)

            # The alert poll is folded into fetch_telemetry's single bulk, so
            # alerts arrive on the payload (one roundtrip per tick, not two).
            current_alerts = payload.current_alerts
            self._fire_alert_events(current_alerts)

            self._previous_outlets = list(payload.outlets)
            self._previous_alerts = list(current_alerts)
            return payload

    def _note_unreachable(self) -> None:
        """Track an unreachable streak and raise the repair once it crosses
        the threshold. Re-issued each failing tick (``async_create_issue`` is
        idempotent on the issue_id) so the "for N minutes" text stays current.
        """
        now = self.hass.loop.time()
        if self._unreachable_since is None:
            self._unreachable_since = now
            return
        elapsed = now - self._unreachable_since
        if elapsed >= UNREACHABLE_REPAIR_THRESHOLD:
            create_unreachable_issue(
                self.hass,
                entry_id=self._entry_id,
                host=self._api.host,
                minutes=int(elapsed // 60),
            )

    def _note_reachable(self) -> None:
        """Clear an active unreachable streak after a successful tick."""
        if self._unreachable_since is None:
            return
        self._unreachable_since = None
        clear_unreachable_issue(self.hass, entry_id=self._entry_id)

    def _fire_outlet_state_change_events(self, outlets: list[OutletReading]) -> None:
        if self._previous_outlets is None:
            return
        previous_by_idx = {o.idx: o for o in self._previous_outlets}
        for outlet in outlets:
            prev = previous_by_idx.get(outlet.idx)
            if prev is None or prev.on == outlet.on:
                continue
            self.hass.bus.async_fire(
                EVENT_TYPE_OUTLET_STATE_CHANGED,
                {
                    "serial": self._capabilities.serial,
                    "entry_id": self._entry_id,
                    "outlet_idx": outlet.idx,
                    "outlet_label": outlet.label,
                    "on_before": prev.on,
                    "on_after": outlet.on,
                },
            )

    def _fire_alert_events(self, current_alerts: list[AlertSnapshot]) -> None:
        if self._previous_alerts is None:
            return
        previous_ids = {a.sensor_id for a in self._previous_alerts}
        for alert in current_alerts:
            if alert.sensor_id in previous_ids:
                continue
            self.hass.bus.async_fire(
                EVENT_TYPE_ALERT,
                {
                    "serial": self._capabilities.serial,
                    "entry_id": self._entry_id,
                    "sensor_label": alert.sensor_label,
                    "parent_label": alert.parent_label,
                    "alert_state": alert.alert_state,
                    "sensor_id": alert.sensor_id,
                },
            )
