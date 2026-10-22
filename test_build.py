"""build.py without the network or osmium: what it keeps of a place, where it puts it, and the file a box reads.

    python3 -m unittest -v test_build
"""

import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import build


def feature(geometry: dict, **tags) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": tags}


def point(lon: float, lat: float) -> dict:
    return {"type": "Point", "coordinates": [lon, lat]}


class Kinds(unittest.TestCase):
    def test_every_kind_is_in_the_filter_and_nothing_else_is(self):
        said = " ".join(build.expressions())
        for pairs in build.KINDS.values():
            for key, value in pairs:
                self.assertRegex(said, rf"nwr/{key}=([^ ]*,)?{value}(,| |$)")
        self.assertEqual(len(build.expressions()), len(build.KIND_KEYS))

    def test_the_kinds_are_the_twenty_three_a_box_asks_for(self):
        self.assertEqual(len(build.KINDS), 23)
        self.assertEqual(build.kinds_of({"amenity": "cafe", "name": "Mo's"}), ["cafe"])
        self.assertEqual(build.kinds_of({"shop": "doityourself"}), ["hardware"])
        self.assertEqual(build.kinds_of({"amenity": "doctors", "healthcare": "doctor"}), ["doctor"])
        self.assertEqual(build.kinds_of({"amenity": "bench"}), [])


class Kept(unittest.TestCase):
    def test_only_what_a_box_reads_is_kept(self):
        tags = {"amenity": "cafe", "name": "Mo's", "opening_hours": "Mo-Fr 08:00-17:00", "phone": "+1 555 0100",
                "addr:street": "Main St", "fixme": "check", "wikidata": "Q1", "note": "x", "check_date": "2026-01-01"}
        self.assertEqual(set(build.kept(tags)), {"amenity", "name", "opening_hours", "phone", "addr:street"})

    def test_a_value_that_is_not_text_or_is_too_long_is_not_kept(self):
        self.assertEqual(build.kept({"name": 5, "brand": "", "operator": "x" * 301, "amenity": "atm"}), {"amenity": "atm"})


class Centre(unittest.TestCase):
    def test_a_point_is_where_it_is(self):
        self.assertEqual(build.centre(point(-0.1246, 51.5007)), (51.5007, -0.1246))

    def test_an_area_is_the_middle_of_its_outline_counting_the_closing_corner_once(self):
        square = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
        self.assertEqual(build.centre(square), (1.0, 1.0))
        two = {"type": "MultiPolygon", "coordinates": [[[[10, 10], [11, 10], [10, 10]]], [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]]}
        self.assertEqual(build.centre(two), (2.0, 2.0))  # the larger outline

    def test_nowhere_in_particular_is_nowhere(self):
        for geometry in (None, {}, {"type": "Point"}, {"type": "Point", "coordinates": ["a", "b"]},
                         {"type": "Point", "coordinates": [0, 95]}, {"type": "GeometryCollection", "coordinates": []}):
            self.assertIsNone(build.centre(geometry), geometry)


class Rows(unittest.TestCase):
    def test_a_place_of_two_kinds_is_a_row_for_each_and_others_are_none(self):
        found = build.rows([feature(point(1, 2), amenity="cafe", name="A"), feature(point(3, 4), amenity="bench"),
                            feature(point(5, 6), shop="bakery", amenity="cafe", name="B"), {"no": "tags"},
                            feature(None, amenity="atm")])  # fmt: skip
        self.assertEqual([(kind, lat, lon) for kind, lat, lon, _ in found], [("cafe", 2.0, 1.0), ("cafe", 6.0, 5.0), ("bakery", 6.0, 5.0)])
        self.assertEqual(json.loads(found[0][3]), {"amenity": "cafe", "name": "A"})

    def test_a_place_with_neither_a_name_nor_an_address_is_left_out(self):
        found = build.rows([feature(point(1, 2), amenity="parking"), feature(point(1, 2), amenity="parking", name=" "),
                            feature(point(1, 2), amenity="parking", **{"addr:street": "Main St"}),
                            feature(point(1, 2), leisure="playground", operator="Town council")])  # fmt: skip
        self.assertEqual([kind for kind, *_ in found], ["parking", "playground"])


class File(unittest.TestCase):
    def test_the_file_is_sqlite_gzipped_with_its_index_and_what_it_is(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "europe-monaco.sqlite.gz"
            found = build.rows([feature(point(7.42, 43.73), amenity="pharmacy", name="Pharmacie"),
                                feature(point(7.43, 43.74), amenity="cafe", name="Café")])  # fmt: skip
            said = build.write(found, path, {"region": "europe/monaco", "licence": build.LICENCE, "attribution": build.ATTRIBUTION})
            self.assertEqual((said["file"], said["places"]), ("europe-monaco.sqlite.gz", 2))
            self.assertEqual(said["bytes"], path.stat().st_size)
            raw = Path(work) / "out.sqlite"
            raw.write_bytes(gzip.decompress(path.read_bytes()))
            con = sqlite3.connect(raw)
            near = con.execute("SELECT tags FROM places WHERE kind = ? AND lat BETWEEN ? AND ?", ("pharmacy", 43.7, 43.8)).fetchall()
            self.assertEqual([json.loads(t)["name"] for (t,) in near], ["Pharmacie"])
            plan = " ".join(str(row) for row in con.execute("EXPLAIN QUERY PLAN SELECT * FROM places WHERE kind = 'cafe' AND lat BETWEEN 1 AND 2"))
            self.assertIn("near", plan)  # the index is used
            about = dict(con.execute("SELECT key, value FROM about"))
            self.assertEqual(about["attribution"], "© OpenStreetMap contributors")
            con.close()

    def test_the_same_places_make_the_same_file(self):
        with tempfile.TemporaryDirectory() as work:
            found = build.rows([feature(point(1, 2), amenity="cafe", name="A")])
            one = build.write(found, Path(work) / "a" / "x.sqlite.gz", {"region": "x"})
            two = build.write(list(reversed(found)), Path(work) / "b" / "x.sqlite.gz", {"region": "x"})
            self.assertEqual(one["sha256"], two["sha256"])


class Regions(unittest.TestCase):
    INDEX = {"features": [
        {"properties": {"id": "us", "name": "United States", "iso3166-1:alpha2": ["US"], "urls": {"pbf": "u"}}},
        {"properties": {"id": "us/california", "name": "California", "iso3166-2": ["US-CA"], "urls": {"pbf": "c"}}},
        {"properties": {"id": "germany", "name": "Germany", "iso3166-1:alpha2": ["DE"], "urls": {"pbf": "d"}}},
        {"properties": {"id": "germany/bayern", "name": "Bayern", "iso3166-2": ["DE-BY"], "urls": {"pbf": "b"}}},
        {"properties": {"id": "europe", "name": "Europe", "urls": {"pbf": "e"}}},
        {"properties": {"id": "malaysia-singapore-brunei", "name": "Malaysia, Singapore, and Brunei",
                        "iso3166-1:alpha2": ["MY", "SG", "BN"], "urls": {"pbf": "m"}}},
    ]}  # fmt: skip

    def test_each_country_and_each_us_state_and_not_the_united_states_whole(self):
        chosen = build.regions(self.INDEX)
        self.assertEqual([r["id"] for r in chosen], ["germany", "malaysia-singapore-brunei", "us/california"])
        self.assertEqual(chosen[1]["countries"], ["BN", "MY", "SG"])
        self.assertEqual(chosen[2]["subdivisions"], ["US-CA"])

    def test_a_region_s_file_is_named_after_it(self):
        self.assertEqual(build.file_name("us/new-york"), "us-new-york.sqlite.gz")

    def test_the_index_lists_every_region_written_with_the_licence(self):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            said = {"id": "germany", "file": "germany.sqlite.gz", "places": 3, "bytes": 10, "sha256": "ab", "countries": ["DE"]}
            (folder / "germany.sqlite.gz.json").write_text(json.dumps(said))
            index = build.index(folder, built="2026-10-25")
            self.assertEqual(json.loads((folder / "regions.json").read_text()), index)
            self.assertEqual(index["regions"], [said])
            self.assertEqual((index["licence"], index["built"]), ("ODbL-1.0", "2026-10-25"))
            self.assertEqual(len(index["kinds"]), 23)


if __name__ == "__main__":
    unittest.main()


class Corrections(unittest.TestCase):
    """Geofabrik's index gives some files no country code, the wrong one, or leaves a country out: checked against the
    countries of GeoNames' town list on 2026-10-22, 56 countries had no file, and VU was on six files."""

    INDEX = {"features": [
        {"properties": {"id": "vanuatu", "name": "Vanuatu", "iso3166-1:alpha2": ["VU"], "urls": {"pbf": "v"}}},
        {"properties": {"id": "american-oceania", "name": "American Oceania", "iso3166-1:alpha2": ["VU"], "urls": {"pbf": "a"}}},
        {"properties": {"id": "ile-de-clipperton", "name": "Île de Clipperton", "iso3166-1:alpha2": ["VU"], "urls": {"pbf": "c"}}},
        {"properties": {"id": "gcc-states", "name": "GCC States", "iso3166-1:alpha2": ["QA", "AE", "OM", "BH", "KW"], "urls": {"pbf": "g"}}},
        {"properties": {"id": "hong-kong", "name": "Hong Kong", "urls": {"pbf": "h"}}},
        {"properties": {"id": "us/new-york", "name": "us/new-york", "iso3166-2": ["US-NY"], "urls": {"pbf": "n"}}},
        {"properties": {"id": "us/district-of-columbia", "iso3166-2": ["US-DC"], "urls": {"pbf": "d"}}},
    ]}  # fmt: skip

    def test_a_country_is_on_its_own_file_and_not_on_others_geofabrik_gave_it_to(self):
        chosen = {r["id"]: r for r in build.regions(self.INDEX)}
        self.assertEqual(chosen["vanuatu"]["countries"], ["VU"])
        self.assertEqual(chosen["american-oceania"]["countries"], ["AS", "GU", "MP", "UM"])
        self.assertNotIn("ile-de-clipperton", chosen)  # nobody lives there
        self.assertEqual([i for i, r in chosen.items() if "VU" in r["countries"]], ["vanuatu"])

    def test_a_country_geofabrik_left_out_is_on_the_file_that_holds_it(self):
        chosen = {r["id"]: r for r in build.regions(self.INDEX)}
        self.assertIn("SA", chosen["gcc-states"]["countries"])
        self.assertEqual(chosen["hong-kong"]["countries"], ["HK"])

    def test_a_us_state_is_called_by_its_name(self):
        chosen = {r["id"]: r for r in build.regions(self.INDEX)}
        self.assertEqual(chosen["us/new-york"]["name"], "New York")
        self.assertEqual(chosen["us/district-of-columbia"]["name"], "District of Columbia")

    def test_every_correction_names_real_country_codes(self):
        for region_id, codes in build.COUNTRIES.items():
            self.assertTrue(all(len(c) == 2 and c.isupper() for c in codes), region_id)
        self.assertEqual(len({c for codes in build.COUNTRIES.values() for c in codes}), sum(len(c) for c in build.COUNTRIES.values()),
                         "a country named for two corrected files")  # fmt: skip
