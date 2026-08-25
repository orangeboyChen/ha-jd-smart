"""Tests for JD Smart authentication retries."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import homeassistant.util.dt as dt_util
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jd_smart.api import (
    JdSmartAuthError,
    JdSmartCredentials,
    JdSmartSnapshot,
    JdSmartTokenRefreshError,
)
from custom_components.jd_smart.const import (
    CONF_COOKIE,
    CONF_TGT,
    DOMAIN,
    auth_refresh_notification_ids,
)
from custom_components.jd_smart.coordinator import (
    JdSmartAuthRetryManager,
    JdSmartCoordinator,
)


def _create_manager(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={CONF_COOKIE: "old-cookie", CONF_TGT: "old-tgt"},
    )
    entry.add_to_hass(hass)
    client = SimpleNamespace(
        credentials=JdSmartCredentials(cookie="old-cookie", tgt="old-tgt"),
        async_refresh_token=AsyncMock(),
    )
    return entry, client, JdSmartAuthRetryManager(hass, entry, client)


def test_backoff_updates_one_notification_and_caps_at_one_hour(hass) -> None:
    """The notification is updated with the changing retry time."""
    _entry, _client, manager = _create_manager(hass)
    cancel = Mock()
    expected_delays = [5, 10, 20, 40, 60, 60]

    with (
        patch(
            "custom_components.jd_smart.coordinator.async_track_point_in_utc_time",
            return_value=cancel,
        ) as track,
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_create"
        ) as create_notification,
    ):
        for attempt, expected_minutes in enumerate(expected_delays, start=1):
            before = dt_util.utcnow()
            manager.async_schedule_failure(JdSmartAuthError("expired"))
            retry_at = track.call_args.args[2]

            assert timedelta(minutes=expected_minutes) <= retry_at - before
            assert retry_at - before < timedelta(minutes=expected_minutes, seconds=1)
            assert (
                create_notification.call_args.kwargs["notification_id"]
                == (auth_refresh_notification_ids("entry-id")[0])
            )
            assert f"Attempt: {attempt}." in create_notification.call_args.args[1]
            manager._retry_cancel = None


async def test_multiple_devices_share_one_immediate_refresh(hass) -> None:
    """Concurrent device failures reuse credentials refreshed by another device."""
    entry, client, manager = _create_manager(hass)

    async def refresh_token():
        client.credentials.tgt = "new-tgt"
        client.credentials.cookie = "new-cookie"
        return "new-tgt", "new-cookie"

    client.async_refresh_token.side_effect = refresh_token

    assert await manager.async_handle_auth_failure("old-tgt")
    assert await manager.async_handle_auth_failure("old-tgt")
    assert not await manager.async_handle_auth_failure("new-tgt")

    client.async_refresh_token.assert_awaited_once()
    assert entry.data[CONF_TGT] == "new-tgt"
    assert entry.data[CONF_COOKIE] == "new-cookie"


async def test_scheduled_retry_validates_and_clears_failure(hass) -> None:
    """A scheduled refresh validates a snapshot before clearing the failure."""
    _entry, client, manager = _create_manager(hass)
    client.async_refresh_token.return_value = ("new-tgt", "new-cookie")
    coordinator = SimpleNamespace(feed_id="feed-id", async_request_refresh=AsyncMock())
    coordinator.async_request_refresh.side_effect = manager.async_mark_recovered
    manager.register_coordinator(coordinator)
    manager._failure_count = 2

    with patch(
        "custom_components.jd_smart.coordinator.persistent_notification.async_dismiss"
    ) as dismiss_notification:
        await manager._async_retry()

    coordinator.async_request_refresh.assert_awaited_once()
    dismiss_notification.assert_any_call(
        hass, auth_refresh_notification_ids("entry-id")[0]
    )
    assert manager._failure_count == 0


async def test_scheduled_validation_401_reschedules_backoff(hass) -> None:
    """A 401 during scheduled validation schedules another retry."""
    _entry, client, manager = _create_manager(hass)
    client.credentials.tgt = "new-tgt"
    manager._validating_refresh = True

    with (
        patch(
            "custom_components.jd_smart.coordinator.async_track_point_in_utc_time",
            return_value=Mock(),
        ) as track,
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_create"
        ) as create_notification,
    ):
        assert not await manager.async_handle_auth_failure(
            "new-tgt", JdSmartAuthError("still expired")
        )

    assert not manager._validating_refresh
    track.assert_called_once()
    create_notification.assert_called_once()


async def test_refresh_failure_schedules_retry_without_repeating(hass) -> None:
    """A failed immediate refresh schedules one retry for all later requests."""
    _entry, client, manager = _create_manager(hass)
    client.async_refresh_token.side_effect = JdSmartTokenRefreshError("rejected")

    with (
        patch(
            "custom_components.jd_smart.coordinator.async_track_point_in_utc_time",
            return_value=Mock(),
        ) as track,
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_create"
        ) as create_notification,
    ):
        assert not await manager.async_handle_auth_failure("old-tgt")
        assert not await manager.async_handle_auth_failure("old-tgt")

    client.async_refresh_token.assert_awaited_once()
    track.assert_called_once()
    create_notification.assert_called_once()


async def test_shutdown_discards_inflight_refresh_result(hass) -> None:
    """An in-flight refresh cannot overwrite credentials after shutdown."""
    entry, client, manager = _create_manager(hass)
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def refresh_token():
        refresh_started.set()
        await release_refresh.wait()
        return "stale-tgt", "stale-cookie"

    client.async_refresh_token.side_effect = refresh_token
    refresh_task = asyncio.create_task(manager.async_handle_auth_failure("old-tgt"))
    await refresh_started.wait()
    manager.async_shutdown()
    release_refresh.set()

    assert not await refresh_task
    assert entry.data[CONF_TGT] == "old-tgt"
    assert entry.data[CONF_COOKIE] == "old-cookie"


def test_failure_cleans_legacy_notifications(hass) -> None:
    """New failures dismiss legacy global and per-device notifications."""
    _entry, _client, manager = _create_manager(hass)
    coordinator = SimpleNamespace(feed_id="feed-id")
    manager.register_coordinator(coordinator)

    with (
        patch(
            "custom_components.jd_smart.coordinator.async_track_point_in_utc_time",
            return_value=Mock(),
        ),
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_dismiss"
        ) as dismiss_notification,
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_create"
        ),
    ):
        manager.async_schedule_failure(JdSmartAuthError("expired"))

    dismissed_ids = [call.args[1] for call in dismiss_notification.call_args_list]
    assert dismissed_ids == [
        "jd_smart_token_refresh_failed",
        "jd_smart_feed-id_token_refresh_failed",
    ]


async def test_snapshot_must_validate_refreshed_credentials(hass) -> None:
    """A refresh followed by another 401 remains in the failed state."""
    entry, client, manager = _create_manager(hass)
    client.async_get_snapshot = AsyncMock(
        side_effect=[JdSmartAuthError("expired"), JdSmartAuthError("still expired")]
    )
    client.async_refresh_token.return_value = ("new-tgt", "new-cookie")
    coordinator = JdSmartCoordinator(
        hass,
        entry,
        client,
        "feed-id",
        "Air conditioner",
        manager,
    )

    with (
        patch(
            "custom_components.jd_smart.coordinator.async_track_point_in_utc_time",
            return_value=Mock(),
        ),
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_create"
        ) as create_notification,
    ):
        with pytest.raises(UpdateFailed, match="validation failed"):
            await coordinator._async_update_data()

    assert coordinator.auth_retry_pending
    create_notification.assert_called_once()


async def test_successful_refresh_persists_and_clears_notification(hass) -> None:
    """A validated refresh saves credentials and clears retry state."""
    entry, client, manager = _create_manager(hass)
    snapshot = JdSmartSnapshot("digest", "0", True, {"power": "1"})
    client.async_get_snapshot = AsyncMock(
        side_effect=[JdSmartAuthError("expired"), snapshot]
    )
    client.async_refresh_token.return_value = ("new-tgt", "new-cookie")
    coordinator = JdSmartCoordinator(
        hass,
        entry,
        client,
        "feed-id",
        "Air conditioner",
        manager,
    )

    with patch(
        "custom_components.jd_smart.coordinator.persistent_notification.async_dismiss"
    ) as dismiss_notification:
        result = await coordinator._async_update_data()

    assert result is snapshot
    assert entry.data[CONF_TGT] == "new-tgt"
    dismiss_notification.assert_any_call(
        hass, auth_refresh_notification_ids("entry-id")[0]
    )


def test_shutdown_cancels_pending_retry(hass) -> None:
    """Unloading the config entry cancels its retry timer."""
    _entry, _client, manager = _create_manager(hass)
    cancel = Mock()

    with (
        patch(
            "custom_components.jd_smart.coordinator.async_track_point_in_utc_time",
            return_value=cancel,
        ),
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_create"
        ),
    ):
        manager.async_schedule_failure(JdSmartAuthError("expired"))
        manager.async_shutdown()

    cancel.assert_called_once()
