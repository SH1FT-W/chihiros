"""Quality-Scale-Ergänzungen: Neu konfigurieren, Diagnose ohne Adresse, Aufräumen beim Löschen."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from custom_components.chihiros.const import DOMAIN
from custom_components.chihiros.diagnostics import async_get_config_entry_diagnostics

from .conftest import DOSER_ADDR, LIGHT_ADDR, FakeDevices, doser_entry


def pump_entities(hass: HomeAssistant, entry_id: str, pump: int) -> list[str]:
    prefix = f"{DOSER_ADDR}_dosing_pump_{pump}_"
    return [
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
        if e.unique_id.startswith(prefix)
    ]


def create_issue(hass: HomeAssistant, entry_id: str, pump: int) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"container_{entry_id}_{pump}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="container_low",
        translation_placeholders={"name": "x", "pump": str(pump), "ml": "1", "days": "1"},
    )


async def test_reconfigure_pump_count(hass: HomeAssistant, doser, devices: FakeDevices):
    assert pump_entities(hass, doser.entry_id, 4)
    create_issue(hass, doser.entry_id, 4)
    result = await doser.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pump_count": 2})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert doser.data["pump_count"] == 2 and doser.unique_id == DOSER_ADDR
    assert pump_entities(hass, doser.entry_id, 1) and pump_entities(hass, doser.entry_id, 2)
    assert pump_entities(hass, doser.entry_id, 3) == [] and pump_entities(hass, doser.entry_id, 4) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"container_{doser.entry_id}_4") is None
    assert doser.runtime_data.pump_count == 2


async def test_reconfigure_light_has_nothing(hass: HomeAssistant, light, devices: FakeDevices):
    result = await light.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "nothing_to_reconfigure"


async def test_diagnostics_without_address(hass: HomeAssistant, light, devices: FakeDevices):
    diag = await async_get_config_entry_diagnostics(hass, light)
    assert diag["entry"] == {"title": "**REDACTED**", "data": {"address": "**REDACTED**"}}
    assert LIGHT_ADDR not in str(diag["entry"]) and diag["kind"] == "light"


async def test_remove_entry_deletes_issues(hass: HomeAssistant, devices: FakeDevices):
    entry = doser_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    create_issue(hass, entry.entry_id, 1)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"container_{entry.entry_id}_1") is None
