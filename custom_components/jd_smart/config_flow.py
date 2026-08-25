"""Config flow for JD Smart."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    JdSmartAuthError,
    JdSmartCannotConnectError,
    JdSmartClient,
    JdSmartCredentials,
    JdSmartDevice,
    JdSmartDeviceProfile,
    JdSmartError,
    JdSmartTokenRefreshError,
)
from .const import (
    CONF_APP_VERSION,
    CONF_CHANNEL,
    CONF_COOKIE,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICES,
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
    DOMAIN,
    LOGGER,
    auth_refresh_notification_ids,
)

ACTION_ADD_DEVICE = "add_device"
ACTION_MANUAL_AUTH = "manual_auth"
ACTION_REFRESH_AUTH = "refresh_auth"
AUTH_KEYS = (
    CONF_COOKIE,
    CONF_TGT,
    CONF_PIN,
    CONF_SGM_CONTEXT,
    CONF_DEVICE_ID,
    CONF_PLATFORM,
    CONF_APP_VERSION,
    CONF_DEVICE_MODEL,
    CONF_PLATFORM_VERSION,
    CONF_CHANNEL,
    CONF_USER_AGENT,
)
CONF_ACTION = "action"
CONF_SELECTED_DEVICES = "selected_devices"


def _action_schema() -> vol.Schema:
    """Return add-service action schema."""
    return vol.Schema(
        {
            vol.Required(CONF_ACTION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        ACTION_MANUAL_AUTH,
                        ACTION_REFRESH_AUTH,
                        ACTION_ADD_DEVICE,
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_ACTION,
                )
            )
        }
    )


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return config schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_COOKIE, default=defaults.get(CONF_COOKIE, "")): str,
            vol.Required(CONF_TGT, default=defaults.get(CONF_TGT, "")): str,
            vol.Optional(CONF_PIN, default=defaults.get(CONF_PIN, "")): str,
            vol.Optional(
                CONF_SGM_CONTEXT, default=defaults.get(CONF_SGM_CONTEXT, "")
            ): str,
            vol.Optional(CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, "")): str,
            vol.Optional(
                CONF_PLATFORM, default=defaults.get(CONF_PLATFORM, DEFAULT_PLATFORM)
            ): str,
            vol.Optional(
                CONF_APP_VERSION,
                default=defaults.get(CONF_APP_VERSION, DEFAULT_APP_VERSION),
            ): str,
            vol.Optional(
                CONF_DEVICE_MODEL,
                default=defaults.get(CONF_DEVICE_MODEL, DEFAULT_DEVICE_MODEL),
            ): str,
            vol.Optional(
                CONF_PLATFORM_VERSION,
                default=defaults.get(CONF_PLATFORM_VERSION, DEFAULT_PLATFORM_VERSION),
            ): str,
            vol.Optional(
                CONF_CHANNEL, default=defaults.get(CONF_CHANNEL, DEFAULT_CHANNEL)
            ): str,
            vol.Optional(
                CONF_USER_AGENT,
                default=defaults.get(CONF_USER_AGENT, DEFAULT_USER_AGENT),
            ): str,
        }
    )


def _clean_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Clean user input and fill defaults."""
    data = {key: value for key, value in user_input.items() if value != ""}
    data.setdefault(CONF_DEVICE_ID, DEFAULT_DEVICE_ID or str(secrets.randbelow(10**20)))
    data.setdefault(CONF_PLATFORM, DEFAULT_PLATFORM)
    data.setdefault(CONF_APP_VERSION, DEFAULT_APP_VERSION)
    data.setdefault(CONF_DEVICE_MODEL, DEFAULT_DEVICE_MODEL)
    data.setdefault(CONF_PLATFORM_VERSION, DEFAULT_PLATFORM_VERSION)
    data.setdefault(CONF_CHANNEL, DEFAULT_CHANNEL)
    data.setdefault(CONF_USER_AGENT, DEFAULT_USER_AGENT)
    return data


def _client_from_data(hass: HomeAssistant, data: dict[str, Any]) -> JdSmartClient:
    """Build an API client from config flow data."""
    return JdSmartClient(
        async_get_clientsession(hass),
        JdSmartCredentials(
            cookie=data[CONF_COOKIE],
            tgt=data[CONF_TGT],
            pin=data.get(CONF_PIN),
            sgm_context=data.get(CONF_SGM_CONTEXT),
        ),
        JdSmartDeviceProfile(
            device_id=data[CONF_DEVICE_ID],
            app_version=data[CONF_APP_VERSION],
            platform=data[CONF_PLATFORM],
            device_model=data[CONF_DEVICE_MODEL],
            platform_version=data[CONF_PLATFORM_VERSION],
            channel=data[CONF_CHANNEL],
            user_agent=data[CONF_USER_AGENT],
        ),
    )


async def _refresh_auth(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Refresh auth data and persist refreshed values into data."""
    new_tgt, new_cookie = await _client_from_data(hass, data).async_refresh_token()
    data[CONF_TGT] = new_tgt
    data[CONF_COOKIE] = new_cookie


async def _fetch_devices(
    hass: HomeAssistant, data: dict[str, Any]
) -> list[JdSmartDevice]:
    """Validate auth by fetching selectable devices."""
    client = _client_from_data(hass, data)
    try:
        return await client.async_get_devices()
    except JdSmartAuthError:
        LOGGER.info("JD Smart device-list auth failed; refreshing token")
        await _refresh_auth(hass, data)
        return await _client_from_data(hass, data).async_get_devices()


class JdSmartAcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for JD Smart."""

    VERSION = 1
    _auth_data: dict[str, Any]
    _devices: list[JdSmartDevice]
    _target_entry: Any | None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if self._async_current_entries() and user_input is None:
            return await self.async_step_action()
        return await self.async_step_manual_auth(user_input)

    async def async_step_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle add-service action selection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            action = user_input[CONF_ACTION]
            if action == ACTION_MANUAL_AUTH:
                return await self.async_step_manual_auth()
            if action == ACTION_ADD_DEVICE:
                return await self.async_step_add_device()
            if action == ACTION_REFRESH_AUTH:
                return await self.async_step_refresh_auth()
            errors["base"] = "unknown"

        return self.async_show_form(
            step_id="action",
            data_schema=_action_schema(),
            errors=errors,
        )

    async def async_step_manual_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual authentication input."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean_input(user_input)
            try:
                await _refresh_auth(self.hass, data)
                devices = await _fetch_devices(self.hass, data)
            except JdSmartTokenRefreshError:
                errors["base"] = "token_refresh_failed"
            except JdSmartAuthError:
                errors["base"] = "invalid_auth"
            except JdSmartCannotConnectError:
                errors["base"] = "cannot_connect"
            except JdSmartError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if self._async_current_entries():
                    await self._async_update_auth_entries(data)
                    return self.async_abort(reason="auth_updated")

                self._auth_data = data
                self._target_entry = None
                configured_feed_ids = _configured_feed_ids(
                    self._async_current_entries()
                )
                self._devices = [
                    device
                    for device in devices
                    if device.feed_id not in configured_feed_ids
                ]
                if not self._devices:
                    return self.async_abort(reason="no_devices")
                return await self.async_step_select_device()

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input),
            errors=errors,
        )

    async def async_step_refresh_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Refresh authentication for existing entries."""
        entry = _primary_entry(self._async_current_entries())
        if entry is None:
            return await self.async_step_manual_auth()

        try:
            data = dict(entry.data)
            await _refresh_auth(self.hass, data)
        except JdSmartTokenRefreshError as err:
            LOGGER.error("JD Smart token refresh failed from config flow: %s", err)
            return self.async_show_form(
                step_id="action",
                data_schema=_action_schema(),
                errors={"base": "token_refresh_failed"},
                description_placeholders={"reason": str(err)},
            )

        await self._async_update_auth_entries(data)
        return self.async_abort(reason="auth_refreshed")

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch devices with existing auth and add new devices."""
        entry = _primary_entry(self._async_current_entries())
        if entry is None:
            return await self.async_step_manual_auth()

        data = dict(entry.data)
        try:
            devices = await _fetch_devices(self.hass, data)
        except JdSmartTokenRefreshError:
            return self.async_show_form(
                step_id="action",
                data_schema=_action_schema(),
                errors={"base": "token_refresh_failed"},
            )
        except JdSmartAuthError:
            return self.async_show_form(
                step_id="action",
                data_schema=_action_schema(),
                errors={"base": "invalid_auth"},
            )
        except JdSmartCannotConnectError:
            return self.async_show_form(
                step_id="action",
                data_schema=_action_schema(),
                errors={"base": "cannot_connect"},
            )
        except JdSmartError:
            return self.async_show_form(
                step_id="action",
                data_schema=_action_schema(),
                errors={"base": "cannot_connect"},
            )

        if _auth_changed(entry.data, data):
            await self._async_update_auth_entries(data)
        self._auth_data = data
        self._target_entry = entry
        configured_feed_ids = _configured_feed_ids(self._async_current_entries())
        self._devices = [
            device for device in devices if device.feed_id not in configured_feed_ids
        ]
        if not self._devices:
            return self.async_abort(reason="no_devices")
        return await self.async_step_select_device()

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle device selection."""
        errors: dict[str, str] = {}
        devices = getattr(self, "_devices", [])
        if user_input is not None:
            selected = user_input[CONF_SELECTED_DEVICES]
            selected_feed_ids = (
                {selected} if isinstance(selected, str) else set(selected)
            )
            selected_devices = [
                device for device in devices if device.feed_id in selected_feed_ids
            ]
            if not selected_devices or len(selected_devices) != len(selected_feed_ids):
                errors["base"] = "unknown"
            else:
                first_device = selected_devices[0]
                data = {
                    **self._auth_data,
                    CONF_FEED_ID: first_device.feed_id,
                    CONF_DEVICE_NAME: first_device.name,
                    CONF_DEVICES: [
                        {
                            CONF_FEED_ID: device.feed_id,
                            CONF_DEVICE_NAME: device.name,
                        }
                        for device in selected_devices
                    ],
                }
                title = (
                    first_device.name
                    if len(selected_devices) == 1
                    else f"JD Smart ({len(selected_devices)} devices)"
                )
                target_entry = getattr(self, "_target_entry", None)
                if target_entry is not None:
                    return self.async_update_reload_and_abort(
                        target_entry,
                        data=_merge_entry_devices(
                            target_entry.data,
                            data,
                            selected_devices,
                        ),
                    )
                return self.async_create_entry(title=title, data=data)

        options = [
            selector.SelectOptionDict(value=device.feed_id, label=_device_label(device))
            for device in devices
        ]
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SELECTED_DEVICES): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **_clean_input(user_input)}
            try:
                await _refresh_auth(self.hass, data)
            except JdSmartTokenRefreshError:
                errors["base"] = "token_refresh_failed"
            except JdSmartAuthError:
                errors["base"] = "invalid_auth"
            except JdSmartError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                feed_ids = tuple(
                    device[CONF_FEED_ID] for device in _entry_devices(entry.data)
                )
                for notification_id in auth_refresh_notification_ids(
                    entry.entry_id, feed_ids
                ):
                    persistent_notification.async_dismiss(self.hass, notification_id)
                return self.async_update_reload_and_abort(
                    entry,
                    data=data,
                    reason="auth_refreshed",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_schema(entry.data),
            errors=errors,
        )

    async def _async_update_auth_entries(self, auth_data: dict[str, Any]) -> None:
        """Update auth fields for all existing entries and reload them."""
        for entry in self._async_current_entries():
            data = dict(entry.data)
            for key in AUTH_KEYS:
                if key in auth_data:
                    data[key] = auth_data[key]
            self.hass.config_entries.async_update_entry(entry, data=data)
            feed_ids = tuple(
                device[CONF_FEED_ID] for device in _entry_devices(entry.data)
            )
            for notification_id in auth_refresh_notification_ids(
                entry.entry_id, feed_ids
            ):
                persistent_notification.async_dismiss(self.hass, notification_id)
            await self.hass.config_entries.async_reload(entry.entry_id)


def _device_label(device: JdSmartDevice) -> str:
    """Return a readable device option label."""
    details = [value for value in (device.room_name, device.category_name) if value]
    suffix = f" - {' / '.join(details)}" if details else ""
    return f"{device.name}{suffix} ({device.feed_id})"


def _entry_devices(data: dict[str, Any]) -> list[dict[str, str]]:
    """Return configured devices from new or legacy entry data."""
    if devices := data.get(CONF_DEVICES):
        return devices
    if feed_id := data.get(CONF_FEED_ID):
        return [
            {
                CONF_FEED_ID: feed_id,
                CONF_DEVICE_NAME: data.get(CONF_DEVICE_NAME, ""),
            }
        ]
    return []


def _merge_entry_devices(
    entry_data: dict[str, Any],
    auth_data: dict[str, Any],
    selected_devices: list[JdSmartDevice],
) -> dict[str, Any]:
    """Merge selected devices into an existing entry."""
    devices = {
        device[CONF_FEED_ID]: dict(device) for device in _entry_devices(entry_data)
    }
    for device in selected_devices:
        devices[device.feed_id] = {
            CONF_FEED_ID: device.feed_id,
            CONF_DEVICE_NAME: device.name,
        }

    merged_devices = list(devices.values())
    first_device = merged_devices[0]
    data = {
        **entry_data,
        **{key: auth_data[key] for key in AUTH_KEYS if key in auth_data},
        CONF_FEED_ID: first_device[CONF_FEED_ID],
        CONF_DEVICE_NAME: first_device.get(CONF_DEVICE_NAME, ""),
        CONF_DEVICES: merged_devices,
    }
    return data


def _primary_entry(entries):
    """Return the entry used for account-level add-service actions."""
    return entries[0] if entries else None


def _auth_changed(old_data: dict[str, Any], new_data: dict[str, Any]) -> bool:
    """Return whether auth fields changed."""
    return any(old_data.get(key) != new_data.get(key) for key in AUTH_KEYS)


def _configured_feed_ids(entries) -> set[str]:
    """Return feed IDs already configured in existing entries."""
    feed_ids: set[str] = set()
    for entry in entries:
        feed_ids.update(device[CONF_FEED_ID] for device in _entry_devices(entry.data))
    return feed_ids
