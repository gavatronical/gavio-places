#!/usr/bin/env python3
"""Each country's places, and each US state's, as a small file a Gavio box keeps: the places of the kinds a box can
ask for (kinds.json) from OpenStreetMap, rebuilt every week. A box downloads the file for where its owner lives and
answers "is there a pharmacy open near me?" from it, asking no map server.

    python3 build.py regions                    the regions built, from Geofabrik's index, as JSON
    python3 build.py region <id> <folder>       one region: downloaded, filtered, written as <file>.sqlite.gz
    python3 build.py index <folder>             regions.json, for every region written to <folder>

Needs osmium-tool and curl, and Python's standard library. The extracts come from Geofabrik (download.geofabrik.de),
one at a time, each deleted once its places are out of it.

Each file is SQLite, gzipped: a table `places` (kind, lat, lon, tags as JSON), indexed by kind and latitude, and a
table `about` saying where it came from. What is kept of a place is only what a box reads: its kind's own tag, its
names, its address, its opening hours, its phone and its website. The data is OpenStreetMap's, under the Open
Database Licence: © OpenStreetMap contributors.
"""

from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS: dict[str, list[list[str]]] = json.loads((HERE / "kinds.json").read_text())
INDEX = "https://download.geofabrik.de/index-v1-nogeom.json"
AGENT = "gavio-places (+https://github.com/gavatronical/gavio-places)"
ATTRIBUTION = "© OpenStreetMap contributors"
LICENCE = "ODbL-1.0"

# What a box reads of a place (gavio_api/places.py): names, the address, the hours, the phone and the website, and the
# kind's own tag so that a box can see for itself that a place is of the kind it asked for.
SAID = ("name", "alt_name", "official_name", "short_name", "brand", "operator", "addr:housenumber", "addr:street",
        "addr:city", "addr:postcode", "opening_hours", "phone", "contact:phone", "website", "contact:website")  # fmt: skip
KIND_KEYS = tuple(sorted({key for pairs in KINDS.values() for key, _ in pairs}))
LONGEST = 300  # a value longer than this is not something a box would show
# A place a box can tell a person about has a name or an address (gavio_api/places.py drops the others); most car
# parks and playgrounds have neither, and they were most of the file.
NAMES = ("name", "alt_name", "official_name", "short_name", "brand", "operator")
ADDRESS = ("addr:street", "addr:city", "addr:postcode")


def expressions() -> list[str]:
    """osmium tags-filter's expressions for every kind: one per key, with its values."""
    values: dict[str, set[str]] = {}
    for pairs in KINDS.values():
        for key, value in pairs:
            values.setdefault(key, set()).add(value)
    return [f"nwr/{key}={','.join(sorted(values[key]))}" for key in sorted(values)]


def kinds_of(tags: dict) -> list[str]:
    """The kinds a place is, by its tags: usually one."""
    return [kind for kind, pairs in KINDS.items() if any(tags.get(key) == value for key, value in pairs)]


def kept(tags: dict) -> dict:
    """The tags a box reads, and no others: strings only, none too long to be anything a box would show."""
    return {key: value for key in (*KIND_KEYS, *SAID) if isinstance(value := tags.get(key), str) and 0 < len(value) <= LONGEST}


def centre(geometry: dict | None) -> tuple[float, float] | None:
    """Where a place is, as latitude and longitude: a point's own, or the middle of a way's or an area's outline (what
    an Overpass server gives as an element's centre is near enough for "how far away")."""
    if not isinstance(geometry, dict):
        return None
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    try:
        if kind == "Point":
            ring = [coordinates]
        elif kind == "LineString":
            ring = coordinates
        elif kind == "Polygon":
            ring = coordinates[0][:-1] or coordinates[0]
        elif kind == "MultiPolygon":
            ring = max((polygon[0] for polygon in coordinates), key=len)[:-1]
        else:
            return None
        lon = sum(float(p[0]) for p in ring) / len(ring)
        lat = sum(float(p[1]) for p in ring) / len(ring)
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 6), round(lon, 6)


def rows(features) -> list[tuple[str, float, float, str]]:
    """(kind, lat, lon, tags as JSON) for every feature of a kind a box asks for, one row per kind it is."""
    found = []
    for feature in features:
        tags = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(tags, dict):
            continue
        kinds = kinds_of(tags)
        said = kept(tags) if kinds else {}
        at = centre(feature.get("geometry")) if any(said.get(key, "").strip() for key in (*NAMES, *ADDRESS)) else None
        if at is None:
            continue
        said = json.dumps(said, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        found.extend((kind, at[0], at[1], said) for kind in kinds)
    return found


def write(found: list[tuple[str, float, float, str]], path: Path, about: dict) -> dict:
    """The region's file: SQLite, gzipped, written beside itself and moved into place. Returns what regions.json says
    of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as work:
        db = Path(work) / "places.sqlite"
        con = sqlite3.connect(db)
        try:
            with con:
                con.execute("CREATE TABLE about (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                con.execute("CREATE TABLE places (kind TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, tags TEXT NOT NULL)")
                con.executemany("INSERT INTO about VALUES (?, ?)", [(key, str(value)) for key, value in sorted(about.items())])
                con.executemany("INSERT INTO places VALUES (?, ?, ?, ?)", sorted(found))
                con.execute("CREATE INDEX near ON places (kind, lat)")
            con.execute("VACUUM")
        finally:
            con.close()
        packed = Path(work) / path.name
        with db.open("rb") as raw, gzip.GzipFile(packed, "wb", compresslevel=9, mtime=0) as out:
            shutil.copyfileobj(raw, out)
        data = packed.read_bytes()
        os.replace(packed, path)
    return {"file": path.name, "places": len(found), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def file_name(region_id: str) -> str:
    """us/california is us-california.sqlite.gz."""
    return region_id.replace("/", "-") + ".sqlite.gz"


def regions(index: dict) -> list[dict]:
    """The regions built: every country Geofabrik has a file for, and each US state (the United States whole is too
    large for one box to download every week, and a box knows its state). Some files hold more than one country."""
    chosen = []
    for feature in index.get("features", []):
        props = feature.get("properties", {})
        countries = props.get("iso3166-1:alpha2") or []
        states = [code for code in props.get("iso3166-2") or [] if code.startswith("US-")]
        if (countries and props.get("id") != "us") or states:
            chosen.append({"id": props["id"], "name": props.get("name", props["id"]), "countries": sorted(countries),
                           "subdivisions": sorted(states), "pbf": props["urls"]["pbf"]})  # fmt: skip
    return sorted(chosen, key=lambda region: region["id"])


def fetch(url: str, to: Path | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=120) as answer:
        if to is None:
            return answer.read()
        with to.open("wb") as out:
            shutil.copyfileobj(answer, out, length=1 << 20)
        return b""


def region(region_id: str, folder: Path) -> dict:
    """One region, from its Geofabrik extract to its file, with a sidecar of what regions.json says of it."""
    chosen = next((r for r in regions(json.loads(fetch(INDEX))) if r["id"] == region_id), None)
    if chosen is None:
        raise SystemExit(f"no such region: {region_id}")
    with tempfile.TemporaryDirectory() as work:
        extract, kept_pbf, features = Path(work) / "in.osm.pbf", Path(work) / "kept.osm.pbf", Path(work) / "places.geojsonseq"
        fetch(chosen["pbf"], extract)
        header = subprocess.run(["osmium", "fileinfo", "-g", "header.option.osmosis_replication_timestamp", str(extract)],
                                capture_output=True, text=True).stdout.strip()  # fmt: skip
        subprocess.run(["osmium", "tags-filter", "--overwrite", "-o", str(kept_pbf), str(extract), *expressions()], check=True)
        extract.unlink()
        subprocess.run(["osmium", "export", "--overwrite", "-f", "geojsonseq", "-x", "print_record_separator=false",
                        "-o", str(features), str(kept_pbf)], check=True, stderr=subprocess.DEVNULL)  # fmt: skip
        with features.open() as lines:
            found = rows(json.loads(line) for line in lines if line.strip())
    about = {"region": chosen["id"], "name": chosen["name"], "data_as_of": header or "unknown", "source": chosen["pbf"],
             "licence": LICENCE, "attribution": ATTRIBUTION, "kinds": ",".join(sorted(KINDS))}  # fmt: skip
    said = chosen | write(found, folder / file_name(chosen["id"]), about) | {"data_as_of": about["data_as_of"]}
    said.pop("pbf")
    (folder / (file_name(chosen["id"]) + ".json")).write_text(json.dumps(said, sort_keys=True) + "\n")
    return said


def index(folder: Path, built: str | None = None) -> dict:
    """regions.json: every region written to the folder, what each file holds and its checksum, and the licence."""
    built = built or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    listed = [json.loads(p.read_text()) for p in sorted(folder.glob("*.sqlite.gz.json"))]
    said = {"built": built, "licence": LICENCE, "attribution": ATTRIBUTION, "kinds": sorted(KINDS), "regions": listed}
    (folder / "regions.json").write_text(json.dumps(said, indent=1, sort_keys=True) + "\n")
    return said


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["regions"]:
            print(json.dumps(regions(json.loads(fetch(INDEX))), indent=1))
        case ["region", region_id, folder]:
            print(json.dumps(region(region_id, Path(folder))))
        case ["index", folder]:
            said = index(Path(folder))
            print(f"{len(said['regions'])} regions, {sum(r['places'] for r in said['regions'])} places")
        case _:
            raise SystemExit(__doc__)
