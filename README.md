# Gavio's places

Every country's places, and every US state's, as one small file each: cafés, pharmacies, supermarkets, post offices
and the other kinds of place a [Gavio](https://gavio.me) box can be asked about ([kinds.json](kinds.json)), from
[OpenStreetMap](https://www.openstreetmap.org/), rebuilt every week.

A Gavio box downloads the file for where its owner lives, once a week, and answers "is there a pharmacy open near me?"
from it, without asking a map server anything.

## The files

Each week's files are a [release](https://github.com/gavatronical/gavio-places/releases/latest). `regions.json` lists
every region: its Geofabrik name, the countries (ISO 3166-1) or US state (ISO 3166-2) it covers, its file, the
number of places, its size, its SHA-256, the date of the map data, and which files beside it gave it places (below).
The four newest weeks are kept.

A town near a border has shops on the other side as near as its own, so each file also holds the places of the files
beside it that are within about 4 km of its own places: North Dakota's has Moorhead's, over the river in Minnesota,
for Fargo. A place is in a file once.

A region's file (`germany.sqlite.gz`, `us-california.sqlite.gz`) is an SQLite database, gzipped:

- `places (kind, lat, lon, tags)`, indexed by kind and latitude. `tags` is JSON, holding only what a box shows: the
  kind's own tag, names, the address, opening hours, phone and website. A place with neither a name nor an address
  is left out, since nobody could be told which one it is. An area's position is the middle of its outline.
- `about (key, value)`: the region, the date of its data, its source, the licence and the attribution.

## How it is built

[`build.py`](build.py), run by [the weekly workflow](.github/workflows/weekly.yml) on Mondays: for each region, the
extract from [Geofabrik](https://download.geofabrik.de/) is downloaded, filtered to the kinds in `kinds.json` with
[osmium](https://osmcode.org/osmium-tool/), and written as above. The extracts are fetched one at a time per batch,
six batches at once at most, and deleted as soon as their places are out of them. Once every region is built, each
file is given the places beside it over its borders, from the files as built.

    python3 -m unittest -v test_build             the tests (no network)
    python3 build.py region liechtenstein out     one region (needs osmium-tool and curl)
    python3 build.py beside out                   each file in out/ given the places beside it
    python3 build.py index out                    regions.json for what is in out/

## What a box's download says about it

A box asks GitHub for `regions.json` and for one region's file. GitHub sees the box's internet address and which
country or US state it downloads, as with any download. Nothing else is sent, and nothing a person asks their
assistant ever leaves the box for this.

## Licence

The data is © OpenStreetMap contributors and is available under the
[Open Database Licence (ODbL 1.0)](https://opendatacommons.org/licenses/odbl/1-0/). These files are a database
derived from it and are under the same licence; this repository is how they are made. The code here is under the
GNU Affero General Public License v3.0 ([LICENSE](LICENSE)).
