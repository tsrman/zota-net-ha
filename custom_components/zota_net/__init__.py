"""ZOTA Net read-only integration."""
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZotaApi
from .const import (CONF_ALLOW_HTTP, CONF_BASE_URL, CONF_RETRY_COUNT, CONF_RETRY_DELAY,
                    DEFAULT_RETRY_COUNT, DEFAULT_RETRY_DELAY)
from .coordinator import ZotaCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass, entry):
    api = ZotaApi(async_get_clientsession(hass), entry.data[CONF_BASE_URL],
                  entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD],
                  entry.data.get(CONF_ALLOW_HTTP, False),
                  retry_count=entry.options.get(CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT),
                  retry_delay=entry.options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY))
    coordinator = ZotaCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
