"""Die Lampe als ein Licht – so wie die App: je Kanal 0–100 %, 1 bis 4 Kanäle je nach Modell.

Farbmodus: RGB (drei Farbkanäle), RGBW (Rot/Grün/Blau/Weiß) oder nur Helligkeit (Weiß, Weiß/Warmweiß,
Rot/Grün – das Mischverhältnis bleibt, einzelne Kanäle über die Regler).
HA-Regel: Kanal % = Farbe / 255 × Helligkeit / 255 × 100 (gerundet). Die Kanäle direkt setzen geht über
die Regler bzw. `chihiros.set_channels`. Ohne Farbe/Helligkeit beim Einschalten: letzte eingeschaltete
Mischung (nie ein Sprung auf 100 % Weiß).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChihirosConfigEntry
from .const import DOMAIN
from .coordinator import ChihirosLight
from .entity import ChihirosEntity
from .models import RGB, WRGB

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ChihirosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    device = entry.runtime_data
    assert isinstance(device, ChihirosLight)
    async_add_entities([ChihirosLightEntity(device)])


def scale(color: tuple[int, ...], brightness: int) -> tuple[int, ...]:
    return tuple(max(0, min(100, round(c * brightness / 255 / 255 * 100))) for c in color)


def _unique_suffix(device: ChihirosLight) -> str:
    """`rgb`/`rgbw` wie beim Original, einkanalig der Kanalname (Original: ein Licht je Kanal)."""
    layout = device.model.layout
    if layout == RGB:
        return "rgb"
    if layout == WRGB:
        return "rgbw"
    return device.model.channel_names[0] if len(layout) == 1 else "light"


class ChihirosLightEntity(ChihirosEntity, LightEntity):
    device: ChihirosLight
    _attr_name = None  # Hauptfunktion: heißt wie das Gerät
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(self, device: ChihirosLight) -> None:
        super().__init__(device, "light", _unique_suffix(device))
        layout = device.model.layout
        self._color_key: str | None = None
        if layout == RGB:
            self._attr_color_mode, self._color_key = ColorMode.RGB, ATTR_RGB_COLOR
        elif layout == WRGB:
            self._attr_color_mode, self._color_key = ColorMode.RGBW, ATTR_RGBW_COLOR
        else:
            self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_supported_color_modes = {self._attr_color_mode}

    @property
    def assumed_state(self) -> bool:
        # Die Lampe meldet ihre Kanalwerte nicht zurück – nach einem Befehl von HA ist der Zustand aber sicher.
        return not self.device.known

    @property
    def is_on(self) -> bool | None:
        levels = self.device.shown_levels
        if levels is None:
            return True if self.device.auto else None  # Auto mit unbekanntem Ablauf (z. B. aus der App)
        return any(levels)

    @property
    def brightness(self) -> int | None:
        top = max(self.device.shown_levels or (), default=0)
        return round(top * 2.55) if top else None

    def _color(self) -> tuple[int, ...] | None:
        levels = self.device.shown_levels or ()
        top = max(levels, default=0)
        if not top:
            return None
        return tuple(round(v * 255 / top) for v in levels)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        color = self._color() if self._attr_color_mode == ColorMode.RGB else None
        return (color[0], color[1], color[2]) if color else None

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        color = self._color() if self._attr_color_mode == ColorMode.RGBW else None
        return (color[0], color[1], color[2], color[3]) if color else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self.device
        return {
            "auto_mode": dev.auto,
            "channels": dict(zip(dev.model.channel_names, dev.shown_levels or (0,) * dev.channels, strict=False)),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        dev = self.device
        current = dev.levels if any(dev.levels) else (dev.last_on or (100,) * dev.channels)
        top = max(current)
        color: tuple[int, ...] = kwargs.get(self._color_key or "") or tuple(round(v * 255 / top) for v in current)
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(kwargs[ATTR_BRIGHTNESS])
        elif self._color_key in kwargs:
            brightness = round(top * 2.55) or 255
        else:
            await dev.async_set_levels(current, kwargs.get(ATTR_TRANSITION))
            return
        await dev.async_set_levels(scale(color, brightness), kwargs.get(ATTR_TRANSITION))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_set_levels((0,) * self.device.channels, kwargs.get(ATTR_TRANSITION))

    async def async_set_channels(self, transition: float | None = None, **channels: int) -> None:
        """Dienst chihiros.set_channels: Kanäle in %, fehlende bleiben."""
        names = self.device.model.channel_names
        if unknown := set(channels) - set(names):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_channel",
                translation_placeholders={"channel": ", ".join(sorted(unknown)), "channels": ", ".join(names)},
            )
        levels = list(self.device.levels)
        for i, name in enumerate(names):
            if name in channels:
                levels[i] = channels[name]
        await self.device.async_set_levels(tuple(levels), transition)

    async def async_set_weekday_schedule(self, settings: list[dict[str, Any]]) -> None:
        """Dienst chihiros.set_weekday_schedule: Abläufe je Wochentag wie die App."""
        await self.device.async_set_weekday_schedule(settings)

    async def async_set_curve(self, points: list[dict[str, Any]], activate: bool = True) -> None:
        """Dienst chihiros.set_auto_curve: Tagesablauf in die Lampe schreiben (fehlende Kanäle = 0)."""
        names = self.device.model.channel_names
        await self.device.async_set_curve([(p["time"], tuple(p.get(n, 0) for n in names)) for p in points], activate)
