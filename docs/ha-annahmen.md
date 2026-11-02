# HA-Annahmen

<!-- Vorlage aus repo-vorlagen/vorlagen/ha-annahmen.md. Liegt im Repo als docs/ha-annahmen.md.
     Der HA-Beta-Bericht (repo-vorlagen/ha-beta-bericht.md) gleicht diese Liste jeden Monat mit den
     Neuerungen der HA-Beta ab. Nur festhalten, was bei einer Änderung in HA still kaputtginge. -->

Worauf sich dieses Repo bei Home Assistant verlässt. Bei jeder Änderung am Code mitpflegen.

**Getestet mit:** HA 2026.9.3 (`requirements_test.txt`) · **Mindestversion:** 2026.9.0 (`hacs.json`)

## Annahmen

| Annahme | Wo im Code | Abgesichert durch |
|---|---|---|
| Bluetooth-Entdeckung startet den Flow allein über `local_name`-Präfixe aus `manifest.json`; `BluetoothServiceInfoBleak.name` ist der Gerätename, `.address` die MAC (unter Linux/ESPHome-Proxys im Format `AA:BB:…`) | `manifest.json` `bluetooth`, `config_flow.py` `async_step_bluetooth`, `models.py` `name_matches_address` | `tests/test_doser.py::test_config_flow_bluetooth_and_user`, `tests/test_models.py::test_name_matches_address` (Matcher selbst: hassfest in CI) |
| Ein Proxy kann einen zwischengespeicherten Namen mit einer fremden Adresse melden (Geister-Entdeckung); dagegen hilft der MAC-Abgleich Name ↔ Adresse | `config_flow.py` `async_step_bluetooth`, `async_step_user` | `tests/test_doser.py::test_config_flow_bluetooth_and_user` |
| `async_discovered_service_info(hass, connectable=True)` liefert die gesehenen Geräte für die manuelle Auswahl | `config_flow.py` `async_step_user` | `tests/test_doser.py::test_config_flow_bluetooth_and_user` (gemockt) |
| `bluetooth.async_ble_device_from_address(…, connectable=True)` liefert ein BLEDevice über lokale Adapter oder Proxys, `establish_connection` (bleak-retry-connector) baut darüber auf; abgebrochener Aufbau über ESPHome-Proxys kann eine verwaiste Verbindung hinterlassen | `ble.py` `_connect` | `tests/test_ble.py` (gemockt), Proxy-Verhalten ungetestet |
| Notify-Rückrufe von bleak kommen im Event-Loop (kein Thread-Wechsel nötig) | `coordinator.py` `_on_frame`, `ble.py` `_notified` | ungetestet |
| `async_register_callback` mit `BluetoothCallbackMatcher(address=…)` (PASSIVE) und `async_track_unavailable` melden Werbepakete bzw. Verschwinden eines Geräts; `async_address_present` beim Start | `coordinator.py` `async_start`, `_on_advertisement`, `_on_unavailable` | `tests/test_background.py::test_advertisements_update_only_on_change`, `tests/test_light.py::test_restore_after_power_loss` (über `FakeBluetooth`) |
| Beim Entladen ruft HA zuerst `async_unload_entry` und bricht danach die Hintergrundaufgaben des Eintrags ab (`entry.async_create_background_task`); ein dabei abgebrochenes `send()` muss die Verbindung selbst trennen | `__init__.py` `async_unload_entry`, `coordinator.py` `_background`, `ble.py` `send` | `tests/test_background.py::test_unload_cancels_background_work`, `tests/test_ble.py::test_cancelled_send_disconnects` |
| Entity-IDs entstehen aus `has_entity_name` + `translation_key` in der Systemsprache (Tests auf Deutsch: `sensor.<name>_pumpe_1_gesamt`); bestehende Registry-Einträge des Originals behalten ihre Entity-ID über die unique_id `<MAC>_<key>` | `entity.py` `ChihirosEntity` | `tests/test_light.py::test_keeps_production_entity_ids` |
| Gerät wird über `CONNECTION_BLUETOOTH` + Adresse zugeordnet; `sw_version` lässt sich nachträglich per `async_update_device` setzen | `entity.py`, `coordinator.py` `_update_device_registry` | `tests/test_doser.py::test_counters_from_pump` |
| `Store` (Version `STORAGE_VERSION`, Schlüssel `chihiros.<entry_id>`) hält Zähler und Füllstand über Neustarts | `coordinator.py` | `tests/test_extras.py::test_remaining_anchors_when_counter_arrives_later`, `tests/test_light.py::test_state_survives_restart` |
| Reparatur-Hinweise (`issue_registry`, `container_<entry_id>_<n>`) für „Behälter fast leer“; beim Löschen bzw. Verkleinern werden sie entfernt | `coordinator.py` `_check_containers`, `__init__.py` `async_remove_entry`, `config_flow.py` `_remove_unused_pumps` | `tests/test_extras.py::test_container_level_and_warning`, `tests/test_quality.py::test_remove_entry_deletes_issues`, `tests/test_quality.py::test_reconfigure_pump_count` |
| Push über einen beliebigen `notify.<dienst>` mit den Feldern `title` und `message`; Optionen listen `hass.services.async_services_for_domain("notify")` | `coordinator.py` `_push`, `config_flow.py` `DoserOptionsFlow` | `tests/test_extras.py::test_set_container_remaining_and_push`, `tests/test_extras.py::test_doser_options_flow` |
| Dienste finden das Gerät über `device_id` → Device-Registry → `config_entries` → `runtime_data` | `__init__.py` | `tests/test_doser.py::test_services` |
| Reconfigure-Flow über `_get_reconfigure_entry()` | `config_flow.py` `async_step_reconfigure` | `tests/test_quality.py::test_reconfigure_pump_count` |
| Zeitplaner `async_call_later`, `async_track_time_change`, `async_track_time_interval` laufen mit dem eingefrorenen Testzeitgeber (Tests springen per `freezer`) | `coordinator.py` | alle Tests mit `freezer` |

## Fremde Integrationen

Integrationen, deren Entitäten oder Attribute dieses Repo liest (Domain, was genau, wo):

- `bluetooth` / `bluetooth_adapters` (Abhängigkeit in `manifest.json`): Geräte, Werbepakete, Verbindungen, siehe oben.
- `notify`: nur Dienstaufruf für den Push, kein Lesen von Entitäten.

## Prüfbefehl

Befehl, der die Annahmen gegen eine bestimmte HA-Version prüft (`HA_VERSION=beta` o. ä.), oder „keiner“:

```sh
keiner (Pakete in requirements_test.txt anheben, dann .venv/bin/python -m pytest -q -p no:logging)
```
