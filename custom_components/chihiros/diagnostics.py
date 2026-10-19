"""Diagnose-Download: Verbindungsstatistik, gespeicherter Zustand, letzte Ereignisse (ohne Bluetooth-Adresse)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ChihirosConfigEntry
from .const import CONF_ADDRESS

# Titel = Bluetooth-Name, der die Adresse enthält (z. B. DYNCRGP…)
TO_REDACT = {CONF_ADDRESS, "title"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ChihirosConfigEntry) -> dict[str, Any]:
    return {
        "entry": async_redact_data({"title": entry.title, "data": dict(entry.data)}, TO_REDACT),
        **entry.runtime_data.diagnostics(),
    }
