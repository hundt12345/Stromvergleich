"""Reine Textauswertung: aus dem Seitentext Arbeitspreis und Grundgebühr extrahieren.

Bewusst ohne Browser, damit die Logik einzeln testbar ist (siehe tests/).
Preise werden ohne Boni betrachtet; Boni-Beträge (z. B. "Bonus: 193 €") werden
nicht als Grundgebühr oder Arbeitspreis gewertet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

# Arbeitspreis: "28,10 Cent/kWh", "28,90ct Arbeitspreis", "Arbeitspreis: 35,30 Cent"
AP_RE = re.compile(r"(\d{1,2}[,.]\d{1,2})\s*(?:ct|cent)(?![a-zäöü])", re.IGNORECASE)
# Geldbetrag mit Einheit Monat/Jahr: "23,90 €/Monat", "13,90€ Grundpreis pro Monat", "277,33 € pro Jahr"
EUR_RE = re.compile(
    r"(\d{1,4}[,.]\d{2})\s*€\s*(/\s*Mon\w*|pro\s*Monat|monatlich|/\s*Jahr|pro\s*Jahr|im\s*Jahr|Grundpreis)?",
    re.IGNORECASE,
)
# Kontext, der einen Geldbetrag als Grundgebühr kennzeichnet
GRUND_CTX = re.compile(r"grundpreis|grundgebühr|grundgebuehr|grundgebühr", re.IGNORECASE)
ABSCHLAG_CTX = re.compile(r"abschlag|vorauszahlung|bonus|rabatt", re.IGNORECASE)

AP_MIN, AP_MAX = 15.0, 50.0    # plausibler Arbeitspreis in ct/kWh (brutto)
GP_MIN, GP_MAX = 0.0, 60.0     # plausible Grundgebühr in €/Monat (brutto)
WINDOW = 700                   # Zeichen um einen Arbeitspreis, in denen eine Grundgebühr gesucht wird


@dataclass
class Tariff:
    ap_ct: float            # Arbeitspreis in ct/kWh
    gp_month: float | None  # Grundgebühr in €/Monat (None = nicht gefunden)
    annual_eur: float | None
    snippet: str            # kurzer Textauszug als Nachweis

    def as_dict(self) -> dict:
        return asdict(self)


def _num(s: str) -> float:
    """'28,10' -> 28.1 ; '2.05' -> 2.05 (Punkt oder Komma als Dezimaltrennzeichen)."""
    return float(s.replace(",", "."))


def _is_grundgebuehr(text: str, start: int, end: int) -> bool:
    ctx = text[max(0, start - 60): min(len(text), end + 60)]
    return bool(GRUND_CTX.search(ctx)) and not ABSCHLAG_CTX.search(text[max(0, start - 30): end + 5])


def _gp_candidates(text: str) -> list[tuple[int, float]]:
    """Liefert (Position, Grundgebühr in €/Monat) für alle Geldbeträge, die als Grundgebühr gelten."""
    out = []
    for m in EUR_RE.finditer(text):
        try:
            value = _num(m.group(1))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        if not _is_grundgebuehr(text, m.start(), m.end()) and "grund" not in unit:
            continue
        if "jahr" in unit:
            value /= 12.0
        if GP_MIN <= value <= GP_MAX:
            out.append((m.start(), round(value, 2)))
    return out


def parse_tariffs(text: str, kwh: int) -> list[Tariff]:
    """Paart jeden Arbeitspreis mit der nächstgelegenen Grundgebühr im Text."""
    gps = _gp_candidates(text)
    tariffs: list[Tariff] = []
    seen: set[tuple[float, float | None]] = set()
    for m in AP_RE.finditer(text):
        try:
            ap = _num(m.group(1))
        except ValueError:
            continue
        if not (AP_MIN <= ap <= AP_MAX):
            continue
        # Netzentgelt-/Steuerzeilen wie "Stromsteuer: 2.05 ct" fallen über AP_MIN heraus.
        near = [(abs(pos - m.start()), gp) for pos, gp in gps if abs(pos - m.start()) <= WINDOW]
        gp = min(near)[1] if near else None
        key = (ap, gp)
        if key in seen:
            continue
        seen.add(key)
        annual = round(kwh * ap / 100 + 12 * gp, 2) if gp is not None else None
        snippet = re.sub(r"\s+", " ", text[max(0, m.start() - 60): m.end() + 60]).strip()
        tariffs.append(Tariff(ap_ct=ap, gp_month=gp, annual_eur=annual, snippet=snippet[:180]))
    return tariffs


UNAVAILABLE_RE = re.compile(
    r"(nicht\s+(?:verfügbar|beliefer\w*|möglich)|keine\s+belieferung|liefern\s+(?:wir\s+)?(?:leider\s+)?nicht"
    r"|liefern\s+wir\s+[^.\n]{0,60}nicht|beliefern\s+wir\s+[^.\n]{0,60}nicht"
    r"|nicht\s+in\s+ihrer\s+(?:region|postleitzahl)|keine\s+tarife\s+(?:verfügbar|gefunden)|außerhalb\s+unseres\s+(?:liefer|netz))",
    re.IGNORECASE,
)


def detect_unavailable(text: str) -> bool:
    return bool(UNAVAILABLE_RE.search(text))


def best_tariff(tariffs: list[Tariff]) -> Tariff | None:
    complete = [t for t in tariffs if t.annual_eur is not None]
    return min(complete, key=lambda t: t.annual_eur) if complete else None
