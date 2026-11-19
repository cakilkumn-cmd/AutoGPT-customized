# AutoCheck-Blöcke

Zwei eigene Blöcke für die AutoGPT Platform. Der Rechenkern stammt aus `cakilkumn-cmd/CarSearch-Professionals`.

| Block | Eingaben | Ausgaben |
|---|---|---|
| **AutoCheck: NoVA und Endpreis AT** | Preis, Erstzulassung (JJJJ-MM), CO2 WLTP, Neupreis netto, Händler/privat, Nebenkosten (unter „Erweitert“: MwSt ausweisbar, Kilometerstand, Elektro, Kaufdatum) | NoVA, Endpreis AT, Steuersatz, USt AT, Neufahrzeug, Hinweise |
| **AutoCheck: Red Flags** | Preis, Marktwert, Kilometerstand, Erstzulassung, Land, Kraftstoff, CO2, unfallfrei, Vorbesitzer, Scheckheft, Beschreibung, KI-Hinweise | Flags, Anzahl kritisch, hat kritische |

Beide Blöcke rechnen ohne KI und ohne Internet.

## Anteilige Kürzung bei Gebrauchtimporten

- Ist der Neupreis netto angegeben, werden Abzugsposten und Malus mit dem Faktor *NoVA-Basis ÷ Neupreis netto* (höchstens 1) gekürzt.
- Ohne Neupreis gibt es keine Kürzung, dafür einen Hinweis.
- Neufahrzeuge (≤ 6 Monate oder ≤ 6.000 km) werden nicht gekürzt.
- Die Kürzungsregel und die NoVA-Werte 2020–2027 sind **nicht amtlich geprüft**. Vor einem Kauf mit dem BMF-NoVA-Rechner gegenprüfen.

## Dateien

- `nova.py`, `nova_params.py`: NoVA- und Importrechner
- `red_flags.py`: Regeln
- `blocks.py`: die zwei Blöcke
- `*_test.py`: Tests
- `beispiel_agent.json`: Beispiel-Agent zum Importieren (Inseratstext → Claude → beide Blöcke → Ausgabe)

## Tests ausführen

Im Ordner `autogpt_platform/backend`:

```
poetry install
poetry run prisma generate
poetry run pytest backend/blocks/autocheck -v
poetry run pytest backend/blocks/test/test_block.py -k AutoCheck
```

## Beispiel-Agent: Grenzen

- Liefert Claude für einen verbundenen Wert `null`, z. B. weil im Inserat kein CO2-Wert steht, wartet AutoGPT auf diesen Wert. Der betroffene Block läuft dann nicht. Der Agent zeigt in diesem Fall keine NoVA, die Red Flags aber schon.
- Deshalb verbindet der Agent nur Felder, die praktisch jedes Inserat enthält. Kilometerstand, Unfallfreiheit und Vorbesitzer verbindet er nicht.
