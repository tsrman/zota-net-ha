DOMAIN = 'zota_net'
DEFAULT_URL = 'http://control.zota.ru:81'
CONF_BASE_URL = 'base_url'
CONF_ALLOW_HTTP = 'allow_http'
CONF_BOILERS = 'boilers'
CONF_POLL_INTERVAL = 'poll_interval'
CONF_HISTORY_INTERVAL = 'history_interval'
CONF_STALE_MINUTES = 'stale_minutes'
CONF_RETRY_COUNT = 'retry_count'
CONF_RETRY_DELAY = 'retry_delay'
DEFAULT_POLL_INTERVAL = 60
DEFAULT_HISTORY_INTERVAL = 300
DEFAULT_STALE_MINUTES = 15
DEFAULT_RETRY_COUNT = 1
DEFAULT_RETRY_DELAY = 2


def boiler_key(boiler):
    return f"{boiler['type']}:{boiler['serial']}"
