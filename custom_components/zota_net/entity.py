from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, boiler_key


class ZotaEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, boiler, key):
        super().__init__(coordinator)
        self.boiler_id = boiler_key(boiler)
        self._attr_unique_id = f'{self.boiler_id}:{key}'
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.boiler_id)}, manufacturer='ZOTA',
            model='Solid X', name=boiler['name'])

    @property
    def snapshot(self):
        return (self.coordinator.data or {}).get(self.boiler_id, {})

    @property
    def available(self):
        return super().available and self.snapshot.get('current') is not None
