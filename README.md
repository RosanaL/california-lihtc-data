# California LIHTC Grocery Store Eligibility Dataset

A store-level database for checking whether a grocery store can count toward
the CTCAC site-amenity points (4 CCR §10325(c)(4)(A)(4)). Pilot area is Orange County.

Knowing where a supermarket is takes one API call. Knowing whether it has
**25,000 gross interior sqft** (or 5,000 for a neighborhood market) and sells
**staples, fresh meat and fresh produce** doesn't come from any single source.
This project builds that answer with field-level provenance.

## Rules implemented (CTCAC Regulations, Dec 10 2025, p.38–39)

| Class | Min gross interior sqft | Standard tiers | Rural tiers |
|---|---|---|---|
| Full-scale grocery/supermarket | 25,000 | ½ mi 5 pts · 1 mi 4 · 1.5 mi 3 | 1 mi 5 · 2 mi 4 · 3 mi 3 |
| Neighborhood market | 5,000 | ¼ mi 4 pts · ½ mi 3 | ½ mi 4 · 1 mi 3 |

- A large multipurpose store (Target, Walmart, Costco) counts only if the
  **grocery section's** interior measurements are documented. Total store
  area doesn't count.
- In a shopping centre the distance is measured **to the store's exterior
  wall**, minus 250 ft. The store's outline is part of the data.
- Rules live in [configs/eligibility_rules.yaml](configs/eligibility_rules.yaml).

## Status (Day 1, 2026-11-25)

| Step | Result |
|---|---|
| OSM acquisition | **387** OC candidates (319 supermarket, 44 department store, 22 wholesale, 2 grocery). 245 have store outlines. |
| Footprint join | OC 2021 footprints (781k polygons, ArcGIS REST): 385/387 stores matched to a polygon or an OSM outline. |
| Food inspection portal | JSON API found via network inspection. Scripted access returns **403**. See below. |
| Classifier | Implemented and tested (16 tests). Every store is currently `unverified`/`ineligible`: no food-category evidence is loaded yet, by design. |
| PostGIS | Loaded into `lihtc` (PostgreSQL 18 + PostGIS 3.7, port 5433): 387 stores, 435 buildings, 385 area-evidence rows, 387 eligibility results, each traceable to a raw record and snapshot hash. PostGIS `ST_Area` agrees with the Python areas to within 0.016%. |

### Finding 3: the county layer's `OBJECTID` isn't unique

23,544 footprints have `OBJECTID = 0`. The service's real key is `FID`
(system-maintained). Keying on OBJECTID merged 6 different buildings near our
stores into one and pointed evidence at the wrong building. Everything now keys
on FID, and a SQL check confirms 0 evidence rows linked to a building that
doesn't contain the store.

### Finding 1: building footprints are a poor area proxy in shopping centres

Footprint area treats the whole roof as the store. Trader Joe's stores are
typically ~10–15k sqft, yet among 19 Trader Joe's matched by point-in-polygon,
**12 footprints reach 25,000 sqft** (range 8.6k–91k). A naive spatial join
would label most of them full-scale supermarkets.

Mitigations built in:
- When OSM mapped the store itself and the county polygon is >1.6× larger,
  the OSM outline is used (106 stores).
- Graded address agreement: 133 stores share one site address with their
  building (`shared_site_address`, a typical multi-tenant centre), and 61 are on a different street.
- Footprint evidence is `proxy` strength. The classifier can return at most
  `likely`, never `eligible`.

Only **102 of 317** supermarkets got a clean, flag-free single-building match.

### Finding 2: health permits show which food departments a store has

The OC Health Care Agency portal (inspections.myhealthdepartment.com) isn't
bulk-exportable. Its search page calls a JSON endpoint (`POST /`, task
`searchInspections`). Each supermarket holds a main permit (e.g. `MARKET W/ 1 PREP`)
plus `MARKET - ADDITIONAL PREP AREA` sub-permits named **Meat, Seafood, Deli,
Bakery**. A Meat sub-permit is regulator-issued evidence of a fresh-meat
department, the only non-self-reported food-category signal found so far.

The endpoint answers a browser session but returns HTTP 403 to scripted
requests (load-balancer bot protection). It hasn't been circumvented. Planned path:
a California Public Records Act request to OC HCA for active retail food
permits, plus manual lookups for the benchmark set.

## Layout

```
configs/      eligibility_rules.yaml, sources.yaml (tested status of each source)
src/lihtc/
  acquisition/   base.py (raw snapshots + manifest), osm.py, arcgis.py
  normalization/ address.py (street/unit/name normalization, address agreement)
  spatial/       footprints.py (footprint selection, sqft, multi-tenant flags)
  validation/    eligibility.py (store class + eligible/likely/ineligible/unverified)
  pipeline.py    raw -> data/processed/oc_grocery_candidates.csv
  load.py        raw + results -> PostGIS (sources, raw_records, stores, buildings, evidence, results)
database/schema.sql   PostGIS schema incl. attribute_evidence provenance table
tests/
```

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install pypdf requests pyyaml rapidfuzz shapely pyproj pytest "psycopg[binary]"
PYTHONPATH=src .venv/bin/python -m lihtc.pipeline --fetch   # ~2.5 min, re-downloads sources
.venv/bin/python -m pytest -q
```

Database (Homebrew's PostGIS bottle targets PostgreSQL 18/19, so it runs as a
separate PG 18 server on port 5433):

```bash
brew install postgis postgresql@18      # set port = 5433 in /opt/homebrew/var/postgresql@18/postgresql.conf
/opt/homebrew/opt/postgresql@18/bin/pg_ctl -D /opt/homebrew/var/postgresql@18 -l /opt/homebrew/var/postgresql@18/server.log start
/opt/homebrew/opt/postgresql@18/bin/createdb -p 5433 lihtc
/opt/homebrew/opt/postgresql@18/bin/psql -p 5433 -d lihtc -f database/schema.sql
PYTHONPATH=src .venv/bin/python -m lihtc.load               # idempotent; DSN via $LIHTC_DSN
```

## Next

1. Benchmark: hand-verify 20–30 OC stores (area from leasing brochures or
   permits, food departments from the inspection portal).
2. Entity resolution: OSM ↔ health permits (store number, address, RapidFuzz name).
3. Measure area-proxy error against the benchmark and finish the failure analysis.

Data: © OpenStreetMap contributors (ODbL); County of Orange GIS.
