"""Lüfter der WRGB VIVID III: Geschwindigkeit in % oder automatisch nach Temperatur (wie die App)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChihirosConfigEntry
from .coordinator import ChihirosLight
from .entity import ChihirosEntity

PARALLEL_UPDATES = 0

AUTO, MANUAL = "auto", "manual"


async def async_setup_entry(
    hass: HomeAssistant, entry: ChihirosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    device = entry.runtime_data
    if isinstance(device, ChihirosLight) and device.model.fan:
        async_add_entities([ChihirosFan(device, "fan")])


class ChihirosFan(ChihirosEntity, FanEntity):
    """Der Lüfter meldet nur Drehzahl und Temperatur (eigene Sensoren); Modus und Stufe merkt sich HA."""

    device: ChihirosLight
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.PRESET_MODE | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )
    _attr_preset_modes = [AUTO, MANUAL]  # noqa: RUF012 (HA-Konvention)

    @property
    def is_on(self) -> bool:
        return self.device.fan_auto or self.device.fan_speed > 0

    @property
    def percentage(self) -> int | None:
        return None if self.device.fan_auto else self.device.fan_speed

    @property
    def preset_mode(self) -> str:
        return AUTO if self.device.fan_auto else MANUAL

    async def async_set_percentage(self, percentage: int) -> None:
        await self.device.async_set_fan_speed(percentage)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == AUTO:
            await self.device.async_set_fan_auto()
        else:
            await self.device.async_set_fan_speed(self.device.fan_last_speed)

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        else:
            await self.device.async_set_fan_speed(percentage or self.device.fan_last_speed)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_set_fan_speed(0)
