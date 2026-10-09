import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.parser import parse_tariffs, detect_unavailable, best_tariff  # noqa: E402

# Texte sind sinngemäß aus den echten Seiten übernommen (Stand Okt. 2026).
NATURSTROM = """
13,90€ Grundpreis pro Monat
28,90ct Arbeitspreis / kWh
Mehr Tarif-Infos
13,90€ Grundpreis pro Monat
30,90ct Arbeitspreis / kWh
Stromsteuer: 2.05 ct
Netznutzung und Abgaben*: 9.28 ct
"""

VATTENFALL = """
Der Tarif ohne Bonus
Online-Tarif
28,10 Cent/kWh
Grundpreis: 5)
20,90 €/Monat
Tarifdetails
Der Tarif mit Shop-Gutschein
Online-Tarif
29,00 Cent/kWh
Grundpreis: 5)
23,90 €/Monat
Sofort-Bonus: 193 € einmalig
"""

ABSCHLAG_ONLY = """
Arbeitspreis 30,00 ct/kWh
Abschlag: 58 €/Monat geschätzt
Bonus: 193 € einmalig
"""

UNAVAILABLE = "Leider liefern wir in Ihrer Region nicht. Bitte wählen Sie einen anderen Anbieter."


def test_naturstrom_pairs_ap_with_nearest_gp():
    ts = parse_tariffs(NATURSTROM, 1200)
    best = best_tariff(ts)
    assert best is not None
    assert best.ap_ct == 28.9
    assert best.gp_month == 13.9
    # 1200 * 0.289 + 12 * 13.90 = 513.60
    assert round(best.annual_eur, 2) == 513.60


def test_netzentgelte_are_not_tariffs():
    ts = parse_tariffs(NATURSTROM, 1200)
    assert all(t.ap_ct >= 15 for t in ts)


def test_vattenfall_yearly_and_monthly():
    ts = parse_tariffs(VATTENFALL, 1200)
    aps = sorted({t.ap_ct for t in ts})
    assert aps == [28.1, 29.0]
    cheapest = best_tariff(ts)
    # 1200 * 0.281 + 12 * 20.90 = 588.00
    assert round(cheapest.annual_eur, 2) == 588.00


def test_abschlag_is_not_grundgebuehr():
    ts = parse_tariffs(ABSCHLAG_ONLY, 1200)
    assert len(ts) == 1
    assert ts[0].gp_month is None
    assert ts[0].annual_eur is None


def test_unavailable_detection():
    assert detect_unavailable(UNAVAILABLE)
    assert not detect_unavailable(NATURSTROM)
