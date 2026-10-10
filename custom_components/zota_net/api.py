"""Read-only ZOTA Net API and Solid X decoding, independent of Home Assistant."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
import logging
import re
import random
import time
from urllib.parse import urlsplit

import aiohttp


class ApiError(Exception):
    """Local error messages only: never include an API body or credentials."""


class TransientApiError(ApiError):
    """Retryable transport failure or server HTTP 5xx."""


_LOGGER = logging.getLogger(__name__)


class AuthError(ApiError):
    """Credentials rejected or token rejected after one renewal."""


def validate_url(url, allow_http):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ApiError('Invalid server URL') from None
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('', '/')):
        raise ApiError('Invalid server URL')
    if parsed.scheme == 'http' and not allow_http:
        raise ApiError('HTTP requires explicit consent')
    return url.rstrip('/')


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = re.fullmatch(r'\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:°?\s*[CFС]|кВт|kW|Вт|W)?\s*', value, re.IGNORECASE)
        if not match:
            return None
        value = match[1].replace(',', '.')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def temperature(value, divisor=1):
    raw = number(value)
    if raw is None or raw >= 16384:
        return None
    value = raw / divisor
    return value if -100 <= value <= 150 else None


def room_temperature(value):
    # Solid X API observed returning 217°C for raw deci-degrees 217.
    raw = number(value)
    if raw is None or raw >= 16384:
        return None
    return temperature(raw, 10 if abs(raw) > 100 else 1)


def check_result(body):
    if not isinstance(body, dict):
        raise ApiError('Unexpected JSON structure')
    for envelope in (body, body.get('response'), body.get('httpResponseData')):
        if isinstance(envelope, dict) and envelope.get('Result', 0) != 0:
            raise ApiError('ZOTA reported a nonzero Result')


def parse_current(body):
    check_result(body)
    envelope = body.get('httpResponseData')
    data = envelope.get('Data') if isinstance(envelope, dict) else None
    if not isinstance(data, dict):
        raise ApiError('Missing current readings')
    power = number(data.get('Power'))
    raw_power = data.get('Power')
    if power is not None and isinstance(raw_power, str) and re.search(r'(?:\bW|(?<!к)Вт)\s*$', raw_power, re.IGNORECASE):
        power /= 1000
    if power is not None and power < 0:
        power = None
    errors, warnings = data.get('Errors'), data.get('Warnings')
    stages = data.get('Stages')
    return {'power': power, 'water': temperature(data.get('Water')),
            'air': room_temperature(data.get('Air')), 'dhw': temperature(data.get('DHW')),
            'stage': number(data.get('CurrentStage')),
            'stages': [number(x) for x in stages] if isinstance(stages, list) else [],
            'status': data.get('Status') if isinstance(data.get('Status'), str) else None,
            'status_code': number(data.get('StatusCode')),
            'error_count': len(errors) if isinstance(errors, list) else None,
            'warning_count': len(warnings) if isinstance(warnings, list) else None}


def parse_history(body, stages):
    check_result(body)
    points = body.get('boilersData')
    if not isinstance(points, list):
        raise ApiError('Missing history list')
    valid = []
    for point in points:
        if not isinstance(point, dict) or not isinstance(point.get('Info'), dict):
            continue
        try:
            stamp = datetime.fromisoformat(point['Time'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            valid.append((stamp.astimezone(timezone.utc), point['Info']))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if not valid:
        return None
    stamp, data = max(valid, key=lambda p: p[0])
    stage = number(data.get('CurPower'))
    power = None
    if stage is not None and stage.is_integer() and 0 <= stage < len(stages):
        power = number(stages[int(stage)])
    pressure = number(data.get('Pressure'))
    if pressure is not None:
        pressure = pressure / 1000 if 0 <= pressure < 16384 else None
    return {'timestamp': stamp, 'pressure': pressure,
            'room': temperature(data.get('TempRoom'), 10),
            'radiator': temperature(data.get('TempSSR')),
            'outside': temperature(data.get('TempOAir')),
            'water': temperature(data.get('TempWater')),
            'dhw': temperature(data.get('TempDHW')),
            'power': power}


def history_fresh(point, now, minutes):
    if not point:
        return False
    age = (now - point['timestamp']).total_seconds()
    return -300 <= age <= minutes * 60


class ZotaApi:
    def __init__(self, session, base_url, username, password, allow_http=False,
                 *, retry_count=1, retry_delay=2):
        if type(retry_count) is not int or not 0 <= retry_count <= 5:
            raise ApiError('Retry count must be an integer between 0 and 5')
        if type(retry_delay) is not int or not 1 <= retry_delay <= 60:
            raise ApiError('Retry delay must be an integer between 1 and 60 seconds')
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.session = session
        self.base_url = validate_url(base_url, allow_http)
        self.username = username
        self.password = password
        self._authorization = None
        self._expires = 0
        self._login_lock = asyncio.Lock()

    async def _fetch(self, path, query=None, form=None, authenticated=True, *, timeout=20):
        headers = {'Accept': 'application/json'}
        if authenticated and self._authorization:
            headers['Authorization'] = self._authorization
        try:
            async with self.session.request('POST' if form is not None else 'GET',
                    self.base_url + path, params=query, data=form, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=False) as response:
                if response.status in (401, 403) or path == '/token' and response.status == 400:
                    raise AuthError('Authentication rejected')
                if 500 <= response.status <= 599:
                    raise TransientApiError(f'ZOTA returned HTTP {response.status}')
                if response.status != 200:
                    raise ApiError(f'ZOTA returned HTTP {response.status}')
                chunks, total = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > 8 * 1024 * 1024:
                        raise ApiError('Response exceeded 8 MiB')
                    chunks.append(chunk)
                body = json.loads(b''.join(chunks).decode('utf-8-sig'))
        except (aiohttp.ClientSSLError, aiohttp.ClientConnectorCertificateError):
            raise ApiError('TLS certificate or handshake error') from None
        except asyncio.TimeoutError:
            raise TransientApiError(f'Request timed out ({timeout} seconds)') from None
        except aiohttp.ClientConnectionError:
            raise TransientApiError('Connection or DNS error') from None
        except aiohttp.ClientPayloadError:
            raise TransientApiError('Incomplete response from ZOTA') from None
        except aiohttp.ClientError:
            raise ApiError('HTTP client error') from None
        except (UnicodeError, ValueError):
            raise ApiError('Response is not valid JSON') from None
        check_result(body)
        return body

    async def login(self, force=False):
        async with self._login_lock:
            if not force and self._authorization and time.monotonic() < self._expires:
                return
            body = await self._fetch('/token', form={'username': self.username,
                         'password': self.password, 'grant_type': 'password'}, authenticated=False)
            token, kind = body.get('access_token'), body.get('token_type')
            if (not isinstance(token, str) or not token or '\n' in token or '\r' in token or
                    not isinstance(kind, str) or not re.fullmatch(r'[A-Za-z][\w-]*', kind)):
                raise AuthError('Missing access token')
            self._authorization = kind + ' ' + token
            ttl = number(body.get('expires_in'))
            self._expires = time.monotonic() + max(1, (ttl if ttl is not None and ttl > 0 else 3600) - 60)

    async def request(self, path, query=None, *, timeout=20):
        await self.login()
        try:
            return await self._fetch(path, query=query, timeout=timeout)
        except AuthError:
            await self.login(force=True)
            return await self._fetch(path, query=query, timeout=timeout)

    async def boilers(self):
        body = await self.request('/api/Boilers/GetBoilers')
        result = body.get('boilers')
        if not isinstance(result, list):
            raise ApiError('Missing boiler list')
        # Keep only identity/name; never keep boiler passwords or account metadata.
        return [{'type': b['Type'], 'serial': str(b['Serial']),
                 'name': b.get('BoilerName') or 'ZOTA Solid X'} for b in result
                if isinstance(b, dict) and b.get('Type') == 'SOLID_X' and b.get('Serial') is not None]

    async def current(self, boiler_type, serial):
        for attempt in range(self.retry_count + 1):
            try:
                body = await self.request('/api/Boilers/GetBoilerData',
                                          {'type': boiler_type, 'serial': serial},
                                          timeout=20 * (attempt + 1))
                return parse_current(body)
            except TransientApiError as error:
                if attempt == self.retry_count:
                    raise
                delay = random.uniform(self.retry_delay * 0.8, self.retry_delay * 1.2)
                _LOGGER.debug('Current request failed; retry %s/%s in %.2f seconds with timeout %s seconds: %s',
                              attempt + 1, self.retry_count, delay, 20 * (attempt + 2), error)
                await asyncio.sleep(delay)

    async def history(self, boiler_type, serial, stages):
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        body = await self.request('/api/Boilers/GetBoilerHistory', {
            'type': boiler_type, 'serial': serial,
            'startDate': (end - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'endDate': end.strftime('%Y-%m-%dT%H:%M'), 'offset': 1})
        return parse_history(body, stages)
