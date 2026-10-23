#!/usr/bin/env python3
"""
Heimweg-Kartendaten: macht aus OpenStreetMap-Auszügen kleine Kacheln für die App.

Eingabe:  GeoJSON-Sequence (eine Zeile pro Objekt), erzeugt mit `osmium export -f geojsonseq`
Ausgabe:  <out>/t/<i>/<j>.json  – eine Kachel je 0,02° × 0,02° (ca. 2,2 × 1,5 km)
          <out>/index.json      – welche Kacheln es gibt, Stand, Gebiete

Inhalt einer Kachel:
  {"lit":    [[id, "polyline"], ...],   # als beleuchtet eingetragen
   "likely": [[id, "polyline"], ...],   # ohne Angabe, aber im Ortsgebiet
   "places": [[id, kind, name, lat, lon, opening_hours, phone], ...]}

Nur Python-Standardbibliothek, damit es überall läuft.
"""
import json
import math
import os
import re
import sys
from collections import defaultdict

TILE = 0.02
LIT_VALUES = {"yes", "24/7", "automatic", "sunset-sunrise", "limited"}
TOWN_ROADS = {"residential", "living_street", "pedestrian", "tertiary", "secondary", "primary", "unclassified"}
TOWN_LANDUSE = {"residential", "commercial", "retail"}
FOOD = {"fast_food", "restaurant", "bar", "pub", "cafe", "nightclub"}
SHOPS = {"convenience", "supermarket", "kiosk"}
SMALL_HOTEL = re.compile(r"pension|gasthof|gasthaus|gästehaus|privatzimmer|apartment|ferienwohnung|frühstück|b&b|bed and breakfast", re.I)


def tile_of(lat, lon):
    return math.floor(lat / TILE), math.floor(lon / TILE)


# ---------- Google-Polyline (Genauigkeit 5 Stellen ≈ 1 m) ----------
def _enc(v):
    v = ~(v << 1) if v < 0 else v << 1
    out = []
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))
    return "".join(out)


def encode(points):
    """points: [(lat, lon), ...]"""
    res, plat, plon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = int(round(lat * 1e5)), int(round(lon * 1e5))
        res.append(_enc(ilat - plat))
        res.append(_enc(ilon - plon))
        plat, plon = ilat, ilon
    return "".join(res)


# ---------- Vereinfachen (Douglas-Peucker, ~4 m) ----------
def simplify(pts, tol_m=4.0):
    if len(pts) <= 2:
        return pts
    k = math.cos(math.radians(pts[0][0])) * 111320
    xy = [(p[1] * k, p[0] * 110540) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy or 1e-9
        best, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
            d = math.hypot(ax + t * dx - px, ay + t * dy - py)
            if d > best:
                best, idx = d, i
        if best > tol_m and idx > 0:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k_ in zip(pts, keep) if k_]


# ---------- Punkt im Vieleck ----------
def inside(lat, lon, ring):
    """ring: [(lat, lon), ...] (Außenring)"""
    res = False
    j = len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            res = not res
        j = i
    return res


def centroid(coords):
    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def place_kind(t):
    a = t.get("amenity")
    if a == "police":
        return "police"
    if a == "fuel":
        return "fuel"
    if a == "hospital" and t.get("emergency") != "no":
        return "hospital"
    if a == "taxi":
        return "taxi"
    if a == "pharmacy":
        return "pharmacy" if t.get("opening_hours") else None
    if t.get("tourism") in ("hotel", "hostel"):
        if t.get("opening_hours"):
            return "hotel"
        name = t.get("name", "")
        stars = re.sub(r"\D", "", t.get("stars", "")) or "0"
        rooms = re.sub(r"\D", "", t.get("rooms", "")) or "0"
        big = int(stars[:2]) >= 3 or int(rooms[:5]) >= 30 or "brand" in t or t.get("tourism") == "hostel"
        return "hotel" if big and not SMALL_HOTEL.search(name) else None
    if t.get("shop") in SHOPS:
        return "shop" if t.get("opening_hours") else None
    if t.get("railway") == "station" or t.get("public_transport") == "station":
        return "station"
    if a in FOOD:
        return "food" if t.get("opening_hours") else None
    return None


def first_ring(geom):
    if geom["type"] == "Polygon":
        return geom["coordinates"][0]
    if geom["type"] == "MultiPolygon":
        return max((poly[0] for poly in geom["coordinates"]), key=len)
    return None


def main(inputs, out_dir, regions):
    lit = []        # (id, pts)
    cand = []       # (id, pts)
    zones = []      # (s, n, w, e, ring)
    zone_grid = defaultdict(list)
    places = []     # (id, kind, name, lat, lon, oh, phone)
    seen_places = set()

    n = 0
    for path in inputs:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                n += 1
                feat = json.loads(line)
                g, t = feat.get("geometry"), feat.get("properties") or {}
                if not g:
                    continue
                oid = f'{t.get("@type", "x")[0]}{t.get("@id", n)}'
                gt = g["type"]

                if gt == "LineString" and "highway" in t:
                    pts = [(c[1], c[0]) for c in g["coordinates"]]
                    if len(pts) < 2:
                        continue
                    litv = t.get("lit")
                    if litv in LIT_VALUES:
                        lit.append((oid, pts))
                    elif litv is None and t["highway"] in TOWN_ROADS:
                        cand.append((oid, pts))
                    continue

                if gt in ("Polygon", "MultiPolygon") and t.get("landuse") in TOWN_LANDUSE:
                    ring = first_ring(g)
                    if ring and len(ring) >= 4:
                        r = [(c[1], c[0]) for c in ring]
                        lats = [p[0] for p in r]
                        lons = [p[1] for p in r]
                        z = (min(lats), max(lats), min(lons), max(lons), r)
                        zi = len(zones)
                        zones.append(z)
                        for i in range(math.floor(z[0] / 0.01), math.floor(z[1] / 0.01) + 1):
                            for j in range(math.floor(z[2] / 0.01), math.floor(z[3] / 0.01) + 1):
                                zone_grid[(i, j)].append(zi)
                    # Ein Gebäude-Umriss kann zusätzlich ein Ort sein → weiter unten prüfen

                kind = place_kind(t)
                if kind and oid not in seen_places:
                    if gt == "Point":
                        lon, lat = g["coordinates"]
                    else:
                        ring = first_ring(g) if gt in ("Polygon", "MultiPolygon") else g.get("coordinates")
                        if not ring:
                            continue
                        lat, lon = centroid(ring)
                    seen_places.add(oid)
                    places.append((oid, kind, t.get("name") or t.get("brand") or "", round(lat, 6), round(lon, 6),
                                   t.get("opening_hours", ""), t.get("phone") or t.get("contact:phone") or ""))

    print(f"gelesen: {n} Objekte · beleuchtet {len(lit)} · Kandidaten {len(cand)} · Ortsgebiete {len(zones)} · Orte {len(places)}", flush=True)

    def in_town(lat, lon):
        for zi in zone_grid.get((math.floor(lat / 0.01), math.floor(lon / 0.01)), ()):
            s, nn, w, e, ring = zones[zi]
            if s <= lat <= nn and w <= lon <= e and inside(lat, lon, ring):
                return True
        return False

    likely = []
    for oid, pts in cand:
        probe = (pts[0], pts[len(pts) // 2], pts[-1])
        if any(in_town(p[0], p[1]) for p in probe):
            likely.append((oid, pts))
    print(f"wahrscheinlich hell: {len(likely)}", flush=True)

    tiles = defaultdict(lambda: {"lit": [], "likely": [], "places": []})

    def add_line(layer, oid, pts):
        s = simplify(pts)
        enc = encode(s)
        for key in {tile_of(p[0], p[1]) for p in s}:
            tiles[key][layer].append([oid, enc])

    for oid, pts in lit:
        add_line("lit", oid, pts)
    for oid, pts in likely:
        add_line("likely", oid, pts)
    for p in places:
        tiles[tile_of(p[3], p[4])]["places"].append(list(p))

    os.makedirs(os.path.join(out_dir, "t"), exist_ok=True)
    keys = []
    total = 0
    for (i, j), data in tiles.items():
        d = os.path.join(out_dir, "t", str(i))
        os.makedirs(d, exist_ok=True)
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(d, f"{j}.json"), "w", encoding="utf-8") as f:
            f.write(body)
        total += len(body)
        keys.append(f"{i}/{j}")
    keys.sort()
    index = {
        "v": 1,
        "tile": TILE,
        "regions": regions,
        "generated": os.environ.get("BUILD_DATE", ""),
        "attribution": "© OpenStreetMap-Mitwirkende, ODbL",
        "tiles": keys,
    }
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, separators=(",", ":"))
    print(f"{len(keys)} Kacheln, zusammen {total / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    # build_tiles.py <ausgabe-ordner> <regionen,komma,getrennt> <eingabe.geojsonseq>...
    main(sys.argv[3:], sys.argv[1], sys.argv[2].split(","))
