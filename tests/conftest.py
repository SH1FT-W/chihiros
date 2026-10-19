"""Nachgebaute Geräte: Lampen und Dosierpumpe, mit Rückmeldungen wie in den Mitschnitten der App."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.chihiros.ble import ChihirosUnavailable
from custom_components.chihiros.const import DOMAIN

LIGHT_ADDR = "C0:FF:EE:12:34:56"
DOSER_ADDR = "D0:5E:00:AB:CD:EF"


class FakeBluetooth:
    """Ersetzt homeassistant.components.bluetooth im Koordinator: Sichtbarkeit steuerbar."""

    BluetoothCallbackMatcher = dict
    BluetoothScanningMode = MagicMock()
    BluetoothServiceInfoBleak = object

    def __init__(self) -> None:
        self.present = True
        self.adv: dict[str, Callable[..., None]] = {}
        self.gone: dict[str, Callable[..., None]] = {}

    def async_address_present(self, hass: Any, address: str, connectable: bool = True) -> bool:
        return self.present

    def async_register_callback(
        self, hass: Any, cb: Callable[..., None], matcher: dict, mode: Any
    ) -> Callable[[], None]:
        self.adv[matcher["address"]] = cb
        return lambda: None

    def async_track_unavailable(
        self, hass: Any, cb: Callable[..., None], address: str, connectable: bool
    ) -> Callable[[], None]:
        self.gone[address] = cb
        return lambda: None

    def advertise(self, address: str, rssi: int = -70) -> None:
        info = MagicMock()
        info.rssi = rssi
        self.adv[address](info, None)

    def vanish(self, address: str) -> None:
        self.gone[address](MagicMock())


class FakeDevices:
    """Fängt alle gesendeten Rahmen ab und antwortet wie die Geräte."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bool]] = []
        self.offline = False
        self.firmware = 0x17
        self.daily = [0, 1, 2, 3]  # 0,1-ml-Schritte × 100 µl
        self.total = [1055, 0, 40, 30]
        self.red_curve: list[tuple[int, int, int]] | None = None  # None = Mitschnitt-Ablauf aus der Doku
        self.drop_after_reset = 0  # so viele Rahmen nach „Ablauf löschen“ verschlucken (wie die echte Lampe)
        self._dropping = 0
        self.fan = False  # WRGB VIVID III: Lüfter-Statusmeldung auf jede Statusabfrage

    def frames(self, name: str) -> list[bytes]:
        return [f for n, f, _ in self.sent if n == name]

    def clear(self) -> None:
        self.sent.clear()

    async def send(self, link: Any, frames: Sequence[bytes], wait: float = 0.5, *, retry: bool = True) -> None:
        if self.offline:
            link.failures += 1
            link.last_error = "BleakNotFoundError: kein Proxy"
            raise ChihirosUnavailable("BleakNotFoundError: kein Proxy")
        link.last_error = None
        for f in frames:
            self.sent.append((link.name, bytes(f), retry))
            if self._dropping:
                self._dropping -= 1
                continue
            self._answer(link, bytes(f))

    def _answer(self, link: Any, f: bytes) -> None:
        cmd, mode = f[0], f[5]
        if cmd == 0x5A and mode == 0x05 and f[6] == 5:
            self.red_curve = []
            self._dropping = self.drop_after_reset
        if cmd == 0x5A and mode == 0x06 and f[6] == 0 and self.red_curve is not None:
            self.red_curve.append((f[7], f[8], f[9]))
        if cmd == 0x5A and mode == 0x04:
            # Laufzeit 511 Min. (Mitschnitt aus der Doku) + Tagesablauf-Snapshot
            link._on_notification(
                bytes.fromhex("5b") + bytes([self.firmware]) + bytes.fromhex("0a00010a01ffffffff13888c")
            )
            snap = bytearray([0x5B, self.firmware, 0x30, 0, 2, 0xFE]) + bytes(19)
            if self.red_curve is None:
                snap += bytes([9, 0, 0, 10, 0, 70, 19, 0, 70, 19, 30, 0, 0, 0, 0, 0])
            else:
                for point in self.red_curve[:8]:
                    snap += bytes(point)
                snap += bytes(3 * (8 - min(8, len(self.red_curve))))
            link._on_notification(bytes(snap))
            if self.fan:  # 600 U/min, 25 °C (Mitschnitt aus der Doku)
                link._on_notification(bytes.fromhex("5b1b1000010b02581900010000000000004822"))
        if cmd == 0xA5 and mode == 0x04:
            values = self.total if f[6] == 4 else self.daily
            body = bytearray([0x5B, 0x10, 0x10, 0, 1, 0x1E if f[6] == 4 else 0x22])
            for v in values:
                body += bytes(divmod(v, 256))
            link._on_notification(bytes(body))


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, hass: HomeAssistant):
    hass.config.language = "de"
    # Bluetooth wird im Koordinator komplett nachgebaut (FakeBluetooth); die echte Komponente braucht D-Bus.
    hass.config.components.update({"bluetooth", "bluetooth_adapters"})


@pytest.fixture
def bt():
    fake = FakeBluetooth()
    with patch("custom_components.chihiros.coordinator.bluetooth", fake):
        yield fake


@pytest.fixture
def devices(bt):
    fake = FakeDevices()

    async def send(self, frames, wait=0.5, *, retry=True):
        await fake.send(self, frames, wait, retry=retry)

    with patch("custom_components.chihiros.ble.ChihirosLink.send", send):
        yield fake


def light_entry() -> MockConfigEntry:
    """So liegt der Eintrag der alten Integration (Original) in einer bestehenden Installation."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id="01JAAAAAAAAAAAAAAAAAAAAAA1",
        title="DYNCRGPC0FFEE123456",
        unique_id=LIGHT_ADDR,
        version=1,
        source="bluetooth",
        data={"address": LIGHT_ADDR},
    )


def doser_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id="01JAAAAAAAAAAAAAAAAAAAAAA2",
        title="DYDOSED05E00ABCDEF",
        unique_id=DOSER_ADDR,
        version=1,
        source="bluetooth",
        data={"address": DOSER_ADDR, "pump_count": 4},
    )


@pytest.fixture
async def light(hass: HomeAssistant, devices: FakeDevices):
    entry = light_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state.recoverable:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture
async def doser(hass: HomeAssistant, devices: FakeDevices):
    entry = doser_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state.recoverable:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
