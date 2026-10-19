"""Hintergrundarbeit: Unerreichbarkeit darf nie als Fehler aus einem Task fallen, Timer sammeln sich nicht an,
Werbepakete fluten die Entitäten nicht, Entladen bricht laufende Arbeit ab."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.chihiros.const import DOMAIN

from .conftest import LIGHT_ADDR, FakeBluetooth, FakeDevices

L = "dyncrgpc0ffee123456"
LIGHT = f"light.{L}"
P = "dydosed05e00abcdef"


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


def spy_tasks(device) -> list[asyncio.Task]:
    """Alle Hintergrundaufgaben des Geräts mitschreiben."""
    tasks: list[asyncio.Task] = []
    original = device._background

    def record(coro, what):
        task = original(coro, what)
        tasks.append(task)
        return task

    device._background = record
    return tasks


async def test_nightly_rewrite_survives_unreachable(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    """03:00 mit Sonnenverlauf, Lampe weg: `async_set_curve` wirft HomeAssistantError, der Task bleibt sauber."""
    tasks = spy_tasks(light.runtime_data)
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 70, "green": 60, "blue": 50})
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{L}_sonnenverlauf"})
    devices.offline = True
    await tick(hass, freezer, 86400)
    nightly = [t for t in tasks if t.get_name().endswith("nachts")]
    assert nightly and all(t.done() and t.exception() is None for t in nightly)
    assert hass.states.get(f"switch.{L}_sonnenverlauf").state == STATE_ON


async def test_feeding_end_survives_unreachable(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    """Fütterung aus Auto heraus, am Ende ist die Lampe weg: `async_set_auto` wirft HomeAssistantError."""
    tasks = spy_tasks(light.runtime_data)
    await call(hass, "switch", "turn_on", {"entity_id": f"switch.{L}_auto_modus"})
    await call(hass, "button", "press", {"entity_id": f"button.{L}_futterungslicht"})
    devices.offline = True
    await tick(hass, freezer, 15 * 60 + 1)
    ended = [t for t in tasks if t.get_name().endswith("Fütterung beenden")]
    assert ended and all(t.done() and t.exception() is None for t in ended)
    assert light.runtime_data.feeding_until is None


async def test_contact_retry_keeps_single_timer(hass: HomeAssistant, light, devices: FakeDevices, freezer):
    """Jeder gescheiterte Erstkontakt ersetzt den Timer, statt einen weiteren anzuhängen."""
    dev = light.runtime_data
    devices.offline = True
    subs = len(dev._unsubs)
    for _ in range(3):
        await tick(hass, freezer, 301)
        assert dev._contact_timer is not None and len(dev._unsubs) == subs
    devices.offline = False
    await tick(hass, freezer, 301)
    assert dev._contact_timer is None  # einmal erreicht: kein weiterer Versuch mehr


async def test_dose_recheck_keeps_single_timer(hass: HomeAssistant, doser, devices: FakeDevices, freezer):
    dev = doser.runtime_data
    subs = len(dev._unsubs)
    for _ in range(3):
        await call(hass, "button", "press", {"entity_id": f"button.{P}_pumpe_1_dosieren"})
    assert dev._recheck is not None and len(dev._unsubs) == subs
    devices.clear()
    await tick(hass, freezer, 61)
    assert dev._recheck is None
    assert [f[5] for f in devices.frames("DYDOSED05E00ABCDEF")] == [0x04, 0x04]  # ein Nachlesen, nicht drei


async def test_advertisements_update_only_on_change(hass: HomeAssistant, light, bt: FakeBluetooth):
    dev = light.runtime_data
    updates: list[int] = []
    dev.async_add_listener(lambda: updates.append(1))
    bt.advertise(LIGHT_ADDR, -70)
    assert len(updates) == 1 and dev.rssi == -70  # erster RSSI
    bt.advertise(LIGHT_ADDR, -73)
    bt.advertise(LIGHT_ADDR, -68)
    assert len(updates) == 1 and dev.rssi == -68  # Wert gemerkt, Entitäten in Ruhe gelassen
    bt.advertise(LIGHT_ADDR, -80)
    assert len(updates) == 2
    bt.vanish(LIGHT_ADDR)
    bt.advertise(LIGHT_ADDR, -80)
    assert len(updates) == 4  # weg + wieder da


async def test_unload_cancels_background_work(hass: HomeAssistant, light, devices: FakeDevices, bt: FakeBluetooth):
    await call(hass, DOMAIN, "set_channels", {"entity_id": LIGHT, "red": 10, "green": 10, "blue": 10})
    started = asyncio.Event()

    async def hang(self, frames, wait=0.5, *, retry=True):
        started.set()
        await asyncio.sleep(3600)

    with patch("custom_components.chihiros.ble.ChihirosLink.send", hang):
        bt.vanish(LIGHT_ADDR)
        bt.advertise(LIGHT_ADDR)  # Wiederherstellen läuft als Hintergrundaufgabe am Eintrag
        await asyncio.wait_for(started.wait(), 5)
        assert light._background_tasks
        task = next(iter(light._background_tasks))
        assert await hass.config_entries.async_unload(light.entry_id)
        await hass.async_block_till_done()
    assert task.cancelled()


async def test_doser_unload_cancels_poll(hass: HomeAssistant, doser, devices: FakeDevices, freezer):
    started = asyncio.Event()

    async def hang(self, frames, wait=0.5, *, retry=True):
        started.set()
        await asyncio.sleep(3600)

    with patch("custom_components.chihiros.ble.ChihirosLink.send", hang):
        await tick(hass, freezer, 91)
        assert started.is_set() and doser._background_tasks
        task = next(iter(doser._background_tasks))
        assert await hass.config_entries.async_unload(doser.entry_id)
        await hass.async_block_till_done()
    assert task.cancelled()
