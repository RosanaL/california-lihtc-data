"""Compare two snapshots and describe what changed.

Every function is pure: it takes old and new data and returns Change records.
Severity drives what a refresh does next:
  info   recorded only
  warn   recorded and surfaced in the run report for operations to review
  error  the refresh is gated: the new data is NOT published
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

SEVERITY_ORDER = {"info": 0, "warn": 1, "error": 2}

MOVE_WARN_M = 50              # a store point moving further than this is suspicious
AREA_WARN_RATIO = 0.10        # footprint area change worth a look
COUNT_WARN_RATIO = 0.10       # record-count drop that needs review
COUNT_ERROR_RATIO = 0.30      # record-count drop that blocks publishing


@dataclass
class Change:
    source_id: str
    kind: str
    severity: str
    key: str = ""             # store / record the change is about ("" = whole source)
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def worst(changes: list[Change]) -> str:
    return max((c.severity for c in changes), key=SEVERITY_ORDER.get, default="info")


def _meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---- source-level checks ------------------------------------------------------

def diff_count(source_id: str, old_n: int, new_n: int) -> list[Change]:
    """A sudden drop usually means a broken query or a partial response, not
    that a third of the county's supermarkets closed overnight."""
    if old_n == 0:
        return []
    ratio = (new_n - old_n) / old_n
    detail = {"old": old_n, "new": new_n, "change": round(ratio, 4)}
    if ratio <= -COUNT_ERROR_RATIO:
        return [Change(source_id, "record_count_drop", "error", detail=detail)]
    if ratio <= -COUNT_WARN_RATIO:
        return [Change(source_id, "record_count_drop", "warn", detail=detail)]
    if abs(ratio) >= COUNT_WARN_RATIO:
        return [Change(source_id, "record_count_change", "info", detail=detail)]
    return []


def diff_schema(source_id: str, old_fields: list[str], new_fields: list[str],
                required: set[str]) -> list[Change]:
    """Field-level drift in a source's published schema."""
    old, new = set(old_fields), set(new_fields)
    out = []
    missing_required = sorted(required - new)
    if missing_required:
        out.append(Change(source_id, "required_field_missing", "error",
                          detail={"fields": missing_required}))
    removed = sorted((old - new) - required)
    added = sorted(new - old)
    if removed:
        out.append(Change(source_id, "fields_removed", "warn", detail={"fields": removed}))
    if added:
        out.append(Change(source_id, "fields_added", "info", detail={"fields": added}))
    return out


# ---- store-level checks -------------------------------------------------------

def diff_stores(source_id: str, old: list[dict], new: list[dict]) -> list[Change]:
    """Candidate stores keyed by source record id (e.g. OSM 'way/123')."""
    o = {s["source_record_id"]: s for s in old}
    n = {s["source_record_id"]: s for s in new}
    out: list[Change] = []
    for k in sorted(n.keys() - o.keys()):
        s = n[k]
        out.append(Change(source_id, "store_added", "info", k,
                          {"name": s.get("name"), "brand": s.get("brand"), "city": s.get("city")}))
    for k in sorted(o.keys() - n.keys()):
        s = o[k]
        # TCAC requires the amenity to be in place at application: a vanished
        # store is a possible closure, which can cost a project its points.
        out.append(Change(source_id, "store_removed", "warn", k,
                          {"name": s.get("name"), "brand": s.get("brand"), "city": s.get("city")}))
    for k in sorted(o.keys() & n.keys()):
        a, b = o[k], n[k]
        if (a.get("name"), a.get("brand")) != (b.get("name"), b.get("brand")):
            out.append(Change(source_id, "store_renamed", "warn", k,
                              {"old": [a.get("name"), a.get("brand")], "new": [b.get("name"), b.get("brand")]}))
        d = _meters(a["lat"], a["lon"], b["lat"], b["lon"])
        if d > MOVE_WARN_M:
            out.append(Change(source_id, "store_moved", "warn", k, {"meters": round(d)}))
        addr = lambda s: (s.get("housenumber"), s.get("street"), s.get("city"))
        if addr(a) != addr(b):
            out.append(Change(source_id, "address_changed", "info", k,
                              {"old": list(addr(a)), "new": list(addr(b))}))
        if a.get("shop") != b.get("shop"):
            out.append(Change(source_id, "shop_type_changed", "warn", k,
                              {"old": a.get("shop"), "new": b.get("shop")}))
    return out


# ---- result-level checks ------------------------------------------------------

def diff_results(old_rows: list[dict], new_rows: list[dict]) -> list[Change]:
    """Compare two pipeline outputs (oc_grocery_candidates.csv rows)."""
    o = {r["osm_id"]: r for r in old_rows}
    n = {r["osm_id"]: r for r in new_rows}
    out: list[Change] = []
    for k in sorted(o.keys() & n.keys()):
        a, b = o[k], n[k]
        if (a["store_class"], a["status"]) != (b["store_class"], b["status"]):
            out.append(Change("pipeline", "classification_changed", "warn", k,
                              {"name": b["name"], "old": [a["store_class"], a["status"]],
                               "new": [b["store_class"], b["status"]]}))
        if a["building_id"] != b["building_id"]:
            out.append(Change("pipeline", "building_changed", "info", k,
                              {"old": a["building_id"], "new": b["building_id"]}))
        if a["footprint_sqft"] and b["footprint_sqft"]:
            x, y = float(a["footprint_sqft"]), float(b["footprint_sqft"])
            if abs(y - x) / x > AREA_WARN_RATIO:
                out.append(Change("pipeline", "area_changed", "warn", k,
                                  {"name": b["name"], "old": round(x), "new": round(y)}))
        elif bool(a["footprint_sqft"]) != bool(b["footprint_sqft"]):
            out.append(Change("pipeline", "area_presence_changed", "warn", k,
                              {"name": b["name"], "old": a["footprint_sqft"], "new": b["footprint_sqft"]}))
    return out
