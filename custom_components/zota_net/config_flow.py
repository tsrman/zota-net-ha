import hashlib

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, AuthError, ZotaApi, validate_url
from .const import (DOMAIN, DEFAULT_URL, CONF_BASE_URL, CONF_ALLOW_HTTP, CONF_BOILERS,
                    CONF_POLL_INTERVAL, CONF_HISTORY_INTERVAL, CONF_STALE_MINUTES, CONF_RETRY_COUNT, CONF_RETRY_DELAY,
                    DEFAULT_POLL_INTERVAL, DEFAULT_HISTORY_INTERVAL, DEFAULT_STALE_MINUTES, DEFAULT_RETRY_COUNT, DEFAULT_RETRY_DELAY, boiler_key)


def user_schema(defaults=None):
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, DEFAULT_URL)): str,
        vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, '')): str,
        vol.Required(CONF_PASSWORD): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required(CONF_ALLOW_HTTP, default=defaults.get(CONF_ALLOW_HTTP, False)): bool,
    })


class ZotaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                if not user_input[CONF_USERNAME].strip() or not user_input[CONF_PASSWORD]:
                    raise AuthError('Empty credentials')
                url = validate_url(user_input[CONF_BASE_URL].strip(), user_input[CONF_ALLOW_HTTP])
                identity = hashlib.sha256((url + '\0' + user_input[CONF_USERNAME].strip().casefold()).encode()).hexdigest()
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                api = ZotaApi(async_get_clientsession(self.hass), url,
                    user_input[CONF_USERNAME].strip(), user_input[CONF_PASSWORD], user_input[CONF_ALLOW_HTTP])
                boilers = await api.boilers()
                if not boilers:
                    return self.async_abort(reason='no_boilers')
                self._pending = {**user_input, CONF_BASE_URL: url,
                                 CONF_USERNAME: user_input[CONF_USERNAME].strip()}
                self._boilers = {boiler_key(b): b for b in boilers}
                return await self.async_step_boilers()
            except AuthError:
                errors['base'] = 'invalid_auth'
            except ApiError:
                errors['base'] = 'cannot_connect'
        return self.async_show_form(step_id='user', data_schema=user_schema(user_input), errors=errors)

    async def async_step_boilers(self, user_input=None):
        errors = {}
        if user_input is not None:
            selected = user_input.get(CONF_BOILERS, [])
            if selected and all(key in self._boilers for key in selected):
                return self.async_create_entry(title='ZOTA Net', data={**self._pending,
                    CONF_BOILERS: [self._boilers[key] for key in selected]})
            errors['base'] = 'select_boiler'
        labels = {key: f"{b['name']} (Solid X, {b['serial']})" for key, b in self._boilers.items()}
        return self.async_show_form(step_id='boilers', errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_BOILERS, default=list(labels)): cv.multi_select(labels)}))

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self.context['entry_id'])
        errors = {}
        if user_input is not None:
            try:
                api = ZotaApi(async_get_clientsession(self.hass), entry.data[CONF_BASE_URL],
                    entry.data[CONF_USERNAME], user_input[CONF_PASSWORD], entry.data[CONF_ALLOW_HTTP])
                await api.boilers()
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]})
            except AuthError:
                errors['base'] = 'invalid_auth'
            except ApiError:
                errors['base'] = 'cannot_connect'
        return self.async_show_form(step_id='reauth_confirm', errors=errors,
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ZotaOptionsFlow()


class ZotaOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)
        options = self.config_entry.options
        return self.async_show_form(step_id='init', data_schema=vol.Schema({
            vol.Required(CONF_POLL_INTERVAL, default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Required(CONF_HISTORY_INTERVAL, default=options.get(CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
            vol.Required(CONF_STALE_MINUTES, default=options.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES)):
                vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
            vol.Required(CONF_RETRY_COUNT, default=options.get(CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=5)),
            vol.Required(CONF_RETRY_DELAY, default=options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        }))
