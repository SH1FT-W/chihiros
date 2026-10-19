"""C II RGB in Home Assistant gegen das nachgebaute Gerät."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.chihiros.const import DOMAIN
from custom_components.chihiros.diagnostics import async_get_config_entry_diagnostics

from .conftest import LIGHT_ADDR, FakeBluetooth, FakeDevices, light_entry

NAME = "DYNCRGPC0FFEE123456"
LIGHT = "light.dyncrgpc0ffee123456"


def levels(devices: FakeDevices) -> dict[int, int]:
    """Letzter gesendeter Wert je Kanal."""
    out: dict[int, int] = {}
    for f in devices.frames(NAME):
        if f[0] == 0x5A and f[5] == 0x07:
            out[f[6]] = f[7]
    return out


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


async def test_keeps_production_entity_ids(hass: HomeAssistant, devices: FakeDevices):
    """Registry wie beim Original → gleiche Entity-IDs nach dem Umstieg."""
    entry = light_entry()
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    for platform, uid, eid in (
        ("light", f"{LIGHT_ADDR}_rgb", "c_ii_rgb_dyncrgpc0ffee123456_rgb"),
        ("switch", f"{LIGHT_ADDR}_auto_mode", "c_ii_rgb_auto_mode"),
        ("sensor", f"{LIGHT_ADDR}_firmware_version", "c_ii_rgb_dyncrgpc0ffee123456_firmware_version"),
    ):
        reg.async_get_or_create(platform, DOMAIN, uid, suggested_object_id=eid, config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("light.c_ii_rgb_dyncrgpc0ffee123456_rgb") is not None
    assert hass.states.get("switch.c_ii_rgb_auto_mode").state == STATE_OFF
    assert hass.states.get("number.dyncrgpc0ffee123456_rot") is not None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_does_not_touch_bluetooth(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    """Beim Start nie auf Bluetooth warten – erster Kontakt erst später im Hintergrund."""
    assert devices.sent == []
    assert hass.states.get(LIGHT).state == "unknown"  # Zustand noch unbekannt, nicht „aus“
    await tick(hass, freezer, 91)
    assert devices.frames(NAME)[0][5] == 0x04
    assert hass.states.get("sensor.dyncrgpc0ffee123456_firmware").state == "23"
    assert hass.states.get("sensor.dyncrgpc0ffee123456_tagesablauf").attributes["points"][1] == {
        "time": "10:00",
        "level": 70,
    }


async def test_rgb_brightness_and_toggle(hass: HomeAssistant, light, devices: FakeDevices):
    await call(hass, "light", "turn_on", {"entity_id": LIGHT, "rgb_color": [255, 180, 120], "brightness": 40})
    assert levels(devices) == {0: 16, 1: 11, 2: 7}
    state = hass.states.get(LIGHT)
    assert state.state == STATE_ON and not state.attributes.get("assumed_state")
    await call(hass, "light", "turn_off", {"entity_id": LIGHT})
    assert levels(devices) == {0: 0, 1: 0, 2: 0}
    assert hass.states.get(LIGHT).state == STATE_OFF
    await call(hass, "light", "turn_on", {"entity_id": LIGHT})  # letzte Mischung, kein Weiß-Sprung
    assert levels(devices) == {0: 16, 1: 11, 2: 7}
    await call(hass, "light", "turn_on", {"entity_id": LIGHT, "brightness": 255})
    assert levels(devices) == {0: 100, 1: 69, 2: 44}
    await call(hass, "light", "turn_on", {"entity_id": LIGHT, "rgb_color": [0, 0, 255]})
    assert levels(devices) == {0: 0, 1: 0, 2: 100}


async def test_channel_sliders_and_service(hass: HomeAssistant, light, devices: FakeDevices):
    await call(hass, "number", "set_value", {"entity_id": "number.dyncrgpc0ffee123456_rot", "value": 30})
    assert levels(devices) == {0: 30, 1: 0, 2: 0}
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "green": 20, "blue": 10})
    assert levels(devices) == {0: 30, 1: 20, 2: 10}
    assert hass.states.get("number.dyncrgpc0ffee123456_grun").state == "20"
    assert hass.states.get(LIGHT).attributes["channels"] == {"red": 30, "green": 20, "blue": 10}


async def test_transition(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 0, "green": 0, "blue": 0})
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 100, "green": 50, "blue": 0, "transition": 60})
    assert 0 < levels(devices)[0] < 100
    for _ in range(35):
        await tick(hass, freezer, 2)
    assert levels(devices) == {0: 100, 1: 50, 2: 0}
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 0, "transition": 60})
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 5})  # bricht den Übergang ab
    for _ in range(35):
        await tick(hass, freezer, 2)
    assert levels(devices)[0] == 5


async def test_auto_mode_and_curve(hass: HomeAssistant, light, devices: FakeDevices):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 50, "green": 50, "blue": 50})
    devices.clear()
    await call(hass, "switch", "turn_on", {"entity_id": "switch.dyncrgpc0ffee123456_auto_modus"})
    assert list(devices.frames(NAME)[0][5:7]) == [5, 3]
    assert hass.states.get(LIGHT).attributes["auto_mode"] is True
    devices.clear()
    # Licht stellen übernimmt manuell (wie die App): erst Manuell-Rahmen, dann Kanäle
    await call(hass, "light", "turn_on", {"entity_id": LIGHT, "brightness": 128})
    assert list(devices.frames(NAME)[0][5:7]) == [5, 11]
    assert hass.states.get("switch.dyncrgpc0ffee123456_auto_modus").state == STATE_OFF
    devices.clear()
    await call(
        hass,
        DOMAIN,
        "set_auto_curve",
        {
            "entity_id": LIGHT,
            "points": [
                {"time": "19:30"},
                {"time": "9:00"},
                {"time": "10:00", "red": 70, "green": 70, "blue": 70},
                {"time": "19:00", "red": 70, "green": 70, "blue": 70},
            ],
        },
    )
    frames = devices.frames(NAME)
    assert list(frames[0][5:7]) == [5, 5]  # alten Ablauf löschen
    points = [list(f[6:10]) for f in frames if f[5] == 6]
    assert len(points) == 12 and points[0] == [0, 9, 0, 0] and points[4] == [1, 9, 0, 0] and points[9] == [2, 10, 0, 70]
    assert [list(f[5:7]) for f in frames if f[5] == 5][-1] == [5, 3]  # Auto an, danach Snapshot-Prüfung
    assert list(frames[-1][5:7]) == [4, 1]
    assert hass.states.get("switch.dyncrgpc0ffee123456_auto_modus").state == STATE_ON
    sched = hass.states.get("sensor.dyncrgpc0ffee123456_tagesablauf")
    assert sched.state == "4" and sched.attributes["points"][1] == {"time": "10:00", "red": 70, "green": 70, "blue": 70}
    await call(hass, "switch", "turn_off", {"entity_id": "switch.dyncrgpc0ffee123456_auto_modus"})
    with pytest.raises(HomeAssistantError):
        await call(hass, DOMAIN, "set_auto_curve", {"entity_id": LIGHT, "points": [{"time": "9:00"}, {"time": "9:00"}]})


async def test_restore_after_power_loss(hass: HomeAssistant, light, devices: FakeDevices, bt: FakeBluetooth, freezer):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 40, "green": 30, "blue": 20})
    bt.present = False
    bt.vanish(LIGHT_ADDR)
    await hass.async_block_till_done()
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE
    devices.clear()
    bt.present = True
    bt.advertise(LIGHT_ADDR, -65)
    await hass.async_block_till_done()
    assert levels(devices) == {0: 40, 1: 30, 2: 20}  # Farbe nach Stromausfall wieder gesetzt
    assert hass.states.get(LIGHT).state == STATE_ON
    diag = await async_get_config_entry_diagnostics(hass, light)
    assert diag["rssi"] == -65 and any("wieder in Reichweite" in line for line in diag["log"])
    # Sperre: sofort erneutes Auftauchen sendet nicht nochmal
    devices.clear()
    bt.vanish(LIGHT_ADDR)
    bt.advertise(LIGHT_ADDR)
    await hass.async_block_till_done()
    assert devices.sent == []
    # Im Auto-Modus wird nur Status/Uhr abgefragt
    await call(hass, "switch", "turn_on", {"entity_id": "switch.dyncrgpc0ffee123456_auto_modus"})
    await tick(hass, freezer, 200)
    devices.clear()
    bt.vanish(LIGHT_ADDR)
    bt.advertise(LIGHT_ADDR)
    await hass.async_block_till_done()
    assert [f[5] for f in devices.frames(NAME)] == [0x04]


async def test_unreachable(hass: HomeAssistant, light, devices: FakeDevices):
    devices.offline = True
    with pytest.raises(HomeAssistantError, match=r"nicht erreichbar|unreachable"):
        await call(hass, "light", "turn_on", {"entity_id": LIGHT, "brightness": 10})
    reach = hass.states.get("binary_sensor.dyncrgpc0ffee123456_erreichbar")
    assert reach.state == STATE_OFF and "BleakNotFoundError" in reach.attributes["last_error"]
    devices.offline = False
    await call(hass, "light", "turn_on", {"entity_id": LIGHT, "brightness": 10})
    assert hass.states.get("binary_sensor.dyncrgpc0ffee123456_erreichbar").state == STATE_ON


async def test_state_survives_restart(hass: HomeAssistant, light, devices: FakeDevices):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 12, "green": 0, "blue": 0})
    await hass.config_entries.async_reload(light.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.dyncrgpc0ffee123456_rot").state == "12"
    assert hass.states.get(LIGHT).state == STATE_ON


async def test_auto_mode_shows_program(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    """Im Auto-Modus zeigt das Licht, was der Tagesablauf gerade spielt (die Lampe meldet es nicht)."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2030-10-07 07:30:00+00:00")  # 09:30 Ortszeit, nach der echten Uhr (Minutentakt fällig)
    points = [
        {"time": "09:00", "red": 0, "green": 0, "blue": 0},
        {"time": "10:00", "red": 60, "green": 40, "blue": 20},
        {"time": "19:00"},
    ]
    await call(hass, DOMAIN, "set_auto_curve", {"entity_id": LIGHT, "points": points})
    state = hass.states.get(LIGHT)
    assert state.state == STATE_ON and state.attributes["channels"] == {"red": 30, "green": 20, "blue": 10}
    assert state.attributes["brightness"] == round(30 * 2.55)
    await tick(hass, freezer, 60 * 60 * 10 + 5)  # 19:30:05 → nach dem letzten Punkt aus (Anzeige je Minute bei :05)
    assert hass.states.get(LIGHT).state == STATE_OFF
    # Auto ohne bekannten Ablauf (z. B. in der App gestellt): an, ohne Werte
    light.runtime_data.curve = ()
    light.runtime_data.async_update_listeners()
    assert hass.states.get(LIGHT).state == STATE_ON


async def test_first_contact_retries_and_hourly_contact(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    """Klappt der erste Kontakt nicht, wird es nach 5 Min. erneut versucht; danach stündlich (Uhr stellen)."""
    devices.offline = True
    await tick(hass, freezer, 91)
    assert hass.states.get("binary_sensor.dyncrgpc0ffee123456_erreichbar").state == STATE_OFF
    devices.offline = False
    devices.clear()
    await tick(hass, freezer, 301)
    assert devices.frames(NAME)[0][5] == 0x04
    assert hass.states.get("binary_sensor.dyncrgpc0ffee123456_erreichbar").state == STATE_ON
    devices.clear()
    await tick(hass, freezer, 3600)
    assert devices.frames(NAME)[0][5] == 0x04


async def test_curve_rewrites_swallowed_points(hass: HomeAssistant, light, devices: FakeDevices):
    """Die echte Lampe verschluckt direkt nach dem Löschen manchmal Punkte – die werden nachgeschrieben."""
    points = [
        {"time": "08:59"},
        {"time": "09:00", "red": 2, "green": 2, "blue": 2},
        {"time": "10:00", "red": 71, "green": 71, "blue": 71},
        {"time": "19:00"},
    ]
    devices.drop_after_reset = 2
    devices.clear()
    await call(hass, DOMAIN, "set_auto_curve", {"entity_id": LIGHT, "points": points})
    assert devices.red_curve is not None
    assert sorted(devices.red_curve) == [(8, 59, 0), (9, 0, 2), (10, 0, 71), (19, 0, 0)]
    point_frames = [f for f in devices.frames(NAME) if f[5] == 6]
    assert len(point_frames) == 12 + 6  # zwei fehlende Punkte × drei Kanäle nachgeschrieben


async def test_curve_reports_points_that_stay_missing(hass: HomeAssistant, light, devices: FakeDevices):
    devices.drop_after_reset = 1
    original = devices._answer

    def never_store_0859(link, f: bytes) -> None:
        if f[5] == 6 and list(f[7:9]) == [8, 59]:
            return
        original(link, f)

    devices._answer = never_store_0859  # type: ignore[method-assign]
    points = [{"time": "08:59"}, {"time": "10:00", "red": 50, "green": 50, "blue": 50}, {"time": "19:00"}]
    await call(hass, DOMAIN, "set_auto_curve", {"entity_id": LIGHT, "points": points})
    assert light.runtime_data._log[-2].endswith("Tagesablauf unvollständig in der Lampe, fehlt: 08:59")
