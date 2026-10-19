"""Chihiros-BLE-Protokoll für LED-Lampen und Dosierpumpe (rein, ohne Home Assistant).

Quelle: Protokoll-Doku von TheMicDiet/chihiros-led-control (aus „My Chihiros“ 2.8.59 – die aktuelle
Play-Store-Version – per Dart-Analyse und Bluetooth-Mitschnitten gewonnen).

Rahmen: [Befehl, 0x01, Länge(=Parameter+5), Nachricht-ID hi, lo, Modus, Parameter…, Prüfsumme]
Prüfsumme = XOR über Byte 1 bis vorletztes. Ältere LED-Befehle meiden das Byte 0x5A (in ID, Parametern
und Prüfsumme); Dosier- und Kurvenbefehle schicken ihre Parameter unverändert.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

RESERVED = 0x5A

# LED-Kanäle der C II RGB (Wire-ID)
RED, GREEN, BLUE = 0, 1, 2
RGB_CHANNELS = (RED, GREEN, BLUE)


class MessageIds:
    """Fortlaufende Nachrichten-ID, überspringt 0x5A (wie die App)."""

    def __init__(self) -> None:
        self._hi, self._lo = 0, 0

    def next(self) -> tuple[int, int]:
        while True:
            if (self._hi, self._lo) == (255, 255):
                self._hi, self._lo = 0, 1
            elif self._lo == 255:
                self._hi, self._lo = (self._hi + 1) % 256, 0
            else:
                self._lo += 1
            if RESERVED not in (self._hi, self._lo):
                return self._hi, self._lo


def checksum(frame: bytes | bytearray) -> int:
    value = frame[1]
    for byte in frame[2:]:
        value ^= byte
    return value


def encode(ids: MessageIds, cmd: int, mode: int, params: Sequence[int], *, avoid_reserved: bool = True) -> bytes:
    """Einen Befehl als Rahmen bauen."""
    if any(not 0 <= p <= 255 for p in params):
        raise ValueError(f"Parameter außerhalb 0–255: {list(params)}")
    payload = [RESERVED - 1 if avoid_reserved and p == RESERVED else p for p in params]
    while True:
        hi, lo = ids.next()
        frame = bytes([cmd, 1, len(payload) + 5, hi, lo, mode, *payload])
        check = checksum(frame)
        if not (avoid_reserved and check == RESERVED):
            return frame + bytes([check])


# --- Gemeinsam -------------------------------------------------------------------------------------


def status_query(ids: MessageIds) -> bytes:
    return encode(ids, 0x5A, 0x04, [1])


def set_time(ids: MessageIds, now: datetime) -> bytes:
    return encode(ids, 0x5A, 0x09, [now.year - 2000, now.month, now.isoweekday(), now.hour, now.minute, now.second])


def prelude(ids: MessageIds, now: datetime) -> list[bytes]:
    """Was die App bei jeder neuen Verbindung schickt: Status + zweimal die Uhrzeit."""
    return [status_query(ids), set_time(ids, now), set_time(ids, now)]


# --- Lampe (C II RGB, „SeaLed“-Familie) -----------------------------------------------------------


def set_channel(ids: MessageIds, channel: int, level: int) -> bytes:
    return encode(ids, 0x5A, 0x07, [channel, max(0, min(100, int(level)))])


def manual_mode(ids: MessageIds) -> bytes:
    return encode(ids, 0x5A, 0x05, [11, 255, 255])


def auto_mode(ids: MessageIds) -> bytes:
    """Gespeicherten Tagesablauf abspielen – App-eigenes switchToAuto `[3]`.

    Nicht `[18]` (switchToScene, so in upstream): Damit blieb die C II RGB dunkel (Steckdose 0 W), mit `[3]`
    spielt sie die Kurve sofort ab (am 08.10.2026 an der echten Lampe geprüft).
    """
    return encode(ids, 0x5A, 0x05, [3, 255, 255])


def reset_auto(ids: MessageIds) -> bytes:
    """Gespeicherten Tagesablauf löschen (vor dem Neuschreiben)."""
    return encode(ids, 0x5A, 0x05, [5, 255, 255])


def curve_point(ids: MessageIds, channel: int, minutes: int, level: int, *, sea_led: bool = True) -> bytes:
    """Ein Stützpunkt des Tagesablaufs für einen Kanal.

    SeaLed-Familie: `[Kanal, Stunde, Minute, Wert]`. BleLed-Familie (ältere Commander, WRGB II `DYWRGB`,
    Z Light TINY, Terrarium Egg): `[Kanal, Halbstunden-Index, Wert]`, gerundet wie die App (Rest > 14 → nächster).
    """
    if not 0 <= minutes <= 1439:
        raise ValueError("Uhrzeit außerhalb des Tages")
    level = max(0, min(100, int(level)))
    if sea_led:
        hour, minute = divmod(minutes, 60)
        return encode(ids, 0x5A, 0x06, [channel, hour, minute, level], avoid_reserved=False)
    slot, rest = divmod(minutes, 30)
    return encode(ids, 0x5A, 0x06, [channel, slot + (rest > 14), level], avoid_reserved=False)


# --- Lüfter (WRGB VIVID III) ----------------------------------------------------------------------


def fan_speed(ids: MessageIds, percent: int) -> bytes:
    """Lüfter manuell, 0–100 % (die App hebt alles unter 25 % auf 25 % an)."""
    return encode(ids, 0x5A, 0x0F, [max(0, min(100, int(percent)))])


def fan_auto(ids: MessageIds) -> bytes:
    """Lüfter nach Temperatur (App `autoFan`)."""
    return encode(ids, 0x5A, 0x05, [0x11, 255, 255])


# --- Dosierpumpe ----------------------------------------------------------------------------------

DOSE_MIN_ML = 0.2
DOSE_MAX_ML = 999.9


def _volume(ml: float) -> list[int]:
    """Volumen in 0,1-ml-Schritten: hi × 25,6 ml + lo × 0,1 ml."""
    tenths = round(ml * 10)
    if not 0 <= tenths <= 65535:
        raise ValueError("Volumen außerhalb 0–6553,5 ml")
    return list(divmod(tenths, 256))


def dosing_auth(ids: MessageIds) -> list[bytes]:
    """Zwei Abfragen, die die Pumpe vor Befehlen erwartet – sie liefern zugleich Gesamt- und Tageszähler."""
    return [encode(ids, 0xA5, 0x04, [4]), encode(ids, 0xA5, 0x04, [5])]


def dose(ids: MessageIds, pump: int, ml: float) -> bytes:
    if not DOSE_MIN_ML <= ml <= DOSE_MAX_ML:
        raise ValueError(f"Dosis muss zwischen {DOSE_MIN_ML} und {DOSE_MAX_ML} ml liegen")
    return encode(ids, 0xA5, 27, [pump, 0, 0, *_volume(ml)], avoid_reserved=False)


def calibrate(ids: MessageIds, pump: int, seconds: int | None, measured_ml: float | None) -> bytes:
    """Kalibrieren: Testlauf in Sekunden (255 = keiner) und gemessene Menge als [ganze ml, Hundertstel]."""
    time_byte = 255 if seconds is None else max(0, min(254, int(seconds)))
    if measured_ml is None:
        vol = [255, 255]
    else:
        microliters = round(measured_ml * 1000)
        whole, rest = divmod(microliters, 1000)
        if not 0 <= whole <= 255:
            raise ValueError("Messwert außerhalb 0–255 ml")
        vol = [whole, (rest + 5) // 10]
    return encode(ids, 0xA5, 22, [pump, time_byte, *vol], avoid_reserved=False)


def reset_totals(ids: MessageIds, pump: int) -> bytes:
    return encode(ids, 0xA5, 5, [pump + 21, 255, 255], avoid_reserved=False)


def reset_program(ids: MessageIds, pump: int) -> bytes:
    return encode(ids, 0xA5, 5, [pump + 25, 255, 255], avoid_reserved=False)


# Wochentage als Bitmaske (Montag = 64 … Sonntag = 1, jeden Tag = 127)
WEEKDAY_BITS = {"mon": 64, "tue": 32, "wed": 16, "thu": 8, "fri": 4, "sat": 2, "sun": 1}
EVERY_DAY = 127


def weekday_mask(days: Sequence[str] | None) -> int:
    if not days:
        return EVERY_DAY
    return sum({WEEKDAY_BITS[d] for d in days})


def channel_state(ids: MessageIds, pump: int, *, active: bool, compensate: bool) -> bytes:
    """Kanal aktiv/pausiert und „verpasste Dosen nachholen“ (App: Unterbrechungs-Ausgleich)."""
    return encode(ids, 0xA5, 32, [pump, 1 if compensate else 0, 1 if active else 0], avoid_reserved=False)


def dosing_delay(ids: MessageIds, on: bool) -> bytes:
    """Geräteeinstellung Dosierverzögerung."""
    return encode(ids, 0xA5, 31, [1 if on else 0], avoid_reserved=False)


def dosing_set(ids: MessageIds, pump: int, daily_ml: float | None, weekdays: int) -> bytes:
    """Tagesmenge + Wochentage eines Kanals (App: dosingSet, „erste Einstellung des Tages“)."""
    vol = [255, 255] if daily_ml is None else _volume(daily_ml)
    return encode(ids, 0xA5, 27, [pump, weekdays, 1, 0, *vol], avoid_reserved=False)


PLAN_ONCE, PLAN_DAILY, PLAN_SPREAD, PLAN_TIMER = 0, 1, 2, 3
_BATCH_LIMIT = 50  # wie die App: Rahmen wird geschickt, sobald die Nutzdaten 50 Bytes überschreiten


def _batched(ids: MessageIds, mode: int, header: list[int], records: Sequence[list[int]]) -> list[bytes]:
    frames: list[bytes] = []
    acc = list(header)
    for record in records:
        acc += record
        if len(acc) > _BATCH_LIMIT:
            frames.append(encode(ids, 0xA5, mode, acc, avoid_reserved=False))
            acc = list(header)
    if acc != header:
        frames.append(encode(ids, 0xA5, mode, acc, avoid_reserved=False))
    return frames


def plan(
    ids: MessageIds,
    pump: int,
    kind: int,
    *,
    weekdays: int = EVERY_DAY,
    doses: Sequence[tuple[int, float]] = (),
    spread: tuple[int, int, int, float] | None = None,
    compensate: bool = False,
) -> list[bytes]:
    """Einen Dosierplan wie die App programmieren.

    - PLAN_ONCE / PLAN_DAILY / PLAN_TIMER: `doses` = (Minuten, ml) je Zeitpunkt
    - PLAN_SPREAD („frei“): `spread` = (Start-Minuten, Dauer in Minuten, Anzahl Gaben, ml pro Tag) –
      die Pumpe verteilt die Tagesmenge selbst
    Reihenfolge wie die App: Plan löschen, Kanal aktiv, Tagesmenge + Wochentage, Zeitpunkte.
    """
    frames = [reset_program(ids, pump), channel_state(ids, pump, active=True, compensate=compensate)]
    if kind == PLAN_SPREAD:
        if spread is None:
            raise ValueError("Für „frei“ fehlen Start, Dauer, Anzahl und Tagesmenge")
        start, duration, count, daily = spread
        if duration < 1 or not 1 <= count <= 255:
            raise ValueError("Dauer ≥ 1 Minute, 1–255 Gaben")
        end = start + duration  # wie die App: nicht über Mitternacht umbrechen
        frames.append(dosing_set(ids, pump, daily, weekdays))
        record = [start // 60, start % 60, end // 60, end % 60, count]
        return frames + _batched(ids, 23, [pump], [record])
    if not doses:
        raise ValueError("Mindestens ein Zeitpunkt")
    frames.append(dosing_set(ids, pump, sum(ml for _, ml in doses), weekdays))
    records = [[t // 60, t % 60, *_volume(ml)] for t, ml in sorted(doses)]
    if kind == PLAN_TIMER:
        return frames + _batched(ids, 21, [pump, PLAN_TIMER], records)
    return frames + [encode(ids, 0xA5, 21, [pump, kind, *r], avoid_reserved=False) for r in records]


def program(ids: MessageIds, pump: int, times: Sequence[int], ml_each: float) -> list[bytes]:
    """Kurzform: täglich zu festen Uhrzeiten dieselbe Menge."""
    return plan(ids, pump, PLAN_DAILY, doses=[(t, ml_each) for t in times])


def weekday_setting(
    ids: MessageIds, sunrise: int, sunset: int, ramp_minutes: int, weekdays: int, levels: Sequence[int]
) -> bytes:
    """Lampe: Tageseinstellung (App „Auto“-Einstellung) – Sonnenaufgang, -untergang, Rampe, Wochentage, Kanäle.

    Höchstens 7 Einstellungen, je Wochentag nur eine.
    """
    params = [sunrise // 60, sunrise % 60, sunset // 60, sunset % 60, max(0, min(255, ramp_minutes)), weekdays]
    params += [max(0, min(100, int(v))) for v in levels]
    params += [255] * (14 - len(params))
    return encode(ids, 0xA5, 25, params)


# --- Rückmeldungen --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedRuntime:
    firmware: int
    runtime_minutes: int


@dataclass(frozen=True, slots=True)
class LedSchedule:
    firmware: int
    points: tuple[tuple[int, int], ...]  # (Minuten, Helligkeit)


@dataclass(frozen=True, slots=True)
class DosingCounters:
    daily: bool  # True = heute, False = gesamt
    microliters: tuple[int, ...]
    firmware: int = 0


@dataclass(frozen=True, slots=True)
class FanStatus:
    firmware: int
    rpm: int
    temperature: int  # ganze °C


Notification = LedRuntime | LedSchedule | DosingCounters | FanStatus


def parse(data: bytes | bytearray) -> Notification | None:
    """Rückmeldung (0x5B …) auswerten; Unbekanntes → None."""
    if len(data) < 7 or data[0] != 0x5B:
        return None
    mode = data[5]
    if mode in (0x1E, 0x22) and len(data) >= 8:
        count = (len(data) - 6) // 2
        values = tuple(((data[6 + 2 * i] << 8) | data[7 + 2 * i]) * 100 for i in range(count))
        return DosingCounters(daily=mode == 0x22, microliters=values, firmware=data[1])
    if mode == 0x0A and len(data) >= 8:
        return LedRuntime(data[1], (data[6] << 8) | data[7])
    if mode == 0x0B and len(data) >= 9:
        return FanStatus(data[1], (data[6] << 8) | data[7], data[8])
    if mode == 0xFE:
        points = []
        for i in range(25, len(data) - 2, 3):
            hour, minute, level = data[i], data[i + 1], data[i + 2]
            if hour > 23 or minute > 59 or level > 100 or (hour == minute == level == 0):
                continue
            points.append((hour * 60 + minute, level))
        return LedSchedule(data[1], tuple(points))
    return None
