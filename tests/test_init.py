"""Tests for JD Smart setup behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jd_smart import async_setup_entry
from custom_components.jd_smart.api import JdSmartAuthError, JdSmartTokenRefreshError
from custom_components.jd_smart.const import (
    CONF_COOKIE,
    CONF_FEED_ID,
    CONF_TGT,
    DOMAIN,
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
