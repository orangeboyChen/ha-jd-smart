"""Base entities for JD Smart."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_MANUFACTURER, DOMAIN
from .coordinator import JdSmartCoordinator


class JdSmartEntity(CoordinatorEntity[JdSmartCoordinator]):
    """Base JD Smart entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: JdSmartCoordinator, key: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.feed_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.feed_id)},
            manufacturer=ATTR_MANUFACTURER,
            name=coordinator.device_name or f"JD Smart {coordinator.feed_id}",
        )

    @property
    def streams(self) -> dict[str, str]:
        """Return latest streams."""
        return self.coordinator.data.streams if self.coordinator.data else {}
