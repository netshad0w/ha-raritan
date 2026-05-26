"""Shared DeviceInfo builders and id helpers for Raritan sub-devices.

Outlet, OCP and env peripherals each get their own HA sub-device (linked to
the PDU via ``via_device``). The DeviceInfo for each was constructed
identically across the entity platforms, so the builders live here to keep
them in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from .models import CapabilityMatrix


def slug_sensor_id(sid: str) -> str:
    """Slugify a peripheral sensor id for use in unique_id / identifiers.

    Env sensor ids may contain colons or slashes that aren't safe in those
    contexts, so replace them with underscores.
    """
    return sid.replace(":", "_").replace("/", "_")


def env_display_name(sensor_type: str) -> str:
    """Human-readable display name from an env sensor type (e.g. ``DEW_POINT``)."""
    return sensor_type.replace("_", " ").title()


def _pdu_device_name(cap: CapabilityMatrix) -> str:
    """Display name of the parent PDU device."""
    return f"Raritan {cap.model} ({cap.serial})"


def outlet_device_info(cap: CapabilityMatrix, idx: int) -> DeviceInfo:
    """DeviceInfo for the per-outlet sub-device.

    The name is bare ("Outlet 3") so entities read "Outlet 3 Active power"
    instead of repeating the PDU model+serial on every one. The owning PDU is
    carried by ``via_device`` (nesting in the UI) and ``serial_number`` (shown
    in the device info box), which keep things unambiguous across several PDUs.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{cap.serial}_outlet_{idx}")},
        name=f"Outlet {idx}",
        manufacturer="Raritan",
        model=f"{cap.model} outlet",
        serial_number=cap.serial,
        via_device=(DOMAIN, cap.serial),
    )


def ocp_device_info(cap: CapabilityMatrix, idx: int) -> DeviceInfo:
    """DeviceInfo for the per-OCP (over-current protector) sub-device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{cap.serial}_ocp_{idx}")},
        name=f"OCP {idx}",
        manufacturer="Raritan",
        model=f"{cap.model} OCP",
        serial_number=cap.serial,
        via_device=(DOMAIN, cap.serial),
    )


def inlet_device_info(cap: CapabilityMatrix, idx: int, host: str) -> DeviceInfo:
    """DeviceInfo for an inlet sensor.

    Multi-inlet PDUs (ATS, dual-feed) get a sub-device per inlet so each feed
    can be assigned to its own HA Area. Single-inlet PDUs (the 99% case) keep
    the inlet sensors flat on the PDU device itself; adding a sub-device for the
    only feed would just deepen navigation without conveying anything.
    """
    if cap.nb_inlets > 1:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{cap.serial}_inlet_{idx}")},
            manufacturer="Raritan",
            model=f"{cap.model} inlet",
            name=f"Inlet {idx}",
            serial_number=cap.serial,
            via_device=(DOMAIN, cap.serial),
        )
    return DeviceInfo(
        identifiers={(DOMAIN, cap.serial)},
        manufacturer="Raritan",
        model=cap.model,
        name=_pdu_device_name(cap),
        sw_version=cap.firmware,
        hw_version=cap.hw_revision,
        configuration_url=f"https://{host}/",
    )


def psu_device_info(cap: CapabilityMatrix, idx: int) -> DeviceInfo:
    """DeviceInfo for a controller PSU health sensor.

    Single-PSU PDUs (the common case) keep the entity flat on the PDU device.
    Multi-PSU PDUs (rare, mostly large i7 / 4-pole models) get a sub-device per
    PSU so the user can see which one failed without parsing the entity ID.
    """
    if cap.nb_psu > 1:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{cap.serial}_psu_{idx}")},
            name=f"PSU {idx}",
            manufacturer="Raritan",
            model=f"{cap.model} PSU",
            serial_number=cap.serial,
            via_device=(DOMAIN, cap.serial),
        )
    return DeviceInfo(
        identifiers={(DOMAIN, cap.serial)},
    )


def env_device_info(cap: CapabilityMatrix, safe_id: str, label: str) -> DeviceInfo:
    """DeviceInfo for an env (peripheral) sub-device.

    ``safe_id`` must already be slugified via :func:`slug_sensor_id`.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{cap.serial}_env_{safe_id}")},
        name=label,
        manufacturer="Raritan",
        model=f"{cap.model} env",
        serial_number=cap.serial,
        via_device=(DOMAIN, cap.serial),
    )
