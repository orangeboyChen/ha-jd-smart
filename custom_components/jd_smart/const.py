"""Constants for the JD Smart integration."""

import logging
from datetime import timedelta

DOMAIN = "jd_smart"
LOGGER = logging.getLogger(__package__)

DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
FAST_POLL_INTERVAL = timedelta(seconds=2)
FAST_POLL_DURATION = timedelta(seconds=10)
AUTH_REFRESH_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(minutes=20),
    timedelta(minutes=40),
    timedelta(minutes=60),
)

CONF_APP_VERSION = "app_version"
CONF_CHANNEL = "channel"
CONF_COOKIE = "cookie"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_MODEL = "device_model"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICES = "devices"
CONF_FEED_ID = "feed_id"
CONF_PLATFORM = "platform"
CONF_PLATFORM_VERSION = "platform_version"
CONF_PIN = "pin"
CONF_SGM_CONTEXT = "sgm_context"
CONF_TGT = "tgt"
CONF_USER_AGENT = "user_agent"

DEFAULT_APP_VERSION = "2.2.0"
DEFAULT_CHANNEL = "76161171"
DEFAULT_DEVICE_ID = "1780721316039153856527"
DEFAULT_DEVICE_MODEL = "Pixel 8"
DEFAULT_PLATFORM = "Android"
DEFAULT_PLATFORM_VERSION = "14"
DEFAULT_USER_AGENT = "android"

JD_SMART_BASE_URL = "https://api.smart.jd.com"
SNAPSHOT_PATH = "/c/service/integration/v1/getDeviceSnapshot_v1"
CONTROL_PATH = "/c/service/integration/v1/controlDevice_v1"
DEVICE_LIST_PATH = "/c/service/devmanager/v2/getDevicesAndCategory"

APP_KEY = "a188caaf009839ba200bb55bb8fa38407a595c2a"
HMAC_KEY = "e685c8d1daa7e4dec8821a3df41c0b34a56db779"

ATTR_MANUFACTURER = "JD Smart"
UPDATE_AUTH_FAILURE_THRESHOLD = 3


def auth_refresh_notification_ids(
    entry_id: str, feed_ids: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Return current and legacy notification IDs for an entry."""
    return (
        f"{DOMAIN}_{entry_id}_auth_refresh_failed",
        f"{DOMAIN}_token_refresh_failed",
        *(f"{DOMAIN}_{feed_id}_token_refresh_failed" for feed_id in feed_ids),
    )
