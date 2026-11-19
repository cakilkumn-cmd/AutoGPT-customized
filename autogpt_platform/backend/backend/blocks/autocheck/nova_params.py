"""NoVA-Parameter für Pkw (Klasse M1), nach Gültigkeitszeitraum.

Übernommen aus CarSearch-Professionals (src/autocheck/import_at/nova_params.py).
Werte stammen aus übereinstimmenden Sekundärquellen (WKO, novamobile.at,
Wikipedia), nicht direkt vom BMF; ``verifiziert=False`` markiert das.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict


class NovaParameter(BaseModel):
    model_config = ConfigDict(frozen=True)

    gueltig_ab: date
    gueltig_bis: date  # inklusive
    co2_abzug_g: int  # Steuersatz = (CO2 - co2_abzug_g) / 5
    hoechststeuersatz_pct: int
    malus_grenze_g: int
    malus_eur_pro_g: int
    abzugsposten_eur: int
    norm: str = "WLTP"
    verifiziert: bool = False
    hinweis: str = ""


def parameter_fuer(stichtag: date) -> NovaParameter | None:
    """Parameter am Stichtag. None = nicht abgedeckt (z. B. vor 2020 / NEFZ)."""
    return next(
        (p for p in PKW_PARAMETER if p.gueltig_ab <= stichtag <= p.gueltig_bis),
        None,
    )


def _p(
    ab: date, bis: date, abzug: int, hoechst: int, grenze: int, malus: int, **kw
) -> NovaParameter:
    return NovaParameter(
        gueltig_ab=ab,
        gueltig_bis=bis,
        co2_abzug_g=abzug,
        hoechststeuersatz_pct=hoechst,
        malus_grenze_g=grenze,
        malus_eur_pro_g=malus,
        abzugsposten_eur=350,
        **kw,
    )


PKW_PARAMETER: tuple[NovaParameter, ...] = (
    _p(date(2020, 1, 1), date(2020, 12, 31), 115, 32, 275, 40),
    _p(date(2021, 1, 1), date(2021, 6, 30), 112, 32, 275, 40),
    _p(date(2021, 7, 1), date(2021, 12, 31), 112, 50, 200, 50),
    _p(date(2022, 1, 1), date(2022, 12, 31), 107, 60, 185, 60),
    _p(date(2023, 1, 1), date(2023, 12, 31), 102, 70, 170, 70),
    _p(date(2024, 1, 1), date(2024, 12, 31), 97, 80, 155, 80),
    _p(date(2025, 1, 1), date(2025, 12, 31), 94, 80, 155, 80),
    _p(date(2026, 1, 1), date(2026, 12, 31), 91, 80, 155, 80),
    _p(
        date(2027, 1, 1),
        date(2027, 12, 31),
        88,
        80,
        155,
        80,
        hinweis="Wert 2027 laut Sekundärquellen angekündigt, noch nicht bestätigt.",
    ),
)
