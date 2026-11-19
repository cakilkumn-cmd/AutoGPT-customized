"""Übernommen aus CarSearch-Professionals tests/test_bewertung.py (die Red-Flag-Tests)."""

from datetime import date

from backend.blocks.autocheck.red_flags import InseratDaten, KiHinweis, pruefe

HEUTE = date(2026, 11, 18)


def inserat(**kw) -> InseratDaten:
    basis = {
        "preis_eur": 23_800.0, "kilometerstand": 45_000, "erstzulassung": date(2022, 3, 1),
        "land": "DE", "kraftstoff": "diesel", "co2_g_km": 130, "unfallfrei": True,
        "vorbesitzer": 1, "scheckheft": True, "beschreibung": "", "ki_hinweise": [],
    }  # fmt: skip
    basis.update(kw)
    return InseratDaten(**basis)


def codes(flags):
    return {f.code for f in flags}


def test_betrugspreis_ist_kritisch():
    flags = pruefe(inserat(preis_eur=15_000), 25_000, HEUTE)
    assert "preis_viel_zu_niedrig" in codes(flags)
    assert flags[0].schwere == "kritisch"


def test_textregeln():
    i = inserat(
        beschreibung="Fahrzeug steht im Ausland, bitte Anzahlung per Western Union. Kontakt nur WhatsApp."
    )
    titel = {f.titel for f in pruefe(i, None, HEUTE)}
    assert "Zahlung vor Besichtigung verlangt" in titel
    assert "Kontakt nur über Messenger/E-Mail" in titel
    assert "Fahrzeug angeblich nicht vor Ort / nur Lieferung" in titel


def test_unfallfrei_ist_kein_flag():
    i = inserat(beschreibung="Unfallfrei, kein Unfall, scheckheftgepflegt.")
    assert not any("Unfall" in f.titel for f in pruefe(i, None, HEUTE))


def test_wenig_km_flag():
    i = inserat(erstzulassung=date(2018, 7, 1), kilometerstand=12_000)
    assert "km_sehr_niedrig" in codes(pruefe(i, None, HEUTE))


def test_llm_hinweise_uebernommen():
    i = inserat(
        ki_hinweise=[
            KiHinweis(
                schwere="warnung",
                titel="Widerspruch",
                zitat="Erstbesitz, 3 Vorbesitzer",
            )
        ]
    )
    assert "llm" in codes(pruefe(i, None, HEUTE))
