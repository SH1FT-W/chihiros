"""Fütterungslicht, Sonnenverlauf, Eingewöhnung, Wochentage; Pumpe: Pläne, Kanal-Status, Verzögerung, Behälter."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.chihiros.const import DOMAIN

from .conftest import DOSER_ADDR, FakeDevices

L = "dyncrgpc0ffee123456"
LIGHT = f"light.{L}"
LNAME = "DYNCRGPC0FFEE123456"
P = "dydosed05e00abcdef"
PNAME = "DYDOSED05E00ABCDEF"


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


def levels(devices: FakeDevices) -> dict[int, int]:
    out: dict[int, int] = {}
    for f in devices.frames(LNAME):
        if f[0] == 0x5A and f[5] == 0x07:
            out[f[6]] = f[7]
    return out


def curve(devices: FakeDevices) -> list[list[int]]:
    return [list(f[6:10]) for f in devices.frames(LNAME) if f[0] == 0x5A and f[5] == 0x06]


# --- Lampe -----------------------------------------------------------------------------------------


async def test_feeding_returns_to_previous(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 20, "green": 10, "blue": 5})
    await call(hass, "number", "set_value", {"entity_id": f"number.{L}_futterungslicht_dauer", "value": 10})
    await call(hass, "button", "press", {"entity_id": f"button.{L}_futterungslicht"})
    assert levels(devices) == {0: 100, 1: 50, 2: 25}
    assert hass.states.get(f"sensor.{L}_futterungslicht_bis").state not in ("unknown", "unavailable")
    await tick(hass, freezer, 10 * 60 + 1)
    assert levels(devices) == {0: 20, 1: 10, 2: 5}
    # aus Auto heraus: danach wieder Auto
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{L}_auto_modus"})
    await call(hass, "button", "press", {"entity_id": f"button.{L}_futterungslicht"})
    assert hass.states.get(f"switch.{L}_auto_modus").state == STATE_OFF
    await tick(hass, freezer, 10 * 60 + 1)
    assert hass.states.get(f"switch.{L}_auto_modus").state == STATE_ON
    # ein anderer Befehl beendet die Fütterung ohne Zurückspringen
    await call(hass, "button", "press", {"entity_id": f"button.{L}_futterungslicht"})
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 1, "green": 1, "blue": 1})
    await tick(hass, freezer, 10 * 60 + 1)
    assert levels(devices) == {0: 1, 1: 1, 2: 1}


async def test_acclimatization_scales_everything(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 80, "green": 40, "blue": 20})
    await call(hass, "number", "set_value", {"entity_id": f"number.{L}_eingewohnung_start_helligkeit", "value": 50})
    await call(hass, "number", "set_value", {"entity_id": f"number.{L}_eingewohnung_dauer", "value": 2})
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{L}_eingewohnung"})
    assert levels(devices) == {0: 40, 1: 20, 2: 10}  # 50 % am ersten Tag
    assert hass.states.get(f"number.{L}_rot").state == "80"  # HA zeigt den Wunschwert
    assert hass.states.get(f"switch.{L}_eingewohnung").attributes["factor_percent"] == 50
    await tick(hass, freezer, 7 * 86400)  # eine Woche später, nachts neu geschrieben
    assert levels(devices) == {0: 60, 1: 30, 2: 15}
    await tick(hass, freezer, 7 * 86400)
    assert levels(devices) == {0: 80, 1: 40, 2: 20}
    assert hass.states.get(f"switch.{L}_eingewohnung").state == STATE_OFF  # endet von selbst
    # Kurven werden ebenfalls gedämpft
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{L}_eingewohnung"})
    devices.clear()
    await call(
        hass,
        DOMAIN,
        "set_auto_curve",
        {"entity_id": LIGHT, "points": [{"time": "9:00"}, {"time": "10:00", "red": 100, "green": 100, "blue": 100}]},
    )
    assert curve(devices)[1] == [0, 10, 0, 50]


async def test_sun_cycle(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 70, "green": 60, "blue": 50})
    await call(hass, "time", "set_value", {"entity_id": f"time.{L}_sonnenverlauf_fruhestens_hell", "time": "06:00"})
    await call(hass, "time", "set_value", {"entity_id": f"time.{L}_sonnenverlauf_spatestens_dunkel", "time": "23:00"})
    devices.clear()
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{L}_sonnenverlauf"})
    points = curve(devices)
    assert len(points) == 18  # 6 Stützpunkte × 3 Kanäle
    red = [p for p in points if p[0] == 0]
    assert red[2][3] == 100 and red[1][3] == 0  # Tagesfarbe auf 100 % hochgezogen
    assert hass.states.get(f"switch.{L}_auto_modus").state == STATE_ON
    devices.clear()
    await tick(hass, freezer, 86400)  # jede Nacht neu nach der Sonne
    assert len(curve(devices)) == 18
    await call(hass, "switch", "turn_off", {"entity_id": f"switch.{L}_sonnenverlauf"})


async def test_weekday_schedules(hass: HomeAssistant, light, devices: FakeDevices):
    devices.clear()
    await call(
        hass,
        DOMAIN,
        "set_weekday_schedule",
        {
            "entity_id": LIGHT,
            "settings": [
                {
                    "weekdays": ["mon", "tue", "wed", "thu", "fri"],
                    "sunrise": "9:00",
                    "sunset": "19:00",
                    "ramp": 60,
                    "red": 70,
                    "green": 70,
                    "blue": 70,
                },
                {"weekdays": ["sat", "sun"], "sunrise": "8:00", "sunset": "21:00", "red": 50, "green": 50, "blue": 50},
            ],
        },
    )
    frames = devices.frames(LNAME)
    settings = [list(f[6:15]) for f in frames if f[0] == 0xA5 and f[5] == 25]
    assert settings[0] == [9, 0, 19, 0, 60, 124, 70, 70, 70] and settings[1][5] == 3 and settings[1][4] == 30
    assert list(frames[-1][5:7]) == [5, 3]
    sched = hass.states.get(f"sensor.{L}_tagesablauf").attributes["weekday_settings"]
    assert len(sched) == 2 and sched[1]["weekdays"] == ["sat", "sun"]
    with pytest.raises(HomeAssistantError):
        await call(
            hass,
            DOMAIN,
            "set_weekday_schedule",
            {
                "entity_id": LIGHT,
                "settings": [
                    {"sunrise": "9:00", "sunset": "19:00"},
                    {"weekdays": ["mon"], "sunrise": "8:00", "sunset": "9:00"},
                ],
            },
        )


# --- Pumpe -----------------------------------------------------------------------------------------


def doser_device(hass: HomeAssistant) -> str:
    entry = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.data["address"] == DOSER_ADDR)
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0].id


async def test_plans(hass: HomeAssistant, doser, devices: FakeDevices):
    dev = doser_device(hass)
    devices.clear()
    await call(
        hass,
        DOMAIN,
        "set_dosing_program",
        {
            "device_id": dev,
            "pump": 2,
            "weekdays": ["mon", "wed", "fri"],
            "doses": [{"time": "20:00", "ml": 2.0}, {"time": "08:00", "ml": 1.0}],
        },
    )
    frames = devices.frames(PNAME)
    assert [f[5] for f in frames] == [5, 32, 27, 21, 21]
    assert list(frames[2][6:12]) == [1, 84, 1, 0, 0, 30]  # Mo+Mi+Fr = 84, 3,0 ml am Tag
    assert list(frames[3][6:12]) == [1, 1, 8, 0, 0, 10]
    plan = hass.states.get(f"sensor.{P}_pumpe_2_plan")
    assert plan.state == "daily" and plan.attributes["doses"][0] == {"time": "08:00", "ml": 1.0}
    devices.clear()
    await call(
        hass,
        DOMAIN,
        "set_dosing_program",
        {"device_id": dev, "pump": 1, "mode": "spread", "start": "10:00", "duration": 180, "count": 6, "daily_ml": 5},
    )
    frames = devices.frames(PNAME)
    assert [f[5] for f in frames] == [5, 32, 27, 23] and list(frames[3][6:12]) == [0, 10, 0, 13, 0, 6]
    devices.clear()
    await call(
        hass,
        DOMAIN,
        "set_dosing_program",
        {"device_id": dev, "pump": 1, "mode": "timer", "doses": [{"time": "07:00", "ml": 1}]},
    )
    assert list(devices.frames(PNAME)[-1][5:8]) == [21, 0, 3]
    await call(
        hass, DOMAIN, "set_dosing_program", {"device_id": dev, "pump": 1, "mode": "once", "times": ["07:00"], "ml": 1}
    )
    assert hass.states.get(f"sensor.{P}_pumpe_1_plan").state == "once"
    await call(hass, DOMAIN, "clear_dosing_program", {"device_id": dev, "pump": 1})
    assert hass.states.get(f"sensor.{P}_pumpe_1_plan").state == "none"
    with pytest.raises(HomeAssistantError):
        await call(hass, DOMAIN, "set_dosing_program", {"device_id": dev, "pump": 1, "mode": "spread"})
    with pytest.raises(HomeAssistantError):
        await call(hass, DOMAIN, "set_dosing_program", {"device_id": dev, "pump": 1})


async def test_channel_switches_and_delay(hass: HomeAssistant, doser, devices: FakeDevices):
    devices.clear()
    await call(hass, "switch", "turn_off", {"entity_id": f"switch.{P}_pumpe_3_aktiv"})
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{P}_pumpe_3_verpasste_dosen_nachholen"})
    frames = devices.frames(PNAME)
    assert list(frames[0][5:9]) == [32, 2, 0, 0] and list(frames[1][5:9]) == [32, 2, 1, 0]
    assert hass.states.get(f"switch.{P}_pumpe_3_aktiv").state == STATE_OFF
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{P}_dosierverzogerung"})
    assert list(devices.frames(PNAME)[-1][5:7]) == [31, 1]
    await call(hass, "switch", "turn_off", {"entity_id": f"switch.{P}_dosierverzogerung"})
    assert hass.states.get(f"switch.{P}_dosierverzogerung").state == STATE_OFF


async def test_container_level_and_warning(hass: HomeAssistant, doser, devices: FakeDevices, freezer):
    await tick(hass, freezer, 91)  # Zähler der Pumpe kennen (105,5 ml gesamt)
    await call(hass, "number", "set_value", {"entity_id": f"number.{P}_pumpe_1_behaltergrosse", "value": 100})
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "100.0"
    devices.total[0] = 1055 + 400  # 40 ml verbraucht (auch durch Plan/App – zählt die Pumpe)
    await tick(hass, freezer, 2 * 86400)
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "60.0"
    assert hass.states.get(f"sensor.{P}_pumpe_1_reicht_noch").state == "3"  # 20 ml/Tag
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"container_{doser.entry_id}_1") is not None
    await call(hass, "button", "press", {"entity_id": f"button.{P}_pumpe_1_behalter_aufgefullt"})
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "100.0"
    assert issues.async_get_issue(DOMAIN, f"container_{doser.entry_id}_1") is None
    assert hass.states.get(f"button.{P}_pumpe_2_behalter_aufgefullt").state == "unavailable"  # ohne Größe


async def test_set_container_remaining_and_push(hass: HomeAssistant, doser, devices: FakeDevices, freezer):
    """Behälter nur teilweise gefüllt: Restmenge setzen; Push einmal unter der Schwelle, neu erst nach Auffüllen."""
    pushes: list[dict] = []

    async def fake_push(call) -> None:  # notify.handy
        pushes.append(dict(call.data))

    hass.services.async_register("notify", "handy", fake_push)
    hass.config_entries.async_update_entry(doser, options={"notify_service": "notify.handy", "notify_percent": 20})
    await tick(hass, freezer, 91)
    await call(hass, "number", "set_value", {"entity_id": f"number.{P}_pumpe_1_behaltergrosse", "value": 450})
    device_id = dr.async_entries_for_config_entry(dr.async_get(hass), doser.entry_id)[0].id
    await call(hass, DOMAIN, "set_container_remaining", {"device_id": device_id, "pump": 1, "ml": 200})
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "200.0"
    assert pushes == []  # 200 von 450 = 44 %

    devices.total[0] = 1055 + 1200  # 120 ml verbraucht → 80 ml = 18 %
    await tick(hass, freezer, 2 * 86400)
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "80.0"
    assert len(pushes) == 1 and "Pumpe 1" in pushes[0]["title"]
    assert hass.states.get(f"sensor.{P}_pumpe_1_reicht_noch").state == "1"  # Verbrauch 60 ml/Tag, ohne den Startwert
    await tick(hass, freezer, 3600)
    await hass.async_block_till_done()
    assert len(pushes) == 1  # nicht nochmal

    await call(hass, "button", "press", {"entity_id": f"button.{P}_pumpe_1_behalter_aufgefullt"})
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "450.0"
    devices.total[0] = 1055 + 1200 + 3700  # wieder unter 20 %
    await tick(hass, freezer, 86400)
    await hass.async_block_till_done()
    assert len(pushes) == 2

    with pytest.raises(HomeAssistantError):  # ohne Behältergröße geht es nicht
        await call(hass, DOMAIN, "set_container_remaining", {"device_id": device_id, "pump": 2, "ml": 10})


async def test_push_without_notify_service(hass: HomeAssistant, doser):
    """Fehlender notify-Dienst: nur eine Warnung im Log, kein Fehler in der Aufgabe."""
    await doser.runtime_data._push("notify.gibt_es_nicht", {"title": "t", "message": "m"})


async def test_doser_options_flow(hass: HomeAssistant, doser):
    hass.services.async_register("notify", "handy", lambda call: None)
    result = await hass.config_entries.options.async_init(doser.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"notify_service": "notify.handy", "notify_percent": 25}
    )
    assert doser.options == {"notify_service": "notify.handy", "notify_percent": 25}


async def test_app_scenes(hass: HomeAssistant, light, devices: FakeDevices):
    sel = f"select.{L}_szene"
    assert hass.states.get(sel).attributes["options"] == ["red", "green", "buce", "fish", "shrimp", "all", "schedule"]
    await call(hass, "select", "select_option", {"entity_id": sel, "option": "fish"})
    assert levels(devices) == {0: 80, 1: 40, 2: 61}
    assert hass.states.get(sel).state == "fish"
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 1})
    assert hass.states.get(sel).state == "unknown"


async def test_auto_mode_sliders_and_scene(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    """Im Auto-Modus zeigen Regler und Szene den Tagesablauf statt 0 / unbekannt."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2030-10-07 07:30:00+00:00")  # 09:30 Ortszeit
    points = [
        {"time": "09:00", "red": 0, "green": 0, "blue": 0},
        {"time": "10:00", "red": 60, "green": 40, "blue": 20},
        {"time": "19:00"},
    ]
    await call(hass, DOMAIN, "set_auto_curve", {"entity_id": LIGHT, "points": points})
    sel = f"select.{L}_szene"
    assert hass.states.get(sel).state == "schedule"
    assert [hass.states.get(f"number.{L}_{c}").state for c in ("rot", "grun", "blau")] == ["30", "20", "10"]
    # Regler bewegen: raus aus Auto, die anderen Kanäle bleiben auf dem angezeigten Stand
    await call(hass, "number", "set_value", {"entity_id": f"number.{L}_rot", "value": 50})
    assert levels(devices) == {0: 50, 1: 20, 2: 10}
    assert hass.states.get(sel).state == "unknown"
    # Szene „Tagesablauf“ schaltet Auto wieder ein
    await call(hass, "select", "select_option", {"entity_id": sel, "option": "schedule"})
    assert hass.states.get(f"switch.{L}_auto_modus").state == STATE_ON
    assert hass.states.get(sel).state == "schedule"


async def test_remaining_anchors_when_counter_arrives_later(hass: HomeAssistant, doser, devices: FakeDevices, freezer):
    """Restmenge gesetzt, bevor die Pumpe ihren Gesamtzähler gemeldet hat: ab der ersten Meldung zählt der Verbrauch."""
    await call(hass, "number", "set_value", {"entity_id": f"number.{P}_pumpe_1_behaltergrosse", "value": 450})
    device_id = dr.async_entries_for_config_entry(dr.async_get(hass), doser.entry_id)[0].id
    await call(hass, DOMAIN, "set_container_remaining", {"device_id": device_id, "pump": 1, "ml": 200})
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "200.0"
    await tick(hass, freezer, 91)  # erster Kontakt: Zähler 105,5 ml wird zum Startwert
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "200.0"

    devices.total[0] = 1055 + 60  # Plan der Pumpe hat 6 ml dosiert (Rohwert in 0,1 ml)
    await tick(hass, freezer, 2 * 86400)
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "194.0"
    assert hass.states.get(f"sensor.{P}_pumpe_1_reicht_noch").state == "64"  # 3 ml/Tag

    # nach einem Neustart rechnet der Füllstand mit dem gemerkten Zähler weiter, statt auf 200 zurückzuspringen
    await hass.config_entries.async_reload(doser.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{P}_pumpe_1_restmenge").state == "194.0"
    # Gesamtzähler gleich nach dem Neuladen sichtbar (aus dem Speicher), nicht erst nach der nächsten Meldung
    assert hass.states.get(f"sensor.{P}_pumpe_1_gesamt").state == "111.5"
