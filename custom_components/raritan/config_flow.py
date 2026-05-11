"""Config flow for the Raritan PDU integration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
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
    CONF_CA_BUNDLE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFY_TLS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_TLS,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

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

    async def _ca_bundle_missing(self, ca_bundle: str | None) -> bool:
        """Return True when a CA bundle path is given but is not a readable file."""
        if not ca_bundle:
            return False
        return not await self.hass.async_add_executor_job(os.path.isfile, ca_bundle)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle a PDU discovered (or IP-changed) via DHCP."""
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
            if await self._ca_bundle_missing(user_input.get(CONF_CA_BUNDLE)):
                errors["base"] = "ca_bundle_not_found"
            else:
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

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
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
            if await self._ca_bundle_missing(user_input.get(CONF_CA_BUNDLE)):
                errors["base"] = "ca_bundle_not_found"
            else:
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
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
