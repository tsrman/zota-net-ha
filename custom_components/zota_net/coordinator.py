"""One coordinator per account, sharing its authentication token."""
from datetime import timedelta
import logging
import time

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, AuthError
from .const import (DOMAIN, CONF_BOILERS, CONF_POLL_INTERVAL, CONF_HISTORY_INTERVAL,
                    CONF_STALE_MINUTES, DEFAULT_POLL_INTERVAL, DEFAULT_HISTORY_INTERVAL,
                    DEFAULT_STALE_MINUTES, boiler_key)

_LOGGER = logging.getLogger(__name__)


class ZotaCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, api):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(
            seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)))
        self.api = api
        self.boilers = entry.data[CONF_BOILERS]
        self.history_interval = entry.options.get(CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL)
        self.stale_minutes = entry.options.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES)
        self._history_cache = {}
        self._history_next = {}

    async def _async_update_data(self):
        result = {}
        successes = 0
        auth_error = False
        failures = []
        for index, boiler in enumerate(self.boilers, start=1):
            key = boiler_key(boiler)
            current = None
            try:
                current = await self.api.current(boiler['type'], boiler['serial'])
                successes += 1
            except AuthError:
                auth_error = True
                break
            except ApiError as error:
                failures.append(f'boiler {index}: {error}')
                _LOGGER.debug('Current readings unavailable for configured boiler %s: %s', index, error)
            if current is not None and time.monotonic() >= self._history_next.get(key, 0):
                self._history_next[key] = time.monotonic() + self.history_interval
                try:
                    self._history_cache[key] = await self.api.history(
                        boiler['type'], boiler['serial'], current['stages'])
                except AuthError:
                    auth_error = True
                    break
                except ApiError as error:
                    _LOGGER.debug('History request failed for configured boiler %s; previous point retained until stale: %s', index, error)
            result[key] = {'current': current, 'history': self._history_cache.get(key)}
        if auth_error:
            raise ConfigEntryAuthFailed('ZOTA authentication rejected')
        if not successes:
            raise UpdateFailed('No current readings available from ZOTA; ' + '; '.join(failures))
        return result
