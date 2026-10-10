from dataclasses import dataclass
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import UnitOfPower, UnitOfPressure, UnitOfTemperature
from homeassistant.helpers.entity import EntityCategory

from .api import history_fresh
from .entity import ZotaEntity


@dataclass(frozen=True, kw_only=True)
class ZotaSensorDescription(SensorEntityDescription):
    history: bool = False


def temp(key, history=False):
    return ZotaSensorDescription(key=key, translation_key=key, history=history,
        device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1)


SENSORS = (
    ZotaSensorDescription(key='power', translation_key='power', device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT, state_class=SensorStateClass.MEASUREMENT),
    temp('water'), temp('air'), temp('dhw'),
    ZotaSensorDescription(key='stage', translation_key='stage', icon='mdi:stairs'),
    ZotaSensorDescription(key='status', translation_key='status', icon='mdi:fire'),
    ZotaSensorDescription(key='error_count', translation_key='error_count', entity_category=EntityCategory.DIAGNOSTIC),
    ZotaSensorDescription(key='warning_count', translation_key='warning_count', entity_category=EntityCategory.DIAGNOSTIC),
    ZotaSensorDescription(key='pressure', translation_key='pressure', history=True,
        device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=3),
    temp('radiator', True), temp('outside', True),
    ZotaSensorDescription(key='timestamp', translation_key='timestamp', history=True,
        device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(ZotaSensor(coordinator, boiler, description)
        for boiler in coordinator.boilers for description in SENSORS)


class ZotaSensor(ZotaEntity, SensorEntity):
    def __init__(self, coordinator, boiler, description):
        super().__init__(coordinator, boiler, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        source = self.snapshot.get('history' if self.entity_description.history else 'current')
        return source.get(self.entity_description.key) if source else None

    @property
    def available(self):
        if not super().available:
            return False
        if self.entity_description.history and self.entity_description.key != 'timestamp':
            return history_fresh(self.snapshot.get('history'), datetime.now(timezone.utc), self.coordinator.stale_minutes)
        return True

    @property
    def extra_state_attributes(self):
        if not self.entity_description.history:
            return None
        point = self.snapshot.get('history')
        return {'source': 'history', 'source_timestamp': point['timestamp'].isoformat() if point else None}
