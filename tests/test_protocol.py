"""Rahmen gegen die Mitschnitte aus der Protokoll-Doku (My Chihiros 2.8.59)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.chihiros import protocol as p


def ids_at(hi: int, lo: int) -> p.MessageIds:
    ids = p.MessageIds()
    ids._hi, ids._lo = hi, lo
    return ids


def test_manual_brightness_capture():
    # 5a 01 07 00 20 07 00 64 45 – Kanal 0 auf 100 %
    assert p.set_channel(ids_at(0, 0x1F), 0, 100).hex(" ") == "5a 01 07 00 20 07 00 64 45"


def test_status_query_capture():
    assert p.status_query(ids_at(0, 3)).hex(" ") == "5a 01 06 00 04 04 01 06"


def test_manual_dose_capture():
    # 165 1 10 0 6 27 0 0 0 0 20 2 – Pumpe 0, 2,0 ml
    assert list(p.dose(ids_at(0, 5), 0, 2.0)) == [165, 1, 10, 0, 6, 27, 0, 0, 0, 0, 20, 2]


def test_message_ids_skip_reserved():
    ids = ids_at(0, 0x59)
    assert ids.next() == (0, 0x5B)
    ids = ids_at(0x59, 0xFF)
    assert ids.next() == (0x5B, 0)
    ids = ids_at(255, 255)
    assert ids.next() == (0, 1)


def test_parameters_and_checksum_verbatim_only_message_id_avoids_reserved():
    frame = p.set_channel(ids_at(0, 1), 0, 90)
    assert frame[7] == 0x5A  # 90 % bleiben 90 % (wie die App)
    point = p.curve_point(ids_at(0, 1), 0, 9 * 60, 90)
    assert point[-2] == 0x5A
    for n in range(300):
        frame = p.set_channel(ids_at(0, n % 255), n % 3, n % 101)
        assert 0x5A not in frame[3:5], "Nachrichten-ID meidet 0x5A"
        assert frame[-1] == p.checksum(frame[:-1]), "Prüfsumme unverändert"


def test_set_time_and_prelude():
    now = datetime(2026, 10, 7, 19, 30, 5, tzinfo=UTC)
    frame = p.set_time(ids_at(0, 0), now)
    assert list(frame[6:12]) == [26, 10, 3, 19, 30, 5]  # Mittwoch = 3
    assert len(p.prelude(ids_at(0, 0), now)) == 3


def test_curve_and_modes():
    assert list(p.curve_point(ids_at(0, 0), 2, 19 * 60 + 30, 70)[5:10]) == [6, 2, 19, 30, 70]
    assert list(p.auto_mode(ids_at(0, 0))[5:9]) == [5, 3, 255, 255]
    assert list(p.manual_mode(ids_at(0, 0))[5:9]) == [5, 11, 255, 255]
    assert list(p.reset_auto(ids_at(0, 0))[5:9]) == [5, 5, 255, 255]
    with pytest.raises(ValueError, match="Uhrzeit"):
        p.curve_point(ids_at(0, 0), 0, 1440, 0)


def test_dosing_frames():
    ids = ids_at(0, 0)
    assert [f[6] for f in p.dosing_auth(ids)] == [4, 5]
    assert list(p.calibrate(ids, 1, 60, 2.5)[5:10]) == [22, 1, 60, 2, 50]
    assert list(p.calibrate(ids, 1, None, None)[5:10]) == [22, 1, 255, 255, 255]
    assert list(p.calibrate(ids, 0, None, 2.999)[8:10]) == [2, 100]
    assert list(p.reset_totals(ids, 2)[5:9]) == [5, 23, 255, 255]
    assert list(p.reset_program(ids, 2)[5:9]) == [5, 27, 255, 255]
    frames = p.program(ids, 0, [20 * 60, 8 * 60], 1.5)
    assert list(frames[0][5:9]) == [5, 25, 255, 255]  # Plan erst löschen
    assert list(frames[1][5:9]) == [32, 0, 0, 1]
    assert list(frames[2][5:12]) == [27, 0, 127, 1, 0, 0, 30]  # 3,0 ml am Tag
    assert list(frames[3][5:12]) == [21, 0, 1, 8, 0, 0, 15]
    assert list(frames[4][8:10]) == [20, 0]
    with pytest.raises(ValueError, match="Dosis"):
        p.dose(ids, 0, 0.1)
    with pytest.raises(ValueError, match="Zeitpunkt"):
        p.program(ids, 0, [], 1)
    with pytest.raises(ValueError, match="Parameter"):
        p.encode(ids, 1, 1, [300])
    with pytest.raises(ValueError, match="Messwert"):
        p.calibrate(ids, 0, None, 300)


def test_parse_notifications():
    runtime = p.parse(bytes.fromhex("5b170a00010a01ffffffff13888c"))
    assert runtime == p.LedRuntime(0x17, 511)
    totals = p.parse(bytes.fromhex("5b101000011e041f0000"))
    assert totals == p.DosingCounters(daily=False, microliters=(105500, 0), firmware=0x10)
    snap = (
        bytearray([0x5B, 0x17, 0x30, 0, 2, 0xFE])
        + bytes(19)
        + bytes([13, 15, 0, 13, 45, 100, 0, 0, 0, 25, 99, 1, 0, 0])
    )
    parsed = p.parse(bytes(snap))
    assert isinstance(parsed, p.LedSchedule) and parsed.points == ((795, 0), (825, 100))
    assert p.parse(b"\x00\x01") is None
    assert p.parse(bytes.fromhex("5b17080001ee00")) is None
