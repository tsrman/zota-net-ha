import importlib.util
import json
from pathlib import Path
import sys
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / 'custom_components/zota_net/api.py'
SAMPLE = json.loads((Path(__file__).parent / 'solid_x_sample.json').read_text())
if API.exists():
    spec = importlib.util.spec_from_file_location('zota_api_test', API)
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)


class ValuesTests(unittest.TestCase):
    def test_implementation_exists(self):
        self.assertTrue(API.exists())

    @unittest.skipUnless(API.exists(), 'not implemented yet')
    def test_real_current_and_preformatted_air(self):
        data = api.parse_current(SAMPLE['current'])
        self.assertEqual(data['power'], 1.5)
        self.assertEqual(data['water'], 26)
        self.assertEqual(data['air'], 21.7)
        self.assertIsNone(data['dhw'])
        self.assertEqual(data['stage'], 1)
        self.assertEqual(data['status'], 'Работа')
        self.assertEqual(data['error_count'], 0)
        changed = json.loads(json.dumps(SAMPLE['current']))
        changed['httpResponseData']['Data']['Air'] = '21.7°C'
        self.assertEqual(api.parse_current(changed)['air'], 21.7)
        changed['httpResponseData']['Data']['Air'] = '217'
        self.assertEqual(api.parse_current(changed)['air'], 21.7)

    @unittest.skipUnless(API.exists(), 'not implemented yet')
    def test_history_sorting_and_error_sentinels(self):
        current = api.parse_current(SAMPLE['current'])
        history = json.loads(json.dumps(SAMPLE['history']))
        history['boilersData'].reverse()
        point = api.parse_history(history, current['stages'])
        self.assertEqual(point['timestamp'], datetime(2026, 10, 6, 10, 25, tzinfo=timezone.utc))
        self.assertEqual(point['pressure'], 1.101)
        self.assertEqual(point['room'], 21.5)
        self.assertEqual(point['radiator'], 27)
        self.assertEqual(point['power'], 1.5)
        self.assertIsNone(point['outside'])
        self.assertIsNone(point['dhw'])
        self.assertIsNone(api.parse_history({'boilersData': []}, current['stages']))

    @unittest.skipUnless(API.exists(), 'not implemented yet')
    def test_stale_and_future_data(self):
        now = datetime(2026, 10, 6, 10, 30, tzinfo=timezone.utc)
        point = api.parse_history(SAMPLE['history'], [0, 1.5])
        self.assertTrue(api.history_fresh(point, now, 15))
        self.assertFalse(api.history_fresh(point, datetime(2026, 10, 6, 10, 41, tzinfo=timezone.utc), 15))
        point['timestamp'] = datetime(2026, 10, 6, 11, 30, tzinfo=timezone.utc)
        self.assertFalse(api.history_fresh(point, now, 15))

    @unittest.skipUnless(API.exists(), 'not implemented yet')
    def test_unknown_and_locale_values_are_not_zero(self):
        for value in (None, 'Ошибка датчика', '16385°C', float('nan'), True):
            self.assertIsNone(api.temperature(value))
        self.assertEqual(api.number('1,5 кВт'), 1.5)
        self.assertEqual(api.temperature('-5°C'), -5)


@unittest.skipUnless(API.exists(), 'not implemented yet')
class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import ClientSession, web
        self.web = web
        self.seen = []
        self.login_count = 0
        self.get_count = 0
        self.current_count = 0
        self.mode = 'normal'
        app = web.Application()
        app.router.add_post('/token', self.token)
        app.router.add_get('/api/Boilers/GetBoilers', self.boilers)
        app.router.add_get('/api/Boilers/GetBoilerData', self.current)
        app.router.add_get('/redirect', self.redirect)
        app.router.add_get('/leak', self.leak)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        self.port = self.site._server.sockets[0].getsockname()[1]
        self.session = ClientSession()
        self.client = api.ZotaApi(self.session, f'http://127.0.0.1:{self.port}', 'alice', 'a+b&c', True)

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def token(self, req):
        self.login_count += 1
        self.seen.append(dict(await req.post()))
        return self.web.json_response({'access_token': 'token' + str(self.login_count), 'token_type': 'bearer', 'expires_in': 2592000})

    async def boilers(self, req):
        self.seen.append(req.headers.get('Authorization'))
        self.get_count += 1
        if self.mode == 'reauth' and self.get_count == 1:
            return self.web.json_response({'Result': 1}, status=401)
        if self.mode == 'always401':
            return self.web.json_response({'Result': 1}, status=401)
        if self.mode == 'app_error':
            return self.web.json_response({'response': {'Result': 2, 'Messages': ['private-credential']}})
        if self.mode == 'html':
            return self.web.Response(text='<html>login</html>', content_type='text/html')
        return self.web.json_response({'response': {'Result': 0}, 'boilers': [{'Type': 'SOLID_X', 'Serial': 123}]})

    async def current(self, req):
        self.current_count += 1
        if self.mode == 'slow_current':
            import asyncio
            await asyncio.sleep(0.05)
        if self.mode == 'recover504' and self.current_count <= 2:
            return self.web.Response(status=504)
        if self.mode == 'recover503' and self.current_count == 1 or self.mode == 'always503':
            return self.web.Response(status=503, text='private-server-body')
        if self.mode == 'recover_disconnect' and self.current_count <= 2:
            req.transport.close()
            return self.web.Response()
        if self.mode == 'current_bad_json':
            return self.web.Response(text='private-invalid-json')
        if self.mode == 'current403':
            return self.web.Response(status=403)
        self.seen.append(dict(req.query))
        return self.web.json_response(SAMPLE['current'])

    async def redirect(self, req):
        raise self.web.HTTPFound('/leak')

    async def leak(self, req):
        self.seen.append('leaked')
        return self.web.json_response({})

    async def test_read_contract_and_refresh_once(self):
        self.mode = 'reauth'
        self.assertEqual(len(await self.client.boilers()), 1)
        self.assertEqual(self.login_count, 2)
        self.assertEqual(self.seen[0], {'username': 'alice', 'password': 'a+b&c', 'grant_type': 'password'})
        self.assertIn('bearer token2', self.seen)
        current = await self.client.current('SOLID_X', 123)
        self.assertEqual(current['air'], 21.7)
        self.assertEqual(self.seen[-1], {'type': 'SOLID_X', 'serial': '123'})

    async def test_http_200_app_error_and_html_are_safe(self):
        for mode in ('app_error', 'html'):
            self.mode = mode
            with self.assertRaises(api.ApiError) as ctx:
                await self.client.boilers()
            self.assertNotIn('private-credential', str(ctx.exception))

    async def test_repeated_401_and_redirect(self):
        self.mode = 'always401'
        with self.assertRaises(api.AuthError):
            await self.client.boilers()
        self.assertEqual(self.login_count, 2)
        with self.assertRaises(api.ApiError):
            await self.client.request('/redirect')
        self.assertNotIn('leaked', self.seen)

    async def test_current_recovers_after_one_503(self):
        self.mode = 'recover503'
        result = await self.client.current('SOLID_X', 123)
        self.assertEqual(result['water'], 26)
        self.assertEqual(self.current_count, 2)

    async def test_current_recovers_after_connection_drop(self):
        self.mode = 'recover_disconnect'
        result = await self.client.current('SOLID_X', 123)
        self.assertEqual(result['water'], 26)
        self.assertGreaterEqual(self.current_count, 3)
        self.assertLessEqual(self.current_count, 4)  # aiohttp may retry an idempotent GET internally.

    async def test_persistent_503_is_bounded_and_safe(self):
        self.mode = 'always503'
        with self.assertRaises(api.ApiError) as ctx:
            await self.client.current('SOLID_X', 123)
        self.assertEqual(self.current_count, 2)
        self.assertIn('503', str(ctx.exception))
        self.assertNotIn('private-server-body', str(ctx.exception))

    async def test_invalid_json_is_not_retried(self):
        self.mode = 'current_bad_json'
        with self.assertRaises(api.ApiError):
            await self.client.current('SOLID_X', 123)
        self.assertEqual(self.current_count, 1)

    async def test_auth_rejection_is_not_treated_as_transient(self):
        self.mode = 'current403'
        with self.assertRaises(api.AuthError):
            await self.client.current('SOLID_X', 123)
        self.assertEqual(self.current_count, 2)
        self.assertEqual(self.login_count, 2)

    async def test_retries_can_be_disabled(self):
        self.mode = 'always503'
        client = api.ZotaApi(self.session, f'http://127.0.0.1:{self.port}', 'alice', 'p', True,
                             retry_count=0, retry_delay=1)
        with self.assertRaises(api.ApiError):
            await client.current('SOLID_X', 123)
        self.assertEqual(self.current_count, 1)

    async def test_configured_retry_count_and_delay(self):
        import time
        self.mode = 'always503'
        client = api.ZotaApi(self.session, f'http://127.0.0.1:{self.port}', 'alice', 'p', True,
                             retry_count=2, retry_delay=1)
        start = time.monotonic()
        with self.assertRaises(api.ApiError):
            await client.current('SOLID_X', 123)
        self.assertEqual(self.current_count, 3)
        self.assertGreaterEqual(time.monotonic() - start, 2)

    async def test_retry_timeout_increases_and_resets_after_success(self):
        observed = []
        session = self.session
        class RecordedSession:
            def request(self, method, url, **kwargs):
                if url.endswith('/GetBoilerData'):
                    observed.append(kwargs['timeout'].total)
                return session.request(method, url, **kwargs)
        self.client.session = RecordedSession()
        self.client.retry_count = 2
        self.client.retry_delay = 1
        self.mode = 'recover504'
        self.assertEqual((await self.client.current('SOLID_X', 123))['water'], 26)
        self.assertEqual(observed, [20, 40, 60])
        self.mode = 'normal'
        await self.client.current('SOLID_X', 123)
        self.assertEqual(observed, [20, 40, 60, 20])

    async def test_timeout_error_reports_budget_of_last_attempt(self):
        from aiohttp import ClientTimeout
        observed = []
        session = self.session
        class ShortTimeoutSession:
            def request(self, method, url, **kwargs):
                if url.endswith('/GetBoilerData'):
                    observed.append(kwargs['timeout'].total)
                    kwargs['timeout'] = ClientTimeout(total=0.01)
                return session.request(method, url, **kwargs)
        self.client.session = ShortTimeoutSession()
        self.client.retry_delay = 1
        self.mode = 'slow_current'
        with self.assertRaises(api.ApiError) as ctx:
            await self.client.current('SOLID_X', 123)
        self.assertEqual(observed, [20, 40])
        self.assertIn('40 seconds', str(ctx.exception))

    async def test_insecure_http_needs_consent(self):
        with self.assertRaises(api.ApiError):
            api.ZotaApi(self.session, 'http://control.zota.ru:81', 'alice', 'p', False)


if __name__ == '__main__':
    unittest.main()
