"""Sensoren: Firmware, Tagesablauf, Signal, letzter Kontakt; bei der Pumpe die Zähler je Kanal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ChihirosConfigEntry
from .coordinator import ChihirosDevice, ChihirosDoser, ChihirosLight
from .entity import ChihirosEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ChihirosSensorDescription(SensorEntityDescription):
    value: Callable[[Any], Any]
    attributes: Callable[[Any], dict[str, Any] | None] | None = None
    always_available: bool = False


FAN = (
    ChihirosSensorDescription(
        key="fan_rpm",
        value=lambda d: d.fan_rpm,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ChihirosSensorDescription(
        key="fan_temperature_celsius",
        value=lambda d: d.temperature,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


COMMON = (
    ChihirosSensorDescription(
        key="firmware_version",
        value=lambda d: d.firmware,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ChihirosSensorDescription(
        key="signal",
        value=lambda d: d.rssi,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    ChihirosSensorDescription(
        key="last_contact",
        value=lambda d: d.last_contact,
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        always_available=True,
    ),
)

LIGHT = (
    ChihirosSensorDescription(
        key="feeding_until",
        value=lambda d: d.feeding_until,
        device_class=SensorDeviceClass.TIMESTAMP,
        always_available=True,
    ),
    ChihirosSensorDescription(
        key="schedule_points",
        value=lambda d: len(d.curve) or len(d.schedule_points) or None,
        attributes=lambda d: {
            "weekday_settings": d.weekday_settings or None,
            "points": [{"time": _hhmm(t), **dict(zip(d.model.channel_names, lv, strict=False))} for t, lv in d.curve]
            or [{"time": _hhmm(t), "level": lv} for t, lv in d.schedule_points],
        },
        entity_category=EntityCategory.DIAGNOSTIC,
        always_available=True,
    ),
)


def _pump(index: int) -> tuple[ChihirosSensorDescription, ...]:
    i = index - 1

    def device_ml(values: tuple[int, ...] | None) -> float | None:
        return round(values[i] / 1000, 1) if values and i < len(values) else None

    def last_cal(d: ChihirosDoser) -> datetime | None:
        raw = d.pumps[i].last_calibration
        return dt_util.parse_datetime(raw) if raw else None

    return (
        # vom Gerät gemeldet (alle Dosierungen, auch aus Plan/App)
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_dosing_daily_ul",
            translation_key="device_today",
            value=lambda d: device_ml(d.device_daily_ul),
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            device_class=SensorDeviceClass.VOLUME,
        ),
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_dosing_lifetime_ul",
            translation_key="device_total",
            value=lambda d: device_ml(d.device_total_ul),
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            device_class=SensorDeviceClass.VOLUME,
        ),
        # was HA selbst per Knopf/Dienst dosiert hat
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_dosed_today",
            translation_key="ha_today",
            value=lambda d: d.pumps[i].today_ml,
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.TOTAL,
            always_available=True,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            device_class=SensorDeviceClass.VOLUME,
        ),
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_total_ml",
            translation_key="ha_total",
            value=lambda d: d.pumps[i].total_ml,
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.TOTAL_INCREASING,
            always_available=True,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            device_class=SensorDeviceClass.VOLUME,
        ),
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_total_cycles",
            translation_key="ha_cycles",
            value=lambda d: d.pumps[i].cycles,
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.TOTAL_INCREASING,
            always_available=True,
        ),
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_remaining",
            translation_key="remaining",
            value=lambda d: d.remaining(index),
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            device_class=SensorDeviceClass.VOLUME_STORAGE,
            state_class=SensorStateClass.MEASUREMENT,
            always_available=True,
        ),
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_days_left",
            translation_key="days_left",
            value=lambda d: d.days_left(index),
            native_unit_of_measurement=UnitOfTime.DAYS,
            always_available=True,
        ),
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_plan",
            translation_key="plan",
            value=lambda d: (d.pumps[i].plan or {}).get("mode", "none"),
            attributes=lambda d: d.pumps[i].plan,
            device_class=SensorDeviceClass.ENUM,
            options=["none", "daily", "once", "timer", "spread"],
            always_available=True,
        ),
        ChihirosSensorDescription(
            key=f"dosing_pump_{index}_last_calibration",
            translation_key="last_calibration",
            value=last_cal,
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            always_available=True,
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ChihirosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    device = entry.runtime_data
    descriptions: list[ChihirosSensorDescription] = list(COMMON)
    if isinstance(device, ChihirosLight):
        descriptions += LIGHT
        if device.model.fan:
            descriptions += FAN
    elif isinstance(device, ChihirosDoser):
        for n in range(1, device.pump_count + 1):
            descriptions += _pump(n)
    async_add_entities(ChihirosSensor(device, d) for d in descriptions)


class ChihirosSensor(ChihirosEntity, SensorEntity):
    entity_description: ChihirosSensorDescription

    def __init__(self, device: ChihirosDevice, description: ChihirosSensorDescription) -> None:
        super().__init__(device, description.translation_key or description.key, description.key)
        self.entity_description = description
        pump = description.key.split("_")[2] if description.key.startswith("dosing_pump_") else None
        if pump:
            self._attr_translation_placeholders = {"pump": pump}

    @property
    def available(self) -> bool:
        return self.entity_description.always_available or super().available

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = self.entity_description.attributes
        return attrs(self.device) if attrs else None
