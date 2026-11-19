"""Switch platform for JD Smart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JdSmartConfigEntry
from .const import (
    DEVICE_TYPE_AIR_CONDITIONER,
    DEVICE_TYPE_SMART_PLUG,
    resolve_stream_id,
)
from .entity import JdSmartEntity


@dataclass(frozen=True, kw_only=True)
class JdSmartSwitchDescription(SwitchEntityDescription):
    """JD Smart switch description."""

    stream_id: str


SWITCHES: tuple[JdSmartSwitchDescription, ...] = (
    JdSmartSwitchDescription(
        key="bglight", stream_id="bglight", translation_key="backlight"
    ),
    JdSmartSwitchDescription(
        key="scrdispaly", stream_id="scrdispaly", translation_key="display"
    ),
    JdSmartSwitchDescription(
        key="ecomode", stream_id="ecomode", translation_key="powerful"
    ),
)

# Smart plugs expose a single binary mains switch. The appliance reports it as
# ``Power`` (title case) while air conditioners use lowercase ``power``, so the
# plug description carries the exact stream id seen in its snapshot.
PLUG_SWITCHES: tuple[JdSmartSwitchDescription, ...] = (
    JdSmartSwitchDescription(
        key="power", stream_id="Power", translation_key="power"
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JdSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up JD Smart switches."""
    entities: list[JdSmartSwitch] = []
    for coordinator in entry.runtime_data.coordinators.values():
        if coordinator.device_type == DEVICE_TYPE_AIR_CONDITIONER:
            descriptions = SWITCHES
        elif coordinator.device_type == DEVICE_TYPE_SMART_PLUG:
            descriptions = PLUG_SWITCHES
        else:
            continue
        streams = coordinator.data.streams if coordinator.data else None
        entities.extend(
            JdSmartSwitch(coordinator, description)
            for description in descriptions
            if streams is None
            or resolve_stream_id(streams, description.stream_id) is not None
        )
    async_add_entities(entities)


class JdSmartSwitch(JdSmartEntity, SwitchEntity):
    """JD Smart stream switch."""

    entity_description: JdSmartSwitchDescription

    def __init__(
        self,
        coordinator,
        description: JdSmartSwitchDescription,
    ) -> None:
        """Initialize switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def _stream_id(self) -> str:
        """Return the snapshot stream id, matching case-insensitively."""
        return (
            resolve_stream_id(self.streams, self.entity_description.stream_id)
            or self.entity_description.stream_id
        )

    @property
    def is_on(self) -> bool | None:
        """Return switch state."""
        value = self.streams.get(self._stream_id)
        if value in (None, ""):
            return None
        return value == "1"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn switch on."""
        await self._control(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn switch off."""
        await self._control(0)

    async def _control(self, value: int) -> None:
        """Control helper."""
        try:
            await self.coordinator.async_control_streams({self._stream_id: value})
        except Exception as err:
            raise HomeAssistantError("Unable to control JD Smart") from err

