"""Repair-issue lifecycle helpers.

Issue IDs are keyed by ``entry_id`` (an opaque ULID), not by the PDU serial,
because HA's diagnostics framework auto-includes the issue registry entries
for the domain in every config-entry diagnostics dump. A serial in the
issue_id would leak an identifier that the rest of the diagnostics carefully
redacts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    ISSUE_FIRMWARE_TOO_OLD,
    ISSUE_TLS_DISABLED,
    ISSUE_UNREACHABLE_EXTENDED,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def create_tls_disabled_issue(hass: HomeAssistant, *, entry_id: str, host: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_TLS_DISABLED}_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TLS_DISABLED,
        # Only `host` is exposed in placeholders. Serial intentionally omitted:
        # placeholders are persisted to issues.json and surfaced verbatim in
        # diagnostics dumps that users paste into public bug reports. The
        # repair UI already shows the integration entry context so the user
        # still knows which PDU is affected.
        translation_placeholders={"host": host},
        learn_more_url="https://github.com/netshad0w/ha-raritan#troubleshooting",
    )


def clear_tls_disabled_issue(hass: HomeAssistant, *, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_TLS_DISABLED}_{entry_id}")


def create_firmware_too_old_issue(
    hass: HomeAssistant, *, entry_id: str, firmware: str, minimum: str
) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_FIRMWARE_TOO_OLD}_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_FIRMWARE_TOO_OLD,
        # Serial intentionally omitted; see create_tls_disabled_issue.
        translation_placeholders={"firmware": firmware, "minimum": minimum},
        learn_more_url="https://github.com/netshad0w/ha-raritan#requirements",
    )


def clear_firmware_too_old_issue(hass: HomeAssistant, *, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_FIRMWARE_TOO_OLD}_{entry_id}")


def create_unreachable_issue(
    hass: HomeAssistant, *, entry_id: str, host: str, minutes: int
) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_UNREACHABLE_EXTENDED}_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UNREACHABLE_EXTENDED,
        # Serial intentionally omitted; see create_tls_disabled_issue.
        translation_placeholders={"host": host, "minutes": str(minutes)},
        learn_more_url="https://github.com/netshad0w/ha-raritan#troubleshooting",
    )


def clear_unreachable_issue(hass: HomeAssistant, *, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_UNREACHABLE_EXTENDED}_{entry_id}")


def cleanup_legacy_serial_keyed_issues(hass: HomeAssistant, *, serial: str) -> None:
    """Delete pre-1.0.1 issues whose IDs embedded the PDU serial.

    Earlier versions used ``f"{prefix}_{serial}"`` as the issue ID. Since
    1.0.1, IDs are keyed by ``entry_id``. This sweep keeps a long-lived
    install from accumulating two issues for the same condition after the
    upgrade, and removes the orphaned ID that still contains the serial.
    """
    registry = ir.async_get(hass)
    for prefix in (ISSUE_TLS_DISABLED, ISSUE_FIRMWARE_TOO_OLD, ISSUE_UNREACHABLE_EXTENDED):
        legacy_id = f"{prefix}_{serial}"
        if registry.async_get_issue(DOMAIN, legacy_id) is not None:
            ir.async_delete_issue(hass, DOMAIN, legacy_id)
