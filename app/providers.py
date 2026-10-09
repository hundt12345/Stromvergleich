"""Lädt die Anbieterliste (StromAuskunft, 1.387 Einträge) und ordnet bekannte Websites zu.

Quellen:
- data/anbieter_details.csv: Name, Sitz (PLZ/Ort) aus den StromAuskunft-Detailseiten
- data/domain_guess.json: Websites, die über Domain-Varianten des Namens gefunden wurden
- data/overrides.json: manuell gepflegte Websites (Slug -> URL), haben Vorrang
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Provider:
    slug: str
    name: str
    plz: str
    ort: str
    website: str  # leer = keine Website bekannt

    def public(self) -> dict:
        return {"slug": self.slug, "name": self.name, "plz": self.plz, "ort": self.ort, "website": self.website}


def load_providers() -> list[Provider]:
    overrides = json.loads((DATA / "overrides.json").read_text(encoding="utf-8"))
    guesses = json.loads((DATA / "domain_guess.json").read_text(encoding="utf-8"))
    providers: list[Provider] = []
    with open(DATA / "anbieter_details.csv", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for row in reader:
            slug = row["slug"].strip()
            name = row["name"].strip()
            website = overrides.get(slug) or guesses.get(name) or ""
            providers.append(Provider(slug=slug, name=name, plz=row.get("plz", "").strip(),
                                      ort=row.get("ort", "").strip(), website=website))
    return providers
