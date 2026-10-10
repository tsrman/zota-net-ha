from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest

HAS_HA = importlib.util.find_spec('homeassistant') is not None


@unittest.skipUnless(HAS_HA, 'Home Assistant package not installed')
class HomeAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from homeassistant.core import HomeAssistant
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.directory.cleanup()

    async def test_real_entity_classes_and_stale_history(self):
        from custom_components.zota_net.coordinator import ZotaCoordinator
        from custom_components.zota_net.sensor import ZotaSensor, SENSORS
        from custom_components.zota_net.binary_sensor import ZotaBinarySensor
        boiler = {'type': 'SOLID_X', 'serial': 'test-serial', 'name': 'Test boiler'}
        entry = types.SimpleNamespace(data={'boilers': [boiler]}, options={})
        coord = ZotaCoordinator(self.hass, entry, None)
        coord.async_set_updated_data({'SOLID_X:test-serial': {
            'current': {'air': 21.7, 'error_count': 0},
            'history': {'timestamp': datetime.now(timezone.utc) - timedelta(minutes=2), 'pressure': 1.101}}})
        sensor = ZotaSensor(coord, boiler, next(d for d in SENSORS if d.key == 'pressure'))
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 1.101)
        self.assertEqual(sensor.native_unit_of_measurement, 'bar')
        self.assertEqual(sensor.unique_id, 'SOLID_X:test-serial:pressure')
        fresh = ZotaBinarySensor(coord, boiler, 'history_fresh')
        self.assertTrue(fresh.is_on)
        coord.data['SOLID_X:test-serial']['history']['timestamp'] -= timedelta(hours=1)
        self.assertFalse(sensor.available)
        self.assertFalse(fresh.is_on)

    async def test_history_failure_does_not_break_current(self):
        from custom_components.zota_net.api import ApiError
        from custom_components.zota_net.coordinator import ZotaCoordinator
        class FakeApi:
            async def current(self, kind, serial):
                if serial == 'bad':
                    raise ApiError('unavailable')
                return {'water': 26, 'stages': [0, 1.5]}
            async def history(self, *args):
                raise ApiError('history unavailable')
        boilers = [{'type': 'SOLID_X', 'serial': x, 'name': x} for x in ('good', 'bad')]
        coord = ZotaCoordinator(self.hass, types.SimpleNamespace(data={'boilers': boilers}, options={}), FakeApi())
        data = await coord._async_update_data()
        self.assertEqual(data['SOLID_X:good']['current']['water'], 26)
        self.assertIsNone(data['SOLID_X:good']['history'])
        self.assertIsNone(data['SOLID_X:bad']['current'])

    async def test_failed_current_exposes_safe_reason_and_remains_unavailable(self):
        from custom_components.zota_net.api import ApiError
        from custom_components.zota_net.coordinator import ZotaCoordinator
        from custom_components.zota_net.sensor import ZotaSensor, SENSORS
        from homeassistant.helpers.update_coordinator import UpdateFailed
        class FailedApi:
            async def current(self, *args):
                raise ApiError('ZOTA returned HTTP 503')
        boiler = {'type': 'SOLID_X', 'serial': 'private-serial', 'name': 'Test'}
        coord = ZotaCoordinator(self.hass, types.SimpleNamespace(data={'boilers': [boiler]}, options={}), FailedApi())
        coord.async_set_updated_data({'SOLID_X:private-serial': {'current': {'water': 26}, 'history': None}})
        sensor = ZotaSensor(coord, boiler, next(d for d in SENSORS if d.key == 'water'))
        with self.assertRaises(UpdateFailed) as ctx:
            await coord._async_update_data()
        self.assertIn('503', str(ctx.exception))
        self.assertNotIn('private-serial', str(ctx.exception))
        coord.async_set_update_error(ctx.exception)
        self.assertFalse(sensor.available)

    async def test_options_retry_defaults_limits_and_storage(self):
        import voluptuous as vol
        from custom_components.zota_net.config_flow import ZotaOptionsFlow
        class Flow(ZotaOptionsFlow):
            @property
            def config_entry(self):
                return types.SimpleNamespace(options={})
        flow = Flow()
        flow.hass = self.hass
        result = await flow.async_step_init()
        schema = result['data_schema']
        defaults = schema({})
        self.assertEqual(defaults['retry_count'], 1)
        self.assertEqual(defaults['retry_delay'], 2)
        values = schema({'retry_count': 0, 'retry_delay': 60})
        stored = await flow.async_step_init(values)
        self.assertEqual(stored['data']['retry_count'], 0)
        self.assertEqual(stored['data']['retry_delay'], 60)
        for changed in ({'retry_count': -1}, {'retry_count': 6},
                        {'retry_delay': 0}, {'retry_delay': 61}):
            with self.assertRaises(vol.Invalid):
                schema(changed)

    async def test_schema_loads_and_requires_device_selection(self):
        from custom_components.zota_net.config_flow import ZotaConfigFlow, user_schema
        flow = ZotaConfigFlow()
        flow.hass = self.hass
        flow._boilers = {'SOLID_X:test': {'type': 'SOLID_X', 'serial': 'test', 'name': 'Test'}}
        flow._pending = {'username': 'test', 'password': 'test'}
        result = await flow.async_step_boilers({'boilers': []})
        self.assertEqual(result['errors']['base'], 'select_boiler')
        valid = result['data_schema']({'boilers': ['SOLID_X:test']})
        self.assertEqual(valid['boilers'], ['SOLID_X:test'])
        result = await flow.async_step_boilers(valid)
        self.assertEqual(result['data']['boilers'][0]['serial'], 'test')
        self.assertEqual(user_schema()({'base_url': 'http://control.zota.ru:81', 'username': 'u',
                                       'password': 'p', 'allow_http': True})['password'], 'p')


if __name__ == '__main__':
    unittest.main()
