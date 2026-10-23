# heimweg-karten

Kartendaten für die App **Heimweg**: sichere Orte (Polizei, Kliniken, offene Lokale, Tankstellen, …) und beleuchtete Straßen.

- Wird **jeden Montag** automatisch aus [OpenStreetMap](https://www.openstreetmap.org) neu gebaut (GitHub Actions → `Kartendaten bauen`).
- Ergebnis liegt auf GitHub Pages: `index.json` + Kacheln unter `t/<i>/<j>.json` (je 0,02° × 0,02°).
- Gebiete: Österreich, Schweiz, Liechtenstein. Deutschland folgt.

## Einmalig einrichten
1. **Settings → Pages → Build and deployment → Source: „GitHub Actions“**
2. **Actions → Kartendaten bauen → Run workflow**

## Lizenz
Daten: © OpenStreetMap-Mitwirkende, [ODbL](https://opendatacommons.org/licenses/odbl/). Die aufbereiteten Daten stehen ebenfalls unter der ODbL.
