"""Eine BLE-Verbindung pro Gerät – über die Bluetooth-Proxys von Home Assistant.

Regeln (aus Erfahrung mit ESP32-Bluetooth-Proxys, die nur wenige Verbindungsplätze haben):
- immer nur ein Vorgang gleichzeitig (Lock), das Schreiben hat eine harte Obergrenze (OP_TIMEOUT),
- der Verbindungsaufbau wird nie von außen abgebrochen (Zeitgrenzen je Versuch setzt `establish_connection`):
  ein abgebrochener Aufbau über einen ESPHome-Proxy kann eine verwaiste Verbindung hinterlassen – das Gerät ist dann
  belegt und bleibt unerreichbar, bis der Proxy neu startet,
- nach dem letzten Befehl wird nach IDLE_DISCONNECT Sekunden getrennt, damit der Proxy-Platz frei wird
  (das Original hielt 120 s – mit Fluval + zwei Chihiros auf zwei Proxys führte das zu OutOfConnectionSlots),
- bei jeder neuen Verbindung schickt die App Status + Uhrzeit (Prelude) – das stellt auch die Uhr nach
  Stromausfall,
- ein Fehler trennt sofort; der nächste Versuch baut neu auf.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
import logging
from typing import Any

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import protocol
from .const import (
    BATCH_GAP,
    CUSTOM_NOTIFY_CHAR,
    HM10_RX_CHAR,
    IDLE_DISCONNECT,
    OP_TIMEOUT,
    UART_RX_CHAR,
    UART_TX_CHAR,
)

_LOGGER = logging.getLogger(__name__)

# Höchstens zwei Versuche je `send()`: nach dem ersten Fehlschlag wird die Verbindung neu aufgebaut.
LAST_ATTEMPT = 2


class ChihirosUnavailable(Exception):
    """Gerät gerade nicht erreichbar (kein Proxy sieht es, Verbindung scheitert oder dauert zu lange)."""


class ChihirosLink:
    """Verbindung zu einem Gerät. `send()` ist der einzige Weg, Rahmen zu schicken."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        on_notification: Callable[[bytes], None],
        extra_prelude: Callable[[protocol.MessageIds], list[bytes]] | None = None,
    ) -> None:
        self.hass = hass
        self.address = address
        self.name = name
        self.ids = protocol.MessageIds()
        self._on_notification = on_notification
        self._extra_prelude = extra_prelude
        self._client: BleakClientWithServiceCache | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None
        self.connects = 0
        self.failures = 0
        self.last_error: str | None = None

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def send(self, frames: Sequence[bytes], wait: float = 0.5, *, retry: bool = True) -> None:
        """Rahmen schicken; danach `wait` Sekunden auf Rückmeldungen warten.

        `retry=False` für Befehle, die nicht doppelt ausgeführt werden dürfen (Dosieren): ein zweiter
        Versuch könnte sonst zweimal dosieren, wenn der erste angekommen ist, die Antwort aber nicht.
        """
        async with self._lock:
            self._cancel_idle()
            try:
                # Ohne retry gibt es nur den letzten Versuch: ein Fehler endet dann sofort als ChihirosUnavailable.
                attempts = (1, LAST_ATTEMPT) if retry else (LAST_ATTEMPT,)
                for attempt in attempts:
                    try:
                        await self._send_once(frames)
                        break
                    except TimeoutError:
                        raise
                    except (*BLEAK_RETRY_EXCEPTIONS, ChihirosUnavailable) as err:
                        await self._drop()
                        if attempt == LAST_ATTEMPT:
                            raise ChihirosUnavailable(f"{type(err).__name__}: {err}".strip()) from err
                        _LOGGER.debug("%s: Versuch %s gescheitert: %s", self.name, attempt, err)
                if wait:
                    await asyncio.sleep(wait)
            except TimeoutError as err:
                await self._drop()
                self._failed(f"Zeitüberschreitung nach {OP_TIMEOUT} s")
                raise ChihirosUnavailable(f"Zeitüberschreitung nach {OP_TIMEOUT} s") from err
            except ChihirosUnavailable as err:
                self._failed(str(err))
                raise
            self.last_error = None
            self._arm_idle()

    def _failed(self, error: str) -> None:
        self.failures += 1
        self.last_error = error

    async def _send_once(self, frames: Sequence[bytes]) -> None:
        if not self.connected:
            await self._connect()  # ohne äußere Zeitgrenze, siehe Modulbeschreibung
        client, char = self._client, self._write_char
        if client is None or char is None:
            raise ChihirosUnavailable("keine Verbindung")
        async with asyncio.timeout(OP_TIMEOUT):
            for i, frame in enumerate(frames):
                if i:
                    await asyncio.sleep(BATCH_GAP)
                await client.write_gatt_char(char, frame, False)

    async def _connect(self) -> None:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            raise ChihirosUnavailable("kein Bluetooth-Proxy sieht das Gerät")
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.name,
            disconnected_callback=self._on_disconnect,
            max_attempts=2,
            ble_device_callback=lambda: (
                bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True) or device
            ),
        )
        services = client.services
        write = services.get_characteristic(HM10_RX_CHAR) or services.get_characteristic(UART_RX_CHAR)
        if write is None:
            await client.disconnect()
            raise ChihirosUnavailable("Schreib-Merkmal fehlt")
        if write.uuid.lower() == UART_RX_CHAR.lower():
            notify = services.get_characteristic(UART_TX_CHAR)
        else:
            # HM-10: die App bekommt Rückmeldungen auf FFE1 selbst (8EC90003 ist laut Original v0.19.0 nur fürs
            # Firmware-Update); der alte Weg bleibt Ersatz für Geräte, deren FFE1 nicht melden kann
            notify = (
                write
                if "notify" in write.properties
                else services.get_characteristic(CUSTOM_NOTIFY_CHAR) or services.get_characteristic(UART_TX_CHAR)
            )
        self._client, self._write_char = client, write
        self.connects += 1
        first = protocol.prelude(self.ids, dt_util.now())
        if self._extra_prelude:
            first += self._extra_prelude(self.ids)
        async with asyncio.timeout(OP_TIMEOUT):  # Verbindung steht: GATT-Vorgänge dürfen begrenzt werden
            if notify is not None:
                await client.start_notify(notify, self._notified)
            for frame in first:
                await client.write_gatt_char(write, frame, False)
                await asyncio.sleep(BATCH_GAP)

    def _notified(self, _sender: Any, data: bytearray) -> None:
        try:
            self._on_notification(bytes(data))
        except Exception:
            _LOGGER.exception("%s: Rückmeldung nicht verarbeitbar: %s", self.name, data.hex())

    def _on_disconnect(self, client: Any) -> None:
        # Der Rückruf einer alten, schon verworfenen Verbindung kann über den Proxy verspätet kommen, wenn längst
        # eine neue steht; die neue dann nicht vergessen (sonst bliebe sie verwaist und belegte den Proxy-Platz).
        if client is not None and self._client is not None and client is not self._client:
            _LOGGER.debug("%s: Trennung einer alten Verbindung gemeldet, aktuelle bleibt", self.name)
            return
        self._client = None
        self._write_char = None

    def _arm_idle(self) -> None:
        self._cancel_idle()
        self._idle = self.hass.loop.call_later(
            IDLE_DISCONNECT, lambda: self.hass.async_create_background_task(self.disconnect(), "chihiros trennen")
        )

    def _cancel_idle(self) -> None:
        if self._idle:
            self._idle.cancel()
            self._idle = None

    async def _drop(self) -> None:
        client, self._client, self._write_char = self._client, None, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001 – Trennen darf nie selbst scheitern
                _LOGGER.debug("%s: Trennen gescheitert: %s", self.name, err)

    async def disconnect(self) -> None:
        self._cancel_idle()
        if self._lock.locked():
            return  # ein Vorgang läuft – der stellt den Timer neu
        await self._drop()
