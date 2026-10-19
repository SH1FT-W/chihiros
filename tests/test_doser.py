"""Dosierpumpe in Home Assistant gegen das nachgebaute Gerät."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.chihiros.const import DOMAIN

from .conftest import DOSER_ADDR, FakeDevices

NAME = "DYDOSED05E00ABCDEF"
P = "dydosed05e00abcdef"


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def device_id(hass: HomeAssistant) -> str:
    entry = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.data["address"] == DOSER_ADDR)
    devs = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert devs
    return devs[0].id


async def test_counters_from_pump(hass: HomeAssistant, doser, devices: FakeDevices, freezer):
    await tick(hass, freezer, 91)
    assert hass.states.get(f"sensor.{P}_pumpe_1_gesamt").state == "105.5"
    assert hass.states.get(f"sensor.{P}_pumpe_4_heute").state == "0.3"
    devices.total[0] = 1100
    await tick(hass, freezer, 30 * 60)
    assert hass.states.get(f"sensor.{P}_pumpe_1_gesamt").state == "110.0"
    assert hass.states.get(f"sensor.{P}_firmware").state == "16"
    dev = dr.async_entries_for_config_entry(dr.async_get(hass), doser.entry_id)[0]
    assert dev.sw_version == "16"
    await hass.config_entries.async_reload(doser.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(f"sensor.{P}_firmware").state == "16"  # gemerkt, auch ohne Kontakt


async def test_dose_button_never_retries(hass: HomeAssistant, doser, devices: FakeDevices):
    await hass.services.async_call(
        "number", "set_value", {"entity_id": f"number.{P}_pumpe_2_dosiermenge", "value": 2.5}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": f"button.{P}_pumpe_2_dosieren"}, blocking=True)
    dose = [(f, retry) for n, f, retry in devices.sent if f[5] == 27]
    assert len(dose) == 1 and list(dose[0][0][6:11]) == [1, 0, 0, 0, 25] and dose[0][1] is False
    assert hass.states.get(f"sensor.{P}_pumpe_2_heute_per_ha").state == "2.5"
    assert hass.states.get(f"sensor.{P}_pumpe_2_dosierungen_per_ha").state == "1"


async def test_services(hass: HomeAssistant, doser, devices: FakeDevices):
    dev = device_id(hass)
    await hass.services.async_call(DOMAIN, "dose", {"device_id": dev, "pump": 1, "ml": 1.2}, blocking=True)
    assert hass.states.get(f"sensor.{P}_pumpe_1_gesamt_per_ha").state == "1.2"
    await hass.services.async_call(
        DOMAIN, "calibrate_pump", {"device_id": dev, "pump": 3, "seconds": 60}, blocking=True
    )
    assert hass.states.get(f"sensor.{P}_pumpe_3_zuletzt_kalibriert").state == "unknown"
    await hass.services.async_call(
        DOMAIN, "calibrate_pump", {"device_id": dev, "pump": 3, "measured_ml": 2.5}, blocking=True
    )
    assert hass.states.get(f"sensor.{P}_pumpe_3_zuletzt_kalibriert").state != "unknown"
    devices.clear()
    await hass.services.async_call(
        DOMAIN,
        "set_dosing_program",
        {"device_id": dev, "pump": 1, "times": ["08:00", "20:00"], "ml": 1.5},
        blocking=True,
    )
    assert [f[5] for f in devices.frames(NAME)] == [5, 32, 27, 21, 21]
    await hass.services.async_call(DOMAIN, "clear_dosing_program", {"device_id": dev, "pump": 1}, blocking=True)
    await hass.services.async_call(DOMAIN, "reset_pump_totals", {"device_id": dev, "pump": 1}, blocking=True)
    assert hass.states.get(f"sensor.{P}_pumpe_1_gesamt_per_ha").state == "0.0"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "dose", {"device_id": dev, "pump": 6}, blocking=True)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "dose", {"device_id": "gibtsnicht", "pump": 1}, blocking=True)


async def test_day_rollover(hass: HomeAssistant, doser, devices: FakeDevices, freezer):
    dev = device_id(hass)
    await hass.services.async_call(DOMAIN, "dose", {"device_id": dev, "pump": 1, "ml": 1.0}, blocking=True)
    await tick(hass, freezer, 24 * 3600)
    assert hass.states.get(f"sensor.{P}_pumpe_1_heute_per_ha").state == "0.0"
    assert hass.states.get(f"sensor.{P}_pumpe_1_gesamt_per_ha").state == "1.0"


async def test_config_flow_bluetooth_and_user(hass: HomeAssistant, bt):
    from unittest.mock import MagicMock, patch

    info = MagicMock()
    info.name, info.address = "DYDOSEAABBCCDDEEFF", "aa:bb:cc:dd:ee:ff"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pump_count": 2})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"address": "AA:BB:CC:DD:EE:FF", "name": "DYDOSEAABBCCDDEEFF", "pump_count": 2}

    other = MagicMock()
    other.name, other.address = "DYHET123", "11:22:33:44:55:66"  # Heizer: bewusst nicht unterstützt
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=other)
    assert result["reason"] == "not_supported"

    lamp = MagicMock()
    lamp.name, lamp.address = "DYNCRGP0011", "00:11:22:33:44:55"
    with patch("custom_components.chihiros.config_flow.async_discovered_service_info", return_value=[lamp, other]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": "00:11:22:33:44:55"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"] == {
        "address": "00:11:22:33:44:55",
        "name": "DYNCRGP0011",
    }
    with patch("custom_components.chihiros.config_flow.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["reason"] == "no_devices_found"


async def test_send_raw(hass: HomeAssistant, doser, devices: FakeDevices):
    dev = device_id(hass)
    devices.clear()
    resp = await hass.services.async_call(
        DOMAIN,
        "send_raw",
        {"device_id": dev, "frames": [{"cmd": 90, "mode": 4, "params": [1]}], "wait": 0.2},
        blocking=True,
        return_response=True,
    )
    assert resp["sent"][0].startswith("5a0106") and len(resp["received"]) == 2
    assert resp["received"][0].startswith("5b170a")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "send_raw",
            {"device_id": "gibtsnicht", "frames": [{"cmd": 90, "mode": 4}]},
            blocking=True,
            return_response=True,
        )
