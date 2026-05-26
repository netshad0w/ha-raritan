"""Config flow for the Raritan PDU integration."""

from __future__ import annotations

import ipaddress
import os
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .api import (
    RaritanAPI,
    RaritanAPIError,
    RaritanAuthError,
    RaritanConnectionError,
    RaritanTLSError,
)
from .const import (
    CA_BUNDLE_EXTENSIONS,
    CONF_CA_BUNDLE,
    CONF_VERIFY_TLS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_TLS,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

# A single hostname label: alphanumeric, may contain hyphens but not at the
# ends. A full hostname is one or more such labels joined by dots (a trailing
# dot for the root zone is allowed). IPv4/IPv6 are validated separately.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)


def _is_valid_host(host: str) -> bool:
    """Return True when host is a valid hostname or IPv4 literal.

    CONF_HOST is later string-formatted into ``https://{host}/`` (see
    __init__.py), so anything carrying a scheme, path, whitespace, or other URL
    metacharacters must be rejected here before it is ever accepted.

    A bare IPv6 literal (e.g. ``::1``) would produce a malformed URL that breaks
    http.client (IPv6 literals require bracketing). Raritan PDUs are
    IPv4/hostname in practice, so IPv6 addresses are rejected outright rather
    than silently carried into a broken URL.
    """
    host = host.strip()
    if not host:
        return False
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        pass
    else:
        return True
    # Reject IPv6 literals explicitly so they don't fall through to the
    # hostname regex (which would reject them anyway) and to make intent clear.
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        pass
    else:
        return False
    return bool(_HOSTNAME_RE.match(host))


def _resolve_ca_bundle(ca_bundle: str) -> str | None:
    """Return the resolved realpath of a safe, readable CA bundle, else None.

    Runs in an executor: resolves the real path, requires a certificate file
    extension (.pem/.crt/.cer), and requires it to be a regular file. The
    extension gate stops an arbitrary host path (e.g. ``/etc/passwd``) from
    being opened and parsed as PEM by the TLS stack.

    Returning the *resolved* path (rather than just True/False) lets callers
    store and later load the exact path that was validated, closing the TOCTOU
    window where a symlink could be swapped between validation and the TLS
    stack's ``ssl.create_default_context(cafile=...)`` call.
    """
    try:
        resolved = os.path.realpath(ca_bundle)
    except ValueError, OSError:
        # ValueError: embedded NUL byte in the path; OSError: unresolvable path.
        # Either way the path is unusable -> treat as not found rather than let
        # it escape the executor job as an internal error in the config flow.
        return None
    if not resolved.lower().endswith(CA_BUNDLE_EXTENSIONS):
        return None
    if not os.path.isfile(resolved):
        return None
    return resolved


USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_VERIFY_TLS, default=DEFAULT_VERIFY_TLS): bool,
        vol.Optional(CONF_CA_BUNDLE): str,
    }
)


class RaritanPduConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Raritan PDU."""

    VERSION = 1

    _discovered_host: str | None = None

    async def _resolve_ca_bundle(self, ca_bundle: str | None) -> tuple[bool, str | None]:
        """Validate a CA bundle path and return ``(ok, resolved_path)``.

        ``ok`` is False only when a non-empty path is unusable: a path is
        rejected unless it (a) carries a certificate file extension
        (.pem/.crt/.cer) and (b) resolves to a readable regular file. The
        extension gate stops an arbitrary host path (e.g. ``/etc/passwd``) from
        being opened and parsed as PEM by the TLS stack.

        On success ``resolved_path`` is the realpath actually validated; callers
        store that (not the user's possibly-symlinked input) so the path loaded
        by the TLS stack is the one that was checked.
        """
        if not ca_bundle:
            return True, None
        resolved = await self.hass.async_add_executor_job(_resolve_ca_bundle, ca_bundle)
        return resolved is not None, resolved

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle a PDU discovered (or IP-changed) via DHCP."""
        # The lease IP is later string-formatted into a URL; reject anything
        # malformed rather than carry it into entry data.
        if not _is_valid_host(discovery_info.ip):
            return self.async_abort(reason="invalid_host")
        mac = dr.format_mac(discovery_info.macaddress)
        # If this hardware is already configured, update its host if the lease
        # changed (discovery-update-info) and abort, rather than prompt again.
        device = dr.async_get(self.hass).async_get_device(
            connections={(dr.CONNECTION_NETWORK_MAC, mac)}
        )
        if device is not None:
            for entry_id in device.config_entries:
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry is None or entry.domain != DOMAIN:
                    continue
                if entry.data.get(CONF_HOST) != discovery_info.ip:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_HOST: discovery_info.ip}
                    )
                    self.hass.config_entries.async_schedule_reload(entry_id)
                return self.async_abort(reason="already_configured")
        # New hardware: dedupe concurrent discovery flows by MAC, then collect
        # credentials. The user step re-keys the entry on the PDU serial.
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()
        self._discovered_host = discovery_info.ip
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Trim the host before validating/storing: it is later string-formatted
            # into ``https://{host}/``, so stray surrounding whitespace would yield
            # a malformed URL.
            user_input = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            ca_ok, resolved_ca = await self._resolve_ca_bundle(user_input.get(CONF_CA_BUNDLE))
            if not _is_valid_host(user_input[CONF_HOST]):
                errors["base"] = "invalid_host"
            elif not ca_ok:
                errors["base"] = "ca_bundle_not_found"
            else:
                # Persist the resolved realpath so the TLS stack later loads the
                # exact file that was validated (TOCTOU-safe).
                if resolved_ca is not None:
                    user_input = {**user_input, CONF_CA_BUNDLE: resolved_ca}
                api = RaritanAPI(
                    host=user_input[CONF_HOST],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    verify_tls=user_input[CONF_VERIFY_TLS],
                    ca_bundle=user_input.get(CONF_CA_BUNDLE),
                )
                try:
                    cap = await self.hass.async_add_executor_job(api.probe)
                except RaritanAuthError:
                    errors["base"] = "invalid_auth"
                except RaritanTLSError:
                    errors["base"] = "tls_failed"
                except RaritanConnectionError:
                    errors["base"] = "cannot_connect"
                except RaritanAPIError:
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(cap.serial)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Raritan {cap.model} ({cap.serial})",
                        data=user_input,
                    )
        if self._discovered_host is not None:
            data_schema = vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._discovered_host): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_VERIFY_TLS, default=DEFAULT_VERIFY_TLS): bool,
                    vol.Optional(CONF_CA_BUNDLE): str,
                }
            )
        else:
            data_schema = USER_SCHEMA
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Initiated when ConfigEntryAuthFailed is raised at runtime."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-auth: same form fields as user step, updates the existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            # Merge with existing entry data so user can keep host but only re-enter creds
            merged = {**entry.data, **user_input}
            # Re-validate host/ca_bundle on the merged data: the host is
            # string-formatted into a URL and the CA bundle is loaded by the
            # TLS stack, so both must be checked before any probe.
            ca_ok, resolved_ca = await self._resolve_ca_bundle(merged.get(CONF_CA_BUNDLE))
            if not _is_valid_host(merged[CONF_HOST]):
                errors["base"] = "invalid_host"
            elif not ca_ok:
                errors["base"] = "ca_bundle_not_found"
            elif resolved_ca is not None:
                merged = {**merged, CONF_CA_BUNDLE: resolved_ca}
        if user_input is not None and not errors:
            api = RaritanAPI(
                host=merged[CONF_HOST],
                username=merged[CONF_USERNAME],
                password=merged[CONF_PASSWORD],
                verify_tls=merged[CONF_VERIFY_TLS],
                ca_bundle=merged.get(CONF_CA_BUNDLE),
            )
            try:
                # Lightweight probe: validates credentials + returns serial in
                # a single roundtrip. The full capability discovery happens on
                # entry reload immediately after.
                serial, _model = await self.hass.async_add_executor_job(api.probe_identity)
            except RaritanAuthError:
                errors["base"] = "invalid_auth"
            except RaritanTLSError:
                errors["base"] = "tls_failed"
            except RaritanConnectionError:
                errors["base"] = "cannot_connect"
            except RaritanAPIError:
                errors["base"] = "unknown"
            else:
                # Verify the same PDU (same serial)
                if serial != entry.unique_id:
                    return self.async_abort(reason="reauth_serial_mismatch")
                return self.async_update_reload_and_abort(entry, data=merged)

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change host, credentials, or TLS settings of an entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            # Trim the host before validating/storing (see async_step_user): the
            # value is string-formatted into a URL, so surrounding whitespace
            # would produce a malformed one.
            user_input = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            ca_ok, resolved_ca = await self._resolve_ca_bundle(user_input.get(CONF_CA_BUNDLE))
            if not _is_valid_host(user_input[CONF_HOST]):
                errors["base"] = "invalid_host"
            elif not ca_ok:
                errors["base"] = "ca_bundle_not_found"
            else:
                if resolved_ca is not None:
                    user_input = {**user_input, CONF_CA_BUNDLE: resolved_ca}
                api = RaritanAPI(
                    host=user_input[CONF_HOST],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    verify_tls=user_input[CONF_VERIFY_TLS],
                    ca_bundle=user_input.get(CONF_CA_BUNDLE),
                )
                try:
                    serial, _model = await self.hass.async_add_executor_job(api.probe_identity)
                except RaritanAuthError:
                    errors["base"] = "invalid_auth"
                except RaritanTLSError:
                    errors["base"] = "tls_failed"
                except RaritanConnectionError:
                    errors["base"] = "cannot_connect"
                except RaritanAPIError:
                    errors["base"] = "unknown"
                else:
                    # Refuse to repoint an entry at a different physical PDU; that
                    # would orphan all the entities keyed on the original serial.
                    if serial != entry.unique_id:
                        return self.async_abort(reason="reconfigure_serial_mismatch")
                    return self.async_update_reload_and_abort(entry, data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_VERIFY_TLS, default=entry.data.get(CONF_VERIFY_TLS, DEFAULT_VERIFY_TLS)
                ): bool,
                vol.Optional(
                    CONF_CA_BUNDLE,
                    description={"suggested_value": entry.data.get(CONF_CA_BUNDLE)},
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RaritanPduOptionsFlow()


class RaritanPduOptionsFlow(OptionsFlow):
    """Options flow for the Raritan PDU integration (polling interval)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show/handle the options form for the polling interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
