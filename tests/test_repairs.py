"""Tests for repair issue lifecycle helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from custom_components.raritan.const import (
    DOMAIN,
    ISSUE_FIRMWARE_TOO_OLD,
    ISSUE_TLS_DISABLED,
    ISSUE_UNREACHABLE_EXTENDED,
)
from custom_components.raritan.repairs import (
    cleanup_legacy_serial_keyed_issues,
    clear_firmware_too_old_issue,
    clear_tls_disabled_issue,
    clear_unreachable_issue,
    create_firmware_too_old_issue,
    create_tls_disabled_issue,
    create_unreachable_issue,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_ENTRY_ID = "01ABC123456789DEF0123456"  # opaque ULID-like fixture


async def test_create_and_clear_tls_issue(hass: HomeAssistant) -> None:
    create_tls_disabled_issue(hass, entry_id=_ENTRY_ID, host="10.0.0.1")
    issues = ir.async_get(hass)
    issue_id = f"{ISSUE_TLS_DISABLED}_{_ENTRY_ID}"
    assert issues.async_get_issue(DOMAIN, issue_id) is not None
    clear_tls_disabled_issue(hass, entry_id=_ENTRY_ID)
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_tls_issue_does_not_leak_serial(hass: HomeAssistant) -> None:
    """The issue_id and translation_placeholders are both surfaced in
    diagnostics dumps that users paste into public bug reports. Neither
    must embed the PDU serial.
    """
    create_tls_disabled_issue(hass, entry_id=_ENTRY_ID, host="10.0.0.1")
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, f"{ISSUE_TLS_DISABLED}_{_ENTRY_ID}")
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert "serial" not in issue.translation_placeholders
    clear_tls_disabled_issue(hass, entry_id=_ENTRY_ID)


async def test_create_firmware_too_old_severity_error(hass: HomeAssistant) -> None:
    create_firmware_too_old_issue(hass, entry_id=_ENTRY_ID, firmware="3.5.0", minimum="4.0.10")
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_FIRMWARE_TOO_OLD}_{_ENTRY_ID}")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.ERROR
    assert issue.translation_placeholders is not None
    assert "serial" not in issue.translation_placeholders
    clear_firmware_too_old_issue(hass, entry_id=_ENTRY_ID)


async def test_unreachable_issue_translation_placeholders(hass: HomeAssistant) -> None:
    create_unreachable_issue(hass, entry_id=_ENTRY_ID, host="10.0.0.1", minutes=42)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_UNREACHABLE_EXTENDED}_{_ENTRY_ID}")
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders.get("host") == "10.0.0.1"
    assert issue.translation_placeholders.get("minutes") == "42"
    assert "serial" not in issue.translation_placeholders
    clear_unreachable_issue(hass, entry_id=_ENTRY_ID)


async def test_cleanup_legacy_serial_keyed_issues_removes_old_format(
    hass: HomeAssistant,
) -> None:
    """A pre-1.0.1 install carrying serial-suffixed issue IDs should have them
    deleted by the migration sweep. New entry_id-suffixed IDs are untouched.
    """
    serial = "TESTSERIAL001"
    # Simulate stale registry state: directly create both legacy and new IDs.
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_TLS_DISABLED}_{serial}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TLS_DISABLED,
    )
    create_tls_disabled_issue(hass, entry_id=_ENTRY_ID, host="10.0.0.1")
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_TLS_DISABLED}_{serial}") is not None
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_TLS_DISABLED}_{_ENTRY_ID}") is not None

    cleanup_legacy_serial_keyed_issues(hass, serial=serial)

    assert registry.async_get_issue(DOMAIN, f"{ISSUE_TLS_DISABLED}_{serial}") is None
    # New-format issue must still be there.
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_TLS_DISABLED}_{_ENTRY_ID}") is not None
    clear_tls_disabled_issue(hass, entry_id=_ENTRY_ID)
