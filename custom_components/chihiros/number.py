"""Regler: Kanäle der Lampe (je Kanal in %) und Dosiermenge je Pumpe."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime, UnitOfVolume
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
        async_add_entities(ChannelNumber(device, i, name) for i, name in enumerate(device.model.channel_names))
        async_add_entities(
            [
                LightSettingNumber(device, "feed_minutes", 1, 120, UnitOfTime.MINUTES),
                LightSettingNumber(device, "sun_ramp", 5, 240, UnitOfTime.MINUTES),
                LightSettingNumber(device, "acclim_start", 10, 100, PERCENTAGE),
                LightSettingNumber(device, "acclim_weeks", 1, 12, UnitOfTime.WEEKS),
            ]
        )
    elif isinstance(device, ChihirosDoser):
        async_add_entities(DoseVolumeNumber(device, n) for n in range(1, device.pump_count + 1))
        async_add_entities(CapacityNumber(device, n) for n in range(1, device.pump_count + 1))


class ChannelNumber(ChihirosEntity, NumberEntity):
    """Ein Kanal als Schieberegler (GitHub-Issue #129 beim Original: einzelne RGB-Regler)."""

    device: ChihirosLight
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: ChihirosLight, index: int, color: str) -> None:
        super().__init__(device, color)
        self._index = index

    @property
    def native_value(self) -> int | None:
        # Im Auto-Modus der Wert aus dem Tagesablauf, sonst der letzte Befehl von HA (wie das Licht).
        levels = self.device.shown_levels
        return levels[self._index] if levels is not None else None

    async def async_set_native_value(self, value: float) -> None:
        # Vom angezeigten Stand ausgehen, damit die anderen Kanäle beim Wechsel aus Auto nicht auf 0 springen.
        levels = list(self.device.shown_levels or self.device.levels)
        levels[self._index] = round(value)
        await self.device.async_set_levels(tuple(levels))


class DoseVolumeNumber(ChihirosEntity, NumberEntity):
    """Menge, die der Knopf „Dosieren“ abgibt."""

    device: ChihirosDoser
    _attr_native_min_value = 0.2
    _attr_native_max_value = 100
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ChihirosDoser, pump: int) -> None:
        super().__init__(device, "dose_volume", f"dosing_pump_{pump}_dose_volume")
        self._pump = pump
        self._attr_translation_placeholders = {"pump": str(pump)}

    @property
    def available(self) -> bool:
        return True  # reine Einstellung in HA, auch ohne Verbindung änderbar

    @property
    def native_value(self) -> float:
        return self.device.pumps[self._pump - 1].volume

    async def async_set_native_value(self, value: float) -> None:
        self.device.set_volume(self._pump, round(value, 1))


class LightSettingNumber(ChihirosEntity, NumberEntity):
    """Einstellungen von Fütterungslicht, Sonnenverlauf und Eingewöhnung."""

    device: ChihirosLight
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, device: ChihirosLight, key: str, low: int, high: int, unit: str) -> None:
        super().__init__(device, key)
        self._attr_native_min_value, self._attr_native_max_value = low, high
        self._attr_native_unit_of_measurement = unit

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return int(getattr(self.device.settings, self._attr_translation_key or ""))

    async def async_set_native_value(self, value: float) -> None:
        await self.device.async_set_setting(self._attr_translation_key or "", int(value))


class CapacityNumber(ChihirosEntity, NumberEntity):
    """Behältergröße je Kanal (0 = Füllstand nicht verfolgen)."""

    device: ChihirosDoser
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 5000
    _attr_native_step = 10
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_mode = NumberMode.BOX

    def __init__(self, device: ChihirosDoser, pump: int) -> None:
        super().__init__(device, "capacity", f"dosing_pump_{pump}_capacity")
        self._pump = pump
        self._attr_translation_placeholders = {"pump": str(pump)}

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float:
        return self.device.pumps[self._pump - 1].capacity

    async def async_set_native_value(self, value: float) -> None:
        self.device.set_capacity(self._pump, float(value))
