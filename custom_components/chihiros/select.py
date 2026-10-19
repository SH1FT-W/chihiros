"""Szenen wie in der App: feste Lichtmischungen für den manuellen Betrieb.

Werte aus „My Chihiros“ 2.8.59 (`SceneSetting.normalScene`, 3 Kanäle – die C II RGB hat keinen eigenen
Eintrag und bekommt in der App genau diese Szenen).
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ChihirosConfigEntry
from .coordinator import ChihirosLight
from .entity import ChihirosEntity
from .models import RGB

PARALLEL_UPDATES = 0

SCHEDULE = "schedule"  # keine feste Mischung: die Lampe spielt ihren Tagesablauf (Auto-Modus)

# Rot, Grün, Blau in %
SCENES: dict[str, tuple[int, int, int]] = {
    "red": (100, 60, 60),
    "green": (80, 100, 100),
    "buce": (81, 65, 100),
    "fish": (80, 40, 61),
    "shrimp": (100, 84, 79),
    "all": (100, 100, 100),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ChihirosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    device = entry.runtime_data
    assert isinstance(device, ChihirosLight)
    if device.model.layout == RGB:  # die App-Szenen gibt es nur für drei Farbkanäle
        async_add_entities([SceneSelect(device, "scene")])


class SceneSelect(ChihirosEntity, SelectEntity):
    device: ChihirosLight
    _attr_options = [*SCENES, SCHEDULE]  # noqa: RUF012 (HA-Konvention)

    @property
    def current_option(self) -> str | None:
        if self.device.auto:
            return SCHEDULE
        if not self.device.known:
            return None
        return next((name for name, levels in SCENES.items() if levels == self.device.levels), None)

    async def async_select_option(self, option: str) -> None:
        if option == SCHEDULE:
            await self.device.async_set_auto(True)
        else:
            await self.device.async_set_levels(SCENES[option])
