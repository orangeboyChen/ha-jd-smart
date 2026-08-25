"""Coordinator for the JD Smart integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import homeassistant.util.dt as dt_util
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    JdSmartAuthError,
    JdSmartCannotConnectError,
    JdSmartClient,
    JdSmartError,
    JdSmartSnapshot,
    JdSmartTokenRefreshError,
)
from .const import (
    AUTH_REFRESH_RETRY_DELAYS,
    CONF_COOKIE,
    CONF_TGT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FAST_POLL_DURATION,
    FAST_POLL_INTERVAL,
    LOGGER,
    UPDATE_AUTH_FAILURE_THRESHOLD,
    auth_refresh_notification_ids,
)

type JdSmartConfigEntry = ConfigEntry[JdSmartRuntimeData]


@dataclass
class JdSmartRuntimeData:
    """Runtime data for JD Smart."""

    client: JdSmartClient
    coordinators: dict[str, JdSmartCoordinator]
    auth_retry_manager: JdSmartAuthRetryManager


class JdSmartAuthRetryManager:
    """Coordinate authentication refresh retries for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: JdSmartConfigEntry,
        client: JdSmartClient,
    ) -> None:
        """Initialize the authentication retry manager."""
        self.hass = hass
        self.config_entry = entry
        self.client = client
        self._coordinators: list[JdSmartCoordinator] = []
        self._refresh_lock = asyncio.Lock()
        self._retry_cancel: Callable[[], None] | None = None
        self._failure_count = 0
        self._validating_refresh = False
        self._shutdown = False

    def register_coordinator(self, coordinator: JdSmartCoordinator) -> None:
        """Register a coordinator for post-refresh validation."""
        self._coordinators.append(coordinator)

    async def async_handle_auth_failure(
        self,
        failed_tgt: str,
        auth_error: Exception | None = None,
    ) -> bool:
        """Refresh credentials immediately when no retry is already pending."""
        async with self._refresh_lock:
            if failed_tgt != self.client.credentials.tgt:
                return True
            if self._retry_cancel is not None:
                return False
            if self._validating_refresh:
                if auth_error is not None:
                    self.async_schedule_failure(auth_error)
                return False
            return await self._async_refresh_locked()

    async def _async_refresh_locked(self) -> bool:
        """Refresh and persist credentials while holding the refresh lock."""
        try:
            new_tgt, new_cookie = await self.client.async_refresh_token()
        except JdSmartTokenRefreshError as err:
            LOGGER.warning("JD Smart token refresh failed: %s", err)
            self.async_schedule_failure(err)
            return False
        if self._shutdown:
            LOGGER.info("Discarding JD Smart refresh result after shutdown")
            return False

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONF_TGT: new_tgt,
                CONF_COOKIE: new_cookie,
            },
        )
        self._validating_refresh = True
        return True

    @callback
    def async_schedule_failure(self, err: Exception) -> None:
        """Schedule the next authentication refresh attempt."""
        self._validating_refresh = False
        if self._retry_cancel is not None or self._shutdown:
            return
        self._failure_count += 1
        delay = AUTH_REFRESH_RETRY_DELAYS[
            min(self._failure_count - 1, len(AUTH_REFRESH_RETRY_DELAYS) - 1)
        ]
        retry_at = dt_util.utcnow() + delay
        self._retry_cancel = async_track_point_in_utc_time(
            self.hass,
            self._async_retry_callback,
            retry_at,
        )
        self._async_update_notification(err, retry_at)

    @callback
    def _async_retry_callback(self, _now: datetime) -> None:
        """Start a scheduled authentication refresh attempt."""
        self._retry_cancel = None
        self.hass.async_create_task(self._async_retry())

    async def _async_retry(self) -> None:
        """Refresh credentials and validate them with a device snapshot."""
        async with self._refresh_lock:
            if self._shutdown:
                return
            refreshed = await self._async_refresh_locked()
        if not refreshed or not self._coordinators:
            return
        try:
            await self._coordinators[0].async_request_refresh()
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("JD Smart post-refresh validation failed")
            self.async_schedule_failure(err)

    @callback
    def async_mark_recovered(self) -> None:
        """Reset retry state and remove the authentication notification."""
        self._failure_count = 0
        self._validating_refresh = False
        if self._retry_cancel:
            self._retry_cancel()
            self._retry_cancel = None
        self._async_dismiss_notifications()

    @callback
    def _async_dismiss_notifications(self) -> None:
        """Dismiss current and legacy authentication notifications."""
        feed_ids = tuple(coordinator.feed_id for coordinator in self._coordinators)
        for notification_id in auth_refresh_notification_ids(
            self.config_entry.entry_id, feed_ids
        ):
            persistent_notification.async_dismiss(self.hass, notification_id)

    @callback
    def _async_update_notification(
        self,
        err: Exception,
        retry_at: datetime,
    ) -> None:
        """Create or update the single authentication retry notification."""
        reason = str(err) or err.__class__.__name__
        local_retry_at = dt_util.as_local(retry_at).strftime("%Y-%m-%d %H:%M:%S %Z")
        feed_ids = tuple(coordinator.feed_id for coordinator in self._coordinators)
        notification_ids = auth_refresh_notification_ids(
            self.config_entry.entry_id, feed_ids
        )
        for legacy_id in notification_ids[1:]:
            persistent_notification.async_dismiss(self.hass, legacy_id)
        persistent_notification.async_create(
            self.hass,
            (
                "JD Smart authentication refresh failed. "
                f"Attempt: {self._failure_count}. "
                f"Reason: {reason}. "
                f"Next automatic refresh: {local_retry_at}. "
                "You can also open Settings > Devices & services and use "
                "Refresh authentication or enter new authentication data."
            ),
            title="JD Smart authentication refresh retrying",
            notification_id=notification_ids[0],
        )

    @callback
    def async_shutdown(self) -> None:
        """Cancel the pending authentication retry."""
        self._shutdown = True
        if self._retry_cancel:
            self._retry_cancel()
            self._retry_cancel = None


class JdSmartCoordinator(DataUpdateCoordinator[JdSmartSnapshot]):
    """Data coordinator for JD Smart."""

    config_entry: JdSmartConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: JdSmartConfigEntry,
        client: JdSmartClient,
        feed_id: str,
        device_name: str | None,
        device_type: str,
        auth_retry_manager: JdSmartAuthRetryManager,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.feed_id = feed_id
        self.device_name = device_name
        self.device_type = device_type
        self.auth_retry_manager = auth_retry_manager
        self._fast_poll_cancel: Callable[[], None] | None = None
        self._consecutive_update_failures = 0
        self.auth_retry_pending = False
        auth_retry_manager.register_coordinator(self)

    async def _async_update_data(self) -> JdSmartSnapshot:
        """Fetch latest snapshot."""
        self.auth_retry_pending = False
        digest = self.data.digest if self.data else ""
        failed_tgt = self.client.credentials.tgt
        try:
            snapshot = await self.client.async_get_snapshot(self.feed_id, digest)
            self._consecutive_update_failures = 0
            self.auth_retry_manager.async_mark_recovered()
            return snapshot
        except JdSmartAuthError as err:
            LOGGER.info("JD Smart snapshot authentication failed; refreshing token")
            if not await self.auth_retry_manager.async_handle_auth_failure(
                failed_tgt, err
            ):
                self.auth_retry_pending = True
                raise UpdateFailed(
                    "JD Smart authentication refresh is scheduled"
                ) from err
            try:
                snapshot = await self.client.async_get_snapshot(self.feed_id, digest)
            except JdSmartError as retry_err:
                self.auth_retry_manager.async_schedule_failure(retry_err)
                self.auth_retry_pending = True
                raise UpdateFailed(
                    "JD Smart authentication validation failed"
                ) from retry_err
            self._consecutive_update_failures = 0
            self.auth_retry_manager.async_mark_recovered()
            return snapshot
        except JdSmartCannotConnectError as err:
            if self.data is None:
                raise ConfigEntryNotReady from err
            await self._async_handle_update_failure(err)
        except JdSmartError as err:
            await self._async_handle_update_failure(err)
        raise UpdateFailed("Unable to update JD Smart")

    async def async_control_streams(self, commands: dict[str, object]) -> None:
        """Control streams and refresh state."""
        failed_tgt = self.client.credentials.tgt
        try:
            snapshot = await self.client.async_control_streams(self.feed_id, commands)
        except JdSmartAuthError as err:
            LOGGER.warning(
                "JD Smart control authentication failed: "
                "feed_id=%s, commands=%s, error=%s",
                self.feed_id,
                commands,
                err,
            )
            try:
                if not await self.auth_retry_manager.async_handle_auth_failure(
                    failed_tgt, err
                ):
                    raise UpdateFailed("JD Smart authentication refresh is scheduled")
                snapshot = await self.client.async_control_streams(
                    self.feed_id,
                    commands,
                )
            except JdSmartError as refresh_err:
                self.auth_retry_manager.async_schedule_failure(refresh_err)
                LOGGER.warning(
                    "JD Smart control failed after token refresh: "
                    "feed_id=%s, commands=%s, error=%s",
                    self.feed_id,
                    commands,
                    refresh_err,
                )
                raise UpdateFailed("Unable to control JD Smart") from refresh_err
            self.auth_retry_manager.async_mark_recovered()
        except JdSmartError as err:
            LOGGER.warning(
                "JD Smart control failed: feed_id=%s, commands=%s, error=%s",
                self.feed_id,
                commands,
                err,
            )
            raise UpdateFailed("Unable to control JD Smart") from err
        if snapshot is not None:
            self.async_set_updated_data(snapshot)
        self.trigger_fast_polling()
        await self.async_request_refresh()

    async def _async_handle_update_failure(self, err: JdSmartError) -> None:
        """Handle repeated update failures."""
        self._consecutive_update_failures += 1
        if self._consecutive_update_failures >= UPDATE_AUTH_FAILURE_THRESHOLD:
            LOGGER.warning(
                "JD Smart update failed repeatedly; requesting reauthentication: "
                "feed_id=%s, failures=%s",
                self.feed_id,
                self._consecutive_update_failures,
            )
            self._async_create_reauth_notification()
            raise ConfigEntryAuthFailed from err
        raise UpdateFailed("Unable to update JD Smart") from err

    @callback
    def _async_create_reauth_notification(self) -> None:
        """Create a persistent reauth notification."""
        persistent_notification.async_create(
            self.hass,
            (
                "JD Smart could not update the device data several times. "
                "Open Settings > Devices & services and reauthenticate JD Smart."
            ),
            title="JD Smart authentication required",
            notification_id=f"{DOMAIN}_{self.feed_id}_reauth",
        )

    def async_shutdown(self) -> None:
        """Cancel pending coordinator callbacks."""
        if self._fast_poll_cancel:
            self._fast_poll_cancel()
            self._fast_poll_cancel = None

    @callback
    def trigger_fast_polling(self) -> None:
        """Temporarily poll faster after a control command."""
        self.update_interval = FAST_POLL_INTERVAL
        if self._fast_poll_cancel:
            self._fast_poll_cancel()
        end = dt_util.utcnow() + FAST_POLL_DURATION
        self._fast_poll_cancel = async_track_point_in_utc_time(
            self.hass, self._reset_polling, end
        )

    @callback
    def _reset_polling(self, _now: datetime) -> None:
        """Reset polling interval."""
        self.update_interval = DEFAULT_SCAN_INTERVAL
        self._fast_poll_cancel = None
