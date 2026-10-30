"""Read-only setup, API credentials, and identity-preserving reconfiguration."""

import ssl
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from . import async_create_client
from .api import ApiError, normalize_origin, normalize_token
from .const import CONF_CA_CERTIFICATE, CONF_ORIGIN, DOMAIN


class ConnexConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_form("user", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_form("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_form("reconfigure", user_input)

    async def _async_form(self, step: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = (
            self._get_reauth_entry()
            if step == "reauth_confirm"
            else self._get_reconfigure_entry()
            if step == "reconfigure"
            else None
        )
        defaults = dict(entry.data) if entry else {}
        if user_input is not None:
            data = {**defaults, **user_input}
            try:
                data[CONF_ORIGIN] = normalize_origin(data[CONF_ORIGIN])
                # Require explicit credentials without prefilling the password field.
                data[CONF_TOKEN] = normalize_token(user_input.get(CONF_TOKEN, ""))
                client = await async_create_client(self.hass, data)
                device = await client.get_device()
                await client.get_zones()
            except ssl.SSLError:
                errors["base"] = "invalid_certificate"
            except ValueError:
                errors["base"] = "invalid_origin"
            except ApiError as err:
                errors["base"] = (
                    err.code
                    if err.code in ("invalid_auth", "forbidden", "incompatible", "cannot_connect")
                    else "cannot_connect"
                )
            else:
                await self.async_set_unique_id(device.id)
                if entry:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(entry, data_updates=data)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Connex controller", data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_ORIGIN, default=defaults.get(CONF_ORIGIN, "")): TextSelector(),
                vol.Required(CONF_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(
                    CONF_CA_CERTIFICATE, default=defaults.get(CONF_CA_CERTIFICATE, "")
                ): TextSelector(TextSelectorConfig(multiline=True)),
            }
        )
        return self.async_show_form(
            step_id=step,
            data_schema=schema,
            errors=errors,
            description_placeholders={"example_address": "http://open-connex.local"}
            if step == "user"
            else None,
        )
