"""Import-Kostenrechner Deutschland -> Österreich (Privatimport eines Pkw).

Rechenkern übernommen aus CarSearch-Professionals (src/autocheck/import_at).
Rein deterministisch, kein LLM. Keine Steuerberatung, Ergebnisse sind Schätzungen.
Neu gegenüber CarSearch: anteilige Kürzung von Abzugsposten und Malus bei
Gebrauchtimporten, wenn der Neupreis netto bekannt ist.
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from .nova_params import NovaParameter, parameter_fuer

UST_DE = 0.19
UST_AT = 0.20
NEUFAHRZEUG_MAX_MONATE = 6
NEUFAHRZEUG_MAX_KM = 6000

VerkaeuferTyp = Literal["haendler", "privat", "unbekannt"]


class Nebenkosten(BaseModel):
    """Schätzwerte in Euro."""

    ueberstellung: float = 250.0  # Kurzzeitkennzeichen, Versicherung, Fahrt
    datenuebernahme_einzelgenehmigung: float = 200.0
    begutachtung_57a: float = 90.0  # §57a-Gutachten ("Pickerl")
    zulassung_kennzeichen: float = 220.0

    def summe(self) -> float:
        return (
            self.ueberstellung
            + self.datenuebernahme_einzelgenehmigung
            + self.begutachtung_57a
            + self.zulassung_kennzeichen
        )


class NovaErgebnis(BaseModel):
    betrag: float
    steuersatz_pct: int
    malus: float
    bemessungsgrundlage: float
    parameter: NovaParameter | None
    stichtag: date
    kuerzungsfaktor: float = 1.0
    hinweise: list[str] = Field(default_factory=list)


class ImportErgebnis(BaseModel):
    berechenbar: bool
    kaufpreis_bezahlt: float = 0.0
    ust_at: float = 0.0
    nova: NovaErgebnis | None = None
    nebenkosten: float = 0.0
    gesamt: float = 0.0
    neufahrzeug: bool = False
    hinweise: list[str] = Field(default_factory=list)


class Fahrzeug(BaseModel):
    """Die Angaben, die der Importrechner braucht."""

    preis_eur: float | None
    erstzulassung: date | None
    co2_g_km: int | None
    verkaeufer_typ: VerkaeuferTyp = "haendler"
    mwst_ausweisbar: bool | None = True
    kilometerstand: int | None = None
    elektro: bool = False
    co2_norm: Literal["WLTP", "NEFZ", "unbekannt"] = "WLTP"
    fahrzeugklasse: Literal["M1", "N1", "unbekannt"] = "M1"
    neupreis_netto: float | None = None


def berechne_import(
    fz: Fahrzeug,
    *,
    kaufdatum: date | None = None,
    nebenkosten: float | None = None,
    bmg_override: float | None = None,
) -> ImportErgebnis:
    kaufdatum = kaufdatum or date.today()
    nk = Nebenkosten().summe() if nebenkosten is None else nebenkosten

    if fz.preis_eur is None:
        return ImportErgebnis(
            berechenbar=False, hinweise=["Kein Preis erkannt – nicht berechenbar."]
        )
    if fz.fahrzeugklasse == "N1":
        return ImportErgebnis(
            berechenbar=False,
            hinweise=[
                "N1-Fahrzeuge (Pickup/Transporter) werden nicht berechnet – "
                "Sonderregeln, seit 07/2025 teils NoVA-frei."
            ],
        )

    hinweise: list[str] = []
    if fz.erstzulassung is None:
        hinweise.append(
            "Erstzulassung unbekannt – Rechtslage zum Kaufdatum angenommen."
        )
    neu = ist_neufahrzeug(fz.erstzulassung, fz.kilometerstand, kaufdatum)
    kaufpreis, ust_at, stichtag, netto = _kaufpreis_und_stichtag(
        fz, fz.preis_eur, neu, kaufdatum, hinweise
    )
    bmg = _bemessungsgrundlage(fz, netto, bmg_override, hinweise)

    if fz.co2_g_km is None and not fz.elektro:
        hinweise.append(
            "CO2-Wert fehlt – bitte eintragen (COC-Papier oder Herstellerangabe), "
            "sonst keine NoVA."
        )
        return ImportErgebnis(
            berechenbar=False, kaufpreis_bezahlt=kaufpreis, ust_at=ust_at,
            nebenkosten=nk, neufahrzeug=neu, hinweise=hinweise,
        )  # fmt: skip
    if fz.co2_norm == "NEFZ":
        hinweise.append(
            "CO2-Wert ist NEFZ – NoVA ab 2020 basiert auf WLTP; zu niedrig."
        )

    faktor = _kuerzungsfaktor(fz, bmg, neu, hinweise)
    nova = berechne_nova(
        fz.co2_g_km or 0, bmg, stichtag, elektro=fz.elektro, kuerzungsfaktor=faktor
    )
    berechenbar = nova.parameter is not None or fz.elektro
    gesamt = kaufpreis + ust_at + nova.betrag + nk if berechenbar else 0.0
    return ImportErgebnis(
        berechenbar=berechenbar, kaufpreis_bezahlt=round(kaufpreis, 2),
        ust_at=round(ust_at, 2), nova=nova, nebenkosten=nk,
        gesamt=round(gesamt, 2), neufahrzeug=neu, hinweise=hinweise + nova.hinweise,
    )  # fmt: skip


def berechne_nova(
    co2_g_km: int,
    bemessungsgrundlage: float,
    stichtag: date,
    *,
    elektro: bool = False,
    kuerzungsfaktor: float = 1.0,
) -> NovaErgebnis:
    """NoVA für Pkw (M1).

    Satz = (CO2 - Abzugsbetrag) / 5, ganze Prozent, gedeckelt.
    Steuer = max(0, BMG * Satz - Abzugsposten * k) + Malus * k, mit k = Kürzungsfaktor.
    """
    basis = {"bemessungsgrundlage": bemessungsgrundlage, "stichtag": stichtag}
    if elektro or co2_g_km <= 0:
        return NovaErgebnis(
            betrag=0.0, steuersatz_pct=0, malus=0.0, parameter=None,
            hinweise=["Emissionsfrei: NoVA-befreit."], **basis,
        )  # fmt: skip

    p = parameter_fuer(stichtag)
    if p is None:
        return NovaErgebnis(
            betrag=0.0, steuersatz_pct=0, malus=0.0, parameter=None,
            hinweise=[
                f"Keine NoVA-Parameter für Stichtag {stichtag:%m/%Y} hinterlegt "
                "(vor 2020 gilt NEFZ-Regime) – bitte BMF-NoVA-Rechner nutzen."
            ],
            **basis,
        )  # fmt: skip

    satz = max(
        0, min(_runde_prozent((co2_g_km - p.co2_abzug_g) / 5), p.hoechststeuersatz_pct)
    )
    abzug = p.abzugsposten_eur * kuerzungsfaktor
    grundbetrag = max(0.0, bemessungsgrundlage * satz / 100 - abzug)
    malus = round(
        max(0, co2_g_km - p.malus_grenze_g) * p.malus_eur_pro_g * kuerzungsfaktor, 2
    )

    hinweise = []
    if not p.verifiziert:
        hinweise.append(
            "NoVA-Parameter aus Sekundärquellen – vor Kauf mit dem BMF-NoVA-Rechner gegenprüfen."
        )
    if p.hinweis:
        hinweise.append(p.hinweis)
    return NovaErgebnis(
        betrag=round(grundbetrag + malus, 2), steuersatz_pct=satz, malus=float(malus),
        parameter=p, kuerzungsfaktor=kuerzungsfaktor, hinweise=hinweise, **basis,
    )  # fmt: skip


def ist_neufahrzeug(
    erstzulassung: date | None, km: int | None, kaufdatum: date
) -> bool:
    """Umsatzsteuerlicher Neufahrzeugbegriff: <= 6 Monate seit EZ ODER <= 6.000 km."""
    if km is not None and km <= NEUFAHRZEUG_MAX_KM:
        return True
    if erstzulassung is None:
        return False
    monate = (kaufdatum.year - erstzulassung.year) * 12 + (
        kaufdatum.month - erstzulassung.month
    )
    return monate <= NEUFAHRZEUG_MAX_MONATE


def parse_erstzulassung(text: str | None) -> date | None:
    """'2022-03', '03/2022', '3.2022' oder '2022' -> Datum. Ohne Monat: Juli."""
    if not text:
        return None
    if m := re.fullmatch(r"\s*(\d{4})(?:[-/.](\d{1,2}))?(?:[-/.]\d{1,2})?\s*", text):
        return date(int(m[1]), int(m[2] or 7), 1)
    if m := re.fullmatch(r"\s*(\d{1,2})[-/.](\d{4})\s*", text):
        return date(int(m[2]), int(m[1]), 1)
    raise ValueError(
        f"Erstzulassung '{text}' nicht lesbar – bitte als JJJJ-MM angeben."
    )


def _kaufpreis_und_stichtag(
    fz: Fahrzeug, preis: float, neu: bool, kaufdatum: date, hinweise: list[str]
) -> tuple[float, float, date, float]:
    """Gibt (bezahlter Kaufpreis, USt AT, NoVA-Stichtag, Nettopreis) zurück."""
    netto = preis / (1 + UST_DE) if fz.mwst_ausweisbar else preis
    if neu:
        if not fz.mwst_ausweisbar:
            hinweise.append(
                "Neufahrzeug ohne ausweisbare MwSt – Netto-Kauf prüfen, "
                "sonst droht Doppelbesteuerung."
            )
        hinweise.append(
            "Gilt als Neufahrzeug (≤ 6 Monate oder ≤ 6.000 km): "
            "20 % Erwerbsteuer in Österreich."
        )
        return netto, netto * UST_AT, kaufdatum, netto
    # EU-Gebrauchtimport: Rechtslage zum Zeitpunkt der Erstzulassung (EuGH C-402/09 "Tatu").
    if fz.mwst_ausweisbar and fz.verkaeufer_typ == "haendler":
        hinweise.append(
            "MwSt. ausweisbar: als Privatkäufer zahlst du die 19 % deutsche MwSt. mit "
            "(keine Erstattung)."
        )
    return preis, 0.0, fz.erstzulassung or kaufdatum, netto


def _bemessungsgrundlage(
    fz: Fahrzeug, netto: float, bmg_override: float | None, hinweise: list[str]
) -> float:
    """Händlerkauf: Netto-Kaufpreis; Privatkauf: gemeiner Wert (Eurotax), hier Näherung."""
    if bmg_override is not None:
        return bmg_override
    if fz.verkaeufer_typ != "haendler":
        hinweise.append(
            "Privatkauf: NoVA-Basis ist der gemeine Wert (Eurotax) – "
            "Kaufpreis als Näherung verwendet."
        )
    elif not fz.mwst_ausweisbar:
        hinweise.append(
            "Differenzbesteuert: Kaufpreis als NoVA-Basis angenommen "
            "(enthält keine ausweisbare MwSt)."
        )
    return netto


def _kuerzungsfaktor(fz: Fahrzeug, bmg: float, neu: bool, hinweise: list[str]) -> float:
    """Gebrauchtimport: Abzugsposten und Malus im Verhältnis BMG / Neupreis netto kürzen."""
    if neu or fz.elektro:
        return 1.0
    if not fz.neupreis_netto or fz.neupreis_netto <= 0:
        hinweise.append(
            "Neupreis netto fehlt – Abzugsposten und Malus nicht anteilig gekürzt; "
            "NoVA dadurch ungenau."
        )
        return 1.0
    faktor = round(min(1.0, bmg / fz.neupreis_netto), 4)
    hinweise.append(
        f"Gebrauchtimport: Abzugsposten und Malus anteilig gekürzt "
        f"(Faktor {faktor:.2f} = NoVA-Basis / Neupreis netto). "
        "Kürzungsregel nicht amtlich geprüft."
    )
    return faktor


def _runde_prozent(wert: float) -> int:
    return int(Decimal(str(wert)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
