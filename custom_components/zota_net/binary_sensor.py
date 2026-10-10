from datetime import datetime, timezone

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import EntityCategory

from .api import history_fresh
from .entity import ZotaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(ZotaBinarySensor(coordinator, boiler, key)
        for boiler in coordinator.boilers for key in ('errors', 'warnings', 'history_fresh'))


class ZotaBinarySensor(ZotaEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, boiler, key):
        super().__init__(coordinator, boiler, key)
        self.key = key
        self._attr_translation_key = key
        self._attr_device_class = (BinarySensorDeviceClass.CONNECTIVITY if key == 'history_fresh'
                                   else BinarySensorDeviceClass.PROBLEM)

    @property
    def is_on(self):
        if self.key == 'history_fresh':
            return history_fresh(self.snapshot.get('history'), datetime.now(timezone.utc), self.coordinator.stale_minutes)
        current = self.snapshot.get('current') or {}
        value = current.get('error_count' if self.key == 'errors' else 'warning_count')
        return value > 0 if value is not None else None
