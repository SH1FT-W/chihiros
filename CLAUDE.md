# Chihiros – Projektregeln

HA-Integration (`custom_components/chihiros`, Domain `chihiros`) für Chihiros-LED-Lampen der 0x5A-Familie und die
Chihiros-Dosierpumpe. Komplett neu geschrieben; Nachfolger von TheMicDiet/chihiros-led-control **mit gleicher Domain
`chihiros` und gleichen unique_ids** – Einbau = Ordner tauschen, nicht beide gleichzeitig. Modelltabelle und
Protokoll nach der Doku des Originals (MIT, Dank und Lizenztext in `NOTICE`). An echter Hardware geprüft: C II RGB
und 4-Kanal-Dosierpumpe; die übrigen Lampen nur gegen die Attrappe. Heizer (DYHET/DYH1T) und Magnetrührer (DYMIXR)
bewusst nicht.

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten MACs/IPs, Heimnetz-Details, lokalen
  Pfade). Testdaten bleiben fiktiv.
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch.
- Lokal committen ok, Push und Releases nur nach Rückfrage.
- **Keine Bluetooth-Verbindung vom Entwicklungsrechner zu Geräten, die eine laufende HA nutzt** (die Lampe nimmt nur
  eine Verbindung an und würde der HA weggenommen).

## Aufbau (`custom_components/chihiros/`)
- `models.py` rein: Modelltabelle (Bluetooth-Namenspräfix → Modell, Kanäle mit Wire-ID in HA-Reihenfolge
  Rot/Grün/Blau/Weiß bzw. Weiß/Warmweiß, SeaLed oder BleLed, Lüfter), längster Präfix gewinnt; unbekannter Name
  eines Licht-Eintrags = C II RGB (Einträge der ersten Versionen). Lite-Controller (`DYZSD DYNA2N DYNA2 DYDD DYCOM`):
  keine Wochentage, Ablauf nach Rückkehr neu schreiben. Commander 1: Belegung white/rgb/wrgb im Config-Flow.
  Neue Präfixe immer auch in `manifest.json` (`bluetooth`) eintragen.
- `protocol.py` rein, ohne HA: Rahmen (XOR-Prüfsumme, 0x5A-Regeln), LED-Befehle (Kanal 0x5A/07, Manuell `[11]`/Auto
  `[3]` 0x5A/05 (nicht `[18]`: C II RGB bleibt dunkel), Uhrzeit 0x5A/09, Kurvenpunkt 0x5A/06 SeaLed `[ch,h,m,level]`
  bzw. BleLed `[ch,Halbstunde,level]`), Lüfter (0x5A/0F, Auto 0x5A/05 `[0x11]`), Pumpe (Auth 0xA5/04, Dosis 0xA5/27,
  Kalibrieren 0xA5/22, Plan 0xA5/32+27+21, Reset 0xA5/05), Rückmeldungen (0x5B: 0A Laufzeit, 0B Lüfter,
  FE Tagesablauf, 1E gesamt, 22 heute). Quelle: `docs/protocol.md` des Originals (My Chihiros 2.8.59).
- `ble.py` eine Verbindung pro Gerät über HA-Bluetooth/Proxys: Lock, harte Grenze `OP_TIMEOUT` 40 s, Trennen nach
  15 s Leerlauf (Proxy-Platz frei!), Prelude (Status + 2× Uhrzeit, Pumpe + Auth) bei jeder Verbindung, 2. Versuch nur
  mit `retry=True` – **Dosieren nie wiederholen**.
- `coordinator.py` `ChihirosLight` (Modell aus `entry.data["name"]` bzw. Titel, Kanäle merken, Store, nach
  Wiederauftauchen Farbe/Uhr neu, Übergänge über `async_call_later`, Auto/Manuell, Tagesablauf schreiben, Lüfter)
  und `ChihirosDoser` (Zähler alle 30 Min., HA-Zähler mit Tageswechsel, Dosis/Kalibrieren/Plan).
  **Beim HA-Start nie Bluetooth anfassen** – erster Kontakt nach 90 s im Hintergrund (das Original blockierte den Start).
- Entitäten: unique_id `<MAC>_<key>`; Licht `rgb` (RGB), `rgbw` (WRGB), einkanalig der Kanalname (`white`), sonst
  `light`; Regler je Kanal (`red`, `green`, `blue`, `white`, `warm`); Schalter `auto_mode`; Sensoren
  `firmware_version`, `schedule_points`; VIVID III `fan`, `fan_rpm`, `fan_temperature_celsius`;
  Pumpe `dosing_pump_<n>_dose_volume|dose|dosed_today|total_ml|total_cycles|last_calibration|dosing_daily_ul|dosing_lifetime_ul`.
  **unique_ids und Verhalten von C II RGB und Pumpe nicht ändern** (bestehende Installationen).
- Licht wie die App: je Kanal 0–100 %, Farbmodus brightness/rgb/rgbw; HA-Regel Farbe × Helligkeit (gerundet).

## Version
Kalender-Schema **YY.M.N** ohne v (Jahr zweistellig, Monat ohne führende Null, N = laufende Nummer im Monat ab 1;
es zählen nur echte, veröffentlichte GitHub-Releases des Monats, auch alte X.Y.Z; bloße Tags nicht; ohne Release im Monat → .1; neuer Monat → wieder .1).
Jedes Release als GitHub-Release veröffentlichen (Tag + Release-Titel ohne v = genau die manifest-Version), sonst zählt es nicht mit.
Nächste Nummer immer aus `tools/next-version`, nie von Hand hochzählen, keine Patch-/Minor-Unterscheidung
(+ `CHANGELOG.md` `## YY.M.N – DD.MM.YYYY`, englisch). Die Versions-Badge im README und die Versionszeile der
Projektseite (`docs/index.html`) lesen die manifest.json selbst.

## Prüfen
- `.venv/bin/python -m pytest -q -p no:logging --cov=custom_components/chihiros` (≥ 96 %), auch mit
  `LC_ALL=en_US.UTF-8 TZ=UTC`. Tests ersetzen Bluetooth komplett (FakeBluetooth/FakeDevices in `tests/conftest.py`).
- `uvx ruff check . && uvx ruff format --check .`, `uvx mypy@2.3.1 --python-executable .venv/bin/python`
- `.venv/bin/python tools/pruefe-uebersetzungen.py` (strings.json gegen translations/*.json, Platzhalter)
- GitHub Actions `.github/workflows/pruefung.yml` (Vorlage repo-vorlagen/vorlagen/ci-ha-integration.yml): dasselbe bei
  Push/PR auf main, montags zusätzlich gegen die neueste HA, plus hassfest; Abdeckungsgrenze 96 %.
  Pakete der CI in `requirements_test.txt` (HA 2026.9.3 + Bluetooth-Pakete). `.github/workflows/hacs.yml`: HACS-Validierung.
- `tools/next-version` und `tools/pruefe-uebersetzungen.py` sind byteidentische Kopien der Vorlagen.
- Testdaten sind fiktiv (MACs wie `C0:FF:EE:12:34:56`, `AA:BB:CC:00:11:22`).
- README-Bilder: Karten aus `docs/readme-images.json`, gerendert mit `theme/v1/readme-images/render.mjs <repo> cards`
  aus sh1ft-w.github.io. Titelbild: `docs/images/readme-title.webp` (fertiges KI-Bild, frei verwendbar, ohne
  Metadaten, unverändert für hell und dunkel); die Projektseite zeigt dasselbe Bild im Hero.
  `docs/images/stage-photo.jpg` ist sein Fotoausschnitt für `docs/readme-title.html`, nur eine Vorlage, falls der
  Titel einmal neu gebaut werden soll.
