"""Other LED models (A II, WRGB II Pro, VIVID III, BleLed family, Commander 1) against the fake device."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.chihiros import models, protocol as p
from custom_components.chihiros.const import DOMAIN

from .conftest import FakeDevices

ADDR = "AA:BB:CC:00:11:22"


async def setup(hass: HomeAssistant, name: str, **data: object) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=name, unique_id=ADDR, source="bluetooth", data={"address": ADDR, "name": name, **data}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


def channel_frames(devices: FakeDevices, name: str) -> dict[int, int]:
    out: dict[int, int] = {}
    for f in devices.frames(name):
        if f[0] == 0x5A and f[5] == 0x07:
            out[f[6]] = f[7]
    return out


def curve_frames(devices: FakeDevices, name: str) -> list[bytes]:
    return [f for f in devices.frames(name) if f[0] == 0x5A and f[5] == 0x06]


def test_model_table():
    assert models.detect_light("DYNA2N1234").name == "A II"
    assert models.detect_light("DYWPRO60ABC").layout == models.WRGB
    assert models.detect_light("DYNW60ABC").layout == models.RGB  # WRGB II (regular) has three channels
    assert models.detect_light("dyvvd3abc").fan
    assert models.detect_light("DYNLED01").sea_led and not models.detect_light("DYLED01").sea_led
    assert models.detect_light("DYHET01") is None and models.detect_light("DYMIXR01") is None
    assert models.detect_light(None) is None
    assert models.resolve_light("whatever") is models.C_II_RGB
    assert models.resolve_light("DYCOM01", "white").layout == models.WHITE
    assert models.resolve_light("DYCOM01").layout == models.WRGB
    assert models.is_lite("DYNA2N01") and not models.is_lite("DYNCRGP01")
    assert models.supported("DYDOSE01") and models.supported("DYU1200X") and not models.supported("DYH1T01")
    assert not models.supported(None)


def test_protocol_bleled_curve_and_fan():
    ids = p.MessageIds()
    # BleLed: 30-minute slot, remainder above 14 rounds up (09:15 → slot 19)
    assert list(p.curve_point(ids, 3, 9 * 60 + 15, 80, sea_led=False)[6:9]) == [3, 19, 80]
    assert list(p.curve_point(ids, 0, 9 * 60 + 14, 80, sea_led=False)[6:9]) == [0, 18, 80]
    assert list(p.fan_speed(ids, 140)[5:7]) == [0x0F, 100]
    assert list(p.fan_auto(ids)[5:9]) == [0x05, 0x11, 255, 255]
    fan = p.parse(bytes.fromhex("5b1b100001" + "0b0258190001000000000000004822"))
    assert fan == p.FanStatus(0x1B, 600, 25)


async def test_a2_white_brightness(hass: HomeAssistant, devices: FakeDevices):
    name = "DYNA2N0011"
    entry = await setup(hass, name)
    state = hass.states.get("light.dyna2n0011")
    assert state.attributes["supported_color_modes"] == ["brightness"]
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.model == "A II"
    assert er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{ADDR}_white")
    assert hass.states.get("select.dyna2n0011_szene") is None  # app scenes are RGB only

    await call(hass, "light", "turn_on", {"entity_id": "light.dyna2n0011", "brightness": 128})
    assert channel_frames(devices, name) == {0: 50}
    assert hass.states.get("light.dyna2n0011").attributes["channels"] == {"white": 50}
    await call(hass, "light", "turn_off", {"entity_id": "light.dyna2n0011"})
    assert channel_frames(devices, name) == {0: 0}
    await call(hass, "light", "turn_on", {"entity_id": "light.dyna2n0011"})
    assert channel_frames(devices, name) == {0: 50}

    with pytest.raises(HomeAssistantError):
        await call(hass, DOMAIN, "set_channels", {"entity_id": "light.dyna2n0011", "red": 10})
    await call(hass, DOMAIN, "set_channels", {"entity_id": "light.dyna2n0011", "white": 30})
    assert channel_frames(devices, name) == {0: 30}

    # Lite controller: no weekdays, only one setting for every day
    week = {"sunrise": "09:00", "sunset": "19:00", "white": 60}
    with pytest.raises(HomeAssistantError):
        await call(
            hass,
            DOMAIN,
            "set_weekday_schedule",
            {"entity_id": "light.dyna2n0011", "settings": [{**week, "weekdays": ["mon"]}]},
        )
    devices.clear()
    await call(hass, DOMAIN, "set_weekday_schedule", {"entity_id": "light.dyna2n0011", "settings": [week]})
    scene = next(f for f in devices.frames(name) if f[0] == 0xA5 and f[5] == 25)
    assert list(scene[6:15]) == [9, 0, 19, 0, 30, 127, 60, 255, 255]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a2_lite_curve_replayed_after_power_loss(hass: HomeAssistant, devices: FakeDevices, bt, freezer):
    name = "DYNA2N0011"
    entry = await setup(hass, name)
    points = [{"time": "09:00", "white": 0}, {"time": "10:00", "white": 70}, {"time": "19:00", "white": 0}]
    await call(hass, DOMAIN, "set_auto_curve", {"entity_id": "light.dyna2n0011", "points": points})
    sent = curve_frames(devices, name)
    assert [list(f[6:10]) for f in sent] == [[0, 9, 0, 0], [0, 10, 0, 70], [0, 19, 0, 0]]
    assert hass.states.get("sensor.dyna2n0011_tagesablauf").attributes["points"][1] == {"time": "10:00", "white": 70}
    freezer.tick(timedelta(minutes=5))
    devices.clear()
    bt.vanish(ADDR)
    bt.advertise(ADDR)
    await hass.async_block_till_done()
    assert len(curve_frames(devices, name)) == 3  # rewritten, Lite controllers need it after power loss
    await hass.config_entries.async_unload(entry.entry_id)


async def test_wrgb2_pro_rgbw(hass: HomeAssistant, devices: FakeDevices):
    name = "DYWPRO600011"
    entry = await setup(hass, name)
    light = "light.dywpro600011"
    assert hass.states.get(light).attributes["supported_color_modes"] == ["rgbw"]
    assert er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{ADDR}_rgbw")
    await call(hass, "light", "turn_on", {"entity_id": light, "rgbw_color": [255, 0, 128, 255], "brightness": 255})
    assert channel_frames(devices, name) == {0: 100, 1: 0, 2: 50, 3: 100}
    state = hass.states.get(light)
    assert state.attributes["rgbw_color"] == (255, 0, 128, 255)
    assert state.attributes["channels"] == {"red": 100, "green": 0, "blue": 50, "white": 100}
    await call(hass, "number", "set_value", {"entity_id": "number.dywpro600011_weiss", "value": 20})
    assert channel_frames(devices, name)[3] == 20

    devices.clear()
    points = [{"time": "09:00"}, {"time": "12:00", "red": 40, "white": 90}]
    await call(hass, DOMAIN, "set_auto_curve", {"entity_id": light, "points": points})
    sent = curve_frames(devices, name)
    assert [list(f[6:10]) for f in sent if f[6] == 3] == [[3, 9, 0, 0], [3, 12, 0, 90]]
    devices.clear()
    week = {"sunrise": "09:00", "sunset": "19:00", "red": 10, "green": 20, "blue": 30, "white": 40, "weekdays": ["mon"]}
    await call(hass, DOMAIN, "set_weekday_schedule", {"entity_id": light, "settings": [week]})
    scene = next(f for f in devices.frames(name) if f[0] == 0xA5 and f[5] == 25)
    assert list(scene[6:16]) == [9, 0, 19, 0, 30, 64, 10, 20, 30, 40]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_vivid3_fan(hass: HomeAssistant, devices: FakeDevices, freezer):
    name = "DYVVD30011"
    devices.fan = True
    entry = await setup(hass, name)
    freezer.tick(timedelta(seconds=91))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dyvvd30011_lufterdrehzahl").state == "600"
    assert hass.states.get("sensor.dyvvd30011_temperatur").state == "25"
    fan = "fan.dyvvd30011_lufter"
    devices.clear()
    await call(hass, "fan", "set_percentage", {"entity_id": fan, "percentage": 10})
    assert [list(f[5:7]) for f in devices.frames(name)] == [[0x0F, 25]]
    assert hass.states.get(fan).attributes["percentage"] == 25
    await call(hass, "fan", "set_preset_mode", {"entity_id": fan, "preset_mode": "auto"})
    assert hass.states.get(fan).attributes["preset_mode"] == "auto"
    await call(hass, "fan", "turn_off", {"entity_id": fan})
    assert hass.states.get(fan).state == "off"
    await call(hass, "fan", "turn_on", {"entity_id": fan})
    assert hass.states.get(fan).attributes["percentage"] == 25
    await call(hass, "fan", "turn_on", {"entity_id": fan, "preset_mode": "auto"})
    await call(hass, "fan", "set_preset_mode", {"entity_id": fan, "preset_mode": "manual"})
    assert hass.states.get(fan).attributes["percentage"] == 25
    await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)  # stored fan state survives a reload
    await hass.async_block_till_done()
    assert hass.states.get(fan).attributes["preset_mode"] == "manual"
    assert hass.states.get(fan).attributes["percentage"] == 25
    await hass.config_entries.async_unload(entry.entry_id)


async def test_bleled_commander_x_curve(hass: HomeAssistant, devices: FakeDevices):
    name = "DYONE0011"
    entry = await setup(hass, name)
    points = [{"time": "09:10", "white": 0}, {"time": "10:20", "white": 70}]
    await call(hass, DOMAIN, "set_auto_curve", {"entity_id": "light.dyone0011", "points": points})
    assert [list(f[6:9]) for f in curve_frames(devices, name)] == [[0, 18, 0], [0, 21, 70]]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_two_channel_egg(hass: HomeAssistant, devices: FakeDevices):
    name = "DYDD0011"
    entry = await setup(hass, name)
    assert er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{ADDR}_light")
    await call(hass, DOMAIN, "set_channels", {"entity_id": "light.dydd0011", "red": 80, "green": 40})
    await call(hass, "light", "turn_on", {"entity_id": "light.dydd0011", "brightness": 102})
    assert channel_frames(devices, name) == {0: 40, 1: 20}  # mix kept, brightness scaled
    await hass.config_entries.async_unload(entry.entry_id)


async def test_commander1_asks_for_layout(hass: HomeAssistant, devices: FakeDevices):
    from unittest.mock import MagicMock

    info = MagicMock()
    info.name, info.address = "DYCOM0011", ADDR.lower()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device_type": "rgb"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"address": ADDR, "name": "DYCOM0011", "device_type": "rgb"}
    await hass.async_block_till_done()
    state = hass.states.get("light.dycom0011")
    assert state.attributes["supported_color_modes"] == ["rgb"]


async def test_stored_levels_of_other_layout_ignored(hass: HomeAssistant, devices: FakeDevices, hass_storage):
    entry = MockConfigEntry(
        domain=DOMAIN, title="DYCOM0011", unique_id=ADDR, data={"address": ADDR, "device_type": "white"}
    )
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "data": {"levels": [1, 2, 3, 4], "last_on": [1, 2, 3, 4], "curve": [[0, [1, 2, 3, 4]]], "auto": False},
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("light.dycom0011").state == "unknown"
    await hass.config_entries.async_unload(entry.entry_id)
