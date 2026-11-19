"""Sensor platform for JD Smart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JdSmartConfigEntry
from .const import (
    DEVICE_TYPE_AIR_CONDITIONER,
    DEVICE_TYPE_SMART_PLUG,
    resolve_stream_id,
)
from .entity import JdSmartEntity


@dataclass(frozen=True, kw_only=True)
class JdSmartSensorDescription(SensorEntityDescription):
    """JD Smart sensor description."""

    stream_id: str


SENSORS: tuple[JdSmartSensorDescription, ...] = (
    JdSmartSensorDescription(
        key="curtemp",
        stream_id="curtemp",
        translation_key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    JdSmartSensorDescription(
        key="curhum",
        stream_id="curhum",
        translation_key="current_humidity",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    JdSmartSensorDescription(
        key="tvoc",
        stream_id="tvoc",
        translation_key="tvoc",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JdSmartSensorDescription(
        key="time_sum",
        stream_id="time_sum",
        translation_key="runtime_total",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JdSmartSensorDescription(
        key="time_clr",
        stream_id="time_clr",
        translation_key="clean_runtime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JdSmartSensorDescription(
        key="speaker",
        stream_id="speaker",
        translation_key="speaker_raw",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JdSmartSensorDescription(
        key="mdpmode",
        stream_id="mdpmode",
        translation_key="mdp_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JdSmartSensorDescription(
        key="ptcheat",
        stream_id="ptcheat",
        translation_key="protection_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


# Smart plugs report instantaneous active power in watts. The stream is named
# ``CurrentPowerSum`` although it fluctuates with the load and is not a running
# total, so it is exposed as a power measurement rather than an energy counter.
# The entity key is ``current_power`` because ``power`` already belongs to the
# plug's switch entity; a duplicate key would collide on the unique id.
PLUG_SENSORS: tuple[JdSmartSensorDescription, ...] = (
    JdSmartSensorDescription(
        key="current_power",
        stream_id="CurrentPowerSum",
        translation_key="current_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JdSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up JD Smart sensors."""
    entities: list[JdSmartSensor] = []
    for coordinator in entry.runtime_data.coordinators.values():
        if coordinator.device_type == DEVICE_TYPE_AIR_CONDITIONER:
            descriptions = SENSORS
        elif coordinator.device_type == DEVICE_TYPE_SMART_PLUG:
            descriptions = PLUG_SENSORS
        else:
            continue
        streams = coordinator.data.streams if coordinator.data else None
        entities.extend(
            JdSmartSensor(coordinator, description)
            for description in descriptions
            if streams is None
            or resolve_stream_id(streams, description.stream_id) is not None
        )
    async_add_entities(entities)


class JdSmartSensor(JdSmartEntity, SensorEntity):
    """JD Smart stream sensor."""

    entity_description: JdSmartSensorDescription

    def __init__(
        self,
        coordinator,
        description: JdSmartSensorDescription,
    ) -> None:
        """Initialize sensor."""
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
    def native_value(self) -> str | float | None:
        """Return sensor value."""
        value = self.streams.get(self._stream_id)
        if value is None or value == "":
            return None
        if self.entity_description.state_class is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the raw value for power sensors so scaling is auditable."""
        if self.entity_description.device_class is not SensorDeviceClass.POWER:
            return None
        value = self.streams.get(self._stream_id)
        if value is None:
            return None
        return {"raw_value": value, "stream_id": self._stream_id}
