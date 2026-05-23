"""Tests for DHCP discovery and host-change tracking."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from custom_components.raritan.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from homeassistant.core import HomeAssistant

_MAC = "00:11:22:33:44:55"


async def _setup(hass: HomeAssistant, host: str = "10.0.0.1") -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: host,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()


def _dhcp(ip: str) -> DhcpServiceInfo:
    return DhcpServiceInfo(ip=ip, hostname="raritan-pdu", macaddress="001122334455")


async def test_dhcp_discovers_new_device(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp("10.0.0.5")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # Host is prefilled from the DHCP lease.
    assert (
        result["data_schema"]({CONF_USERNAME: "x", CONF_PASSWORD: "y", CONF_VERIFY_TLS: True})[
            CONF_HOST
        ]
        == "10.0.0.5"
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.5",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_dhcp_updates_host_of_existing_entry(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    await _setup(hass, host="10.0.0.1")
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp("10.0.0.250")
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.250"


async def test_dhcp_aborts_on_invalid_host(hass: HomeAssistant) -> None:
    """A DHCP lease IP that fails host validation aborts the flow.

    The lease IP is string-formatted into a URL, so a malformed value (e.g. an
    IPv6 literal that breaks http.client) must abort rather than be carried into
    entry data.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp("::1")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_host"


async def test_dhcp_same_host_no_change(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    await _setup(hass, host="10.0.0.1")
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp("10.0.0.1")
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.1"
