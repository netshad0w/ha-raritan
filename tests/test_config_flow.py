"""Tests for the Raritan config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from homeassistant import config_entries, data_entry_flow

from custom_components.raritan.api import (
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
    from homeassistant.core import HomeAssistant


async def test_user_step_form_shown(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_creates_entry_on_success(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Raritan PX3-5487V-N2 (TEST00000001)"
    assert result["data"][CONF_HOST] == "10.0.0.1"
    entries = hass.config_entries.async_entries(DOMAIN)
    assert entries[0].unique_id == "TEST00000001"


async def test_user_step_auth_error_shows_form_again(hass: HomeAssistant) -> None:
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanAuthError("forbidden")
        api_cls.return_value.probe_identity.side_effect = RaritanAuthError("forbidden")
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.1",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "bad",
                CONF_VERIFY_TLS: True,
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_step_connection_error(hass: HomeAssistant) -> None:
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanConnectionError("unreachable")
        api_cls.return_value.probe_identity.side_effect = RaritanConnectionError("unreachable")
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.1",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret",
                CONF_VERIFY_TLS: True,
            },
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_tls_error(hass: HomeAssistant) -> None:
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanTLSError("cert verify failed")
        api_cls.return_value.probe_identity.side_effect = RaritanTLSError("cert verify failed")
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.1",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret",
                CONF_VERIFY_TLS: True,
            },
        )
    assert result["errors"] == {"base": "tls_failed"}


async def test_user_step_rejects_duplicate_serial(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
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
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.2",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_with_ca_bundle(hass: HomeAssistant, mock_raritan: MagicMock, tmp_path):
    # A provided CA bundle must point to a readable file, so create a real one.
    import ssl as _ssl

    ca = tmp_path / "custom.pem"
    ca.write_text("dummy")
    with patch.object(_ssl, "create_default_context", return_value=MagicMock()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "10.0.0.1",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret",
                CONF_VERIFY_TLS: True,
                CONF_CA_BUNDLE: str(ca),
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CA_BUNDLE] == str(ca)


async def test_user_step_ca_bundle_not_found(hass: HomeAssistant) -> None:
    """A CA bundle path that is not a readable file re-shows the form with an error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
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
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "ca_bundle_not_found"}


async def _setup_entry(hass: HomeAssistant, password: str = "secret") -> Any:
    """Helper: complete the user flow once and return the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: password,
            CONF_VERIFY_TLS: True,
        },
    )
    return hass.config_entries.async_entries(DOMAIN)[0]


async def _start_reauth(hass: HomeAssistant, entry: Any) -> Any:
    """Helper: kick off the reauth flow on the given entry."""
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )


async def test_reauth_flow_shown_when_auth_fails(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    entry = await _setup_entry(hass, password="secret")

    result = await _start_reauth(hass, entry)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"


async def test_reauth_success_updates_credentials(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    entry = await _setup_entry(hass, password="old")
    assert entry.data[CONF_PASSWORD] == "old"

    result = await _start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new-secret"},
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data[CONF_PASSWORD] == "new-secret"


async def test_reauth_invalid_auth_shows_form_again(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Bad credentials during reauth show the form again with an error."""
    entry = await _setup_entry(hass, password="old")

    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanAuthError("forbidden")
        api_cls.return_value.probe_identity.side_effect = RaritanAuthError("forbidden")
        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "still-bad"},
        )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_tls_error_shows_form_again(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    entry = await _setup_entry(hass, password="old")
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanTLSError("cert verify failed")
        api_cls.return_value.probe_identity.side_effect = RaritanTLSError("cert verify failed")
        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new"},
        )
    assert result["errors"] == {"base": "tls_failed"}


async def test_reauth_connection_error_shows_form_again(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    entry = await _setup_entry(hass, password="old")
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanConnectionError("unreachable")
        api_cls.return_value.probe_identity.side_effect = RaritanConnectionError("unreachable")
        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new"},
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_unknown_error_shows_form_again(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Generic RaritanAPIError -> 'unknown' errors slot."""
    from custom_components.raritan.api import RaritanAPIError

    entry = await _setup_entry(hass, password="old")
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanAPIError("weird")
        api_cls.return_value.probe_identity.side_effect = RaritanAPIError("weird")
        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new"},
        )
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_serial_mismatch_aborts(hass: HomeAssistant, mock_raritan: MagicMock) -> None:
    """If the PDU at the same address has a different serial, abort to avoid silent device swap."""
    entry = await _setup_entry(hass, password="old")

    from custom_components.raritan.models import CapabilityMatrix

    different_cap = CapabilityMatrix(
        model="X",
        firmware="4.0.10",
        serial="DIFFERENT_SERIAL",
        hw_revision=None,
        nb_inlets=0,
        outlet_ids=(),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=False,
        outlet_metering=False,
        has_alerts_engine=False,
    )
    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.return_value = different_cap
        api_cls.return_value.probe_identity.return_value = ("DIFFERENT_SERIAL", "X")

        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new"},
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_serial_mismatch"


async def test_options_flow_updates_scan_interval(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "10.0.0.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_result["step_id"] == "init"
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"scan_interval": 10},
    )
    assert options_result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 10
