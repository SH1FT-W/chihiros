"""Gerätelogik: LED-Lampen (C II RGB und die übrigen 0x5A-Lampen) und Dosierpumpe.

Wichtig: Beim Start von Home Assistant wird nie auf Bluetooth gewartet. Der erste Kontakt läuft als
Hintergrund-Aufgabe (die das Hochfahren nicht aufhält) – das Original hat den Start damit blockiert.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import itertools
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import models, protocol
from .ble import ChihirosLink, ChihirosUnavailable
from .const import (
    CONF_DEVICE_TYPE,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_PERCENT,
    CONF_PUMP_COUNT,
    CONTACT_RETRY,
    CURVE_RETRIES,
    DEFAULT_NOTIFY_PERCENT,
    DEFAULT_PUMPS,
    DOMAIN,
    DOSER_POLL,
    FADE_MIN_GAP,
    FIRST_CONTACT_DELAY,
    LIGHT_CONTACT,
    MAX_CURVE_POINTS,
    RESET_PAUSE,
    SNAPSHOT_SLOTS,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Nach dem Wiederauftauchen (Strom wieder da) höchstens so oft den Zustand neu senden.
RESTORE_BACKOFF = timedelta(minutes=2)
# Entitäten erst ab dieser RSSI-Änderung (dBm) neu schreiben, nicht bei jedem Werbepaket.
RSSI_STEP = 5


class ChihirosDevice:
    """Gemeinsamer Teil: Verbindung, Erreichbarkeit, Speicher, Listener für Entitäten."""

    kind = "device"
    model_name = "Chihiros"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str) -> None:
        self.hass = hass
        self.entry = entry
        self.address = address.upper()
        self.name = name
        self.link = ChihirosLink(hass, self.address, name, self._on_frame, self._extra_prelude)
        self.firmware: int | None = None  # gemerkt, damit Sensor + Geräteseite nach Neustart sofort stimmen
        self.rssi: int | None = None
        self.last_seen: datetime | None = None
        self.last_contact: datetime | None = None
        self.present = bluetooth.async_address_present(hass, self.address, connectable=True)
        self._listeners: list[CALLBACK_TYPE] = []
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[CALLBACK_TYPE] = []
        self._contact_timer: CALLBACK_TYPE | None = None  # nächster Versuch des ersten Kontakts
        self._log: list[str] = []
        self.raw_frames: deque[tuple[datetime, str]] = deque(maxlen=30)  # Rohrückmeldungen für Diagnose

    # --- Start/Stopp -------------------------------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self.firmware = stored.get("firmware")
        self._load(stored)
        self._unsubs.append(
            bluetooth.async_register_callback(
                self.hass,
                self._on_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        self._unsubs.append(
            bluetooth.async_track_unavailable(self.hass, self._on_unavailable, self.address, connectable=True)
        )
        self._arm_contact(FIRST_CONTACT_DELAY)

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_contact()
        await self._store.async_save(self._full_dump())  # nicht auf das verzögerte Speichern warten (Neuladen)
        await self.link.disconnect()

    def _arm_contact(self, delay: float) -> None:
        """Einen einzigen Timer für den ersten Kontakt halten (ein vorheriger wird ersetzt, nichts sammelt sich an)."""
        self._cancel_contact()
        self._contact_timer = async_call_later(self.hass, delay, self._first_contact)

    def _cancel_contact(self) -> None:
        if self._contact_timer:
            self._contact_timer()
            self._contact_timer = None

    @callback
    def _first_contact(self, _now: datetime) -> None:
        self._contact_timer = None  # ausgelöst, Handle ist verbraucht
        self._background(self._contact_until_reached(), "erster Kontakt")

    async def _contact_until_reached(self) -> None:
        """Kontakt aufnehmen; klappt es nicht, in ein paar Minuten noch einmal (bis es einmal geklappt hat)."""
        try:
            await self.async_refresh()
        except ChihirosUnavailable as err:
            _LOGGER.debug("%s: erster Kontakt fehlgeschlagen, neuer Versuch später: %s", self.name, err)
            self.async_update_listeners()
            self._arm_contact(CONTACT_RETRY)

    def _background(self, coro: Coroutine[Any, Any, Any], what: str) -> asyncio.Task[Any]:
        """Hintergrundaufgabe am Config-Eintrag: wird beim Entladen abgebrochen (nicht an `hass` hängen)."""
        return self.entry.async_create_background_task(self.hass, coro, f"{DOMAIN} {what}")

    async def _safe(self, aw: Awaitable[Any]) -> None:
        """Hintergrundarbeit: nicht erreichbar ist kein Fehler, nur ein Debug-Eintrag.

        `command()` und darauf aufbauende Abläufe (Tagesablauf schreiben, Auto an) melden Unerreichbarkeit
        schon als HomeAssistantError, deshalb beides fangen.
        """
        try:
            await aw
        except (ChihirosUnavailable, HomeAssistantError) as err:
            _LOGGER.debug("%s: im Hintergrund nicht erreichbar: %s", self.name, err)

    # --- Erreichbarkeit ----------------------------------------------------------------------------

    @callback
    def _on_advertisement(self, info: bluetooth.BluetoothServiceInfoBleak, _change: Any) -> None:
        came_back = not self.present
        self.present = True
        previous_rssi, self.rssi = self.rssi, info.rssi
        self.last_seen = dt_util.utcnow()
        if came_back:
            self.note("wieder in Reichweite eines Bluetooth-Proxys")
            self._on_came_back()
        # Werbepakete kommen im Sekundentakt; die Entitäten nur schreiben, wenn sich etwas Sichtbares ändert
        # (Rückkehr oder RSSI um mehr als RSSI_STEP), sonst flutet jedes Paket die Zustandsmaschine.
        if came_back or previous_rssi is None or abs(info.rssi - previous_rssi) > RSSI_STEP:
            self.async_update_listeners()

    @callback
    def _on_unavailable(self, _info: bluetooth.BluetoothServiceInfoBleak) -> None:
        self.present = False
        self.note("von keinem Bluetooth-Proxy mehr gesehen")
        self.async_update_listeners()

    def _on_came_back(self) -> None:
        """Nach Stromausfall o. Ä. – Unterklassen stellen hier ihren Zustand wieder her."""

    # --- Senden ------------------------------------------------------------------------------------

    def _extra_prelude(self, ids: protocol.MessageIds) -> list[bytes]:
        return []

    async def send(self, frames: list[bytes], wait: float = 0.5, *, retry: bool = True) -> None:
        await self.link.send(frames, wait, retry=retry)
        self.last_contact = dt_util.utcnow()
        self.async_update_listeners()

    async def command(self, frames: list[bytes], wait: float = 0.5, *, retry: bool = True) -> None:
        """Wie `send`, aber Fehler als verständliche HA-Fehlermeldung (für Dienste und Entitäten)."""
        try:
            await self.send(frames, wait, retry=retry)
        except ChihirosUnavailable as err:
            self.async_update_listeners()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unreachable",
                translation_placeholders={"name": self.name, "error": str(err)},
            ) from err

    async def async_refresh(self) -> None:
        """Status abfragen (Unterklassen)."""

    async def async_send_raw(self, frames: list[tuple[int, int, list[int]]], wait: float) -> dict[str, Any]:
        """Diagnose: Rohbefehle (Befehl, Modus, Parameter) senden und die Rückmeldungen danach zurückgeben."""
        since = dt_util.utcnow()
        ids = self.link.ids
        encoded = [protocol.encode(ids, cmd, mode, params) for cmd, mode, params in frames]
        await self.command(encoded, wait=wait)
        return {
            "sent": [f.hex() for f in encoded],
            "received": [raw for when, raw in self.raw_frames if when >= since],
        }

    def _on_frame(self, data: bytes) -> None:
        self.raw_frames.append((dt_util.utcnow(), data.hex()))
        _LOGGER.debug("%s: Rückmeldung %s", self.name, data.hex())
        parsed = protocol.parse(data)
        if parsed is None:
            _LOGGER.debug("%s: unbekannte Rückmeldung %s", self.name, data.hex())
            return
        self.last_contact = dt_util.utcnow()
        # HA-Bluetooth (bleak über ESPHome-/lokale Adapter) liefert Notify-Rückrufe im Event-Loop,
        # deshalb hier wie in `_handle` alles direkt aufrufen (kein call_soon_threadsafe nötig).
        self._handle(parsed)
        self.async_update_listeners()

    def _handle(self, parsed: protocol.Notification) -> None:
        # Jede Rückmeldung trägt in Byte 1 die Firmware-/Protokoll-Kennung des Geräts.
        if parsed.firmware and parsed.firmware != self.firmware:
            self.firmware = parsed.firmware
            self._update_device_registry()
            self.save()

    def _update_device_registry(self) -> None:
        """Firmware auch auf der Geräteseite anzeigen (beim Start ist sie noch unbekannt)."""
        reg = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(reg, self.entry.entry_id):
            if device.sw_version != str(self.firmware):
                reg.async_update_device(device.id, sw_version=str(self.firmware))

    # --- Listener / Speicher -----------------------------------------------------------------------

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def async_update_listeners(self) -> None:
        for update in list(self._listeners):
            update()

    def _load(self, data: dict[str, Any]) -> None:
        """Gespeicherten Zustand übernehmen (Unterklassen)."""

    def _dump(self) -> dict[str, Any]:
        return {}

    def save(self) -> None:
        self._store.async_delay_save(self._full_dump, 2)

    def _full_dump(self) -> dict[str, Any]:
        return {**self._dump(), "firmware": self.firmware}

    def note(self, text: str, level: int = logging.INFO) -> None:
        """Eintrag ins Diagnose-Protokoll (letzte 20) und ins HA-Log mit der angegebenen Stufe."""
        stamp = dt_util.now().strftime("%d.%m. %H:%M:%S")
        self._log = [*self._log[-19:], f"{stamp} {text}"]
        _LOGGER.log(level, "%s: %s", self.name, text)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "present": self.present,
            "rssi": self.rssi,
            "firmware": self.firmware,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_contact": self.last_contact.isoformat() if self.last_contact else None,
            "connects": self.link.connects,
            "failures": self.link.failures,
            "last_error": self.link.last_error,
            "stored": self._dump(),
            "log": self._log,
        }


@dataclass(slots=True)
class LightSettings:
    """Einstellungen der Lampe, die nur HA kennt."""

    feed_minutes: int = 15
    acclim_on: bool = False
    acclim_start: int = 50  # % zu Beginn
    acclim_weeks: int = 4
    acclim_since: str | None = None
    sun_on: bool = False
    sun_ramp: int = 60  # Minuten Sonnenauf-/untergang
    sun_earliest: int = 8 * 60  # frühestens hell
    sun_latest: int = 21 * 60  # spätestens dunkel
    sun_levels: list[int] | None = None  # Tagesfarbe für den Sonnenverlauf

    def as_dict(self) -> dict[str, Any]:
        return {s: getattr(self, s) for s in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LightSettings:
        return cls(**{k: v for k, v in data.items() if k in cls.__slots__})


def _local_minutes(when: datetime) -> int:
    local = dt_util.as_local(when)
    return local.hour * 60 + local.minute


def levels_at(curve: tuple[tuple[int, tuple[int, ...]], ...], minute: int) -> tuple[int, ...]:
    """Wert des Tagesablaufs zu einer Minute: zwischen zwei Punkten linear, außerhalb der letzte Punkt."""
    points = sorted(curve)
    if minute < points[0][0] or minute >= points[-1][0]:
        return points[-1][1]
    for (t0, a), (t1, b) in itertools.pairwise(points):
        if t0 <= minute < t1:
            f = (minute - t0) / (t1 - t0)
            return tuple(round(x + (y - x) * f) for x, y in zip(a, b, strict=True))
    return points[-1][1]  # pragma: no cover – durch die Abfrage oben unerreichbar


class ChihirosLight(ChihirosDevice):
    """LED-Lampe: 1–4 Kanäle je 0–100 % (Modell aus dem Bluetooth-Namen), manuell oder Tagesablauf („Auto“)."""

    kind = "light"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str) -> None:
        super().__init__(hass, entry, address, name)
        bt_name = entry.data.get(CONF_NAME) or name
        self.model = models.resolve_light(bt_name, entry.data.get(CONF_DEVICE_TYPE))
        self.model_name = self.model.name
        self.lite = models.is_lite(bt_name)  # Lite-Controller: kein Wochentag, Ablauf nach Rückkehr neu
        self.channels = len(self.model.layout)
        self.fan_rpm: int | None = None
        self.temperature: int | None = None
        self.fan_auto = False
        self.fan_speed = 0
        self.fan_last_speed = 100
        self.levels: tuple[int, ...] = (0,) * self.channels
        self.last_on: tuple[int, ...] | None = None
        self.auto = False
        self.known = False  # ob HA den Zustand kennt (nach Neuinstallation erst nach dem ersten Befehl)
        self.curve: tuple[tuple[int, tuple[int, ...]], ...] = ()
        self.schedule_points: tuple[tuple[int, int], ...] = ()
        self._snapshots = 0  # Zähler empfangener Tagesablauf-Snapshots (zum Prüfen nach dem Schreiben)
        self._fade: asyncio.Task[None] | None = None
        self._last_restore: datetime | None = None
        self.weekday_settings: list[dict[str, Any]] = []
        self.settings = LightSettings()
        self.feeding_until: datetime | None = None
        self._feed_restore: tuple[bool, tuple[int, ...]] | None = None
        self._feed_cancel: CALLBACK_TYPE | None = None

    # --- Eingewöhnung ------------------------------------------------------------------------------

    def acclimatization_factor(self, today: date | None = None) -> float:
        """0–1: Anteil der vollen Helligkeit während der Eingewöhnung (sonst 1)."""
        st = self.settings
        if not st.acclim_on or not st.acclim_since:
            return 1.0
        days = ((today or dt_util.now().date()) - date.fromisoformat(st.acclim_since)).days
        total = max(1, st.acclim_weeks * 7)
        share = min(1.0, max(0.0, days / total))
        return (st.acclim_start + (100 - st.acclim_start) * share) / 100

    def program_levels(self, when: datetime | None = None) -> tuple[int, ...] | None:
        """Was der gespeicherte Tagesablauf gerade zeigt – die Lampe meldet es nicht; None ohne bekannte Kurve."""
        if not self.curve:
            return None
        return self._device_levels(levels_at(self.curve, _local_minutes(when or dt_util.utcnow())))

    @property
    def shown_levels(self) -> tuple[int, ...] | None:
        """Kanäle für die Anzeige: im Auto-Modus aus dem Tagesablauf, sonst der letzte Befehl von HA."""
        if self.auto:
            return self.program_levels()
        return self.levels if self.known else None

    def _device_levels(self, levels: tuple[int, ...]) -> tuple[int, ...]:
        f = self.acclimatization_factor()
        return tuple(max(0, min(100, round(v * f))) for v in levels)

    def _load(self, data: dict[str, Any]) -> None:
        self.settings = LightSettings.from_dict(data.get("settings") or {})
        self.weekday_settings = list(data.get("weekday_settings") or [])
        # Nur übernehmen, was zur Kanalzahl passt (z. B. nach geänderter Commander-1-Belegung).
        if isinstance(data.get("levels"), list) and len(data["levels"]) == self.channels:
            self.levels = tuple(int(v) for v in data["levels"])
            self.known = True
        if isinstance(data.get("last_on"), list) and len(data["last_on"]) == self.channels:
            self.last_on = tuple(int(v) for v in data["last_on"])
        self.auto = bool(data.get("auto", False))
        curve = tuple((int(t), tuple(int(v) for v in lv)) for t, lv in data.get("curve") or [])
        self.curve = curve if all(len(lv) == self.channels for _, lv in curve) else ()
        if self.model.fan:
            self.fan_auto = bool(data.get("fan_auto", False))
            self.fan_speed = int(data.get("fan_speed", 0))
            self.fan_last_speed = int(data.get("fan_last_speed", 100))

    def _dump(self) -> dict[str, Any]:
        return {
            "levels": list(self.levels),
            "last_on": list(self.last_on) if self.last_on else None,
            "auto": self.auto,
            "curve": [[t, list(lv)] for t, lv in self.curve],
            "settings": self.settings.as_dict(),
            "weekday_settings": self.weekday_settings,
        } | (
            {"fan_auto": self.fan_auto, "fan_speed": self.fan_speed, "fan_last_speed": self.fan_last_speed}
            if self.model.fan
            else {}
        )

    def _handle(self, parsed: protocol.Notification) -> None:
        super()._handle(parsed)
        if isinstance(parsed, protocol.LedSchedule):
            self.schedule_points = parsed.points
            self._snapshots += 1
        elif isinstance(parsed, protocol.FanStatus):
            self.fan_rpm, self.temperature = parsed.rpm, parsed.temperature

    async def async_refresh(self) -> None:
        await self.send([protocol.status_query(self.link.ids)], wait=1.5)

    def _on_came_back(self) -> None:
        now = dt_util.utcnow()
        if self._last_restore and now - self._last_restore < RESTORE_BACKOFF:
            return
        self._last_restore = now
        # Die Verbindung selbst stellt die Uhr (Prelude). Im manuellen Betrieb die Farbe neu senden –
        # nach einem Stromausfall startet die Lampe sonst mit ihren eigenen Werten.
        if self.auto and self.lite and self.curve:
            # Lite-Controller starten ihr Auto-Programm nach Stromausfall erst mit neu geschriebenem Ablauf.
            work = self.async_set_curve(list(self.curve), activate=True)
        elif self.auto:
            work = self.async_refresh()
        elif self.known:
            work = self._write(self.levels)
        else:
            return
        self._background(self._safe(work), "wiederherstellen")

    async def _write(self, levels: tuple[int, ...], *, keep_feeding: bool = False) -> None:
        levels = tuple(max(0, min(100, round(v))) for v in levels[: self.channels])
        if not keep_feeding:
            self._end_feeding()
        frames = [protocol.manual_mode(self.link.ids)] if self.auto else []
        frames += [
            protocol.set_channel(self.link.ids, ch, lv)
            for ch, lv in zip(self.model.wire, self._device_levels(levels), strict=False)
        ]
        await self.send(frames, wait=0.2)
        if self.auto:
            self.note("manuell übernommen (Auto aus)", logging.DEBUG)  # passiert bei jedem Farbwechsel aus Auto
        self.auto = False
        self.levels = levels
        self.known = True
        if any(levels):
            self.last_on = levels
        self.save()
        self.async_update_listeners()

    # --- Befehle -----------------------------------------------------------------------------------

    def _cancel_fade(self) -> None:
        if self._fade and not self._fade.done():
            self._fade.cancel()
        self._fade = None

    async def async_set_levels(self, levels: tuple[int, ...], transition: float | None = None) -> None:
        self._cancel_fade()
        start = self.levels
        largest = max((abs(a - b) for a, b in zip(levels, start, strict=False)), default=0)
        if not transition or transition < FADE_MIN_GAP or largest == 0 or self.auto or not self.known:
            await self._command(self._write(levels))
            return
        steps = max(1, min(int(transition / FADE_MIN_GAP), largest))
        await self._command(self._write(_mix(start, levels, 1 / steps)))
        if steps > 1:
            self._fade = self.entry.async_create_background_task(
                self.hass, self._run_fade(start, levels, steps, transition / steps), f"{DOMAIN} Übergang"
            )

    async def _run_fade(self, start: tuple[int, ...], target: tuple[int, ...], steps: int, gap: float) -> None:
        last = _mix(start, target, 1 / steps)
        for i in range(2, steps + 1):
            await _sleep(self.hass, gap)
            step = _mix(start, target, i / steps)
            if step == last:
                continue
            try:
                await self._write(step)
                last = step
            except ChihirosUnavailable as err:
                _LOGGER.debug("%s: Übergangsschritt %s/%s gescheitert: %s", self.name, i, steps, err)
        if last != target:
            await self._safe(self._write(target))

    async def async_set_auto(self, on: bool) -> None:
        self._cancel_fade()
        self._end_feeding()
        if on:
            await self.command([protocol.auto_mode(self.link.ids)])
            self.auto = True
            self.note("Auto an: Lampe spielt ihren Tagesablauf ab")
        else:
            await self.command(
                [protocol.manual_mode(self.link.ids)]
                + [
                    protocol.set_channel(self.link.ids, ch, lv)
                    for ch, lv in zip(self.model.wire, self._device_levels(self.levels), strict=False)
                ]
            )
            self.auto = False
            self.note("Auto aus: HA steuert die Farbe")
        self.save()
        self.async_update_listeners()

    async def async_set_curve(self, points: list[tuple[int, tuple[int, ...]]], activate: bool) -> None:
        """Tagesablauf in die Lampe schreiben: je Stützpunkt Uhrzeit + alle Kanäle in %."""
        if not 2 <= len(points) <= MAX_CURVE_POINTS or len({t for t, _ in points}) != len(points):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="bad_points",
                translation_placeholders={"max": str(MAX_CURVE_POINTS)},
            )
        points = sorted(points)
        self._cancel_fade()
        self._end_feeding()
        # Erst löschen und kurz warten: direkt danach gesendete Punkte verschluckt die Lampe manchmal
        # (08.10.2026: 08:59/09:00 fehlten → Rot blendete nachts Richtung Tageswert, Lampe glomm pink).
        await self.command([protocol.reset_auto(self.link.ids)], wait=RESET_PAUSE)
        await self.command(self._curve_frames(points), wait=0.5)
        if activate:
            await self.command([protocol.auto_mode(self.link.ids)], wait=0.5)
        missing = await self._missing_points(points) if activate else []
        for _ in range(CURVE_RETRIES):
            if not missing:
                break
            await self.command([*self._curve_frames(missing), protocol.auto_mode(self.link.ids)], wait=0.5)
            missing = await self._missing_points(points)
        if missing:
            times = ", ".join(f"{t // 60:02d}:{t % 60:02d}" for t, _ in missing)
            self.note(f"Tagesablauf unvollständig in der Lampe, fehlt: {times}")
            _LOGGER.warning("%s: Tagesablauf unvollständig, fehlende Punkte: %s", self.name, times)
        self.curve = tuple(points)
        self.weekday_settings = []
        if activate:
            self.auto = True
        self.note(f"Tagesablauf mit {len(points)} Stützpunkten gespeichert" + (", Auto an" if activate else ""))
        self.save()
        self.async_update_listeners()

    def _curve_frames(self, points: list[tuple[int, tuple[int, ...]]]) -> list[bytes]:
        ids = self.link.ids
        return [
            protocol.curve_point(ids, ch, t, self._device_levels(lv)[ch_index], sea_led=self.model.sea_led)
            for ch_index, ch in enumerate(self.model.wire)
            for t, lv in points
        ]

    async def _missing_points(self, points: list[tuple[int, tuple[int, ...]]]) -> list[tuple[int, tuple[int, ...]]]:
        """Punkte, die laut Snapshot der Lampe fehlen. Die Lampe meldet nur Kanal 0 und höchstens 8 Punkte.

        Nur bei der SeaLed-Familie prüfbar: BleLed-Lampen runden auf halbe Stunden.
        """
        if len(points) > SNAPSHOT_SLOTS or not self.model.sea_led:
            return []
        first = self.model.wire.index(0)
        seen = self._snapshots
        await self.command([protocol.status_query(self.link.ids)], wait=1.5)
        if self._snapshots == seen:
            return []  # keine Rückmeldung – nicht prüfbar, nicht blind nachschreiben
        stored = set(self.schedule_points)
        return [
            (t, lv)
            for t, lv in points
            if (t, self._device_levels(lv)[first]) not in stored and (t, self._device_levels(lv)[first]) != (0, 0)
        ]

    async def async_set_weekday_schedule(self, settings: list[dict[str, Any]]) -> None:
        """Tagesabläufe je Wochentag wie die App: Sonnenaufgang, -untergang, Rampe, Wochentage, Kanäle.

        Höchstens 7, jeder Wochentag nur in einer Einstellung. Ersetzt einen per Kurve geschriebenen Ablauf.
        """
        used: set[str] = set()
        for item in settings:
            days = set(item.get("weekdays") or protocol.WEEKDAY_BITS)
            if self.lite and days != set(protocol.WEEKDAY_BITS):
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="lite_every_day")
            if used & days or item["sunset"] <= item["sunrise"]:
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="bad_weekday_schedule")
            used |= days
        ids = self.link.ids
        frames = [protocol.reset_auto(ids)]
        for item in settings:
            # in der Reihenfolge der Kanalnummern (Rot/Weiß = 0 …), Rest füllt weekday_setting mit 255
            mine = self._device_levels(tuple(item.get(n, 0) for n in self.model.channel_names))
            levels = [lv for _, lv in sorted(zip(self.model.wire, mine, strict=True))]
            frames.append(
                protocol.weekday_setting(
                    ids,
                    item["sunrise"],
                    item["sunset"],
                    item["ramp"],
                    protocol.weekday_mask(item.get("weekdays")),
                    levels,
                )
            )
        frames.append(protocol.auto_mode(ids))
        self._cancel_fade()
        self._end_feeding()
        await self.command(frames, wait=1.0)
        self.weekday_settings = [
            {**item, "weekdays": list(item.get("weekdays") or protocol.WEEKDAY_BITS)} for item in settings
        ]
        self.curve = ()
        self.auto = True
        self.note(f"{len(settings)} Tagesabläufe je Wochentag gespeichert, Auto an")
        self.save()
        self.async_update_listeners()

    # --- Fütterungslicht ---------------------------------------------------------------------------

    def _feed_levels(self) -> tuple[int, ...]:
        """Hellste bekannte Mischung, auf 100 % hochgezogen."""
        base = max((lv for _, lv in self.curve), key=max, default=None) or self.last_on or (100,) * self.channels
        top = max(base) or 100
        return tuple(round(v * 100 / top) for v in base)

    async def async_feed(self) -> None:
        """Licht für die eingestellte Dauer hell; danach zurück zum vorherigen Zustand (auch Auto)."""
        restore = self._feed_restore or (self.auto, self.levels)
        self._cancel_fade()
        await self._command(self._write(self._feed_levels(), keep_feeding=True))
        self._feed_restore = restore
        if self._feed_cancel:
            self._feed_cancel()
        minutes = self.settings.feed_minutes
        self.feeding_until = dt_util.utcnow() + timedelta(minutes=minutes)
        self._feed_cancel = async_call_later(self.hass, minutes * 60, self._feed_done)
        self.note(f"Fütterungslicht für {minutes} Minuten")
        self.async_update_listeners()

    @callback
    def _feed_done(self, _now: datetime) -> None:
        restore, self._feed_restore, self._feed_cancel = self._feed_restore, None, None
        self.feeding_until = None
        if restore is None:
            return
        was_auto, levels = restore
        work = self.async_set_auto(True) if was_auto else self._write(levels)
        self._background(self._safe(work), "Fütterung beenden")
        self.async_update_listeners()

    def _end_feeding(self) -> None:
        """Ein anderer Befehl beendet das Fütterungslicht (kein Zurückspringen mehr)."""
        if self._feed_cancel:
            self._feed_cancel()
        self._feed_cancel = None
        self._feed_restore = None
        self.feeding_until = None

    # --- Sonnenverlauf und nächtliche Pflege -------------------------------------------------------

    def sun_curve(self, day: date) -> list[tuple[int, tuple[int, ...]]] | None:
        """Tagesablauf nach der echten Sonne am Standort, begrenzt auf frühestens/spätestens."""
        st = self.settings
        rise = get_astral_event_date(self.hass, "sunrise", day)
        dusk = get_astral_event_date(self.hass, "sunset", day)
        if rise is None or dusk is None:
            return None
        start = max(st.sun_earliest, _local_minutes(rise))
        end = min(st.sun_latest, _local_minutes(dusk))
        ramp = max(1, min(st.sun_ramp, (end - start) // 3))
        if end - start < 3 * ramp:
            return None
        day_levels = tuple(st.sun_levels) if st.sun_levels else self._feed_levels()
        off = (0,) * self.channels
        return [(0, off), (start, off), (start + ramp, day_levels), (end - ramp, day_levels), (end, off), (1439, off)]

    async def async_set_setting(self, key: str, value: Any) -> None:
        """Einstellung ändern und – wo nötig – sofort an die Lampe geben."""
        st = self.settings
        if key == "acclim_on" and value and not st.acclim_on:
            st.acclim_since = dt_util.now().date().isoformat()
        if key == "sun_on" and value and not st.sun_on:
            st.sun_levels = list(self._feed_levels())
        setattr(st, key, value)
        self.save()
        self.async_update_listeners()
        if key in ("acclim_on", "acclim_start", "acclim_weeks", "sun_on", "sun_ramp", "sun_earliest", "sun_latest"):
            await self._apply_program()

    async def _apply_program(self) -> None:
        """Aktuellen Ablauf mit Sonnenverlauf/Eingewöhnung neu in die Lampe schreiben."""
        if self.settings.sun_on:
            points = self.sun_curve(dt_util.now().date())
            if points:
                await self.async_set_curve(points, activate=True)
                return
        if self.auto and self.curve:
            await self.async_set_curve(list(self.curve), activate=True)
        elif self.auto and self.weekday_settings:
            await self.async_set_weekday_schedule(self.weekday_settings)
        elif not self.auto and self.known:
            await self._command(self._write(self.levels))

    @callback
    def _nightly(self, _now: datetime) -> None:
        """03:00 – Sonnenverlauf neu berechnen, Eingewöhnung einen Tag weiter."""
        st = self.settings
        finished = st.acclim_on and self.acclimatization_factor() >= 1
        if finished:
            st.acclim_on = False
            self.note("Eingewöhnung abgeschlossen – volle Helligkeit")
            self.save()
        if st.sun_on or st.acclim_on or finished:
            self._background(self._safe(self._apply_program()), "nachts")

    async def async_start(self) -> None:
        await super().async_start()
        self._unsubs.append(async_track_time_change(self.hass, self._nightly, hour=3, minute=0, second=0))
        self._unsubs.append(async_track_time_change(self.hass, self._program_tick, second=5))
        self._unsubs.append(async_track_time_interval(self.hass, self._regular_contact, LIGHT_CONTACT))

    @callback
    def _regular_contact(self, _now: datetime) -> None:
        """Stündlich: Status holen; die Verbindung stellt dabei die Uhr (wichtig für den Tagesablauf)."""
        self._background(self._safe(self.async_refresh()), "regelmäßiger Kontakt")

    @callback
    def _program_tick(self, _now: datetime) -> None:
        """Jede Minute: im Auto-Modus die Anzeige dem Tagesablauf nachführen."""
        if self.auto and self.curve:
            self.async_update_listeners()

    # --- Lüfter (WRGB VIVID III) -------------------------------------------------------------------

    async def async_set_fan_speed(self, percent: int) -> None:
        """Lüfter manuell; 1–24 % hebt die App auf 25 % an, 0 = aus."""
        percent = max(models.FAN_MIN_SPEED, percent) if percent > 0 else 0
        await self.command([protocol.fan_speed(self.link.ids, percent)])
        self.fan_auto, self.fan_speed = False, percent
        if percent:
            self.fan_last_speed = percent
        self.save()
        self.async_update_listeners()

    async def async_set_fan_auto(self) -> None:
        await self.command([protocol.fan_auto(self.link.ids)])
        self.fan_auto = True
        self.save()
        self.async_update_listeners()

    async def _command(self, aw: Awaitable[None]) -> None:
        try:
            await aw
        except ChihirosUnavailable as err:
            self.async_update_listeners()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unreachable",
                translation_placeholders={"name": self.name, "error": str(err)},
            ) from err

    async def async_stop(self) -> None:
        self._cancel_fade()
        if self._feed_cancel:
            self._feed_cancel()
        await super().async_stop()


@dataclass(slots=True)
class PumpCounters:
    """Was HA selbst über seine Handdosierungen weiß (Zähler der Pumpe kommen zusätzlich vom Gerät)."""

    volume: float = 1.0
    today_ml: float = 0.0
    total_ml: float = 0.0
    cycles: int = 0
    last_calibration: str | None = None
    day: str = field(default_factory=lambda: dt_util.now().date().isoformat())
    active: bool = True  # Kanal läuft (nicht pausiert)
    compensate: bool = False  # verpasste Dosen nach Unterbrechung nachholen
    capacity: float = 0.0  # Behältergröße in ml (0 = Füllstand aus)
    refill_at: str | None = None  # wann aufgefüllt
    refill_device_ul: int | None = None  # Gesamtzähler der Pumpe beim Auffüllen
    device_ul: int | None = None  # zuletzt gemeldeter Gesamtzähler (überlebt den Neustart)
    refill_ha_ml: float = 0.0  # HA-Zähler beim Auffüllen (falls die Pumpe nichts meldet)
    refill_start_ml: float | None = None  # Inhalt beim Auffüllen, None = voll (Behältergröße)
    notified: bool = False  # Push „Behälter fast leer“ schon geschickt (bis zum nächsten Auffüllen)
    plan: dict[str, Any] | None = None  # zuletzt gesetzter Plan (nur Anzeige – die Pumpe meldet ihn nicht)


class ChihirosDoser(ChihirosDevice):
    """Dosierpumpe mit 1–8 Kanälen."""

    kind = "doser"
    model_name = "Dosing Pump"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str) -> None:
        super().__init__(hass, entry, address, name)
        self.pump_count = int(entry.data.get(CONF_PUMP_COUNT) or entry.options.get(CONF_PUMP_COUNT) or DEFAULT_PUMPS)
        self.pumps = [PumpCounters() for _ in range(self.pump_count)]
        self.device_daily_ul: tuple[int, ...] | None = None
        self.device_total_ul: tuple[int, ...] | None = None
        self.delay = False
        self._recheck: CALLBACK_TYPE | None = None  # Zähler kurz nach einer Dosis neu lesen

    def _extra_prelude(self, ids: protocol.MessageIds) -> list[bytes]:
        return protocol.dosing_auth(ids)

    def _load(self, data: dict[str, Any]) -> None:
        self.delay = bool(data.get("delay", False))
        for i, raw in enumerate(data.get("pumps") or []):
            if i < self.pump_count and isinstance(raw, dict):
                self.pumps[i] = PumpCounters(**{k: v for k, v in raw.items() if k in PumpCounters.__slots__})
        # Gesamtzähler aus dem Speicher anzeigen, bis die Pumpe nach dem Start neu meldet
        known = [p.device_ul for p in self.pumps]
        if all(ul is not None for ul in known):
            self.device_total_ul = tuple(ul for ul in known if ul is not None)
        self._roll_day()

    def _dump(self) -> dict[str, Any]:
        return {"delay": self.delay, "pumps": [{s: getattr(p, s) for s in PumpCounters.__slots__} for p in self.pumps]}

    def _roll_day(self) -> None:
        today = dt_util.now().date().isoformat()
        for p in self.pumps:
            if p.day != today:
                p.day, p.today_ml = today, 0.0

    async def async_start(self) -> None:
        await super().async_start()
        self._unsubs.append(async_track_time_interval(self.hass, self._poll, DOSER_POLL))

    @callback
    def _poll(self, _now: datetime) -> None:
        self._roll_day()
        self._background(self._safe(self.async_refresh()), "Zähler abfragen")

    async def async_refresh(self) -> None:
        await self.send(protocol.dosing_auth(self.link.ids), wait=1.5)

    def _handle(self, parsed: protocol.Notification) -> None:
        super()._handle(parsed)
        if isinstance(parsed, protocol.DosingCounters):
            values = parsed.microliters[: self.pump_count]
            if parsed.daily:
                self.device_daily_ul = values
                self.async_update_listeners()
            else:
                self.device_total_ul = values
                self._on_device_total(values)

    @callback
    def _on_device_total(self, values: tuple[int, ...]) -> None:
        """Gesamtzähler merken; Füllstand ohne Zähler-Startwert (Pumpe war beim Auffüllen still) hier verankern."""
        for p, ul in zip(self.pumps, values, strict=False):
            p.device_ul = ul
            if p.refill_at is not None and p.refill_device_ul is None:
                # was HA seit dem Auffüllen schon selbst dosiert hat, zählt als verbraucht
                p.refill_device_ul = ul - round((p.total_ml - p.refill_ha_ml) * 1000)
        self.save()
        self._check_containers()
        self.async_update_listeners()

    def _check(self, pump: int) -> None:
        if not 1 <= pump <= self.pump_count:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="bad_pump",
                translation_placeholders={"count": str(self.pump_count)},
            )

    async def async_dose(self, pump: int, ml: float | None = None) -> None:
        self._check(pump)
        amount = self.pumps[pump - 1].volume if ml is None else ml
        frame = protocol.dose(self.link.ids, pump - 1, amount)
        await self.command([*protocol.dosing_auth(self.link.ids), frame], wait=1.0, retry=False)
        self._roll_day()
        p = self.pumps[pump - 1]
        p.today_ml = round(p.today_ml + amount, 2)
        p.total_ml = round(p.total_ml + amount, 2)
        p.cycles += 1
        self.note(f"Pumpe {pump}: {amount} ml dosiert")
        self.save()
        self._check_containers()
        # Die Pumpe läuft noch: Zähler kurz danach neu lesen (ein Timer, eine weitere Dosis ersetzt ihn).
        self._cancel_recheck()
        self._recheck = async_call_later(self.hass, 60, self._recheck_counters)

    @callback
    def _recheck_counters(self, now: datetime) -> None:
        self._recheck = None  # ausgelöst, Handle ist verbraucht
        self._poll(now)

    def _cancel_recheck(self) -> None:
        if self._recheck:
            self._recheck()
            self._recheck = None

    async def async_stop(self) -> None:
        self._cancel_recheck()
        await super().async_stop()

    async def async_calibrate(self, pump: int, seconds: int | None, measured_ml: float | None) -> None:
        self._check(pump)
        await self.command([protocol.calibrate(self.link.ids, pump - 1, seconds, measured_ml)], retry=False)
        if measured_ml is not None:
            self.pumps[pump - 1].last_calibration = dt_util.now().isoformat(timespec="seconds")
            self.note(f"Pumpe {pump}: kalibriert mit {measured_ml} ml")
            self.save()
        self.async_update_listeners()

    async def async_program(self, pump: int, times: list[int], ml_each: float) -> None:
        await self.async_plan(pump, "daily", doses=[(t, ml_each) for t in times])

    async def async_plan(
        self,
        pump: int,
        mode: str,
        *,
        doses: list[tuple[int, float]] | None = None,
        weekdays: list[str] | None = None,
        spread: tuple[int, int, int, float] | None = None,
    ) -> None:
        """Dosierplan wie die App: daily (24 h), once (einmalig), timer, spread („frei“ über ein Zeitfenster)."""
        self._check(pump)
        kinds = {
            "once": protocol.PLAN_ONCE,
            "daily": protocol.PLAN_DAILY,
            "spread": protocol.PLAN_SPREAD,
            "timer": protocol.PLAN_TIMER,
        }
        p = self.pumps[pump - 1]
        try:
            frames = protocol.plan(
                self.link.ids,
                pump - 1,
                kinds[mode],
                weekdays=protocol.weekday_mask(weekdays),
                doses=doses or (),
                spread=spread,
                compensate=p.compensate,
            )
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="bad_plan", translation_placeholders={"error": str(err)}
            ) from err
        await self.command(frames, wait=1.0)
        p.active = True
        p.plan = {
            "mode": mode,
            "weekdays": list(weekdays or protocol.WEEKDAY_BITS),
            "doses": [{"time": _hhmm(t), "ml": ml} for t, ml in sorted(doses or [])],
            "spread": None
            if spread is None
            else {"start": _hhmm(spread[0]), "minutes": spread[1], "count": spread[2], "ml_per_day": spread[3]},
        }
        self.note(f"Pumpe {pump}: Plan „{mode}“ gesetzt")
        self.save()
        self.async_update_listeners()

    async def async_clear_program(self, pump: int) -> None:
        self._check(pump)
        await self.command([protocol.reset_program(self.link.ids, pump - 1)])
        self.pumps[pump - 1].plan = None
        self.note(f"Pumpe {pump}: Plan gelöscht")
        self.save()
        self.async_update_listeners()

    async def async_set_channel_state(
        self, pump: int, *, active: bool | None = None, compensate: bool | None = None
    ) -> None:
        """Kanal pausieren/aktivieren und „verpasste Dosen nachholen“ – der Plan bleibt in der Pumpe."""
        self._check(pump)
        p = self.pumps[pump - 1]
        new_active = p.active if active is None else active
        new_comp = p.compensate if compensate is None else compensate
        await self.command([protocol.channel_state(self.link.ids, pump - 1, active=new_active, compensate=new_comp)])
        p.active, p.compensate = new_active, new_comp
        self.save()
        self.async_update_listeners()

    async def async_set_delay(self, on: bool) -> None:
        await self.command([protocol.dosing_delay(self.link.ids, on)])
        self.delay = on
        self.save()
        self.async_update_listeners()

    # --- Behälter ----------------------------------------------------------------------------------

    def used_since_refill(self, pump: int) -> float | None:
        p = self.pumps[pump - 1]
        if not p.capacity or p.refill_at is None:
            return None
        if p.refill_device_ul is not None and p.device_ul is not None:
            return max(0.0, (p.device_ul - p.refill_device_ul) / 1000)
        return max(0.0, p.total_ml - p.refill_ha_ml)

    def remaining(self, pump: int) -> float | None:
        used = self.used_since_refill(pump)
        if used is None:
            return None
        p = self.pumps[pump - 1]
        start = p.capacity if p.refill_start_ml is None else p.refill_start_ml
        return round(max(0.0, start - used), 1)

    def days_left(self, pump: int) -> int | None:
        p = self.pumps[pump - 1]
        used, left = self.used_since_refill(pump), self.remaining(pump)
        since = dt_util.parse_datetime(p.refill_at) if p.refill_at else None
        if used is None or left is None or since is None:
            return None
        days = (dt_util.utcnow() - since).total_seconds() / 86400
        if days < 1 or used <= 0:
            return None  # noch zu wenig Erfahrung für eine Schätzung
        return int(left / (used / days))

    def refill(self, pump: int) -> None:
        self._check(pump)
        p = self.pumps[pump - 1]
        p.refill_at = dt_util.utcnow().isoformat(timespec="seconds")
        p.refill_device_ul = p.device_ul
        p.refill_ha_ml = p.total_ml
        p.refill_start_ml = None
        p.notified = False
        self.note(f"Pumpe {pump}: Behälter aufgefüllt ({p.capacity} ml)")
        self.save()
        self._check_containers()

    def set_remaining(self, pump: int, ml: float) -> None:
        """Füllstand ab jetzt auf `ml` setzen (z. B. Behälter nur teilweise gefüllt); zählt von hier weiter."""
        self._check(pump)
        p = self.pumps[pump - 1]
        if not p.capacity:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_capacity", translation_placeholders={"pump": str(pump)}
            )
        self.refill(pump)
        p.refill_start_ml = min(float(ml), p.capacity)
        p.notified = False
        self.note(f"Pumpe {pump}: Restmenge auf {p.refill_start_ml:g} ml gesetzt")
        self.save()
        self._check_containers()
        self.async_update_listeners()

    def set_capacity(self, pump: int, ml: float) -> None:
        p = self.pumps[pump - 1]
        p.capacity = ml
        if ml and p.refill_at is None:
            self.refill(pump)  # Füllstand startet „voll“
        self.save()
        self._check_containers()

    @callback
    def _check_containers(self) -> None:
        """Reparatur-Hinweis „Behälter fast leer“ (unter 10 % oder weniger als 5 Tage)."""
        for n in range(1, self.pump_count + 1):
            issue = f"container_{self.entry.entry_id}_{n}"
            left, days, cap = self.remaining(n), self.days_left(n), self.pumps[n - 1].capacity
            if left is not None and cap and (left <= cap * 0.1 or (days is not None and days <= 5)):
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="container_low",
                    translation_placeholders={
                        "name": self.name,
                        "pump": str(n),
                        "ml": f"{left:.0f}",
                        "days": "?" if days is None else str(days),
                    },
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue)
            self._maybe_notify(n, left, days, cap)
        self.async_update_listeners()

    @callback
    def _maybe_notify(self, pump: int, left: float | None, days: int | None, cap: float) -> None:
        """Einmalige Push-Nachricht unter der eingestellten Schwelle (Optionen); neu erst nach dem Auffüllen."""
        p = self.pumps[pump - 1]
        target = str(self.entry.options.get(CONF_NOTIFY) or "")
        if left is None or not cap:
            return
        percent = float(self.entry.options.get(CONF_NOTIFY_PERCENT, DEFAULT_NOTIFY_PERCENT))
        low = left <= cap * percent / 100
        if not low:
            if p.notified:
                p.notified = False  # z. B. Behältergröße erhöht
                self.save()
            return
        if p.notified or not target.startswith("notify."):
            return
        p.notified = True
        self.save()
        reach = "" if days is None else f" (reicht noch ca. {days} Tage)"
        self.hass.async_create_task(
            self._push(
                target,
                {
                    "title": f"{self.name}: Pumpe {pump} fast leer",
                    "message": f"Noch etwa {left:.0f} ml von {cap:.0f} ml{reach}. Bitte nachfüllen und danach "
                    f"„Pumpe {pump} Behälter aufgefüllt“ drücken.",
                },
            ),
            f"{DOMAIN}_notify_pump_{pump}",
        )

    async def _push(self, target: str, data: dict[str, str]) -> None:
        """Push schicken; ein fehlender oder kaputter notify-Dienst wird verständlich geloggt statt als Task-Fehler."""
        try:
            await self.hass.services.async_call("notify", target.removeprefix("notify."), data, blocking=True)
        except (HomeAssistantError, vol.Invalid) as err:
            _LOGGER.warning("%s: Push über %s fehlgeschlagen: %s", self.name, target, err)

    async def async_reset_totals(self, pump: int) -> None:
        self._check(pump)
        await self.command(
            [protocol.reset_totals(self.link.ids, pump - 1), *protocol.dosing_auth(self.link.ids)], wait=1.5
        )
        p = self.pumps[pump - 1]
        p.total_ml, p.cycles = 0.0, 0
        self.save()
        self.async_update_listeners()

    def set_volume(self, pump: int, ml: float) -> None:
        self.pumps[pump - 1].volume = ml
        self.save()
        self.async_update_listeners()


async def _sleep(hass: HomeAssistant, seconds: float) -> None:
    """Warten über den HA-Zeitplaner (in Tests per Zeitsprung steuerbar)."""
    done: asyncio.Future[None] = hass.loop.create_future()

    @callback
    def wake(_now: datetime) -> None:
        if not done.done():
            done.set_result(None)

    cancel = async_call_later(hass, seconds, wake)
    try:
        await done
    finally:
        cancel()


def _mix(a: tuple[int, ...], b: tuple[int, ...], f: float) -> tuple[int, ...]:
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b, strict=False))


DeviceFactory = Callable[[HomeAssistant, ConfigEntry, str, str], ChihirosDevice]


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
