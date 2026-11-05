# Chihiros – Projektregeln

HA-Integration (`custom_components/chihiros`, Domain `chihiros`) für Chihiros-LED-Lampen der 0x5A-Familie und die
Chihiros-Dosierpumpe. Komplett neu geschrieben; Nachfolger von TheMicDiet/chihiros-led-control **mit gleicher Domain
`chihiros` und gleichen unique_ids** – Einbau = Ordner tauschen, nicht beide gleichzeitig. Modelltabelle und
Protokoll nach der Doku des Originals (MIT, Dank und Lizenztext in `NOTICE`). An echter Hardware geprüft: C II RGB
und 4-Kanal-Dosierpumpe; die übrigen Lampen nur gegen die Attrappe. Heizer (DYHET/DYH1T) und Magnetrührer (DYMIXR)
bewusst nicht.

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten MACs/IPs, Heimnetz-Details, lokalen
  Pfade). Testdaten bleiben fiktiv (MACs wie `C0:FF:EE:12:34:56`, `AA:BB:CC:00:11:22`).
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch.
- Lokal committen ok, Push und Releases nur nach Rückfrage.
- **Keine Bluetooth-Verbindung vom Entwicklungsrechner zu Geräten, die eine laufende HA nutzt** (die Lampe nimmt nur
  eine Verbindung an und würde der HA weggenommen).

## Aufbau (`custom_components/chihiros/`)
- `models.py` rein: Modelltabelle (Namenspräfix → Modell, Kanäle, SeaLed/BleLed, Lüfter). Neue Präfixe immer auch in
  `manifest.json` (`bluetooth`) eintragen.
- `protocol.py` rein, ohne HA: Rahmen, Befehle, Rückmeldungen. Quelle: `docs/protocol.md` des Originals
  (My Chihiros 2.8.59).
- `ble.py` eine Verbindung pro Gerät über HA-Bluetooth/Proxys (Lock, harte Zeitgrenze, Trennen nach Leerlauf).
- `coordinator.py` `ChihirosLight` und `ChihirosDoser`; Entitäten in den Plattform-Dateien, unique_id `<MAC>_<key>`.

## Warum es so ist (steht nicht im Code)
- Auto mit `[3]`, nicht `[18]` wie im Original: damit blieb die C II RGB dunkel.
- Lite-Controller (`DYZSD DYNA2N DYNA2 DYDD DYCOM`): Ablauf nach Rückkehr neu schreiben. Commander 1: Belegung
  white/rgb/wrgb wählt man im Config-Flow.
- Trennen nach Leerlauf, damit der Proxy-Platz frei wird.
- **Dosieren nie wiederholen** (`retry=False`), sonst doppelte Dosis.
- **Beim HA-Start nie Bluetooth anfassen** – erster Kontakt verzögert im Hintergrund (das Original blockierte den Start).
- Unbekannter Name eines Licht-Eintrags = C II RGB (Einträge der ersten Versionen).
- Licht wie die App: je Kanal 0–100 %; HA-Regel Farbe × Helligkeit (gerundet).
- **unique_ids und Verhalten von C II RGB und Pumpe nicht ändern** (bestehende Installationen).

## Version
Versionen über `tools/next-version` (Regeln in der globalen CLAUDE.md); Tag + Release-Titel = genau die
manifest-Version. `CHANGELOG.md` `## YY.M.N – DD.MM.YYYY`, englisch. Die Versions-Badge im README und die
Versionszeile der Projektseite (`docs/index.html`) lesen die manifest.json selbst.

## Prüfen
- `LC_ALL=en_US.UTF-8 TZ=UTC .venv/bin/python -m pytest -q -p no:logging --cov=custom_components/chihiros` (≥ 96 %).
  Tests ersetzen Bluetooth komplett (FakeBluetooth/FakeDevices in `tests/conftest.py`).
- `uvx ruff@0.16.9 check . && uvx ruff@0.16.9 format --check .`, `uvx mypy@2.3.1 --python-executable .venv/bin/python`
  (dieselben Versionen wie in der CI)
- `.venv/bin/python tools/pruefe-uebersetzungen.py` (strings.json gegen translations/*.json, Platzhalter)
- GitHub Actions `.github/workflows/pruefung.yml` (Vorlage repo-vorlagen/vorlagen/ci-ha-integration.yml): dasselbe bei
  Push/PR auf main plus hassfest, montags zusätzlich gegen die neueste HA; Abdeckungsgrenze 96 %.
  Pakete der CI in `requirements_test.txt` (HA 2026.9.3 + Bluetooth-Pakete). `.github/workflows/hacs.yml`: HACS-Validierung.
- `tools/next-version` und `tools/pruefe-uebersetzungen.py` sind byteidentische Kopien der Vorlagen.
- README-Bilder aus `docs/readme-images.json` mit `theme/v1/readme-images/render.mjs <repo>` aus sh1ft-w.github.io:
  Titel `docs/images/readme-title-{light,dark}.webp` (2560 × 1280, Fenster zeigt `docs/images/title-shot-*.png` =
  Funktionsblock der Projektseite, 2× aufgenommen) plus `readme-title-social.png` für die GitHub-Vorschau; README-Aufbau nach `repo-vorlagen/readme-muster.md`, ohne Kartentabellen. Die Projektseite zeigt das
  Titelbild im Hero.
