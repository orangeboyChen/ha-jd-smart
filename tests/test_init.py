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
    DEVICE_TYPE_SMART_PLUG,
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


async def test_plug_entry_setup_creates_working_switch(hass) -> None:
    """A smart-plug entry sets up and exposes a working power switch."""
    from custom_components.jd_smart.switch import async_setup_entry as setup_switches

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="plug-entry",
        data={
            CONF_COOKIE: "cookie",
            CONF_TGT: "tgt",
            CONF_DEVICES: [
                {
                    CONF_FEED_ID: "plug-feed",
                    CONF_DEVICE_NAME: "Living room plug",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SMART_PLUG,
                    CONF_CATEGORY_ID: "102010",
                    CONF_CATEGORY_NAME: "Socket",
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.credentials.tgt = "tgt"
    client.async_get_snapshot.return_value = JdSmartSnapshot(
        "digest",
        "1",
        True,
        {
            "Power": "0",
            "Mode": "0",
            "ScreenDisplay": "1",
            "CurrentPowerSum": "0",
        },
    )
    client.async_control_streams.return_value = JdSmartSnapshot(
        "digest", "1", True, {"Power": "1", "Mode": "0"}
    )

    added = []

    with (
        patch("custom_components.jd_smart.JdSmartClient", return_value=client),
        patch("custom_components.jd_smart.persistent_notification.async_create"),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        assert await async_setup_entry(hass, entry)
        coordinator = entry.runtime_data.coordinators["plug-feed"]
        await setup_switches(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    switch = added[0]
    assert switch.unique_id == "plug-feed_power"
    assert switch.entity_description.stream_id == "Power"
    assert switch.is_on is False

    with patch.object(
        coordinator, "async_control_streams", new_callable=AsyncMock
    ) as control:
        await switch.async_turn_on()
    control.assert_awaited_once_with({"Power": 1})


def test_plug_entry_does_not_warn_about_stream_layout(hass) -> None:
    """A plug with its Power stream is not reported as an unsupported layout."""
    from custom_components.jd_smart import _is_unsupported_stream_layout
    from custom_components.jd_smart.api import JdSmartSnapshot

    class _Coordinator:
        data = JdSmartSnapshot(
            "digest",
            "1",
            True,
            {"Power": "0", "Mode": "0", "ScreenDisplay": "1", "CurrentPowerSum": "0"},
        )

    device = {CONF_DEVICE_TYPE: DEVICE_TYPE_SMART_PLUG}
    assert not _is_unsupported_stream_layout(device, _Coordinator())

    broken = _Coordinator()
    broken.data = JdSmartSnapshot("digest", "1", True, {"Mode": "0"})
    assert _is_unsupported_stream_layout(device, broken)


async def test_plug_entry_creates_switch_and_power_sensor(hass) -> None:
    """A plug entry exposes exactly one switch and one power sensor."""
    from custom_components.jd_smart.sensor import async_setup_entry as setup_sensors
    from custom_components.jd_smart.switch import async_setup_entry as setup_switches

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="plug-entry-2",
        data={
            CONF_COOKIE: "cookie",
            CONF_TGT: "tgt",
            CONF_DEVICES: [
                {
                    CONF_FEED_ID: "plug-feed",
                    CONF_DEVICE_NAME: "Living room plug",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SMART_PLUG,
                    CONF_CATEGORY_ID: "102010",
                    CONF_CATEGORY_NAME: "Socket",
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.credentials.tgt = "tgt"
    client.async_get_snapshot.return_value = JdSmartSnapshot(
        "digest",
        "1",
        True,
        {"Power": "1", "Mode": "0", "ScreenDisplay": "1", "CurrentPowerSum": "3"},
    )

    switches, sensors = [], []
    with (
        patch("custom_components.jd_smart.JdSmartClient", return_value=client),
        patch("custom_components.jd_smart.persistent_notification.async_create"),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        assert await async_setup_entry(hass, entry)
        await setup_switches(hass, entry, lambda e: switches.extend(e))
        await setup_sensors(hass, entry, lambda e: sensors.extend(e))

    assert len(switches) == 1
    assert len(sensors) == 1
    switch, sensor = switches[0], sensors[0]

    assert switch.unique_id == "plug-feed_power"
    assert sensor.unique_id == "plug-feed_current_power"
    assert switch.unique_id != sensor.unique_id

    assert switch.is_on is True
    assert sensor.native_value == 3.0
    assert sensor.entity_description.native_unit_of_measurement == "W"


async def test_plug_entry_skips_power_sensor_without_stream(hass) -> None:
    """Do not create a power sensor when the plug does not report the stream."""
    from custom_components.jd_smart.sensor import async_setup_entry as setup_sensors

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="plug-entry-3",
        data={
            CONF_COOKIE: "cookie",
            CONF_TGT: "tgt",
            CONF_DEVICES: [
                {
                    CONF_FEED_ID: "plug-feed",
                    CONF_DEVICE_NAME: "Basic plug",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SMART_PLUG,
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.credentials.tgt = "tgt"
    client.async_get_snapshot.return_value = JdSmartSnapshot(
        "digest", "1", True, {"Power": "0"}
    )

    sensors = []
    with (
        patch("custom_components.jd_smart.JdSmartClient", return_value=client),
        patch("custom_components.jd_smart.persistent_notification.async_create"),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        assert await async_setup_entry(hass, entry)
        await setup_sensors(hass, entry, lambda e: sensors.extend(e))

    assert sensors == []


async def test_air_conditioner_gets_no_plug_power_sensor(hass) -> None:
    """The plug power sensor must not leak onto air conditioners."""
    from custom_components.jd_smart.sensor import async_setup_entry as setup_sensors

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="ac-entry",
        data={
            CONF_COOKIE: "cookie",
            CONF_TGT: "tgt",
            CONF_DEVICES: [
                {
                    CONF_FEED_ID: "ac-feed",
                    CONF_DEVICE_NAME: "AC",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_AIR_CONDITIONER,
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.credentials.tgt = "tgt"
    client.async_get_snapshot.return_value = JdSmartSnapshot(
        "digest",
        "0",
        True,
        {"power": "1", "mode": "0", "settemp": "25", "CurrentPowerSum": "3"},
    )

    sensors = []
    with (
        patch("custom_components.jd_smart.JdSmartClient", return_value=client),
        patch("custom_components.jd_smart.persistent_notification.async_create"),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        assert await async_setup_entry(hass, entry)
        await setup_sensors(hass, entry, lambda e: sensors.extend(e))

    keys = {entity.unique_id for entity in sensors}
    assert "ac-feed_current_power" not in keys
