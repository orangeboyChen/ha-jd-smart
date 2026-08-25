"""Tests for JD Smart authentication configuration flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jd_smart.config_flow import JdSmartAcConfigFlow
from custom_components.jd_smart.const import (
    CONF_COOKIE,
    CONF_TGT,
    DOMAIN,
    auth_refresh_notification_id,
)


async def test_manual_auth_update_clears_retry_notification(hass) -> None:
    """A successful manual auth update clears the background retry notice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={CONF_COOKIE: "old-cookie", CONF_TGT: "old-tgt"},
    )
    entry.add_to_hass(hass)
    flow = JdSmartAcConfigFlow()
    flow.hass = hass

    with (
        patch.object(flow, "_async_current_entries", return_value=[entry]),
        patch.object(
            hass.config_entries,
            "async_reload",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.jd_smart.config_flow.persistent_notification.async_dismiss"
        ) as dismiss_notification,
    ):
        await flow._async_update_auth_entries(
            {CONF_COOKIE: "new-cookie", CONF_TGT: "new-tgt"}
        )

    dismiss_notification.assert_called_once_with(
        hass,
        auth_refresh_notification_id("entry-id"),
    )
