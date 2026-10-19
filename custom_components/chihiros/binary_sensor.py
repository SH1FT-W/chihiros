"""Erreichbar: sieht ein Bluetooth-Proxy das Gerät, und klappte der letzte Befehl?"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChihirosConfigEntry
from .entity import ChihirosEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ChihirosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ReachableSensor(entry.runtime_data, "reachable")])


class ReachableSensor(ChihirosEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.device.present and self.device.link.last_error is None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        link = self.device.link
        return {
            "seen_by_proxy": self.device.present,
            "connected": link.connected,
            "connects": link.connects,
            "failures": link.failures,
            "last_error": link.last_error,
        }
