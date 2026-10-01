import csv
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

from ptk import mapcheck

MILE_DEG_LAT = 1609.344 / (mapcheck.EARTH_RADIUS_M * math.pi / 180)


def square(lon, lat, miles_e=1.0, miles_n=1.0, clockwise=False):
    """A miles_e x miles_n rectangle with its SW corner at lon/lat."""
    dlat = MILE_DEG_LAT * miles_n
    dlon = MILE_DEG_LAT * miles_e / math.cos(math.radians(lat + dlat / 2))
    ring = [(lon, lat), (lon + dlon, lat), (lon + dlon, lat + dlat), (lon, lat + dlat), (lon, lat)]
    return list(reversed(ring)) if clockwise else ring


def geojson(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}}
        for props, ring in features]}))
    return path


class MapCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tx = geojson(self.tmp / "reeves_surveys.geojson", [
            ({"ABSTRACT_N": "123", "LEVEL2_BLO": "33", "LEVEL3_SUR": "12"}, square(-103.6, 31.40)),
            ({"ABSTRACT_N": "124", "LEVEL2_BLO": "33", "LEVEL3_SUR": "13"}, square(-103.6, 31.38)),
            ({"ABSTRACT_N": "900", "LEVEL2_BLO": "7", "LEVEL3_SUR": "5"}, square(-103.5, 31.30)),
            ({"ABSTRACT_N": "901", "LEVEL2_BLO": "7", "LEVEL3_SUR": "5"}, square(-103.4, 31.30)),
        ])
        self.nm = geojson(self.tmp / "nm_sections.geojson", [
            ({"PLSSID": "NM230230S0310E0", "FRSTDIVNO": "15"}, square(-103.75, 32.30)),
            ({"PLSSID": "NM230230S0310E0", "FRSTDIVNO": "22"}, square(-103.75, 32.28)),
        ])

    def run_rows(self, rows, layers=None):
        path = self.tmp / "tracts.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["tract", "state", "county", "legal_description", "stated_acres"])
            w.writeheader()
            w.writerows(rows)
        return {r.tract: r for r in mapcheck.run(path, layers or [self.tx, self.nm])}

    def test_area_of_a_square_mile_is_640_acres(self):
        f = mapcheck.Feature({}, [[square(-103.6, 31.4)]])
        self.assertAlmostEqual(mapcheck.feature_acres(f), 640, delta=640 * 0.005)

    def test_plssid(self):
        self.assertEqual(mapcheck.plssid("23S", "31E"), "NM230230S0310E0")
        self.assertEqual(mapcheck.plssid("9S", "4W"), "NM230090S0040W0")

    def test_texas_matches_and_acreage(self):
        out = self.run_rows([
            {"tract": "OK", "legal_description": "N/2 of Section 12, Block 33, T-2-S, T&P RR Co. Survey, A-123, Reeves County", "stated_acres": "320"},
            {"tract": "WRONG_AC", "legal_description": "Section 12, Block 33, T&P RR Co. Survey, A-123, Reeves County", "stated_acres": "480"},
            {"tract": "NO_ABST", "legal_description": "All of Sections 12 and 13, Block 33, T&P RR Co Survey, Reeves County", "stated_acres": "1280"},
            {"tract": "MISSING", "legal_description": "Section 4, Block 18, PSL, A-777, Reeves County"},
            {"tract": "AMBIG", "legal_description": "Section 5, Block 7, PSL, Reeves County"},
        ])
        self.assertEqual(out["OK"].status, "OK")
        self.assertLess(abs(out["OK"].difference_pct), 1)
        self.assertEqual(out["WRONG_AC"].status, "ACREAGE_MISMATCH")
        self.assertEqual((out["NO_ABST"].status, out["NO_ABST"].matched), ("OK", 2))
        self.assertEqual(out["MISSING"].status, "NOT_FOUND")
        self.assertEqual(out["AMBIG"].status, "AMBIGUOUS")

    def test_new_mexico_sections_and_aliquots(self):
        out = self.run_rows([
            {"tract": "NE4", "state": "NM", "legal_description": "Section 15, T23S, R31E, NMPM: NE/4, containing 160 acres"},
            {"tract": "TWO", "state": "NM", "legal_description": "Sections 15 and 22: All, T23S, R31E, NMPM", "stated_acres": "1280"},
            {"tract": "BAD", "state": "NM", "legal_description": "Section 15, T23S, R31E, NMPM: N/2", "stated_acres": "160"},
            {"tract": "NONE", "state": "NM", "legal_description": "Section 15, T24S, R31E, NMPM"},
        ])
        self.assertEqual(out["NE4"].status, "OK")
        self.assertEqual(out["TWO"].status, "OK")
        self.assertEqual(out["BAD"].status, "ACREAGE_MISMATCH")
        self.assertAlmostEqual(out["BAD"].difference_pct, 100, delta=1)
        self.assertEqual(out["NONE"].status, "NOT_FOUND")

    def test_outside_the_permian_is_flagged(self):
        far = geojson(self.tmp / "far.geojson", [({"ABSTRACT_N": "55", "LEVEL2_BLO": "1", "LEVEL3_SUR": "1"}, square(-97.0, 30.0))])
        out = self.run_rows([{"tract": "FAR", "legal_description": "Section 1, Block 1, PSL, A-55, Reeves County"}], [far])
        self.assertEqual(out["FAR"].status, "OUTSIDE_PERMIAN")

    def test_projected_layers_are_refused(self):
        bad = geojson(self.tmp / "utm.geojson", [({"ABSTRACT_N": "1"}, [(600000, 3500000), (601000, 3500000), (601000, 3501000), (600000, 3500000)])])
        with self.assertRaisesRegex(ValueError, "longitude/latitude"):
            mapcheck.read_layer(bad)

    @unittest.skipUnless(importlib.util.find_spec("shapefile"), "needs pyshp")
    def test_shapefile_with_a_hole(self):
        import shapefile
        base = self.tmp / "surv"
        with shapefile.Writer(str(base), shapeType=shapefile.POLYGON) as w:
            w.field("ABSTRACT_N", "C")
            w.field("LEVEL2_BLO", "C")
            w.field("LEVEL3_SUR", "C")
            outer = square(-103.6, 31.4, clockwise=True)
            hole = square(-103.595, 31.403, 0.25, 0.25)  # 40 acres, counterclockwise
            w.poly([outer, hole])
            w.record("123", "33", "12")
        feats = mapcheck.read_layer(base.with_suffix(".shp"))
        self.assertEqual(len(feats[0].polygons), 1)
        self.assertAlmostEqual(mapcheck.feature_acres(feats[0]), 600, delta=6)

    def test_geojson_output(self):
        out = self.run_rows([{"tract": "OK", "legal_description": "Section 12, Block 33, T&P RR Co. Survey, A-123, Reeves County"}])
        gj = mapcheck.to_geojson(list(out.values()))
        self.assertEqual(gj["features"][0]["properties"]["status"], "OK")
        self.assertEqual(gj["features"][0]["geometry"]["type"], "MultiPolygon")


if __name__ == "__main__":
    unittest.main()
