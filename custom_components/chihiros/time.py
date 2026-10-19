"""Uhrzeiten des Sonnenverlaufs: frühestens hell, spätestens dunkel."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChihirosConfigEntry
from .coordinator import ChihirosLight
from .entity import ChihirosEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ChihirosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    device = entry.runtime_data
    assert isinstance(device, ChihirosLight)
    async_add_entities([SunTime(device, "sun_earliest"), SunTime(device, "sun_latest")])


class SunTime(ChihirosEntity, TimeEntity):
    device: ChihirosLight
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> time:
        minutes = int(getattr(self.device.settings, self._attr_translation_key or ""))
        return time(minutes // 60, minutes % 60)

    async def async_set_value(self, value: time) -> None:
        await self.device.async_set_setting(self._attr_translation_key or "", value.hour * 60 + value.minute)
