"""Tests for JD Smart server device profiles."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.climate import ClimateEntityFeature

from custom_components.jd_smart.api import JdSmartDevice, _parse_devices
from custom_components.jd_smart.climate import JdSmartClimate, _supported_features
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
    DEVICE_TYPE_SMART_PLUG,
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


def test_parse_device_profile_from_grouped_server_response() -> None:
    """Inherit category metadata from the platform grouping when needed."""
    devices = _parse_devices(
        {
            "platform_list": [
                {
                    "category_id": 101001,
                    "category_name": "Air conditioner",
                    "cards": [
                        {
                            "feed_id": "feed-id",
                            "card_name": "Air conditioner",
                            "config_type": 1113,
                        }
                    ],
                }
            ]
        }
    )

    assert devices[0].category_id == "101001"
    assert devices[0].category_name == "Air conditioner"


def test_supported_category_accepts_multiple_card_configurations() -> None:
    """Route an air conditioner without depending on its card configuration."""
    device = JdSmartDevice(
        feed_id="feed-id",
        name="Air conditioner",
        category_id="101001",
        config_type="another-card-configuration",
    )

    assert _device_type(device) == DEVICE_TYPE_AIR_CONDITIONER
    assert _entry_device(device) == {
        "feed_id": "feed-id",
        "device_name": "Air conditioner",
        CONF_CATEGORY_ID: "101001",
        "category_name": "",
        CONF_CONFIG_TYPE: "another-card-configuration",
        "detail_type": "",
        CONF_DEVICE_TYPE: DEVICE_TYPE_AIR_CONDITIONER,
    }


def test_unsupported_category_creates_pull_request_notification(hass) -> None:
    """Guide users to contribute support for an unknown device category."""
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
    """Show the unsupported-category error after notifying the user."""
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


def test_climate_features_follow_available_optional_streams() -> None:
    """Do not expose controls for unavailable climate streams."""
    features = _supported_features({"power": "1", "mode": "0", "settemp": "25"})

    assert features == (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )


async def test_climate_turn_on_uses_mode_from_remote_snapshot() -> None:
    """Power on with the last mode reported by the device, even while off."""
    coordinator = MagicMock()
    coordinator.feed_id = "feed-id"
    coordinator.device_name = "Air conditioner"
    coordinator.data.streams = {"power": "0", "mode": "2", "settemp": "25"}
    coordinator.async_control_streams = AsyncMock()
    climate = JdSmartClimate(coordinator)

    await climate.async_turn_on()

    coordinator.async_control_streams.assert_awaited_once_with({"power": 1, "mode": 2})


async def test_climate_turn_off_sends_only_power() -> None:
    """Power off leaves the selected mode stream alone."""
    coordinator = MagicMock()
    coordinator.feed_id = "feed-id"
    coordinator.device_name = "Air conditioner"
    coordinator.data.streams = {"power": "1", "mode": "2", "settemp": "25"}
    coordinator.async_control_streams = AsyncMock()
    climate = JdSmartClimate(coordinator)

    await climate.async_turn_off()

    coordinator.async_control_streams.assert_awaited_once_with({"power": 0})


async def test_climate_turn_on_uses_auto_without_remote_mode() -> None:
    """Use auto when the device snapshot has no recognized mode."""
    coordinator = MagicMock()
    coordinator.feed_id = "feed-id"
    coordinator.device_name = "Air conditioner"
    coordinator.data.streams = {"power": "0", "mode": "", "settemp": "25"}
    coordinator.async_control_streams = AsyncMock()
    climate = JdSmartClimate(coordinator)

    await climate.async_turn_on()

    coordinator.async_control_streams.assert_awaited_once_with({"power": 1, "mode": 4})


def test_smart_plug_category_is_recognized() -> None:
    """Route server category 102010 to the smart-plug device type."""
    device = JdSmartDevice(
        feed_id="plug-feed-id",
        name="Living room plug",
        category_id="102010",
        category_name="Socket",
        config_type="1115",
    )

    assert _device_type(device) == DEVICE_TYPE_SMART_PLUG
    assert _entry_device(device)[CONF_DEVICE_TYPE] == DEVICE_TYPE_SMART_PLUG


def test_plug_and_air_conditioner_are_both_supported() -> None:
    """A plug and an air conditioner can be added in one selection."""
    plug = JdSmartDevice(
        feed_id="plug-feed-id", name="Plug", category_id="102010"
    )
    air_conditioner = JdSmartDevice(
        feed_id="ac-feed-id", name="AC", category_id="101001"
    )

    supported, unsupported = _split_supported_devices([plug, air_conditioner])

    assert [device.feed_id for device in supported] == ["plug-feed-id", "ac-feed-id"]
    assert unsupported == []


def test_required_streams_match_case_insensitively() -> None:
    """Match the plug's title-case stream ids against lowercase requirements."""
    from custom_components.jd_smart.const import (
        AIR_CONDITIONER_REQUIRED_STREAMS,
        SMART_PLUG_REQUIRED_STREAMS,
        has_required_streams,
    )

    plug_streams = {
        "Power": "1",
        "Mode": "0",
        "ScreenDisplay": "1",
        "CurrentPowerSum": "0",
    }
    assert has_required_streams(plug_streams, SMART_PLUG_REQUIRED_STREAMS)
    assert not has_required_streams(plug_streams, AIR_CONDITIONER_REQUIRED_STREAMS)
    assert has_required_streams(
        {"POWER": "1", "power": "1"}, SMART_PLUG_REQUIRED_STREAMS
    )


async def test_plug_switch_controls_title_case_power_stream() -> None:
    """The plug switch reads and writes the exact ``Power`` stream id."""
    from custom_components.jd_smart.switch import PLUG_SWITCHES, JdSmartSwitch

    coordinator = MagicMock()
    coordinator.feed_id = "plug-feed-id"
    coordinator.device_name = "Plug"
    coordinator.data.streams = {"Power": "1", "Mode": "0"}
    coordinator.async_control_streams = AsyncMock()
    switch = JdSmartSwitch(coordinator, PLUG_SWITCHES[0])

    assert switch.is_on is True

    coordinator.data.streams = {"Power": "0", "Mode": "0"}
    assert switch.is_on is False

    await switch.async_turn_on()
    coordinator.async_control_streams.assert_awaited_once_with({"Power": 1})

    coordinator.async_control_streams.reset_mock()
    await switch.async_turn_off()
    coordinator.async_control_streams.assert_awaited_once_with({"Power": 0})


async def test_plug_switch_tolerates_lowercase_snapshot() -> None:
    """Fall back to a lower-case snapshot key if the cloud changes casing."""
    from custom_components.jd_smart.switch import PLUG_SWITCHES, JdSmartSwitch

    coordinator = MagicMock()
    coordinator.feed_id = "plug-feed-id"
    coordinator.device_name = "Plug"
    coordinator.data.streams = {"power": "1"}
    coordinator.async_control_streams = AsyncMock()
    switch = JdSmartSwitch(coordinator, PLUG_SWITCHES[0])

    assert switch.is_on is True
    await switch.async_turn_off()
    coordinator.async_control_streams.assert_awaited_once_with({"power": 0})


def test_translations_define_plug_power_switch() -> None:
    """Every translation exposes `power` as a sibling of `powerful`."""
    import json
    from pathlib import Path

    base = Path("custom_components/jd_smart")
    files = [
        base / "strings.json",
        base / "translations" / "en.json",
        base / "translations" / "zh-Hans.json",
    ]
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        switches = data["entity"]["switch"]
        assert "power" in switches, f"{path} missing switch.power"
        assert switches["power"]["name"], f"{path} has an empty switch.power name"
        # `power` must not be nested inside `powerful`
        assert "power" not in switches["powerful"], f"{path} nested power in powerful"
        assert switches["powerful"]["name"], f"{path} broke the powerful label"


def test_plug_sensor_uses_distinct_key_from_power_switch() -> None:
    """The power sensor must not reuse the switch's `power` entity key."""
    from custom_components.jd_smart.sensor import PLUG_SENSORS
    from custom_components.jd_smart.switch import PLUG_SWITCHES

    sensor_keys = {description.key for description in PLUG_SENSORS}
    switch_keys = {description.key for description in PLUG_SWITCHES}

    assert "current_power" in sensor_keys
    assert sensor_keys.isdisjoint(switch_keys), (
        f"plug sensor keys {sensor_keys} collide with switch keys {switch_keys}"
    )


def test_plug_power_sensor_describes_watts() -> None:
    """CurrentPowerSum is instantaneous power in watts, not energy."""
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from homeassistant.const import UnitOfPower

    from custom_components.jd_smart.sensor import PLUG_SENSORS

    description = next(d for d in PLUG_SENSORS if d.key == "current_power")
    assert description.stream_id == "CurrentPowerSum"
    assert description.device_class is SensorDeviceClass.POWER
    assert description.state_class is SensorStateClass.MEASUREMENT
    assert description.native_unit_of_measurement == UnitOfPower.WATT


async def test_plug_power_sensor_reads_live_watts() -> None:
    """The power sensor converts the raw stream into a float and keeps the raw value."""
    from custom_components.jd_smart.sensor import PLUG_SENSORS, JdSmartSensor

    coordinator = MagicMock()
    coordinator.feed_id = "plug-feed"
    coordinator.device_name = "Plug"
    coordinator.data.streams = {"Power": "1", "CurrentPowerSum": "3"}

    description = next(d for d in PLUG_SENSORS if d.key == "current_power")
    sensor = JdSmartSensor(coordinator, description)

    assert sensor.unique_id == "plug-feed_current_power"
    assert sensor.native_value == 3.0
    assert sensor.extra_state_attributes == {
        "raw_value": "3",
        "stream_id": "CurrentPowerSum",
    }

    coordinator.data.streams = {"Power": "1", "CurrentPowerSum": "17"}
    assert sensor.native_value == 17.0


async def test_plug_power_sensor_handles_missing_and_bad_values() -> None:
    """A missing or non-numeric stream reports unknown instead of raising."""
    from custom_components.jd_smart.sensor import PLUG_SENSORS, JdSmartSensor

    coordinator = MagicMock()
    coordinator.feed_id = "plug-feed"
    coordinator.device_name = "Plug"
    description = next(d for d in PLUG_SENSORS if d.key == "current_power")

    coordinator.data.streams = {"Power": "1"}
    sensor = JdSmartSensor(coordinator, description)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None

    coordinator.data.streams = {"Power": "1", "CurrentPowerSum": ""}
    assert sensor.native_value is None

    coordinator.data.streams = {"Power": "1", "CurrentPowerSum": "n/a"}
    assert sensor.native_value is None


async def test_plug_sensor_tolerates_lowercase_stream() -> None:
    """Fall back to a differently cased stream id if the cloud changes it."""
    from custom_components.jd_smart.sensor import PLUG_SENSORS, JdSmartSensor

    coordinator = MagicMock()
    coordinator.feed_id = "plug-feed"
    coordinator.device_name = "Plug"
    coordinator.data.streams = {"power": "1", "currentpowersum": "5"}

    description = next(d for d in PLUG_SENSORS if d.key == "current_power")
    sensor = JdSmartSensor(coordinator, description)

    assert sensor.native_value == 5.0
    assert sensor.extra_state_attributes["stream_id"] == "currentpowersum"
