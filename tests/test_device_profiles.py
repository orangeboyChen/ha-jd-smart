"""Tests for JD Smart server device profiles."""

from __future__ import annotations

from unittest.mock import patch

from custom_components.jd_smart.api import JdSmartDevice, _parse_devices
from custom_components.jd_smart.config_flow import (
    CONF_SELECTED_DEVICES,
    JdSmartAcConfigFlow,
    _device_type,
    _entry_device,
    _notify_unsupported_devices,
    _split_supported_devices,
)
from custom_components.jd_smart.const import (
    CONF_CATEGORY_ID,
    CONF_CONFIG_TYPE,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_AIR_CONDITIONER,
    PULL_REQUEST_URL,
)


def test_parse_device_profile_from_server_card() -> None:
    """Parse a device's category and card configuration types."""
    devices = _parse_devices(
        {
            "platform_list": [
                {
                    "cards": [
                        {
                            "feed_id": "feed-id",
                            "card_name": "Air conditioner",
                            "category_id": 101001,
                            "category_name": "Air conditioner",
                            "config_type": 1113,
                            "detail_type": 0,
                        }
                    ]
                }
            ]
        }
    )

    assert devices == [
        JdSmartDevice(
            feed_id="feed-id",
            name="Air conditioner",
            category_id="101001",
            category_name="Air conditioner",
            config_type="1113",
            detail_type="0",
        )
    ]


def test_supported_profile_is_persisted_with_device() -> None:
    """Store the server profile required to select an entity handler."""
    device = JdSmartDevice(
        feed_id="feed-id",
        name="Air conditioner",
        category_id="101001",
        config_type="1113",
    )

    assert _device_type(device) == DEVICE_TYPE_AIR_CONDITIONER
    assert _entry_device(device) == {
        "feed_id": "feed-id",
        "device_name": "Air conditioner",
        CONF_CATEGORY_ID: "101001",
        "category_name": "",
        CONF_CONFIG_TYPE: "1113",
        "detail_type": "",
        CONF_DEVICE_TYPE: DEVICE_TYPE_AIR_CONDITIONER,
    }


def test_unsupported_profile_creates_pull_request_notification(hass) -> None:
    """Guide users to contribute support for an unknown device profile."""
    device = JdSmartDevice(
        feed_id="feed-id",
        name="Unsupported device",
        category_id="999999",
        category_name="Unsupported category",
        config_type="42",
    )

    supported, unsupported = _split_supported_devices([device])

    assert supported == []
    assert unsupported == [device]
    with patch(
        "custom_components.jd_smart.config_flow.persistent_notification.async_create"
    ) as create_notification:
        _notify_unsupported_devices(hass, unsupported)

    message = create_notification.call_args.args[1]
    assert "category_id=999999" in message
    assert "config_type=42" in message
    assert PULL_REQUEST_URL in message


async def test_selecting_only_unsupported_device_keeps_flow_open(hass) -> None:
    """Show the unsupported-profile error after notifying the user."""
    device = JdSmartDevice(
        feed_id="feed-id",
        name="Unsupported device",
        category_id="999999",
        config_type="42",
    )
    flow = JdSmartAcConfigFlow()
    flow.hass = hass
    flow._devices = [device]
    flow._auth_data = {}

    with patch(
        "custom_components.jd_smart.config_flow.persistent_notification.async_create"
    ) as create_notification:
        result = await flow.async_step_select_device(
            {CONF_SELECTED_DEVICES: [device.feed_id]}
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "unsupported_device"}
    create_notification.assert_called_once()
