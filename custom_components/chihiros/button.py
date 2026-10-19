"""Knopf je Pumpe: einmal die eingestellte Menge dosieren."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChihirosConfigEntry
from .coordinator import ChihirosDoser, ChihirosLight
from .entity import ChihirosEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ChihirosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    device = entry.runtime_data
    if isinstance(device, ChihirosLight):
        async_add_entities([FeedButton(device, "feed")])
        return
    assert isinstance(device, ChihirosDoser)
    async_add_entities(DoseButton(device, n) for n in range(1, device.pump_count + 1))
    async_add_entities(RefillButton(device, n) for n in range(1, device.pump_count + 1))


class DoseButton(ChihirosEntity, ButtonEntity):
    device: ChihirosDoser

    def __init__(self, device: ChihirosDoser, pump: int) -> None:
        super().__init__(device, "dose", f"dosing_pump_{pump}_dose")
        self._pump = pump
        self._attr_translation_placeholders = {"pump": str(pump)}

    async def async_press(self) -> None:
        await self.device.async_dose(self._pump)


class RefillButton(ChihirosEntity, ButtonEntity):
    """Behälter wurde aufgefüllt – Füllstand wieder auf voll."""

    device: ChihirosDoser

    def __init__(self, device: ChihirosDoser, pump: int) -> None:
        super().__init__(device, "refill", f"dosing_pump_{pump}_refill")
        self._pump = pump
        self._attr_translation_placeholders = {"pump": str(pump)}

    @property
    def available(self) -> bool:
        return self.device.pumps[self._pump - 1].capacity > 0

    async def async_press(self) -> None:
        self.device.refill(self._pump)


class FeedButton(ChihirosEntity, ButtonEntity):
    """Fütterungslicht: kurz hell, danach zurück."""

    device: ChihirosLight

    async def async_press(self) -> None:
        await self.device.async_feed()
