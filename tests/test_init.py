"""Tests for JD Smart setup behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jd_smart import (
    _unsupported_stream_layout_notification_id,
    async_setup_entry,
)
from custom_components.jd_smart.api import (
    JdSmartAuthError,
    JdSmartSnapshot,
    JdSmartTokenRefreshError,
)
from custom_components.jd_smart.const import (
    CONF_CATEGORY_ID,
    CONF_CATEGORY_NAME,
    CONF_COOKIE,
    CONF_CONFIG_TYPE,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_FEED_ID,
    CONF_TGT,
    DEVICE_TYPE_AIR_CONDITIONER,
    DOMAIN,
    PULL_REQUEST_URL,
)


async def test_initial_auth_failure_keeps_entry_loaded(hass) -> None:
    """An initial authentication failure loads entities while retrying."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={
            CONF_COOKIE: "old-cookie",
            CONF_TGT: "old-tgt",
            CONF_FEED_ID: "feed-id",
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.credentials.tgt = "old-tgt"
    client.async_get_snapshot.side_effect = JdSmartAuthError("expired")
    client.async_refresh_token.side_effect = JdSmartTokenRefreshError("rejected")

    with (
        patch("custom_components.jd_smart.JdSmartClient", return_value=client),
        patch(
            "custom_components.jd_smart.async_track_point_in_utc_time",
            create=True,
        ),
        patch(
            "custom_components.jd_smart.coordinator.async_track_point_in_utc_time",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.jd_smart.coordinator.persistent_notification.async_create"
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ) as forward_setups,
    ):
        assert await async_setup_entry(hass, entry)

    forward_setups.assert_awaited_once()
    assert entry.runtime_data.coordinators["feed-id"].auth_retry_pending


async def test_setup_notifies_for_unsupported_stream_layout(hass) -> None:
    """Report a device category that lacks the handler's required streams."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={
            CONF_COOKIE: "cookie",
            CONF_TGT: "tgt",
            CONF_DEVICES: [
                {
                    CONF_FEED_ID: "feed-id",
                    CONF_DEVICE_NAME: "Air conditioner",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_AIR_CONDITIONER,
                    CONF_CATEGORY_ID: "101001",
                    CONF_CATEGORY_NAME: "Air conditioner",
                    CONF_CONFIG_TYPE: "different-card",
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.credentials.tgt = "tgt"
    client.async_get_snapshot.return_value = JdSmartSnapshot(
        "digest", "0", True, {"power": "1", "mode": "0"}
    )

    with (
        patch("custom_components.jd_smart.JdSmartClient", return_value=client),
        patch(
            "custom_components.jd_smart.persistent_notification.async_create"
        ) as create_notification,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)

    message = create_notification.call_args.args[1]
    assert "category_id=101001" in message
    assert "mode, power" in message
    assert PULL_REQUEST_URL in message


async def test_setup_clears_recovered_stream_layout_notification(hass) -> None:
    """Dismiss the warning when an air conditioner has its core streams."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={
            CONF_COOKIE: "cookie",
            CONF_TGT: "tgt",
            CONF_DEVICES: [
                {
                    CONF_FEED_ID: "feed-id",
                    CONF_DEVICE_NAME: "Air conditioner",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_AIR_CONDITIONER,
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.credentials.tgt = "tgt"
    client.async_get_snapshot.return_value = JdSmartSnapshot(
        "digest", "0", True, {"power": "1", "mode": "0", "settemp": "25"}
    )

    with (
        patch("custom_components.jd_smart.JdSmartClient", return_value=client),
        patch(
            "custom_components.jd_smart.persistent_notification.async_dismiss"
        ) as dismiss_notification,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        assert await async_setup_entry(hass, entry)

    dismiss_notification.assert_any_call(
        hass,
        _unsupported_stream_layout_notification_id("feed-id"),
    )
