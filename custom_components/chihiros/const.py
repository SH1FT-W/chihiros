"""Konstanten der Chihiros-Integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "chihiros"

CONF_ADDRESS: Final = "address"
CONF_PUMP_COUNT: Final = "pump_count"

CONF_NAME: Final = "name"  # Bluetooth-Name (Modell), der Titel des Eintrags kann umbenannt werden
CONF_DEVICE_TYPE: Final = "device_type"  # Commander 1: white / rgb / wrgb
DEFAULT_PUMPS: Final = 4

# Nordic-UART bzw. HM-10/eigenes Notify-Merkmal
UART_RX_CHAR: Final = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
UART_TX_CHAR: Final = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
HM10_RX_CHAR: Final = "0000ffe1-0000-1000-8000-00805f9b34fb"
CUSTOM_NOTIFY_CHAR: Final = "8ec90003-f315-4f60-9fb8-838830daea50"

OP_TIMEOUT: Final = 40.0  # ein Vorgang (verbinden + schreiben + warten) darf nie länger dauern
IDLE_DISCONNECT: Final = 15.0  # danach Proxy-Platz wieder freigeben
BATCH_GAP: Final = 0.03  # Abstand zwischen Rahmen (wie die App)
FADE_MIN_GAP: Final = 2.0  # Übergänge: höchstens alle 2 s ein Schritt (3 Rahmen)

DOSER_POLL: Final = timedelta(minutes=30)
FIRST_CONTACT_DELAY: Final = 90.0  # erster Kontakt nach dem Start – nie während HA hochfährt
CONTACT_RETRY: Final = 300.0  # erster Kontakt fehlgeschlagen → so viele Sekunden später erneut
LIGHT_CONTACT: Final = timedelta(hours=1)  # Lampe regelmäßig ansprechen: Erreichbarkeit + Uhr (Auto-Programm)

STORAGE_VERSION: Final = 1
MAX_CURVE_POINTS: Final = 16
RESET_PAUSE: Final = 1.0  # nach „Ablauf löschen“ verschluckt die Lampe sonst die ersten Punkte
SNAPSHOT_SLOTS: Final = 8  # so viele Punkte meldet die Lampe im Tagesablauf-Snapshot (nur Rot)
CURVE_RETRIES: Final = 2  # fehlende Punkte so oft nachschreiben

# Optionen der Dosierpumpe: Push bei niedrigem Füllstand
CONF_NOTIFY = "notify_service"  # z. B. "notify.mobile_app_iphone" ("" = aus)
CONF_NOTIFY_PERCENT = "notify_percent"
DEFAULT_NOTIFY_PERCENT = 20
