"""Tests for the reconfigure flow."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.raritan.api import (
    RaritanAPIError,
    RaritanAuthError,
    RaritanConnectionError,
    RaritanTLSError,
)
from custom_components.raritan.const import (
    CONF_CA_BUNDLE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DOMAIN,
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from homeassistant.core import HomeAssistant


async def _setup(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()


async def test_reconfigure_updates_entry(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.99",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "newsecret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.99"
    assert entry.data[CONF_PASSWORD] == "newsecret"


async def test_reconfigure_strips_host_whitespace(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Surrounding whitespace on the host is accepted and stored trimmed."""
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "  10.0.0.99  ",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "newsecret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.99"


async def test_reconfigure_serial_mismatch_aborts(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    original_host = entry.data[CONF_HOST]

    # Point the probe at a PDU reporting a different serial.
    mock_raritan.getMetaData.return_value.nameplate.serialNumber = "DIFFERENT0001"

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.99",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "newsecret",
            CONF_VERIFY_TLS: True,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_serial_mismatch"
    # Entry untouched.
    assert entry.data[CONF_HOST] == original_host


@pytest.mark.parametrize(
    ("exc", "expected_error"),
    [
        (RaritanAuthError, "invalid_auth"),
        (RaritanTLSError, "tls_failed"),
        (RaritanConnectionError, "cannot_connect"),
        (RaritanAPIError, "unknown"),
    ],
)
async def test_reconfigure_probe_errors_reshow_form(
    hass: HomeAssistant,
    mock_raritan: MagicMock,
    exc: type[RaritanAPIError],
    expected_error: str,
) -> None:
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe_identity.side_effect = exc("boom")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.99",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "newsecret",
                CONF_VERIFY_TLS: True,
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


async def test_reconfigure_resolves_ca_bundle(
    hass: HomeAssistant, mock_raritan: MagicMock, tmp_path
) -> None:
    """Reconfigure stores the resolved CA bundle realpath."""
    import ssl as _ssl
    from unittest.mock import MagicMock as _MM

    ca = tmp_path / "reconf.pem"
    ca.write_text("dummy")
    resolved = str(ca.resolve())

    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    with patch.object(_ssl, "create_default_context", return_value=_MM()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.1",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "newsecret",
                CONF_VERIFY_TLS: True,
                CONF_CA_BUNDLE: str(ca),
            },
        )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CA_BUNDLE] == resolved


async def test_reconfigure_rejects_invalid_host(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """A malformed host during reconfigure re-shows the form before any probe."""
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "http://10.0.0.1/evil",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_reconfigure_ca_bundle_not_found(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """A non-existent CA bundle path during reconfigure re-shows the form with an error."""
    await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
            CONF_CA_BUNDLE: "/nonexistent/path/to/ca.pem",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "ca_bundle_not_found"}
