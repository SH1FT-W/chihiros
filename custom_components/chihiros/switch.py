"""Auto-Modus der C II RGB: an = Lampe spielt ihren gespeicherten Tagesablauf, aus = HA steuert die Farbe."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
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
        async_add_entities(
            [
                AutoModeSwitch(device, "auto_mode"),
                LightSettingSwitch(device, "sun_on"),
                LightSettingSwitch(device, "acclim_on"),
            ]
        )
    elif isinstance(device, ChihirosDoser):
        entities: list[SwitchEntity] = [DelaySwitch(device, "dosing_delay")]
        for n in range(1, device.pump_count + 1):
            entities += [PumpSwitch(device, n, "active"), PumpSwitch(device, n, "compensate")]
        async_add_entities(entities)


class AutoModeSwitch(ChihirosEntity, SwitchEntity):
    device: ChihirosLight

    @property
    def is_on(self) -> bool:
        return self.device.auto

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.device.async_set_auto(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_set_auto(False)


class LightSettingSwitch(ChihirosEntity, SwitchEntity):
    """Sonnenverlauf / Eingewöhnung an/aus."""

    device: ChihirosLight
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.device.settings, self._attr_translation_key or ""))

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        if self._attr_translation_key == "acclim_on":
            st = self.device.settings
            return {"since": st.acclim_since, "factor_percent": round(self.device.acclimatization_factor() * 100)}
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.device.async_set_setting(self._attr_translation_key or "", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_set_setting(self._attr_translation_key or "", False)


class DelaySwitch(ChihirosEntity, SwitchEntity):
    """Geräteeinstellung Dosierverzögerung."""

    device: ChihirosDoser
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return self.device.delay

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.device.async_set_delay(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_set_delay(False)


class PumpSwitch(ChihirosEntity, SwitchEntity):
    """Kanal aktiv (an) / pausiert (aus) bzw. verpasste Dosen nachholen."""

    device: ChihirosDoser

    def __init__(self, device: ChihirosDoser, pump: int, what: str) -> None:
        super().__init__(device, f"pump_{what}", f"dosing_pump_{pump}_{what}")
        self._pump, self._what = pump, what
        self._attr_translation_placeholders = {"pump": str(pump)}
        if what == "compensate":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.device.pumps[self._pump - 1], self._what))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.device.async_set_channel_state(self._pump, **{self._what: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_set_channel_state(self._pump, **{self._what: False})
