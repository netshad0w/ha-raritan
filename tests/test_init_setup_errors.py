"""Error-path tests for async_setup_entry."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.raritan.api import RaritanAPIError
from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)
from custom_components.raritan.models import CapabilityMatrix

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_ENTRY_DATA = {
    CONF_HOST: "10.0.0.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_VERIFY_TLS: True,
}


def _caps(firmware: str) -> CapabilityMatrix:
    return CapabilityMatrix(
        model="PX3",
        firmware=firmware,
        serial="TEST00000001",
        hw_revision=None,
        nb_inlets=1,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
        has_alerts_engine=False,
    )


async def test_setup_probe_error_sets_retry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=_ENTRY_DATA, unique_id="TEST00000001")
    entry.add_to_hass(hass)
    api = MagicMock()
    api.probe.side_effect = RaritanAPIError("unreachable")
    with patch("custom_components.raritan.RaritanAPI", return_value=api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_firmware_too_old_sets_retry_and_creates_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=_ENTRY_DATA, unique_id="TEST00000001")
    entry.add_to_hass(hass)
    api = MagicMock()
    api.probe.return_value = _caps("3.0.0")
    with patch("custom_components.raritan.RaritanAPI", return_value=api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issues = ir.async_get(hass)
    assert any("firmware_below_minimum" in issue_id for (_domain, issue_id) in issues.issues)


async def test_setup_with_tls_disabled_creates_issue(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={**_ENTRY_DATA, CONF_VERIFY_TLS: False},
    )
    await hass.async_block_till_done()
    issues = ir.async_get(hass)
    assert any("tls_verification_disabled" in issue_id for (_domain, issue_id) in issues.issues)
