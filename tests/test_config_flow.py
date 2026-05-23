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


async def test_user_step_strips_host_whitespace(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Surrounding whitespace on the host is accepted and stored trimmed."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "  10.0.0.1  ",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    # Stored value is trimmed so the runtime ``https://{host}/`` URL is well-formed.
    assert result["data"][CONF_HOST] == "10.0.0.1"


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


async def test_user_step_unknown_error_shows_form_again(hass: HomeAssistant) -> None:
    """A generic RaritanAPIError on the user step maps to the 'unknown' slot."""
    from custom_components.raritan.api import RaritanAPIError

    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        api_cls.return_value.probe.side_effect = RaritanAPIError("weird")
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
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_step_rejects_empty_host(hass: HomeAssistant) -> None:
    """A blank host is rejected before any probe."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "   ",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_reauth_resolves_ca_bundle_in_entry_data(
    hass: HomeAssistant, mock_raritan: MagicMock, tmp_path
) -> None:
    """Reauth stores the resolved CA bundle realpath from the merged data."""
    import ssl as _ssl

    ca = tmp_path / "reauth.pem"
    ca.write_text("dummy")
    resolved = str(ca.resolve())

    entry = await _setup_entry(hass, password="old")
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_CA_BUNDLE: str(ca)})

    with patch.object(_ssl, "create_default_context", return_value=MagicMock()):
        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new"},
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data[CONF_CA_BUNDLE] == resolved


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


async def test_user_step_ca_bundle_stored_as_resolved_path(
    hass: HomeAssistant, mock_raritan: MagicMock, tmp_path
) -> None:
    """The CA bundle stored in entry data is the resolved realpath.

    _ca_bundle_usable validates os.path.realpath(...); to avoid a TOCTOU where
    the unresolved path (e.g. via a symlink swapped after validation) is the one
    actually loaded by the TLS stack, the resolved path is what gets stored and
    later loaded.
    """
    import ssl as _ssl

    real = tmp_path / "real.pem"
    real.write_text("dummy")
    link = tmp_path / "link.pem"
    link.symlink_to(real)
    # Resolve via pathlib (avoids ASYNC240 on os.path in an async test).
    resolved = str(link.resolve())

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
                CONF_CA_BUNDLE: str(link),
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    # Stored path is the resolved target, not the (swappable) symlink.
    assert result["data"][CONF_CA_BUNDLE] == resolved
    assert result["data"][CONF_CA_BUNDLE] != str(link)


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


async def test_user_step_ca_bundle_rejects_non_cert_extension(
    hass: HomeAssistant, tmp_path
) -> None:
    """A readable file without a cert extension (e.g. /etc/passwd) is rejected.

    os.path.isfile() alone would happily accept any host path and hand it to
    the TLS stack to be parsed as PEM; gate on a certificate file extension so
    arbitrary files cannot be opened/parsed.
    """
    bogus = tmp_path / "passwd"
    bogus.write_text("root:x:0:0:root:/root:/bin/bash\n")
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
            CONF_CA_BUNDLE: str(bogus),
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "ca_bundle_not_found"}


async def test_user_step_rejects_malformed_host(hass: HomeAssistant) -> None:
    """A host that is not a valid hostname / IP is rejected before any probe.

    CONF_HOST is string-formatted into a URL at runtime, so a malformed value
    (spaces, slashes, embedded scheme) must never reach that point.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
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
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_user_step_rejects_ipv6_literal(hass: HomeAssistant) -> None:
    """A bare IPv6 literal is rejected before probe.

    CONF_HOST becomes ``https://{host}/`` at runtime; a bare IPv6 literal
    (e.g. ``::1``) yields a malformed URL that breaks http.client. Raritan PDUs
    are IPv4/hostname in practice, so reject IPv6 with the invalid_host error.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "::1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_TLS: True,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


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


async def test_reauth_rejects_invalid_host_in_entry_data(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Reauth re-validates the (merged) host before probing.

    If an entry somehow carries a malformed host, reauth must surface
    invalid_host and never reach probe_identity, since the host would be
    string-formatted into a URL.
    """
    entry = await _setup_entry(hass, password="old")
    # Corrupt the stored host so the merged reauth data is malformed.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_HOST: "http://evil/path"}
    )

    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new"},
        )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}
    # The probe must not have been attempted on a malformed host.
    api_cls.return_value.probe_identity.assert_not_called()


async def test_reauth_rejects_missing_ca_bundle(
    hass: HomeAssistant, mock_raritan: MagicMock
) -> None:
    """Reauth re-validates the CA bundle before probing."""
    entry = await _setup_entry(hass, password="old")
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CA_BUNDLE: "/nonexistent/ca.pem"}
    )

    with patch("custom_components.raritan.config_flow.RaritanAPI") as api_cls:
        result = await _start_reauth(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new"},
        )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "ca_bundle_not_found"}
    api_cls.return_value.probe_identity.assert_not_called()


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
