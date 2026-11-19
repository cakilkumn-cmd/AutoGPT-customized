"""Spielt beispiel_agent.json durch: Claude-Antwort simuliert, die zwei AutoCheck-Blöcke echt."""

import json
from pathlib import Path

import pytest

from backend.blocks.autocheck.blocks import AutoCheckNovaBlock, AutoCheckRedFlagsBlock
from backend.data.graph import Graph, make_graph_model

AGENT = json.loads((Path(__file__).parent / "beispiel_agent.json").read_text("utf-8"))

# So könnte Claude ein typisches Händlerinserat auslesen.
CLAUDE_ANTWORT = {
    "preis_eur": 23800,
    "erstzulassung": "2022-03",
    "co2_wltp_g_km": 130,
    "neupreis_netto": 40000,
    "verkaeufer": "haendler",
    "land": "DE",
    "kraftstoff": "diesel",
    "beschreibung": "Scheckheftgepflegt. Bitte Anzahlung per Western Union.",
    "ki_hinweise": [{"schwere": "warnung", "titel": "Vorkasse", "zitat": "Anzahlung"}],
}


def eingaben_fuer(block_id: str) -> dict:
    """Werte, die laut den Verbindungen im Agenten aus Claudes Antwort an den Block gehen."""
    knoten = next(n for n in AGENT["nodes"] if n["block_id"] == block_id)
    return {
        link["sink_name"]: CLAUDE_ANTWORT[link["source_name"].split("_#_")[1]]
        for link in AGENT["links"]
        if link["sink_id"] == knoten["id"]
    }


async def ausfuehren(block, eingaben: dict) -> dict:
    daten = block.input_schema(**eingaben)
    return {name: wert async for name, wert in block.run(daten)}


def test_agent_ist_gueltig():
    graph = make_graph_model(Graph.model_validate(AGENT), "test-user")
    graph.validate_graph(for_run=False)
    assert set(graph.output_schema["properties"]) == {
        "nova",
        "endpreis_at",
        "hinweise",
        "red_flags",
    }


@pytest.mark.asyncio
async def test_nova_aus_claude_antwort():
    out = await ausfuehren(AutoCheckNovaBlock(), eingaben_fuer(AutoCheckNovaBlock().id))
    # Gebraucht, EZ 2022-03: BMG 20.000, 5 %, Abzugsposten 350 * 0,5 -> 1.000 - 175 = 825
    assert out["nova"] == 825
    assert out["endpreis_at"] == 23_800 + 825 + 760


@pytest.mark.asyncio
async def test_red_flags_aus_claude_antwort():
    out = await ausfuehren(
        AutoCheckRedFlagsBlock(), eingaben_fuer(AutoCheckRedFlagsBlock().id)
    )
    titel = [f["titel"] for f in out["flags"]]
    assert "Zahlung vor Besichtigung verlangt" in titel
    assert "Vorkasse" in titel
    assert out["hat_kritische"] is True
