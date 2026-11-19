"""Übernommen aus CarSearch-Professionals tests/test_import_at.py, plus Tests der anteiligen Kürzung."""

import itertools
from datetime import date

import pytest

from backend.blocks.autocheck.nova import (
    Fahrzeug,
    Nebenkosten,
    berechne_import,
    berechne_nova,
    parse_erstzulassung,
)
from backend.blocks.autocheck.nova_params import PKW_PARAMETER, parameter_fuer

KAUF = date(2026, 11, 1)


def fahrzeug(**kw) -> Fahrzeug:
    """Entspricht der `inserat`-Fixture aus CarSearch: VW Golf 2.0 TDI, Händler, MwSt ausweisbar."""
    basis = {
        "preis_eur": 23_800.0, "erstzulassung": date(2022, 3, 1), "co2_g_km": 130,
        "verkaeufer_typ": "haendler", "mwst_ausweisbar": True, "kilometerstand": 45_000,
        "elektro": False, "co2_norm": "WLTP", "fahrzeugklasse": "M1",
    }  # fmt: skip
    basis.update(kw)
    return Fahrzeug(**basis)


def test_parameter_lueckenlos_und_ohne_ueberlappung():
    for a, b in itertools.pairwise(PKW_PARAMETER):
        assert (b.gueltig_ab - a.gueltig_bis).days == 1


@pytest.mark.parametrize(
    "stichtag, abzug, hoechst",
    [(date(2020, 5, 1), 115, 32), (date(2021, 6, 30), 112, 32), (date(2021, 7, 1), 112, 50),
     (date(2023, 6, 1), 102, 70), (date(2026, 11, 18), 91, 80)],
)  # fmt: skip
def test_parameter_fuer(stichtag, abzug, hoechst):
    p = parameter_fuer(stichtag)
    assert p is not None
    assert (p.co2_abzug_g, p.hoechststeuersatz_pct) == (abzug, hoechst)


def test_nova_2026_standardfall():
    # (130-91)/5 = 7,8 -> 8 %; 25.000 * 8 % = 2.000 - 350 = 1.650; kein Malus
    n = berechne_nova(130, 25_000, date(2026, 3, 1))
    assert n.steuersatz_pct == 8
    assert n.malus == 0
    assert n.betrag == 1650


def test_nova_mit_malus():
    # (200-91)/5 = 21,8 -> 22 %; 40.000 * 22 % = 8.800 - 350 = 8.450; Malus (200-155)*80 = 3.600
    n = berechne_nova(200, 40_000, date(2026, 1, 1))
    assert n.betrag == 8450 + 3600


def test_nova_hoechststeuersatz():
    n = berechne_nova(600, 100_000, date(2022, 1, 1))
    assert n.steuersatz_pct == 60


def test_nova_niedriger_co2_nie_negativ():
    n = berechne_nova(95, 20_000, date(2026, 1, 1))  # 0,8 -> 1 %; 200 - 350 < 0
    assert n.betrag == 0


def test_nova_elektro_frei():
    assert berechne_nova(0, 50_000, date(2026, 1, 1), elektro=True).betrag == 0


def test_nova_vor_2020_nicht_berechenbar():
    n = berechne_nova(140, 20_000, date(2019, 5, 1))
    assert n.parameter is None and n.hinweise


def test_gebrauchtimport_nutzt_rechtslage_der_erstzulassung():
    r = berechne_import(fahrzeug(), kaufdatum=KAUF, nebenkosten=Nebenkosten().summe())
    assert r.berechenbar and not r.neufahrzeug
    assert r.nova is not None and r.nova.parameter is not None
    assert r.nova.parameter.co2_abzug_g == 107  # Rechtslage 2022
    # Händler, MwSt ausweisbar: BMG = 23.800 / 1,19 = 20.000; (130-107)/5 = 4,6 -> 5 % = 1.000 - 350 = 650
    assert r.nova.bemessungsgrundlage == pytest.approx(20_000)
    assert r.nova.betrag == pytest.approx(650)
    assert r.ust_at == 0
    assert r.kaufpreis_bezahlt == 23_800  # Privatkäufer zahlt deutsche MwSt
    assert r.gesamt == pytest.approx(23_800 + 650 + Nebenkosten().summe())


def test_neufahrzeug_ust_in_at():
    fz = fahrzeug(erstzulassung=date(2026, 8, 1), kilometerstand=3000, preis_eur=35_700)
    r = berechne_import(fz, kaufdatum=KAUF)
    assert r.neufahrzeug
    assert r.kaufpreis_bezahlt == pytest.approx(30_000)
    assert r.ust_at == pytest.approx(6_000)
    assert r.nova is not None and r.nova.stichtag == KAUF


def test_ohne_co2_nicht_berechenbar():
    r = berechne_import(fahrzeug(co2_g_km=None), kaufdatum=KAUF)
    assert not r.berechenbar
    assert any("CO2" in h for h in r.hinweise)


def test_n1_nicht_unterstuetzt():
    assert not berechne_import(fahrzeug(fahrzeugklasse="N1")).berechenbar


def test_bmg_override():
    r = berechne_import(fahrzeug(), kaufdatum=KAUF, bmg_override=30_000)
    assert r.nova is not None and r.nova.bemessungsgrundlage == 30_000


# --- Neu: anteilige Kürzung von Abzugsposten und Malus bei Gebrauchtimporten ---


def test_kuerzung_abzugsposten_mit_neupreis():
    # Faktor 20.000 / 40.000 = 0,5 -> Abzugsposten 175: 1.000 - 175 = 825
    r = berechne_import(fahrzeug(neupreis_netto=40_000), kaufdatum=KAUF)
    assert r.nova is not None
    assert r.nova.kuerzungsfaktor == 0.5
    assert r.nova.betrag == pytest.approx(825)


def test_kuerzung_malus_mit_neupreis():
    # EZ 2026-01: (200-91)/5 -> 22 %; 20.000 * 22 % = 4.400 - 175 = 4.225; Malus 3.600 * 0,5 = 1.800
    fz = fahrzeug(erstzulassung=date(2026, 1, 1), co2_g_km=200, neupreis_netto=40_000)
    r = berechne_import(fz, kaufdatum=KAUF)
    assert r.nova is not None
    assert r.nova.malus == pytest.approx(1_800)
    assert r.nova.betrag == pytest.approx(4_225 + 1_800)


def test_ohne_neupreis_keine_kuerzung_aber_hinweis():
    r = berechne_import(fahrzeug(), kaufdatum=KAUF)
    assert r.nova is not None and r.nova.kuerzungsfaktor == 1.0
    assert any("Neupreis netto fehlt" in h for h in r.hinweise)


def test_neufahrzeug_wird_nicht_gekuerzt():
    fz = fahrzeug(
        erstzulassung=date(2026, 8, 1), kilometerstand=3000, neupreis_netto=60_000
    )
    r = berechne_import(fz, kaufdatum=KAUF)
    assert r.nova is not None and r.nova.kuerzungsfaktor == 1.0


def test_kuerzungsfaktor_hoechstens_eins():
    r = berechne_import(fahrzeug(neupreis_netto=10_000), kaufdatum=KAUF)
    assert r.nova is not None and r.nova.kuerzungsfaktor == 1.0
    assert r.nova.betrag == pytest.approx(650)


@pytest.mark.parametrize(
    "text, erwartet",
    [("2022-03", date(2022, 3, 1)), ("03/2022", date(2022, 3, 1)), ("2022", date(2022, 7, 1)),
     ("2022-03-15", date(2022, 3, 1)), ("", None)],
)  # fmt: skip
def test_parse_erstzulassung(text, erwartet):
    assert parse_erstzulassung(text) == erwartet


def test_parse_erstzulassung_unlesbar():
    with pytest.raises(ValueError):
        parse_erstzulassung("letztes Jahr")
