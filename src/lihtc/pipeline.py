"""End-to-end run over the latest raw snapshots:
OSM candidates -> footprint match -> proxy facts -> classification -> CSV.

Usage:  PYTHONPATH=src python -m lihtc.pipeline [--fetch]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from .acquisition import arcgis, osm
from .acquisition.base import last_good
from .spatial.footprints import match_store
from .validation.eligibility import AreaEvidence, StoreFacts, classify, load_rules

OUT = Path(__file__).resolve().parents[2] / "data" / "processed"

MULTIPURPOSE_SHOPS = {"department_store", "general", "wholesale"}
# Chains whose standard format is a full-line grocer (fresh meat + produce +
# staples). This is a prior, not evidence: it only moves food categories from
# "unknown" to "likely", recorded as source_type=brand_prior.
FULL_LINE_BRANDS = {"Ralphs", "Albertsons", "Vons", "Pavilions", "Stater Bros.", "Food 4 Less",
                    "Sprouts Farmers Market", "Whole Foods Market", "Gelson's", "Northgate Market",
                    "El Super", "Vallarta", "H Mart", "99 Ranch Market", "Mitsuwa Marketplace",
                    "Island Pacific Supermarket", "Amazon Fresh", "Walmart Neighborhood Market",
                    "SF Supermarket"}


def load_inputs(fetch: bool = False) -> tuple[list[dict], dict]:
    if fetch:
        stores = osm.parse(osm.fetch().path)
        fp_path = arcgis.fetch_for_stores(stores).path
    else:
        stores = osm.parse(last_good("osm_overpass"))
        fp_path = last_good("oc_building_footprints_2021")
    return stores, json.loads(Path(fp_path).read_text())


def area_evidence_for(m) -> list[AreaEvidence]:
    if not m.footprint_sqft:
        return []
    src = "building_footprint_multi_tenant" if m.flags else "building_footprint_single_tenant"
    return [AreaEvidence(m.footprint_sqft, src, "footprint")]


def evaluate(stores: list[dict], footprints: dict, rules: dict | None = None):
    """Yield (store, footprint_match, is_multipurpose, classification) per store."""
    rules = rules or load_rules()
    for s in stores:
        m = match_store(s, footprints.get(s["source_record_id"], []))
        multipurpose = s["shop"] in MULTIPURPOSE_SHOPS
        # brand prior is NOT evidence: food stays unknown -> status can be at most unverified
        facts = StoreFacts(s["source_record_id"], is_multipurpose=multipurpose,
                           area=area_evidence_for(m))
        yield s, m, multipurpose, classify(facts, rules)


PUBLISHED = OUT / "oc_grocery_candidates.csv"


def run(fetch: bool = False, out: Path | None = None,
        inputs: tuple[list[dict], dict] | None = None) -> Path:
    """Write the candidate CSV. `inputs`/`out` let a refresh stage a candidate
    run from specific snapshots without touching the published file."""
    stores, footprints = inputs or load_inputs(fetch)

    out = out or PUBLISHED
    out.parent.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["osm_id", "name", "brand", "shop", "address", "city", "lat", "lon",
                    "building_id", "footprint_sqft", "footprint_method", "footprint_flags",
                    "is_multipurpose", "food_evidence", "store_class", "status", "reasons"])
        for s, m, multipurpose, c in evaluate(stores, footprints):
            stats[c.status] += 1
            w.writerow([s["source_record_id"], s["name"], s["brand"], s["shop"],
                        f"{s['housenumber'] or ''} {s['street'] or ''}".strip(), s["city"],
                        round(s["lat"], 6), round(s["lon"], 6), m.building_id,
                        round(m.footprint_sqft) if m.footprint_sqft else None, m.method,
                        ";".join(m.flags), multipurpose,
                        "brand_prior" if s["brand"] in FULL_LINE_BRANDS else "none",
                        c.store_class, c.status, " | ".join(c.reasons)])
    print(f"wrote {out}  {dict(stats)}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="re-download sources first")
    run(ap.parse_args().fetch)
