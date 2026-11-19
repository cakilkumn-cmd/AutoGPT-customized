"""Regelbasierte Red Flags für Autoinserate (deterministisch) + Übernahme der KI-Hinweise.

Regeln übernommen aus CarSearch-Professionals (src/autocheck/flags.py).
"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel

Schwere = Literal["info", "warnung", "kritisch"]
REIHENFOLGE = {"kritisch": 0, "warnung": 1, "info": 2}

# (Regex, Schwere, Titel) – Suche case-insensitive im Beschreibungstext.
TEXT_REGELN: list[tuple[str, Schwere, str]] = [
    (r"vorkasse|anzahlung|vorab überweis|western union|moneygram|treuhand", "kritisch", "Zahlung vor Besichtigung verlangt"),
    (r"whatsapp|nur per (e-?mail|mail)|nur schriftlich", "warnung", "Kontakt nur über Messenger/E-Mail"),
    (r"im ausland|spedition|lieferung (nach|per)|versand des fahrzeugs", "warnung", "Fahrzeug angeblich nicht vor Ort / nur Lieferung"),
    (r"\bexport\b|gewerbe(kunden)?|händlerverkauf|an händler", "warnung", "Nur Export/Gewerbe – keine Gewährleistung"),
    (r"bastler|motorschaden|getriebeschaden|defekt|springt nicht an", "kritisch", "Defekt/Bastlerfahrzeug"),
    (r"(?<!kein )(?<!nicht )\bunfall(?!frei)|unfallschaden|reparierter (vor)?schaden|instandgesetzt", "warnung", "Unfall/Vorschaden erwähnt"),
    (r"nicht unfallfrei", "warnung", "Nicht unfallfrei"),
    (r"hagel|wasserschaden|überschwemm|flut", "warnung", "Hagel-/Wasserschaden erwähnt"),
    (r"re-?import|eu-?fahrzeug", "info", "(Re-)Import-Fahrzeug – Ausstattung/Garantie prüfen"),
    (r"ohne (tüv|hu|pickerl)|tüv (ist )?abgelaufen|hu (ist )?abgelaufen", "warnung", "Keine gültige HU/TÜV"),
    (r"ohne (gewähr|garantie)|gekauft wie gesehen|unter ausschluss", "info", "Gewährleistungsausschluss"),
    (r"nur (heute|diese woche)|schnell(entschlossene)?|muss schnell weg|dringend", "info", "Zeitdruck im Inserat"),
]  # fmt: skip


class Flag(BaseModel):
    code: str
    schwere: Schwere
    titel: str
    detail: str = ""


class KiHinweis(BaseModel):
    """Weiches Warnsignal, das ein Sprachmodell im Inseratstext erkannt hat."""

    schwere: Schwere = "info"
    titel: str
    zitat: str = ""


class InseratDaten(BaseModel):
    preis_eur: float | None = None
    kilometerstand: int | None = None
    erstzulassung: date | None = None
    land: str = "unbekannt"
    kraftstoff: str = "unbekannt"
    co2_g_km: int | None = None
    unfallfrei: bool | None = None
    vorbesitzer: int | None = None
    scheckheft: bool | None = None
    beschreibung: str = ""
    ki_hinweise: list[KiHinweis] = []


def pruefe(
    inserat: InseratDaten, markt_median: float | None = None, heute: date | None = None
) -> list[Flag]:
    heute = heute or date.today()
    flags = (
        _pflichtangaben(inserat)
        + _preis_vs_markt(inserat, markt_median)
        + _laufleistung(inserat, heute)
        + _historie(inserat)
    )
    if (
        inserat.land == "DE"
        and inserat.co2_g_km is None
        and inserat.kraftstoff != "elektro"
    ):
        flags.append(Flag(code="co2_fehlt", schwere="warnung", titel="CO2-Wert fehlt",
                          detail="Für die NoVA nötig – beim Verkäufer COC-Papier anfordern."))  # fmt: skip

    gesehen = {f.titel for f in flags}
    for f in _textregeln(inserat) + _ki_hinweise(inserat):
        if f.titel not in gesehen:
            flags.append(f)
            gesehen.add(f.titel)
    return sorted(flags, key=lambda f: REIHENFOLGE[f.schwere])


def _pflichtangaben(i: InseratDaten) -> list[Flag]:
    felder = (("preis_eur", i.preis_eur, "Preis"), ("kilometerstand", i.kilometerstand, "Kilometerstand"),
              ("erstzulassung_jahr", i.erstzulassung, "Erstzulassung"))  # fmt: skip
    return [
        Flag(code=f"fehlt_{feld}", schwere="warnung", titel=f"{titel} fehlt im Inserat")
        for feld, wert, titel in felder
        if wert is None
    ]


def _preis_vs_markt(i: InseratDaten, median: float | None) -> list[Flag]:
    if not median or not i.preis_eur:
        return []
    abw = (i.preis_eur - median) / median
    if abw <= -0.30:
        return [Flag(code="preis_viel_zu_niedrig", schwere="kritisch", titel="Preis > 30 % unter Marktwert",
                     detail="Typisches Betrugsmuster. Fahrzeug nur vor Ort besichtigen, nie vorab zahlen.")]  # fmt: skip
    if abw <= -0.15:
        return [Flag(code="preis_auffaellig_niedrig", schwere="info", titel="Preis deutlich unter Marktwert",
                     detail="Grund klären: Schaden, hohe Laufleistung, Ausstattung, Eile?")]  # fmt: skip
    if abw >= 0.15:
        return [
            Flag(
                code="preis_hoch", schwere="info", titel="Preis deutlich über Marktwert"
            )
        ]
    return []


def _laufleistung(i: InseratDaten, heute: date) -> list[Flag]:
    if i.erstzulassung is None or i.kilometerstand is None:
        return []
    alter = max((heute - i.erstzulassung).days / 365.25, 0.1)
    km_pro_jahr = i.kilometerstand / alter
    text = f"≈ {km_pro_jahr:,.0f} km/Jahr".replace(",", ".")
    if alter >= 3 and km_pro_jahr < 3000:
        return [Flag(code="km_sehr_niedrig", schwere="warnung", titel="Ungewöhnlich wenig Kilometer",
                     detail=f"{text} – Tachostand anhand Servicehistorie prüfen.")]  # fmt: skip
    if km_pro_jahr > 30000:
        return [
            Flag(
                code="km_hoch",
                schwere="info",
                titel="Hohe Jahresfahrleistung",
                detail=text,
            )
        ]
    return []


def _historie(i: InseratDaten) -> list[Flag]:
    flags = []
    if i.unfallfrei is False:
        flags.append(
            Flag(code="nicht_unfallfrei", schwere="warnung", titel="Nicht unfallfrei")
        )
    if i.vorbesitzer is not None and i.vorbesitzer > 3:
        flags.append(
            Flag(
                code="viele_vorbesitzer",
                schwere="info",
                titel=f"{i.vorbesitzer} Vorbesitzer",
            )
        )
    if i.scheckheft is False:
        flags.append(
            Flag(
                code="kein_scheckheft",
                schwere="info",
                titel="Kein Scheckheft/Servicenachweis",
            )
        )
    return flags


def _textregeln(i: InseratDaten) -> list[Flag]:
    text = i.beschreibung.lower()
    flags = []
    for muster, schwere, titel in TEXT_REGELN:
        if not (m := re.search(muster, text)):
            continue
        start = max(m.start() - 40, 0)
        flags.append(Flag(code="text_" + re.sub(r"\W+", "_", titel.lower())[:30], schwere=schwere, titel=titel,
                          detail=f"„…{i.beschreibung[start:m.end() + 40].strip()}…“"))  # fmt: skip
    return flags


def _ki_hinweise(i: InseratDaten) -> list[Flag]:
    return [
        Flag(
            code="llm",
            schwere=h.schwere,
            titel=h.titel,
            detail=f"„{h.zitat}“ (KI-Hinweis)",
        )
        for h in i.ki_hinweise
    ]
