"""Switch platform for JD Smart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JdSmartConfigEntry
from .const import DEVICE_TYPE_AIR_CONDITIONER
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JdSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up JD Smart switches."""
    async_add_entities(
        JdSmartSwitch(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        if coordinator.device_type == DEVICE_TYPE_AIR_CONDITIONER
        for description in SWITCHES
        if (
            coordinator.data is None
            or description.stream_id in coordinator.data.streams
        )
    )


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
    def is_on(self) -> bool | None:
        """Return switch state."""
        value = self.streams.get(self.entity_description.stream_id)
        if value == "":
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
            await self.coordinator.async_control_streams(
                {self.entity_description.stream_id: value}
            )
        except Exception as err:
            raise HomeAssistantError("Unable to control JD Smart") from err
