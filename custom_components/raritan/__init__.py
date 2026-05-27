"""The Raritan PDU integration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import RaritanAPI, RaritanAPIError
from .const import (
    CONF_CA_BUNDLE,
    CONF_VERIFY_TLS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_FIRMWARE_VERSION,
)
from .coordinator import RaritanDataUpdateCoordinator
from .device_info import slug_sensor_id
from .models import RaritanRuntimeData
from .repairs import (
    cleanup_legacy_serial_keyed_issues,
    clear_firmware_too_old_issue,
    clear_tls_disabled_issue,
    create_firmware_too_old_issue,
    create_tls_disabled_issue,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.EVENT,
]


type RaritanConfigEntry = ConfigEntry[RaritanRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: RaritanConfigEntry) -> bool:
    """Set up Raritan PDU from a config entry."""
    api = RaritanAPI(
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_tls=entry.data[CONF_VERIFY_TLS],
        ca_bundle=entry.data.get(CONF_CA_BUNDLE),
    )
    # Close the agent's socket on unload, including the ConfigEntryNotReady paths
    # below (probe/firmware/first-refresh): those raise before runtime_data is
    # set, so the unload handler can't reach the api -- without this, each
    # SETUP_RETRY would leak a connection. close() is idempotent.
    entry.async_on_unload(api.close)

    try:
        capabilities = await hass.async_add_executor_job(api.probe)
    except RaritanAPIError as exc:
        raise ConfigEntryNotReady(f"Unable to probe Raritan PDU: {exc}") from exc

    # Drop any legacy serial-keyed issues left by earlier builds; see
    # repairs.cleanup_legacy_serial_keyed_issues for rationale.
    cleanup_legacy_serial_keyed_issues(hass, serial=capabilities.serial)

    # One-shot rename: older builds created inlet sensor entity_ids with the
    # literal placeholder "inlet_idx" because HA slugifies the translation
    # template before resolving placeholders. Migrate `..._inlet_idx_<metric>`
    # to `..._inlet_<n>_<metric>` using the inlet index already encoded in the
    # unique_id. Idempotent.
    _migrate_inlet_idx_entity_ids(hass, entry_id=entry.entry_id)

    if not entry.data[CONF_VERIFY_TLS]:
        create_tls_disabled_issue(
            hass,
            entry_id=entry.entry_id,
            host=entry.data[CONF_HOST],
        )
    else:
        clear_tls_disabled_issue(hass, entry_id=entry.entry_id)

    if capabilities.firmware_tuple < MIN_FIRMWARE_VERSION:
        minimum = ".".join(str(x) for x in MIN_FIRMWARE_VERSION)
        create_firmware_too_old_issue(
            hass,
            entry_id=entry.entry_id,
            firmware=capabilities.firmware,
            minimum=minimum,
        )
        raise ConfigEntryNotReady(f"Firmware {capabilities.firmware} is below minimum {minimum}")
    clear_firmware_too_old_issue(hass, entry_id=entry.entry_id)

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = RaritanDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        api=api,
        capabilities=capabilities,
        scan_interval=scan_interval,
    )
    await coordinator.async_config_entry_first_refresh()

    # Register the parent PDU device explicitly. With multi-inlet PDUs no
    # entity DeviceInfo identifies the PDU as itself (every entity goes to a
    # sub-device), so the via_device chain has no anchor unless we declare
    # the parent here. For single-inlet PDUs the inlet sensor's DeviceInfo
    # already points at the PDU directly, so this is a no-op repeat.
    connections = (
        {(dr.CONNECTION_NETWORK_MAC, dr.format_mac(capabilities.mac))}
        if capabilities.mac
        else set()
    )
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, capabilities.serial)},
        connections=connections,
        manufacturer="Raritan",
        model=capabilities.model,
        name=f"Raritan {capabilities.model} ({capabilities.serial})",
        sw_version=capabilities.firmware,
        hw_version=capabilities.hw_revision,
        configuration_url=f"https://{entry.data[CONF_HOST]}/",
    )

    entry.runtime_data = RaritanRuntimeData(
        api=api,
        capabilities=capabilities,
        coordinator=coordinator,
    )

    # stale-devices: when a hot-pluggable env peripheral disappears from a
    # coordinator rescan, drop its device so it doesn't linger as unavailable.
    env_device_prefix = f"{capabilities.serial}_env_"

    @callback
    def _reconcile_env_devices() -> None:
        data = coordinator.data
        if data is None:
            return
        current = {slug_sensor_id(r.sensor_id) for r in data.env}
        device_reg = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(device_reg, entry.entry_id):
            for domain, ident in device.identifiers:
                if domain == DOMAIN and ident.startswith(env_device_prefix):
                    if ident.removeprefix(env_device_prefix) not in current:
                        device_reg.async_remove_device(device.id)
                    break

    entry.async_on_unload(coordinator.async_add_listener(_reconcile_env_devices))

    _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RaritanConfigEntry) -> bool:
    """Unload a config entry."""
    # api.close() runs via the entry.async_on_unload callback registered in setup.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(_hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry."""
    _LOGGER.debug("Migrating entry %s from version %s", entry.entry_id, entry.version)
    if entry.version == 1:
        return True
    _LOGGER.error(
        "Cannot migrate Raritan config entry from version %s (latest supported is 1)",
        entry.version,
    )
    return False


async def _async_update_listener(hass: HomeAssistant, entry: RaritanConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


_OUTLET_UNIQUE_ID_RE = re.compile(r"_outlet_(\d+)_(switch|cycle)$")

_INLET_UNIQUE_ID_RE = re.compile(r"_inlet_(\d+)_")

_INLET_ENERGY_UID_RE = re.compile(r"_inlet_(\d+)_active_energy$")

_OUTLET_ENERGY_UID_RE = re.compile(r"_outlet_(\d+)_active_energy$")


def _migrate_inlet_idx_entity_ids(hass: HomeAssistant, *, entry_id: str) -> None:
    """Rename entity_ids that carry the literal "inlet_idx" placeholder.

    HA composes auto-generated entity_ids by slugifying the *unresolved*
    translation template, so "Inlet {idx} voltage" becomes the slug
    `inlet_idx_voltage`. The unique_id carries the real inlet index, so we
    can rebuild the slug correctly and update the registry in place. Safe
    to call on every setup: only entries with the broken slug are touched.
    """
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry_id)
    for ent in entries:
        if "_inlet_idx_" not in ent.entity_id:
            continue
        match = _INLET_UNIQUE_ID_RE.search(ent.unique_id or "")
        if match is None:
            continue
        new_entity_id = ent.entity_id.replace("_inlet_idx_", f"_inlet_{match.group(1)}_")
        if new_entity_id == ent.entity_id:
            continue
        if registry.async_get(new_entity_id) is not None:
            # The target id is already taken (e.g. both the broken and the fixed
            # entity exist), so the rename would clobber it. Skip, but warn --
            # otherwise the stale `_inlet_idx_` entity stays forever with no clue.
            _LOGGER.warning(
                "Cannot rename %s to %s: the target entity_id already exists; "
                "remove the stale one manually",
                ent.entity_id,
                new_entity_id,
            )
            continue
        _LOGGER.info("Renaming %s -> %s", ent.entity_id, new_entity_id)
        registry.async_update_entity(ent.entity_id, new_entity_id=new_entity_id)


def _resolve_entity_to_outlet(
    hass: HomeAssistant, entity_id: str
) -> tuple[RaritanConfigEntry, int] | None:
    """Resolve an outlet switch entity_id to its (config_entry, outlet_idx)."""
    registry = er.async_get(hass)
    entity = registry.async_get(entity_id)
    if entity is None or entity.platform != DOMAIN:
        return None
    config_entry_id = entity.config_entry_id
    if config_entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    match = _OUTLET_UNIQUE_ID_RE.search(entity.unique_id or "")
    if match is None:
        return None
    return entry, int(match.group(1))


def _iter_outlet_entries(
    hass: HomeAssistant, entity_ids: list[str]
) -> Iterator[tuple[RaritanConfigEntry, int]]:
    """Yield (config_entry, outlet_idx) for each outlet entity_id.

    Shared by the outlet service handlers: resolves each entity to its outlet
    and verifies the owning entry is loaded, raising the same translated
    HomeAssistantError as before on the first failure.
    """
    for entity_id in entity_ids:
        resolved = _resolve_entity_to_outlet(hass, entity_id)
        if resolved is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_resolve_outlet",
                translation_placeholders={"entity_id": entity_id},
            )
        entry, outlet_idx = resolved
        if not hasattr(entry, "runtime_data"):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"entity_id": entity_id},
            )
        yield entry, outlet_idx


def _async_register_services(hass: HomeAssistant) -> None:
    """Register raritan services. Idempotent: only registers once."""
    if hass.services.has_service(DOMAIN, "cycle_outlet"):
        return

    async def _async_cycle_outlet(call: ServiceCall) -> None:
        for entry, outlet_idx in _iter_outlet_entries(hass, call.data.get("entity_id", [])):
            try:
                await entry.runtime_data.coordinator.async_cycle_outlet(idx=outlet_idx)
            except RaritanAPIError as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cycle_failed",
                    translation_placeholders={"idx": str(outlet_idx), "error": str(exc)},
                ) from exc

    async def _async_set_outlet_state(call: ServiceCall) -> None:
        on = bool(call.data["state"])
        for entry, outlet_idx in _iter_outlet_entries(hass, call.data.get("entity_id", [])):
            try:
                await entry.runtime_data.coordinator.async_set_outlet_state(idx=outlet_idx, on=on)
            except RaritanAPIError as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="set_state_failed",
                    translation_placeholders={"idx": str(outlet_idx), "error": str(exc)},
                ) from exc

    async def _async_reset_energy_counter(call: ServiceCall) -> None:
        entity_ids = call.data.get("entity_id", [])
        for entity_id in entity_ids:
            registry = er.async_get(hass)
            entity = registry.async_get(entity_id)
            if entity is None or entity.platform != DOMAIN:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cannot_resolve_sensor",
                    translation_placeholders={"entity_id": entity_id},
                )
            uid = entity.unique_id or ""
            inlet_match = _INLET_ENERGY_UID_RE.search(uid)
            outlet_match = _OUTLET_ENERGY_UID_RE.search(uid)
            if inlet_match is not None:
                kind = "inlet"
                idx = int(inlet_match.group(1))
            elif outlet_match is not None:
                kind = "outlet"
                idx = int(outlet_match.group(1))
            else:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="not_energy_sensor",
                    translation_placeholders={"entity_id": entity_id},
                )

            if entity.config_entry_id is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_config_entry",
                    translation_placeholders={"entity_id": entity_id},
                )
            config_entry = hass.config_entries.async_get_entry(entity.config_entry_id)
            if config_entry is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="no_config_entry",
                    translation_placeholders={"entity_id": entity_id},
                )
            if not hasattr(config_entry, "runtime_data"):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="entry_not_loaded",
                    translation_placeholders={"entity_id": entity_id},
                )
            coordinator = config_entry.runtime_data.coordinator
            try:
                if kind == "inlet":
                    await coordinator.async_reset_inlet_energy(idx=idx)
                else:
                    await coordinator.async_reset_outlet_energy(idx=idx)
            except RaritanAPIError as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="reset_failed",
                    translation_placeholders={"error": str(exc)},
                ) from exc

    entity_target_schema = vol.Schema(
        {vol.Required("entity_id"): cv.entity_ids}, extra=vol.ALLOW_EXTRA
    )
    hass.services.async_register(
        DOMAIN, "cycle_outlet", _async_cycle_outlet, schema=entity_target_schema
    )
    hass.services.async_register(
        DOMAIN,
        "set_outlet_state",
        _async_set_outlet_state,
        schema=entity_target_schema.extend({vol.Required("state"): bool}),
    )
    hass.services.async_register(
        DOMAIN, "reset_energy_counter", _async_reset_energy_counter, schema=entity_target_schema
    )
