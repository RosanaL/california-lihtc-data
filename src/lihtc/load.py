"""Load the latest raw snapshots and derived results into PostGIS.

Idempotent: each run replaces the derived tables (stores, buildings, evidence,
results) and upserts raw_records keyed on snapshot hash, so raw history
accumulates while derived data always reflects the current code.

Usage:  PYTHONPATH=src python -m lihtc.load
DSN:    $LIHTC_DSN, default "dbname=lihtc port=5433"
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import yaml
from psycopg.types.json import Jsonb
from shapely.geometry import Point, Polygon

from .acquisition.base import RAW_ROOT, last_good
from .normalization.address import normalize_address, normalize_name, store_number
from .pipeline import area_evidence_for, evaluate, load_inputs
from .validation.eligibility import _strength, load_rules

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("LIHTC_DSN", "dbname=lihtc port=5433")
FP_SOURCE = "oc_building_footprints_2021"


def store_id(osm_id: str) -> str:
    kind, num = osm_id.split("/")
    return f"CA_OC_{kind[0]}{num}"  # stable across re-runs: CA_OC_w30265679


def latest_manifest(source_id: str) -> dict:
    """Manifest entry of the LAST_GOOD snapshot (the one the pipeline read)."""
    good = last_good(source_id).name
    for line in reversed((RAW_ROOT / source_id / "manifest.jsonl").read_text().splitlines()):
        entry = json.loads(line)
        if Path(entry["path"]).name == good:
            return entry
    raise RuntimeError(f"no manifest entry for {source_id}/{good}")


def load(dsn: str = DSN) -> dict:
    rules = load_rules()
    sources = yaml.safe_load((ROOT / "configs" / "sources.yaml").read_text())["sources"]
    stores, footprints = load_inputs()
    osm_man, fp_man = latest_manifest("osm_overpass"), latest_manifest(FP_SOURCE)
    osm_raw = {f"{e['type']}/{e['id']}": e
               for e in json.loads(Path(osm_man["path"]).read_text())["elements"]}

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for sid, meta in sources.items():
            if not meta.get("url"):
                continue
            cur.execute("""INSERT INTO data_sources (source_id, owner, url, access_method, license, notes)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (source_id) DO UPDATE SET url=EXCLUDED.url,
                             access_method=EXCLUDED.access_method, notes=EXCLUDED.notes""",
                        (sid, meta.get("owner"), meta["url"], meta.get("access"),
                         meta.get("license"), meta.get("status")))

        cur.execute("TRUNCATE eligibility_results, attribute_evidence, store_source_links, stores, buildings")

        def raw_row(source_id, rec_id, man, payload) -> int:
            cur.execute("""INSERT INTO raw_records (source_id, source_record_id, snapshot_sha256, retrieved_at, payload)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (source_id, source_record_id, snapshot_sha256)
                           DO UPDATE SET payload = EXCLUDED.payload
                           RETURNING raw_id""",
                        (source_id, rec_id, man["sha256"], man["retrieved_at"], Jsonb(payload)))
            return cur.fetchone()[0]

        # buildings: every footprint returned around any store, deduplicated by FID
        fp_raw_ids: dict[str, int] = {}
        for feats in footprints.values():
            for f in feats:
                oid = str(f["attributes"]["FID"])  # FID is the service key; OBJECTID is 0 for ~23.5k rows
                bid = f"{FP_SOURCE}:{oid}"
                if bid in fp_raw_ids or not f.get("geometry", {}).get("rings"):
                    continue
                fp_raw_ids[bid] = raw_row(FP_SOURCE, oid, fp_man, f)
                poly = Polygon(f["geometry"]["rings"][0])
                cur.execute("""INSERT INTO buildings (building_id, source_id, site_address, footprint_sqft, geometry)
                               VALUES (%s,%s,%s,%s, ST_MakeValid(ST_GeomFromText(%s, 4326)))""",
                            (bid, FP_SOURCE, f["attributes"].get("Address"),
                             f["attributes"].get("Shape__Area"), poly.wkt))

        counts = {"stores": 0, "evidence": 0}
        for s, m, multipurpose, c in evaluate(stores, footprints, rules):
            sid = store_id(s["source_record_id"])
            outline = Polygon(s["osm_outline"]).wkt if s.get("osm_outline") and len(s["osm_outline"]) >= 4 else None
            cur.execute("""INSERT INTO stores (store_id, canonical_name, brand, store_number, address_norm, city,
                                               is_multipurpose, location, outline)
                           VALUES (%s,%s,%s,%s,%s,%s,%s, ST_GeomFromText(%s,4326),
                                   ST_MakeValid(ST_GeomFromText(%s,4326)))""",
                        (sid, s["name"] or "(unnamed)", s["brand"], store_number(s["name"]),
                         normalize_address(s["housenumber"], s["street"]) or None, s["city"],
                         multipurpose, Point(s["lon"], s["lat"]).wkt, outline))
            osm_raw_id = raw_row("osm_overpass", s["source_record_id"], osm_man,
                                 osm_raw[s["source_record_id"]])
            cur.execute("INSERT INTO store_source_links VALUES (%s,%s,'source_id',1.0)", (sid, osm_raw_id))

            evidence_ids = []
            for a in area_evidence_for(m):
                from_county = m.building_id is not None and m.method != "osm_outline"
                cur.execute("""INSERT INTO attribute_evidence (store_id, attribute, value, source_id, raw_id,
                                   source_type, measurement_type, strength, retrieved_at, note)
                               VALUES (%s,'floor_area_sqft',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING evidence_id""",
                            (sid, Jsonb(round(a.sqft)),
                             FP_SOURCE if from_county else "osm_overpass",
                             fp_raw_ids.get(f"{FP_SOURCE}:{m.building_id}") if from_county else osm_raw_id,
                             a.source_type, a.measurement_type, _strength(a.source_type, rules),
                             fp_man["retrieved_at"] if from_county else osm_man["retrieved_at"],
                             f"method={m.method}; flags={','.join(m.flags) or 'none'}; "
                             f"address_agreement={m.address_agrees}"))
                evidence_ids.append(cur.fetchone()[0])
            counts["evidence"] += len(evidence_ids)

            cur.execute("""INSERT INTO eligibility_results (store_id, rules_version, store_class, status,
                               area_basis_sqft, evidence_ids, reasons)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (sid, rules["regulation"]["version_date"], c.store_class, c.status,
                         c.area_basis_sqft, evidence_ids, c.reasons))
            counts["stores"] += 1
        counts["buildings"] = len(fp_raw_ids)
    return counts


if __name__ == "__main__":
    print(load())
