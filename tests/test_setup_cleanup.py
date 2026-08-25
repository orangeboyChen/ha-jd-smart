"""Tests for JD Smart setup cleanup."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.jd_smart import async_setup_entry
from custom_components.jd_smart.const import (
    CONF_COOKIE,
    CONF_DEVICES,
    CONF_FEED_ID,
    CONF_TGT,
    DOMAIN,
)


async def test_setup_failure_shuts_down_retry_manager(hass) -> None:
    """A later coordinator setup failure cancels earlier retry timers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={
            CONF_COOKIE: "cookie",
            CONF_TGT: "tgt",
            CONF_DEVICES: [
                {CONF_FEED_ID: "first"},
                {CONF_FEED_ID: "second"},
            ],
        },
    )
    entry.add_to_hass(hass)
    first_coordinator = Mock()
    first_coordinator.auth_retry_pending = True
    first_coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=ConfigEntryNotReady
    )
    second_coordinator = Mock()
    second_coordinator.auth_retry_pending = False
    second_coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=ConfigEntryNotReady
    )

    with (
        patch("custom_components.jd_smart.JdSmartClient"),
        patch(
            "custom_components.jd_smart.JdSmartCoordinator",
            side_effect=[first_coordinator, second_coordinator],
        ),
        patch("custom_components.jd_smart.JdSmartAuthRetryManager") as manager_class,
    ):
        manager = manager_class.return_value
        manager.async_shutdown = Mock()
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    manager.async_shutdown.assert_called_once()
