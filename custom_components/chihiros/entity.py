"""Basis aller Entitäten: unique_id = `<MAC>_<key>` wie beim Original, Gerät über die Bluetooth-Adresse."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .coordinator import ChihirosDevice


class ChihirosEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: ChihirosDevice, key: str, unique_suffix: str | None = None) -> None:
        self.device = device
        self._attr_translation_key = key
        self._attr_unique_id = f"{device.address}_{unique_suffix or key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, device.address)},
            name=device.name,
            manufacturer="Chihiros",
            model=device.model_name,
            sw_version=str(device.firmware) if device.firmware else None,
        )

    @property
    def available(self) -> bool:
        return self.device.present

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.device.async_add_listener(self.async_write_ha_state))
