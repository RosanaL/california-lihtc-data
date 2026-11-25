"""Assign a building footprint to each candidate store and derive an area proxy.

Footprint area is NOT gross interior area (it ignores mezzanines, counts
walls, and covers every tenant under one roof), so the output here is labelled
as a proxy and tagged with why it might be wrong.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform

from ..normalization.address import address_agreement, normalize_address, split_address_line

# EPSG:2230 = NAD83 / California zone 6 (ftUS): an equal-enough projection for OC
_to_ft = Transformer.from_crs(4326, 2230, always_xy=True).transform
SQFT_PER_SQFTUS = 1.000004  # US survey foot^2 -> international foot^2

MULTI_TENANT_RATIO = 1.6   # footprint this much larger than OSM's own store outline
LARGE_FOOTPRINT_SQFT = 150_000  # bigger than any single US grocery: likely a shared mall


@dataclass
class FootprintMatch:
    building_id: str | None
    footprint_sqft: float | None
    method: str              # osm_outline | point_in_polygon | nearest_within_25m | none
    address_agrees: str | None  # exact | same_street | different | unknown
    flags: list[str]


def area_sqft(ring: list[tuple[float, float]]) -> float:
    return transform(_to_ft, Polygon(ring)).area * SQFT_PER_SQFTUS


def match_store(store: dict, features: list[dict]) -> FootprintMatch:
    """Pick the footprint for a store from candidate ArcGIS features (EPSG:4326)."""
    pt = Point(store["lon"], store["lat"])
    store_addr = normalize_address(store.get("housenumber"), store.get("street"))
    polys = []
    for f in features:
        rings = (f.get("geometry") or {}).get("rings")
        if rings:
            polys.append((f, Polygon(rings[0])))

    flags: list[str] = []
    chosen, method = None, "none"
    containing = [(f, p) for f, p in polys if p.contains(pt)]
    if containing:
        chosen, method = max(containing, key=lambda fp: fp[1].area), "point_in_polygon"
    elif polys:
        pt_ft = transform(_to_ft, pt)
        f, p = min(polys, key=lambda fp: transform(_to_ft, fp[1]).distance(pt_ft))
        if transform(_to_ft, p).distance(pt_ft) <= 82:  # 25 m
            chosen, method = (f, p), "nearest_within_25m"
            flags.append("store_point_outside_building")

    osm_sqft = area_sqft(store["osm_outline"]) if store.get("osm_outline") else None
    if chosen is None:
        if osm_sqft:
            return FootprintMatch(None, osm_sqft, "osm_outline", None, ["no_county_footprint"])
        return FootprintMatch(None, None, "none", None, ["no_footprint_found"])

    f, poly = chosen
    attrs = f.get("attributes", {})
    sqft = area_sqft(list(poly.exterior.coords))

    bld_addr = normalize_address(*split_address_line(attrs.get("Address")))
    address_agrees = address_agreement(store_addr, bld_addr)
    if address_agrees == "different":
        flags.append("address_mismatch")
    elif address_agrees == "same_street":
        flags.append("shared_site_address")  # likely a multi-tenant center building

    if sqft > LARGE_FOOTPRINT_SQFT:
        flags.append("very_large_footprint_possible_mall")
    if osm_sqft and sqft > MULTI_TENANT_RATIO * osm_sqft:
        flags.append("footprint_exceeds_osm_store_outline")
        # OSM mapped the store itself; that outline is the better proxy
        return FootprintMatch(str(attrs.get("FID")), osm_sqft, "osm_outline",
                              address_agrees, flags)
    return FootprintMatch(str(attrs.get("FID")), sqft, method, address_agrees, flags)
