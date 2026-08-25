"""Climate platform for JD Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JdSmartConfigEntry
from .const import AIR_CONDITIONER_REQUIRED_STREAMS, DEVICE_TYPE_AIR_CONDITIONER
from .entity import JdSmartEntity

MODE_TO_HVAC = {
    "0": HVACMode.COOL,
    "1": HVACMode.HEAT,
    "2": HVACMode.DRY,
    "3": HVACMode.FAN_ONLY,
    "4": HVACMode.AUTO,
}
HVAC_TO_MODE = {value: key for key, value in MODE_TO_HVAC.items()}

FAN_TO_VALUE = {
    "silent": "0",
    "low": "1",
    "medium": "2",
    "high": "3",
    "auto": "5",
}
VALUE_TO_FAN = {value: key for key, value in FAN_TO_VALUE.items()}

SWING_TO_VALUE = {
    "swing": "0",
    "auto": "1",
    "direction_1": "2",
    "direction_2": "3",
    "direction_3": "4",
    "direction_4": "5",
    "direction_5": "6",
    "direction_6": "7",
}
VALUE_TO_SWING = {value: key for key, value in SWING_TO_VALUE.items()}

PRESET_TO_VALUE = {
    "off": "0",
    "normal": "1",
    "elderly": "2",
    "youth": "3",
    "child": "4",
}
VALUE_TO_PRESET = {value: key for key, value in PRESET_TO_VALUE.items()}
CLIMATE_SUPPORTED_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.PRESET_MODE
    | ClimateEntityFeature.SWING_MODE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JdSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up JD Smart climate."""
    async_add_entities(
        JdSmartClimate(coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
        if coordinator.device_type == DEVICE_TYPE_AIR_CONDITIONER
        if (
            coordinator.data is None
            or AIR_CONDITIONER_REQUIRED_STREAMS <= coordinator.data.streams.keys()
        )
    )


class JdSmartClimate(JdSmartEntity, ClimateEntity):
    """JD Smart climate entity."""

    _attr_name = None
    _attr_supported_features = CLIMATE_SUPPORTED_FEATURES
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 18
    _attr_max_temp = 32
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    _attr_fan_modes = list(FAN_TO_VALUE)
    _attr_preset_modes = list(PRESET_TO_VALUE)
    _attr_swing_modes = list(SWING_TO_VALUE)
    _attr_translation_key = "air_conditioner"

    def __init__(self, coordinator) -> None:
        """Initialize climate."""
        super().__init__(coordinator, "climate")
        streams = coordinator.data.streams if coordinator.data else None
        self._attr_supported_features = _supported_features(streams)
        if streams is not None:
            if "mark" not in streams:
                self._attr_fan_modes = None
            if "sleepmode" not in streams:
                self._attr_preset_modes = None
            if "verdir" not in streams:
                self._attr_swing_modes = None

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        return _float_or_none(self.streams.get("curtemp"))

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        return _float_or_none(self.streams.get("settemp"))

    @property
    def current_humidity(self) -> float | None:
        """Return current humidity."""
        return _float_or_none(self.streams.get("curhum"))

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return HVAC mode."""
        if self.streams.get("power") == "0":
            return HVACMode.OFF
        return MODE_TO_HVAC.get(self.streams.get("mode", ""))

    @property
    def fan_mode(self) -> str | None:
        """Return fan mode."""
        return VALUE_TO_FAN.get(self.streams.get("mark", ""))

    @property
    def swing_mode(self) -> str | None:
        """Return swing mode."""
        return VALUE_TO_SWING.get(self.streams.get("verdir", ""))

    @property
    def preset_mode(self) -> str | None:
        """Return preset mode."""
        return VALUE_TO_PRESET.get(self.streams.get("sleepmode", ""))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._control({"settemp": int(temperature)})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._control({"power": 0})
            return
        mode = HVAC_TO_MODE[hvac_mode]
        await self._control({"power": 1, "mode": int(mode)})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        await self._control({"mark": int(FAN_TO_VALUE[fan_mode])})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set swing mode."""
        await self._control({"verdir": int(SWING_TO_VALUE[swing_mode])})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        await self._control({"sleepmode": int(PRESET_TO_VALUE[preset_mode])})

    async def _control(self, commands: dict[str, object]) -> None:
        """Control helper."""
        try:
            await self.coordinator.async_control_streams(commands)
        except Exception as err:
            raise HomeAssistantError("Unable to control JD Smart") from err


def _supported_features(streams: dict[str, str] | None) -> ClimateEntityFeature:
    """Return the climate features supported by available streams."""
    if streams is None:
        return CLIMATE_SUPPORTED_FEATURES

    features = ClimateEntityFeature.TARGET_TEMPERATURE
    if "mark" in streams:
        features |= ClimateEntityFeature.FAN_MODE
    if "sleepmode" in streams:
        features |= ClimateEntityFeature.PRESET_MODE
    if "verdir" in streams:
        features |= ClimateEntityFeature.SWING_MODE
    return features


def _float_or_none(value: str | None) -> float | None:
    """Convert a value to float."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None
