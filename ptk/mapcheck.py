"""Map check: does each legal description land on a real survey or section, at the right size?

Matches parsed descriptions against survey layers you download once:

* Texas: RRC digital map survey polygons (free, by county) or a licensed grid
  such as Whitestar, matched by abstract number, or by block + section (+ T&P
  township) when the description has no abstract.
* New Mexico: BLM CadNSDI ``PLSSFirstDivision`` (sections), matched by township,
  range and section.

Layers must be GeoJSON or shapefiles in longitude/latitude (EPSG:4326 or NAD83
geographic). Reproject anything else first, e.g.
``ogr2ogr -t_srs EPSG:4326 out.geojson in.shp``.

Field names differ between sources and releases, so the defaults below are a
starting point. Check your layer's attribute table and pass ``--fields`` with
the right names. Areas are computed on a sphere and are good to about 1%,
which is plenty to catch a wrong section, a wrong county or a misread aliquot.
The legal description, not the map, controls title.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import legal_nm, legal_tx
from .fracs import parse_fraction

EARTH_RADIUS_M = 6371008.8
SQ_M_PER_ACRE = 4046.8564224
PERMIAN_BOUNDS = (-106.0, 29.0, -99.5, 35.0)  # lon/lat box around the TX + NM Permian

DEFAULT_FIELDS: dict[str, dict[str, str | None]] = {
    # RRC survey polygons: ABSTRACT_N (abstract), LEVEL2_BLO / LEVLE2_BLO (block), LEVEL3_SUR (section).
    "TX": {"county": None, "abstract": "ABSTRACT_N", "block": "LEVEL2_BLO", "section": "LEVEL3_SUR", "township": None},
    # CadNSDI PLSSFirstDivision: PLSSID like NM230230S0310E0, FRSTDIVNO section number.
    "NM": {"plssid": "PLSSID", "section": "FRSTDIVNO", "township": None, "range": None},
}


Ring = list[tuple[float, float]]  # (lon, lat) points


@dataclass
class Feature:
    properties: dict[str, Any]
    polygons: list[list[Ring]]  # each polygon: outer ring first, then holes
    layer: str = ""


@dataclass
class TractResult:
    tract: str
    state: str
    status: str  # OK | NOT_FOUND | AMBIGUOUS | ACREAGE_MISMATCH | OUTSIDE_PERMIAN | UNPARSED
    matched: int = 0
    layer_acres: float | None = None
    expected_acres: float | None = None
    difference_pct: float | None = None
    centroid: tuple[float, float] | None = None
    notes: list[str] = field(default_factory=list)
    features: list[Feature] = field(default_factory=list)


# ---------- geometry ----------

def ring_area_m2(ring: list[tuple[float, float]]) -> float:
    """Spherical polygon area (Chamberlain & Duquette), signed."""
    if len(ring) < 3:
        return 0.0
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(ring, ring[1:] + ring[:1]):
        total += math.radians(lon2 - lon1) * (2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
    return total * EARTH_RADIUS_M ** 2 / 2


def feature_acres(f: Feature) -> float:
    total = 0.0
    for poly in f.polygons:
        if poly:
            total += abs(ring_area_m2(poly[0])) - sum(abs(ring_area_m2(r)) for r in poly[1:])
    return total / SQ_M_PER_ACRE


def centroid(features: list[Feature]) -> tuple[float, float] | None:
    pts = [p for f in features for poly in f.polygons for p in (poly[0] if poly else [])]
    if not pts:
        return None
    return (round(sum(p[0] for p in pts) / len(pts), 6), round(sum(p[1] for p in pts) / len(pts), 6))


# ---------- reading layers ----------

def _polygons_from_geojson(geom: dict) -> list[list[Ring]]:
    if not geom:
        return []
    if geom["type"] == "Polygon":
        return [[[tuple(pt[:2]) for pt in ring] for ring in geom["coordinates"]]]
    if geom["type"] == "MultiPolygon":
        return [[[tuple(pt[:2]) for pt in ring] for ring in poly] for poly in geom["coordinates"]]
    return []


def read_layer(path: str | Path) -> list[Feature]:
    path = Path(path)
    features: list[Feature] = []
    if path.suffix.lower() in (".geojson", ".json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for feat in data.get("features", []):
            polys = _polygons_from_geojson(feat.get("geometry") or {})
            if polys:
                features.append(Feature(dict(feat.get("properties") or {}), polys, path.name))
    elif path.suffix.lower() == ".shp":
        try:
            import shapefile
        except ImportError as e:
            raise RuntimeError("shapefiles need pyshp: pip install pyshp (or convert to GeoJSON)") from e
        prj = path.with_suffix(".prj")
        if prj.exists() and "PROJCS" in prj.read_text(errors="ignore"):
            raise ValueError(f"{path.name} is projected; reproject to EPSG:4326 first (ogr2ogr -t_srs EPSG:4326)")
        reader = shapefile.Reader(str(path))
        names = [f[0] for f in reader.fields[1:]]
        for rec, shp in zip(reader.records(), reader.shapes()):
            parts = list(shp.parts) + [len(shp.points)]
            rings = [[tuple(p) for p in shp.points[a:b]] for a, b in zip(parts, parts[1:])]
            # Shapefile rings: clockwise = outer, counterclockwise = hole. Group holes under the last outer.
            polys: list[list[Ring]] = []
            for ring in rings:
                if ring_area_m2(ring) >= 0 or not polys:  # this formula is positive for clockwise rings
                    polys.append([ring])
                else:
                    polys[-1].append(ring)
            features.append(Feature(dict(zip(names, rec)), polys, path.name))
    else:
        raise ValueError(f"{path}: use a .geojson or .shp layer")
    for f in features[:50]:
        for lon, lat in f.polygons[0][0][:3] if f.polygons and f.polygons[0] else []:
            if abs(lon) > 180 or abs(lat) > 90:
                raise ValueError(f"{path.name} isn't in longitude/latitude; reproject to EPSG:4326 first")
    return features


# ---------- matching ----------

def _norm(v: Any) -> str:
    s = str(v if v is not None else "").strip().upper()
    s = re.sub(r"^(A|ABST|ABSTRACT)[-\s.]*", "", s) if s[:1] == "A" and re.match(r"^A[-\s.]*\d", s) else s
    s = s.lstrip("0") or s
    return s


def _get(props: dict, name: str | None) -> Any:
    if not name:
        return None
    if name in props:
        return props[name]
    alt = {"LEVEL2_BLO": "LEVLE2_BLO", "LEVLE2_BLO": "LEVEL2_BLO"}.get(name)  # RRC's misspelled block field
    return props.get(alt) if alt else None


def plssid(township: str, rng: str, meridian: str = "23") -> str:
    """CadNSDI township ID: '23S','31E' -> 'NM230230S0310E0' (state, meridian, twp, frac, dir, rng, frac, dir, dup)."""
    t = re.match(r"(\d+)([NS])", township)
    r = re.match(r"(\d+)([EW])", rng)
    if not (t and r):
        raise ValueError(f"can't build PLSSID from {township} {rng}")
    return f"NM{meridian}{int(t.group(1)):03d}0{t.group(2)}{int(r.group(1)):03d}0{r.group(2)}0"


def _tx_fraction(aliquots: list[str]) -> Fraction | None:
    if not aliquots:
        return None
    frac = Fraction(1)
    for a in aliquots:
        frac *= Fraction(1, 2) if a.endswith("2") else Fraction(1, 4)
    return frac


def _signature(feature: Feature, names: list[str | None]) -> tuple:
    return tuple(_norm(_get(feature.properties, n)) for n in names if n)


def check_tract(row: dict[str, str], layers: dict[str, list[Feature]], fields: dict[str, dict],
                tolerance: float = 0.05) -> TractResult:
    tract = row.get("tract") or row.get("id") or "?"
    text = row.get("legal_description") or ""
    looks_nm = legal_nm._TWP.search(text) and legal_nm._RNG.search(text)
    state = (row.get("state") or ("NM" if looks_nm else "TX")).upper()
    res = TractResult(tract, state, "NOT_FOUND")
    f = {**DEFAULT_FIELDS[state], **fields.get(state, {})}
    candidates = layers.get(state, [])
    fraction: Fraction | None = None
    stated = (row.get("stated_acres") or "").strip() or None

    if state == "TX":
        d = legal_tx.parse(text, county=row.get("county") or None)
        stated = stated or d.acres
        if d.abstract:
            hits = [x for x in candidates if _norm(_get(x.properties, f["abstract"])) == _norm(d.abstract)]
            res.notes.append(f"searched by abstract {d.abstract}")
            one_section = True
        elif d.block and d.sections:
            wanted = {_norm(x) for x in d.sections}
            hits = [x for x in candidates
                    if _norm(_get(x.properties, f["block"])) == _norm(d.block)
                    and _norm(_get(x.properties, f["section"])) in wanted]
            res.notes.append(f"searched by block {d.block}, section(s) {', '.join(d.sections)} (no abstract given)")
            one_section = False
        else:
            res.status = "UNPARSED"
            res.notes += d.issues
            return res
        if f.get("county") and d.county:
            hits = [x for x in hits if _norm(_get(x.properties, f["county"])) == _norm(d.county)]
        if f.get("township") and d.township:
            hits = [x for x in hits if _norm(_get(x.properties, f["township"])) in (_norm(d.township), _norm(d.township[:-1]))]
        fraction = _tx_fraction(d.aliquots)
        if d.save_and_except:
            res.notes.append("save-and-except carve-outs: the described area is smaller than the parsed fraction")
    else:
        d = legal_nm.parse(text)
        acres = legal_tx._ACRES.search(text)
        stated = stated or (acres.group(1).replace(",", "") if acres else None)
        if not (d.township and d.range and d.sections):
            res.status = "UNPARSED"
            res.notes += d.issues
            return res
        wanted = {str(s.section) for s in d.sections}
        one_section = False
        if f.get("township") and f.get("range"):
            hits = [x for x in candidates
                    if _norm(_get(x.properties, f["township"])) in (_norm(d.township), _norm(d.township[:-1]))
                    and _norm(_get(x.properties, f["range"])) in (_norm(d.range), _norm(d.range[:-1]))]
        else:
            pid = plssid(d.township, d.range)
            hits = [x for x in candidates if str(_get(x.properties, f["plssid"]) or "").upper() == pid]
        hits = [x for x in hits if _norm(_get(x.properties, f["section"])) in wanted]
        parts = [Fraction(1) if s.whole else s.regular_area() for s in d.sections]
        if any(not s.whole for s in d.sections):
            fraction = sum(parts, Fraction(0)) / len(parts)
        if any(s.lots for s in d.sections):
            res.notes.append("lots described: lot acreage comes from the BLM survey, so check stated acres by hand")
        res.notes.append(f"searched by T{d.township} R{d.range}, section(s) {sorted(wanted, key=int)}")

    res.features = hits
    res.matched = len(hits)
    if not hits:
        return res
    # Pieces of one survey share every identifying field. Two different surveys for the same
    # section (or, for an abstract match, more than one section) means the match is ambiguous.
    ident = [n for n in f.values() if n]
    by_section: dict[str, set[tuple]] = {}
    for h in hits:
        by_section.setdefault(_norm(_get(h.properties, f["section"])), set()).add(_signature(h, ident))
    if any(len(sigs) > 1 for sigs in by_section.values()) or (one_section and len(by_section) > 1):
        res.status = "AMBIGUOUS"
        res.notes.append("the matched features are different surveys; add a county field or check one county's layer at a time")
        return res
    res.layer_acres = round(sum(feature_acres(h) for h in hits), 2)
    res.centroid = centroid(hits)
    lon, lat = res.centroid or (0.0, 0.0)
    if not (PERMIAN_BOUNDS[0] <= lon <= PERMIAN_BOUNDS[2] and PERMIAN_BOUNDS[1] <= lat <= PERMIAN_BOUNDS[3]):
        res.status = "OUTSIDE_PERMIAN"
        return res
    described = res.layer_acres * float(fraction) if fraction is not None else res.layer_acres
    if stated:
        try:
            res.expected_acres = float(parse_fraction(stated))
        except Exception:  # noqa: BLE001 - an unreadable acreage just skips the comparison
            res.notes.append(f"couldn't read stated acres {stated!r}")
    if res.expected_acres:
        res.difference_pct = round((described - res.expected_acres) / res.expected_acres * 100, 2)
        res.status = "OK" if abs(res.difference_pct) <= tolerance * 100 else "ACREAGE_MISMATCH"
        if fraction is not None:
            res.notes.append(f"compared stated acres with mapped area × {fraction} = {described:.2f} ac")
    else:
        res.status = "OK"
        res.notes.append("found on the map; no stated acreage to compare")
    return res


def run(tracts_csv: str | Path, layer_paths: list[str | Path], fields: dict[str, dict] | None = None,
        tolerance: float = 0.05) -> list[TractResult]:
    layers: dict[str, list[Feature]] = {"TX": [], "NM": []}
    for p in layer_paths:
        feats = read_layer(p)
        sample = feats[0].properties if feats else {}
        state = "NM" if any(k in sample for k in ("PLSSID", "FRSTDIVNO", "TWNSHPNO")) else "TX"
        layers[state].extend(feats)
    with open(tracts_csv, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    return [check_tract(r, layers, fields or {}, tolerance) for r in rows]


def to_geojson(results: list[TractResult]) -> dict:
    feats = []
    for r in results:
        for f in r.features:
            coords = [[[list(pt) for pt in ring] for ring in poly] for poly in f.polygons]
            feats.append({"type": "Feature",
                          "geometry": {"type": "MultiPolygon", "coordinates": coords},
                          "properties": {"tract": r.tract, "status": r.status, "layer": f.layer,
                                         "layer_acres": r.layer_acres, "expected_acres": r.expected_acres,
                                         **{k: v for k, v in f.properties.items() if isinstance(v, (str, int, float))}}})
    return {"type": "FeatureCollection", "features": feats}
