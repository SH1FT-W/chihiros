"""Verbindungsschicht: Verbinden über HA-Bluetooth, Prelude, Wiederholen, Zeitlimit, Trennen nach Leerlauf."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
import pytest

from custom_components.chihiros import ble
from custom_components.chihiros.const import UART_RX_CHAR, UART_TX_CHAR


def make_client(write_uuid: str = UART_RX_CHAR, notify: bool = True):
    client = MagicMock()
    client.is_connected = True
    write = MagicMock()
    write.uuid = write_uuid
    tx = MagicMock() if notify else None

    def get_char(uuid: str):
        if uuid.lower() == write_uuid.lower():
            return write
        if uuid.lower() == UART_TX_CHAR.lower():
            return tx
        return None

    client.services.get_characteristic.side_effect = get_char
    client.write_gatt_char = AsyncMock()
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    return client


@pytest.fixture
def link(hass: HomeAssistant):
    got: list[bytes] = []
    with (
        patch.object(ble, "BATCH_GAP", 0),
        patch.object(ble.bluetooth, "async_ble_device_from_address", return_value=MagicMock()),
    ):
        lk = ble.ChihirosLink(hass, "AA:BB", "Test", got.append, lambda ids: [b"\xa5extra"])
        yield lk, got
        lk._cancel_idle()


async def test_connect_prelude_and_idle_disconnect(hass: HomeAssistant, link):
    lk, got = link
    client = make_client()
    with (
        patch.object(ble, "establish_connection", AsyncMock(return_value=client)),
        patch.object(ble, "IDLE_DISCONNECT", 0.01),
    ):
        await lk.send([b"frame1", b"frame2"], wait=0)
        written = [c.args[1] for c in client.write_gatt_char.call_args_list]
        assert len(written) == 6 and written[3] == b"\xa5extra" and written[-2:] == [b"frame1", b"frame2"]
        assert lk.connects == 1 and lk.connected
        client.start_notify.call_args.args[1](None, bytearray(b"\x5b\x01"))
        assert got == [b"\x5b\x01"]
        await lk.send([b"frame3"], wait=0)  # Verbindung wird weiterbenutzt
        assert lk.connects == 1
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        client.disconnect.assert_awaited()


async def test_retry_then_fail(hass: HomeAssistant, link):
    lk, _ = link
    client = make_client()
    client.write_gatt_char.side_effect = [None, None, None, BleakError("weg")] + [None] * 10
    with patch.object(ble, "establish_connection", AsyncMock(return_value=client)):
        await lk.send([b"x"], wait=0)  # zweiter Versuch klappt
    assert lk.connects == 2 and lk.last_error is None
    lk._on_disconnect(client)
    with (
        patch.object(ble, "establish_connection", AsyncMock(side_effect=BleakError("kein Platz"))),
        pytest.raises(ble.ChihirosUnavailable, match="kein Platz"),
    ):
        await lk.send([b"x"], wait=0)
    assert lk.failures == 1 and "kein Platz" in lk.last_error


async def test_no_retry_for_doses(hass: HomeAssistant, link):
    lk, _ = link
    connect = AsyncMock(side_effect=BleakError("weg"))
    with patch.object(ble, "establish_connection", connect), pytest.raises(ble.ChihirosUnavailable):
        await lk.send([b"dose"], wait=0, retry=False)
    assert connect.await_count == 1


async def test_timeout(hass: HomeAssistant, link):
    """Hängt das Schreiben bei bestehender Verbindung, ist nach OP_TIMEOUT Schluss."""
    lk, _ = link
    client = make_client()

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    client.write_gatt_char.side_effect = slow
    with (
        patch.object(ble, "establish_connection", AsyncMock(return_value=client)),
        patch.object(ble, "OP_TIMEOUT", 0.05),
        pytest.raises(ble.ChihirosUnavailable, match="Zeitüberschreitung"),
    ):
        await lk.send([b"x"], wait=0)
    assert not lk.connected
    client.disconnect.assert_awaited()


async def test_slow_connect_is_not_cancelled(hass: HomeAssistant, link):
    """Ein langsamer Verbindungsaufbau läuft zu Ende – abbrechen hinterließe eine verwaiste Proxy-Verbindung."""
    lk, _ = link
    client = make_client()

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.2)
        return client

    with patch.object(ble, "establish_connection", slow), patch.object(ble, "OP_TIMEOUT", 0.05):
        await lk.send([b"x"], wait=0)
    assert lk.connected and lk.last_error is None


async def test_not_seen_and_missing_char(hass: HomeAssistant, link):
    lk, _ = link
    with (
        patch.object(ble.bluetooth, "async_ble_device_from_address", return_value=None),
        pytest.raises(ble.ChihirosUnavailable, match="Proxy"),
    ):
        await lk.send([b"x"], wait=0)
    client = make_client(write_uuid="0000ffff-0000-1000-8000-00805f9b34fb")
    with (
        patch.object(ble, "establish_connection", AsyncMock(return_value=client)),
        pytest.raises(ble.ChihirosUnavailable),
    ):
        await lk.send([b"x"], wait=0)


async def test_hm10_write_and_bad_notification(hass: HomeAssistant, link):
    lk, _got = link
    client = make_client(write_uuid=ble.HM10_RX_CHAR)
    with patch.object(ble, "establish_connection", AsyncMock(return_value=client)):
        await lk.send([b"x"], wait=0)
    lk._on_notification = MagicMock(side_effect=ValueError("kaputt"))
    lk._notified(None, bytearray(b"\x01"))  # darf nicht abstürzen
    lk._on_disconnect(MagicMock())  # verspätete Meldung einer alten Verbindung: aktuelle bleibt
    assert lk.connected
    lk._on_disconnect(client)
    assert not lk.connected
    client.disconnect.side_effect = BleakError("schon weg")
    lk._client = client
    await lk.disconnect()
