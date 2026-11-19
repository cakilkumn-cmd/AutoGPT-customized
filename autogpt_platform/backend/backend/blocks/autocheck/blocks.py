from datetime import date
from enum import Enum
from typing import Any

from backend.blocks._base import (
    Block,
    BlockCategory,
    BlockEffect,
    BlockOutput,
    BlockSchemaInput,
    BlockSchemaOutput,
)
from backend.data.model import SchemaField
from backend.util.exceptions import BlockInputError

from .nova import Fahrzeug, Nebenkosten, berechne_import, parse_erstzulassung
from .red_flags import InseratDaten, KiHinweis, pruefe


class Verkaeufer(Enum):
    HAENDLER = "haendler"
    PRIVAT = "privat"


class AutoCheckNovaBlock(Block):
    class Input(BlockSchemaInput):
        preis_eur: float = SchemaField(
            description="Angebotspreis in Euro, wie im Inserat"
        )
        erstzulassung: str = SchemaField(
            description="Erstzulassung als JJJJ-MM, z. B. 2022-03",
            placeholder="2022-03",
        )
        co2_wltp_g_km: int = SchemaField(description="CO2 kombiniert nach WLTP in g/km")
        neupreis_netto: float | None = SchemaField(
            description="Neupreis netto in Euro; nötig für die anteilige Kürzung beim Gebrauchtimport",
            default=None,
        )
        verkaeufer: Verkaeufer = SchemaField(
            description="Händler oder privat", default=Verkaeufer.HAENDLER
        )
        nebenkosten_eur: float = SchemaField(
            description="Überstellung, Gutachten, Zulassung usw. in Euro",
            default=Nebenkosten().summe(),
        )
        mwst_ausweisbar: bool = SchemaField(
            description="Händler weist 19 % deutsche MwSt aus",
            default=True,
            advanced=True,
        )
        kilometerstand: int | None = SchemaField(
            description="Für die Neufahrzeug-Regel (≤ 6.000 km)",
            default=None,
            advanced=True,
        )
        elektro: bool = SchemaField(
            description="Elektroauto (NoVA-frei)", default=False, advanced=True
        )
        kaufdatum: str = SchemaField(
            description="JJJJ-MM-TT; leer = heute", default="", advanced=True
        )

    class Output(BlockSchemaOutput):
        nova: float = SchemaField(description="Normverbrauchsabgabe in Euro")
        endpreis_at: float = SchemaField(
            description="Kaufpreis + USt AT + NoVA + Nebenkosten in Euro"
        )
        steuersatz_pct: int = SchemaField(description="NoVA-Steuersatz in Prozent")
        ust_at: float = SchemaField(
            description="Erwerbsteuer in Österreich (nur Neufahrzeuge)"
        )
        neufahrzeug: bool = SchemaField(
            description="Gilt umsatzsteuerlich als Neufahrzeug"
        )
        hinweise: list[str] = SchemaField(
            description="Annahmen und Warnungen zur Rechnung"
        )

    def __init__(self):
        super().__init__(
            id="02ba1843-3293-4590-bc3a-e19f59c94e8c",
            display_name="AutoCheck: NoVA und Endpreis AT",
            description="Berechnet NoVA und Endpreis in Österreich für ein Auto aus Deutschland.",
            categories={BlockCategory.LOGIC},
            input_schema=AutoCheckNovaBlock.Input,
            output_schema=AutoCheckNovaBlock.Output,
            effect=BlockEffect.NONE,
            test_input=[
                {
                    "preis_eur": 23_800.0,
                    "erstzulassung": "2022-03",
                    "co2_wltp_g_km": 130,
                    "kilometerstand": 45_000,
                    "kaufdatum": "2026-11-01",
                },
                {
                    "preis_eur": 23_800.0,
                    "erstzulassung": "2022-03",
                    "co2_wltp_g_km": 130,
                    "neupreis_netto": 40_000.0,
                    "kilometerstand": 45_000,
                    "kaufdatum": "2026-11-01",
                },
            ],  # fmt: skip
            test_output=[
                # BMG 20.000; (130-107)/5 -> 5 %; 1.000 - 350 = 650
                ("nova", 650.0),
                ("endpreis_at", 23_800 + 650 + 760.0),
                ("steuersatz_pct", 5),
                ("ust_at", 0.0),
                ("neufahrzeug", False),
                ("hinweise", lambda h: any("Neupreis netto fehlt" in x for x in h)),
                # Faktor 20.000 / 40.000 = 0,5 -> Abzugsposten 175: 1.000 - 175 = 825
                ("nova", 825.0),
                ("endpreis_at", 23_800 + 825 + 760.0),
                ("steuersatz_pct", 5),
                ("ust_at", 0.0),
                ("neufahrzeug", False),
                ("hinweise", lambda h: any("anteilig gekürzt" in x for x in h)),
            ],
        )

    async def run(self, input_data: Input, **kwargs) -> BlockOutput:
        fz = Fahrzeug(
            preis_eur=input_data.preis_eur,
            erstzulassung=parse_erstzulassung(input_data.erstzulassung),
            co2_g_km=input_data.co2_wltp_g_km,
            verkaeufer_typ=input_data.verkaeufer.value,
            mwst_ausweisbar=input_data.mwst_ausweisbar,
            kilometerstand=input_data.kilometerstand,
            elektro=input_data.elektro,
            neupreis_netto=input_data.neupreis_netto,
        )
        kaufdatum = (
            date.fromisoformat(input_data.kaufdatum) if input_data.kaufdatum else None
        )
        r = berechne_import(
            fz, kaufdatum=kaufdatum, nebenkosten=input_data.nebenkosten_eur
        )
        if not r.berechenbar or r.nova is None:
            raise BlockInputError(
                message="NoVA nicht berechenbar: " + " ".join(r.hinweise),
                block_name=self.name,
                block_id=self.id,
            )
        yield "nova", r.nova.betrag
        yield "endpreis_at", r.gesamt
        yield "steuersatz_pct", r.nova.steuersatz_pct
        yield "ust_at", r.ust_at
        yield "neufahrzeug", r.neufahrzeug
        yield "hinweise", r.hinweise


class AutoCheckRedFlagsBlock(Block):
    class Input(BlockSchemaInput):
        preis_eur: float | None = SchemaField(
            description="Angebotspreis in Euro", default=None
        )
        marktwert_median: float | None = SchemaField(
            description="Typischer Marktpreis in Euro; leer = kein Preisvergleich",
            default=None,
        )
        kilometerstand: int | None = SchemaField(
            description="Kilometerstand", default=None
        )
        erstzulassung: str = SchemaField(
            description="Erstzulassung als JJJJ-MM", default=""
        )
        land: str = SchemaField(
            description="Standort: DE, AT, andere", default="unbekannt"
        )
        kraftstoff: str = SchemaField(
            description="z. B. benzin, diesel, elektro", default="unbekannt"
        )
        co2_g_km: int | None = SchemaField(description="CO2 in g/km", default=None)
        unfallfrei: bool | None = SchemaField(
            description="Laut Inserat unfallfrei", default=None
        )
        vorbesitzer: int | None = SchemaField(
            description="Anzahl Vorbesitzer", default=None
        )
        scheckheft: bool | None = SchemaField(
            description="Scheckheft/Servicenachweis vorhanden", default=None
        )
        beschreibung: str = SchemaField(
            description="Beschreibungstext des Inserats", default=""
        )
        ki_hinweise: list[dict[str, Any]] = SchemaField(
            description="Hinweise eines KI-Blocks: Liste mit schwere, titel, zitat",
            default_factory=list,
        )
        heute: str = SchemaField(
            description="Stichtag JJJJ-MM-TT; leer = heute", default="", advanced=True
        )

    class Output(BlockSchemaOutput):
        flags: list[dict[str, Any]] = SchemaField(
            description="Red Flags mit code, schwere, titel, detail; kritische zuerst"
        )
        anzahl_kritisch: int = SchemaField(description="Anzahl kritischer Red Flags")
        hat_kritische: bool = SchemaField(
            description="Mindestens eine kritische Red Flag"
        )

    def __init__(self):
        super().__init__(
            id="7201a269-26ba-4405-92d9-22828d3ccacc",
            display_name="AutoCheck: Red Flags",
            description="Prüft ein Autoinserat mit festen Regeln auf Warnsignale (Betrug, Schäden, Tacho).",
            categories={BlockCategory.LOGIC},
            input_schema=AutoCheckRedFlagsBlock.Input,
            output_schema=AutoCheckRedFlagsBlock.Output,
            effect=BlockEffect.NONE,
            test_input={
                "preis_eur": 15_000.0, "marktwert_median": 25_000.0, "kilometerstand": 45_000,
                "erstzulassung": "2022-03", "land": "DE", "kraftstoff": "diesel", "co2_g_km": 130,
                "beschreibung": "Bitte Anzahlung per Western Union.", "heute": "2026-11-18",
            },  # fmt: skip
            test_output=[
                ("flags", lambda f: [x["code"] for x in f] == ["preis_viel_zu_niedrig", "text_zahlung_vor_besichtigung_verla"]),
                ("anzahl_kritisch", 2),
                ("hat_kritische", True),
            ],  # fmt: skip
        )

    async def run(self, input_data: Input, **kwargs) -> BlockOutput:
        daten = InseratDaten(
            preis_eur=input_data.preis_eur,
            kilometerstand=input_data.kilometerstand,
            erstzulassung=parse_erstzulassung(input_data.erstzulassung),
            land=input_data.land,
            kraftstoff=input_data.kraftstoff.lower(),
            co2_g_km=input_data.co2_g_km,
            unfallfrei=input_data.unfallfrei,
            vorbesitzer=input_data.vorbesitzer,
            scheckheft=input_data.scheckheft,
            beschreibung=input_data.beschreibung,
            ki_hinweise=[KiHinweis.model_validate(h) for h in input_data.ki_hinweise],
        )
        heute = date.fromisoformat(input_data.heute) if input_data.heute else None
        flags = pruefe(daten, input_data.marktwert_median, heute)
        kritisch = sum(f.schwere == "kritisch" for f in flags)
        yield "flags", [f.model_dump() for f in flags]
        yield "anzahl_kritisch", kritisch
        yield "hat_kritische", kritisch > 0
