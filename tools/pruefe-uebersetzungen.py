#!/usr/bin/env python3
"""Prüft, dass strings.json und alle translations/*.json dieselben Schlüssel haben
und dass Platzhalter {x} in jeder Sprache gleich sind. Vorlage aus repo-vorlagen/vorlagen.
Aufruf im Repo-Wurzelverzeichnis: python tools/pruefe-uebersetzungen.py"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


def flach(d: object, pfad: str = "") -> dict[str, str]:
    aus: dict[str, str] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            aus.update(flach(v, f"{pfad}.{k}" if pfad else str(k)))
    else:
        aus[pfad] = str(d)
    return aus


def main() -> int:
    fehler = 0
    for comp in sorted(Path("custom_components").glob("*/")):
        strings = comp / "strings.json"
        if not strings.exists():
            continue
        basis = flach(json.loads(strings.read_text(encoding="utf-8")))
        for tr in sorted((comp / "translations").glob("*.json")):
            andere = flach(json.loads(tr.read_text(encoding="utf-8")))
            nur_basis = sorted(set(basis) - set(andere))
            nur_tr = sorted(set(andere) - set(basis))
            for k in nur_basis:
                print(f"{tr}: fehlt {k}")
            for k in nur_tr:
                print(f"{tr}: überzählig {k}")
            fehler += len(nur_basis) + len(nur_tr)
            for k in set(basis) & set(andere):
                p1 = sorted(re.findall(r"\{(\w+)\}", basis[k]))
                p2 = sorted(re.findall(r"\{(\w+)\}", andere[k]))
                if p1 != p2:
                    print(f"{tr}: Platzhalter abweichend in {k}: {p1} vs {p2}")
                    fehler += 1
    if fehler:
        print(f"{fehler} Abweichung(en)")
        return 1
    print("Übersetzungen vollständig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
