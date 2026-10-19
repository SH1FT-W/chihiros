"""Chihiros (LED-Lampen + Dosierpumpe) – eigene Integration, Nachfolger von TheMicDiet/chihiros-led-control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr, issue_registry as ir, service
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import CONF_ADDRESS, CONF_NAME, DOMAIN, MAX_CURVE_POINTS
from .coordinator import ChihirosDevice, ChihirosDoser, ChihirosLight
from .models import DOSER_PREFIXES, resolve_light

LIGHT_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]
DOSER_PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]
_DAYS = vol.All(cv.ensure_list, [vol.In(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])])

type ChihirosConfigEntry = ConfigEntry[ChihirosDevice]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
_PUMP = vol.All(vol.Coerce(int), vol.Range(min=1, max=8))


def _minutes(value: object) -> int:
    t = cv.time(value)
    return t.hour * 60 + t.minute


CHANNEL_NAMES = ("red", "green", "blue", "white", "warm")
_CHANNELS: dict[str | vol.Marker, Any] = {vol.Optional(name): _PERCENT for name in CHANNEL_NAMES}
# Ablauf-Punkte: Rot/Grün/Blau wie bisher mit 0 vorbelegt, Weiß/Warmweiß nur wenn angegeben
_POINT_CHANNELS: dict[str | vol.Marker, Any] = {
    vol.Optional(name, default=0) if name in ("red", "green", "blue") else vol.Optional(name): _PERCENT
    for name in CHANNEL_NAMES
}

_CURVE_POINT = vol.Schema({vol.Required("time"): _minutes, **_POINT_CHANNELS})


def is_doser(entry: ConfigEntry) -> bool:
    return (entry.data.get(CONF_NAME) or entry.title).upper().startswith(DOSER_PREFIXES) or "pump_count" in entry.data


def platforms(entry: ConfigEntry) -> list[Platform]:
    if is_doser(entry):
        return DOSER_PLATFORMS
    model = resolve_light(entry.data.get(CONF_NAME) or entry.title)
    return [*LIGHT_PLATFORMS, Platform.FAN] if model.fan else LIGHT_PLATFORMS


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Lampe: Entitätsdienste am Licht
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_auto_curve",
        entity_domain=LIGHT_DOMAIN,
        schema={
            vol.Required("points"): vol.All(cv.ensure_list, [_CURVE_POINT], vol.Length(min=2, max=MAX_CURVE_POINTS)),
            vol.Optional("activate", default=True): cv.boolean,
        },
        func="async_set_curve",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_channels",
        entity_domain=LIGHT_DOMAIN,
        schema={
            **_CHANNELS,
            vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0, max=21600)),
        },
        func="async_set_channels",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_weekday_schedule",
        entity_domain=LIGHT_DOMAIN,
        schema={
            vol.Required("settings"): vol.All(
                cv.ensure_list,
                [
                    vol.Schema(
                        {
                            vol.Optional("weekdays"): _DAYS,
                            vol.Required("sunrise"): _minutes,
                            vol.Required("sunset"): _minutes,
                            vol.Optional("ramp", default=30): vol.All(vol.Coerce(int), vol.Range(0, 255)),
                            **_POINT_CHANNELS,
                        }
                    )
                ],
                vol.Length(min=1, max=7),
            )
        },
        func="async_set_weekday_schedule",
    )

    def runtime_of(call: ServiceCall) -> object:
        """Laufzeitobjekt zum gewählten Gerät (ein Gerät gehört genau zu einem Eintrag)."""
        device = dr.async_get(hass).async_get(call.data["device_id"])
        entry = hass.config_entries.async_get_entry(device.config_entry_id) if device else None
        return getattr(entry, "runtime_data", None) if entry and entry.domain == DOMAIN else None

    # Dosierpumpe: Dienste mit Gerät
    def doser(call: ServiceCall) -> ChihirosDoser:
        if isinstance(runtime := runtime_of(call), ChihirosDoser):
            return runtime
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_doser")

    async def dose(call: ServiceCall) -> None:
        await doser(call).async_dose(call.data["pump"], call.data.get("ml"))

    async def calibrate(call: ServiceCall) -> None:
        await doser(call).async_calibrate(call.data["pump"], call.data.get("seconds"), call.data.get("measured_ml"))

    async def set_program(call: ServiceCall) -> None:
        data = call.data
        doses = [(d["time"], d["ml"]) for d in data.get("doses", [])]
        if "times" in data:
            doses += [(t, data.get("ml", 1.0)) for t in data["times"]]
        spread = None
        if data.get("mode") == "spread":
            if not {"start", "duration", "count", "daily_ml"} <= data.keys():
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="spread_fields")
            spread = (data["start"], data["duration"], data["count"], data["daily_ml"])
        await doser(call).async_plan(
            data["pump"], data.get("mode", "daily"), doses=doses, weekdays=data.get("weekdays"), spread=spread
        )

    async def clear_program(call: ServiceCall) -> None:
        await doser(call).async_clear_program(call.data["pump"])

    async def reset_totals(call: ServiceCall) -> None:
        await doser(call).async_reset_totals(call.data["pump"])

    async def set_remaining(call: ServiceCall) -> None:
        doser(call).set_remaining(call.data["pump"], call.data["ml"])

    base: dict[vol.Marker, object] = {vol.Required("device_id"): cv.string, vol.Required("pump"): _PUMP}
    hass.services.async_register(
        DOMAIN,
        "dose",
        dose,
        vol.Schema({**base, vol.Optional("ml"): vol.All(vol.Coerce(float), vol.Range(0.2, 999.9))}),
    )
    hass.services.async_register(
        DOMAIN,
        "calibrate_pump",
        calibrate,
        vol.Schema(
            {
                **base,
                vol.Optional("seconds"): vol.All(vol.Coerce(int), vol.Range(1, 254)),
                vol.Optional("measured_ml"): vol.All(vol.Coerce(float), vol.Range(0.1, 255)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_dosing_program",
        set_program,
        vol.Schema(
            {
                **base,
                vol.Optional("mode", default="daily"): vol.In(["daily", "once", "timer", "spread"]),
                vol.Optional("weekdays"): _DAYS,
                vol.Optional("times"): vol.All(cv.ensure_list, [_minutes], vol.Length(min=1, max=24)),
                vol.Optional("ml"): vol.All(vol.Coerce(float), vol.Range(0.1, 999.9)),
                vol.Optional("doses"): vol.All(
                    cv.ensure_list,
                    [
                        vol.Schema(
                            {
                                vol.Required("time"): _minutes,
                                vol.Required("ml"): vol.All(vol.Coerce(float), vol.Range(0.1, 999.9)),
                            }
                        )
                    ],
                    vol.Length(min=1, max=24),
                ),
                vol.Optional("start"): _minutes,
                vol.Optional("duration"): vol.All(vol.Coerce(int), vol.Range(1, 1439)),
                vol.Optional("count"): vol.All(vol.Coerce(int), vol.Range(1, 255)),
                vol.Optional("daily_ml"): vol.All(vol.Coerce(float), vol.Range(0.1, 6553)),
            }
        ),
    )

    # Diagnose: Rohbefehle an ein Gerät (Licht oder Pumpe), Antwort enthält die Rückmeldungen
    def any_device(call: ServiceCall) -> ChihirosDevice:
        if isinstance(runtime := runtime_of(call), ChihirosDevice):
            return runtime
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_device")

    async def send_raw(call: ServiceCall) -> ServiceResponse:
        frames = [(f["cmd"], f["mode"], list(f.get("params", []))) for f in call.data["frames"]]
        return await any_device(call).async_send_raw(frames, call.data["wait"])

    _byte = vol.All(vol.Coerce(int), vol.Range(0, 255))
    hass.services.async_register(
        DOMAIN,
        "send_raw",
        send_raw,
        vol.Schema(
            {
                vol.Required("device_id"): cv.string,
                vol.Required("frames"): vol.All(
                    cv.ensure_list,
                    [
                        vol.Schema(
                            {
                                vol.Required("cmd"): _byte,
                                vol.Required("mode"): _byte,
                                vol.Optional("params", default=[]): vol.All(cv.ensure_list, [_byte]),
                            }
                        )
                    ],
                    vol.Length(min=1, max=40),
                ),
                vol.Optional("wait", default=2.0): vol.All(vol.Coerce(float), vol.Range(0.2, 10)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, "clear_dosing_program", clear_program, vol.Schema(base))
    hass.services.async_register(DOMAIN, "reset_pump_totals", reset_totals, vol.Schema(base))
    hass.services.async_register(
        DOMAIN,
        "set_container_remaining",
        set_remaining,
        vol.Schema({**base, vol.Required("ml"): vol.All(vol.Coerce(float), vol.Range(0, 100000))}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ChihirosConfigEntry) -> bool:
    address = entry.data[CONF_ADDRESS]
    cls = ChihirosDoser if is_doser(entry) else ChihirosLight
    device = cls(hass, entry, address, entry.title)
    await device.async_start()
    entry.runtime_data = device
    await hass.config_entries.async_forward_entry_setups(entry, platforms(entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ChihirosConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, platforms(entry)):
        await entry.runtime_data.async_stop()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ChihirosConfigEntry) -> None:
    """Beim Löschen keine „Behälter fast leer“-Hinweise des Geräts zurücklassen."""
    for n in range(1, 9):
        ir.async_delete_issue(hass, DOMAIN, f"container_{entry.entry_id}_{n}")
