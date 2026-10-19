"""Supported Chihiros LED models (pure, without Home Assistant).

The Bluetooth name prefix decides the model. The table (prefixes, channel layout, curve encoding) follows the
model registry of TheMicDiet/chihiros-led-control (MIT, see NOTICE), which took it from "My Chihiros" 2.8.59.

Channel layouts are listed in Home Assistant order (red, green, blue, white) together with the wire channel id
the lamp expects in `0x5A/07` (manual level) and `0x5A/06` (curve point).
"""

from __future__ import annotations

from dataclasses import dataclass

Layout = tuple[tuple[str, int], ...]

WHITE: Layout = (("white", 0),)
RGB: Layout = (("red", 0), ("green", 1), ("blue", 2))
WRGB: Layout = (("red", 0), ("green", 1), ("blue", 2), ("white", 3))
RED_GREEN: Layout = (("red", 0), ("green", 1))
WHITE_WARM: Layout = (("white", 0), ("warm", 1))

# Commander 1 does not tell what is connected to it: the user picks the layout during setup.
DEVICE_TYPES: dict[str, Layout] = {"white": WHITE, "rgb": RGB, "wrgb": WRGB}

# Lite controllers: the app offers no weekday selection, a schedule always runs every day.
LITE_PREFIXES = ("DYZSD", "DYNA2N", "DYNA2", "DYDD", "DYCOM")

FAN_MIN_SPEED = 25  # VIVID III: manual fan speed below this is raised to it (app behaviour)


@dataclass(frozen=True, slots=True)
class LightModel:
    """One LED model family."""

    name: str
    prefixes: tuple[str, ...]
    layout: Layout
    sea_led: bool = True  # curve points as [channel, hour, minute, level]; otherwise 30-minute slots
    fan: bool = False  # WRGB VIVID III: fan, fan RPM and temperature
    needs_type: bool = False  # Commander 1: layout chosen by the user

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.layout)

    @property
    def wire(self) -> tuple[int, ...]:
        return tuple(ch for _, ch in self.layout)

    def with_layout(self, layout: Layout) -> LightModel:
        return LightModel(self.name, self.prefixes, layout, self.sea_led, self.fan, self.needs_type)


C_II_RGB = LightModel("C II RGB", ("DYNCRGP", "DYNCRGB"), RGB)

LIGHT_MODELS: tuple[LightModel, ...] = (
    C_II_RGB,
    LightModel("A II", ("DYNA2", "DYNA2N"), WHITE),
    LightModel("WRGB II", ("DYWRGB",), RGB, sea_led=False),
    LightModel("WRGB II", ("DYNT90", "DYNW30", "DYNW45", "DYNW60", "DYNW90", "DYNW12P", "DYNWRGB"), RGB),
    LightModel("WRGB II Pro", ("DYWPRO30", "DYWPRO45", "DYWPRO60", "DYWPRO80", "DYWPRO90", "DYWPR120"), WRGB),
    LightModel("WRGB II Slim", ("DYSILN", "DYSL30", "DYSL45", "DYSL60", "DYSL90", "DYSL120", "DYSL12"), RGB),
    LightModel("WRGB VIVID III", ("DYVVD3",), WRGB, fan=True),
    LightModel(
        "Universal WRGB",
        ("DYU550", "DYU600", "DYU700", "DYU800", "DYU920", "DYU1000", "DYU1200", "DYU1500"),
        WRGB,
    ),
    LightModel("SEA_LED", ("DYSEA",), WRGB),
    LightModel("Tiny Terrarium Egg", ("DYDD",), RED_GREEN, sea_led=False),
    LightModel("Z Light TINY", ("DYSSD", "DYZSD"), WHITE_WARM, sea_led=False),
    LightModel("Commander 1", ("DYCOM",), WRGB, sea_led=False, needs_type=True),
    LightModel("Commander 4", ("DYLED",), WRGB, sea_led=False),
    LightModel("Commander 4", ("DYNLED",), WRGB),
    LightModel("Commander X", ("DYONE",), WHITE, sea_led=False),
)

DOSER_PREFIXES = ("DYDOSE",)
LIGHT_PREFIXES = tuple(p for m in LIGHT_MODELS for p in m.prefixes)

# longest prefix first, so that e.g. DYNA2N wins over DYNA2
_BY_PREFIX = sorted(((p, m) for m in LIGHT_MODELS for p in m.prefixes), key=lambda pm: len(pm[0]), reverse=True)


def detect_light(name: str | None) -> LightModel | None:
    """Model for a Bluetooth name, None if it is not a supported lamp."""
    upper = (name or "").upper()
    return next((m for p, m in _BY_PREFIX if upper.startswith(p)), None)


def resolve_light(name: str | None, device_type: str | None = None) -> LightModel:
    """Model for a configured lamp; unknown names stay C II RGB (entries of the first versions)."""
    model = detect_light(name) or C_II_RGB
    if model.needs_type and device_type in DEVICE_TYPES:
        return model.with_layout(DEVICE_TYPES[device_type])
    return model


def is_lite(name: str | None) -> bool:
    return (name or "").upper().startswith(LITE_PREFIXES)


def supported(name: str | None) -> bool:
    return bool(name) and str(name).upper().startswith(LIGHT_PREFIXES + DOSER_PREFIXES)
