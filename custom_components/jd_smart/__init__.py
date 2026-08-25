"""The JD Smart integration."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JdSmartClient, JdSmartCredentials, JdSmartDeviceProfile
from .const import (
    AIR_CONDITIONER_REQUIRED_STREAMS,
    CONF_APP_VERSION,
    CONF_CATEGORY_ID,
    CONF_CATEGORY_NAME,
    CONF_CHANNEL,
    CONF_COOKIE,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_CONFIG_TYPE,
    CONF_FEED_ID,
    CONF_PIN,
    CONF_PLATFORM,
    CONF_PLATFORM_VERSION,
    CONF_SGM_CONTEXT,
    CONF_TGT,
    CONF_USER_AGENT,
    DEFAULT_APP_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_DEVICE_ID,
    DEFAULT_DEVICE_MODEL,
    DEFAULT_PLATFORM,
    DEFAULT_PLATFORM_VERSION,
    DEFAULT_USER_AGENT,
    DEVICE_TYPE_AIR_CONDITIONER,
    DOMAIN,
    PULL_REQUEST_URL,
)
from .coordinator import (
    JdSmartAuthRetryManager,
    JdSmartConfigEntry,
    JdSmartCoordinator,
    JdSmartRuntimeData,
)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: JdSmartConfigEntry) -> bool:
    """Set up JD Smart from a config entry."""
    client = JdSmartClient(
        async_get_clientsession(hass),
        JdSmartCredentials(
            cookie=entry.data[CONF_COOKIE],
            tgt=entry.data[CONF_TGT],
            pin=entry.data.get(CONF_PIN),
            sgm_context=entry.data.get(CONF_SGM_CONTEXT),
        ),
        JdSmartDeviceProfile(
            device_id=entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            app_version=entry.data.get(CONF_APP_VERSION, DEFAULT_APP_VERSION),
            platform=entry.data.get(CONF_PLATFORM, DEFAULT_PLATFORM),
            device_model=entry.data.get(CONF_DEVICE_MODEL, DEFAULT_DEVICE_MODEL),
            platform_version=entry.data.get(
                CONF_PLATFORM_VERSION, DEFAULT_PLATFORM_VERSION
            ),
            channel=entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL),
            user_agent=entry.data.get(CONF_USER_AGENT, DEFAULT_USER_AGENT),
        ),
    )
    auth_retry_manager = JdSmartAuthRetryManager(hass, entry, client)
    coordinators: dict[str, JdSmartCoordinator] = {}
    try:
        for device in _entry_devices(entry.data):
            feed_id = device[CONF_FEED_ID]
            coordinator = JdSmartCoordinator(
                hass,
                entry,
                client,
                feed_id,
                device.get(CONF_DEVICE_NAME),
                device.get(CONF_DEVICE_TYPE, DEVICE_TYPE_AIR_CONDITIONER),
                auth_retry_manager,
            )
            coordinators[feed_id] = coordinator
            try:
                await coordinator.async_config_entry_first_refresh()
            except ConfigEntryNotReady:
                if not coordinator.auth_retry_pending:
                    raise
            else:
                if _is_unsupported_stream_layout(device, coordinator):
                    _notify_unsupported_stream_layout(hass, device, coordinator)
                elif (
                    device.get(CONF_DEVICE_TYPE, DEVICE_TYPE_AIR_CONDITIONER)
                    == DEVICE_TYPE_AIR_CONDITIONER
                ):
                    persistent_notification.async_dismiss(
                        hass,
                        _unsupported_stream_layout_notification_id(coordinator.feed_id),
                    )
    except Exception:
        auth_retry_manager.async_shutdown()
        raise

    entry.runtime_data = JdSmartRuntimeData(
        client=client,
        coordinators=coordinators,
        auth_retry_manager=auth_retry_manager,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: JdSmartConfigEntry) -> bool:
    """Unload a config entry."""
    if runtime_data := getattr(entry, "runtime_data", None):
        runtime_data.auth_retry_manager.async_shutdown()
        for coordinator in runtime_data.coordinators.values():
            coordinator.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: JdSmartConfigEntry) -> bool:
    """Reload a config entry."""
    if not await async_unload_entry(hass, entry):
        return False
    return await async_setup_entry(hass, entry)


def _entry_devices(data: dict) -> list[dict[str, str]]:
    """Return configured devices, supporting old single-device entries."""
    if devices := data.get(CONF_DEVICES):
        return devices
    return [
        {
            CONF_FEED_ID: data[CONF_FEED_ID],
            CONF_DEVICE_NAME: data.get(CONF_DEVICE_NAME, ""),
            CONF_DEVICE_TYPE: DEVICE_TYPE_AIR_CONDITIONER,
        }
    ]


def _is_unsupported_stream_layout(
    device: dict[str, str], coordinator: JdSmartCoordinator
) -> bool:
    """Return whether a recognized device has no usable entity handler."""
    return (
        device.get(CONF_DEVICE_TYPE, DEVICE_TYPE_AIR_CONDITIONER)
        == DEVICE_TYPE_AIR_CONDITIONER
        and coordinator.data is not None
        and not AIR_CONDITIONER_REQUIRED_STREAMS <= coordinator.data.streams.keys()
    )


def _notify_unsupported_stream_layout(
    hass: HomeAssistant,
    device: dict[str, str],
    coordinator: JdSmartCoordinator,
) -> None:
    """Tell the user how to request support for an unknown stream layout."""
    streams = ", ".join(sorted(coordinator.data.streams)) if coordinator.data else "none"
    persistent_notification.async_create(
        hass,
        "JD Smart added a device with an unsupported stream layout:\n"
        f"- {device.get(CONF_DEVICE_NAME, coordinator.feed_id)}: "
        f"{device.get(CONF_CATEGORY_NAME, 'Unknown category')} "
        f"(category_id={device.get(CONF_CATEGORY_ID, 'unknown')}, "
        f"config_type={device.get(CONF_CONFIG_TYPE, 'unknown')})\n"
        f"- Available streams: {streams}\n\n"
        "Please include this information in a "
        f"[pull request]({PULL_REQUEST_URL}).",
        title="JD Smart stream layout unsupported",
        notification_id=_unsupported_stream_layout_notification_id(coordinator.feed_id),
    )


def _unsupported_stream_layout_notification_id(feed_id: str) -> str:
    """Return the notification ID for an unsupported stream layout."""
    return f"{DOMAIN}_{feed_id}_unsupported_stream_layout"
