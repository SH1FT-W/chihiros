"""Einrichtung über Bluetooth-Erkennung (oder Auswahl aus den gesehenen Geräten)."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir, selector
import voluptuous as vol

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_TYPE,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_PERCENT,
    CONF_PUMP_COUNT,
    DEFAULT_NOTIFY_PERCENT,
    DEFAULT_PUMPS,
    DOMAIN,
)
from .models import DEVICE_TYPES, DOSER_PREFIXES, detect_light, supported

__all__ = ["ChihirosConfigFlow", "supported"]


def is_doser_name(name: str) -> bool:
    return name.upper().startswith(DOSER_PREFIXES)


def needs_type(name: str) -> bool:
    """Commander 1: die Belegung (Weiß, RGB, WRGB) kennt nur der Nutzer."""
    model = detect_light(name)
    return bool(model and model.needs_type)


_DEVICE_TYPE = selector.SelectSelector(
    selector.SelectSelectorConfig(options=list(DEVICE_TYPES), translation_key="device_type")
)


class ChihirosConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DoserOptionsFlow:
        return DoserOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Optionen (Push bei leerem Behälter) nur für Dosierpumpen."""
        return is_doser_name(config_entry.title) or CONF_PUMP_COUNT in config_entry.data

    def __init__(self) -> None:
        self._info: BluetoothServiceInfoBleak | None = None
        self._found: dict[str, str] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        if not supported(discovery_info.name):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._info is not None
        return await self._create(self._info.address.upper(), self._info.name, user_input, "confirm")

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None and CONF_ADDRESS in user_input and user_input[CONF_ADDRESS] in self._found:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return await self._create(address, self._found[address], user_input, "user")
        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            address = info.address.upper()
            if supported(info.name) and address not in current:
                self._found[address] = info.name
        if not self._found:
            return self.async_abort(reason="no_devices_found")
        schema: dict[Any, Any] = {vol.Required(CONF_ADDRESS): vol.In({a: f"{n} ({a})" for a, n in self._found.items()})}
        if any(is_doser_name(n) for n in self._found.values()):
            schema[vol.Optional(CONF_PUMP_COUNT, default=DEFAULT_PUMPS)] = vol.All(vol.Coerce(int), vol.Range(1, 8))
        if any(needs_type(n) for n in self._found.values()):
            schema[vol.Optional(CONF_DEVICE_TYPE, default="wrgb")] = _DEVICE_TYPE
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema))

    async def _create(self, address: str, name: str, user_input: dict[str, Any] | None, step: str) -> ConfigFlowResult:
        doser = is_doser_name(name)
        typed = needs_type(name)
        if user_input is not None:
            data: dict[str, Any] = {CONF_ADDRESS: address, CONF_NAME: name}
            if doser:
                data[CONF_PUMP_COUNT] = int(user_input.get(CONF_PUMP_COUNT, DEFAULT_PUMPS))
            if typed:
                data[CONF_DEVICE_TYPE] = user_input.get(CONF_DEVICE_TYPE, "wrgb")
            return self.async_create_entry(title=name, data=data)
        fields: dict[Any, Any] = {}
        if doser:
            fields[vol.Optional(CONF_PUMP_COUNT, default=DEFAULT_PUMPS)] = vol.All(vol.Coerce(int), vol.Range(1, 8))
        if typed:
            fields[vol.Optional(CONF_DEVICE_TYPE, default="wrgb")] = _DEVICE_TYPE
        schema = vol.Schema(fields)
        if not typed:
            self._set_confirm_only()
        return self.async_show_form(step_id=step, data_schema=schema, description_placeholders={"name": name})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pumpenzahl der Dosierpumpe ändern; die Bluetooth-Adresse bleibt fest (unique_id)."""
        entry = self._get_reconfigure_entry()
        if not (is_doser_name(entry.title) or CONF_PUMP_COUNT in entry.data):
            return self.async_abort(reason="nothing_to_reconfigure")
        current = int(entry.data.get(CONF_PUMP_COUNT) or entry.options.get(CONF_PUMP_COUNT) or DEFAULT_PUMPS)
        if user_input is not None:
            count = int(user_input[CONF_PUMP_COUNT])
            _remove_unused_pumps(self.hass, entry.entry_id, entry.data[CONF_ADDRESS], count)
            return self.async_update_reload_and_abort(entry, data_updates={CONF_PUMP_COUNT: count})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_PUMP_COUNT, default=current): vol.All(vol.Coerce(int), vol.Range(1, 8))}
            ),
            description_placeholders={"name": entry.title},
        )


def _remove_unused_pumps(hass: HomeAssistant, entry_id: str, address: str, count: int) -> None:
    """Entitäten und Behälter-Hinweise der Kanäle über `count` entfernen (nur beim Neu-Konfigurieren)."""
    pattern = re.compile(rf"^{re.escape(address.upper())}_dosing_pump_(\d+)_")
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry_id):
        if (m := pattern.match(ent.unique_id)) and int(m.group(1)) > count:
            reg.async_remove(ent.entity_id)
    for n in range(count + 1, 9):
        ir.async_delete_issue(hass, DOMAIN, f"container_{entry_id}_{n}")


class DoserOptionsFlow(OptionsFlow):
    """Dosierpumpe: Push-Nachricht, wenn ein Behälter unter die Schwelle fällt."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_NOTIFY: user_input.get(CONF_NOTIFY) or "",
                    CONF_NOTIFY_PERCENT: int(user_input[CONF_NOTIFY_PERCENT]),
                }
            )
        services = sorted(f"notify.{name}" for name in self.hass.services.async_services_for_domain("notify"))
        options = self.config_entry.options
        current = options.get(CONF_NOTIFY) or ""
        fields: dict[vol.Marker, Any] = {
            vol.Optional(CONF_NOTIFY, description={"suggested_value": current} if current else None): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(options=services, mode=selector.SelectSelectorMode.DROPDOWN)
                )
            ),
            vol.Required(
                CONF_NOTIFY_PERCENT, default=options.get(CONF_NOTIFY_PERCENT, DEFAULT_NOTIFY_PERCENT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=50, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER
                )
            ),
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
