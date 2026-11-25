"""ArcGIS REST adapter for Orange County building footprints.

Rather than mirroring all ~780k polygons, we ask the FeatureServer for the
footprints within a small envelope around each candidate store. The layer is
stored in EPSG:2230 (NAD83 / California zone 6, US feet), so the server's
Shape__Area is already square feet; we also request geometry in EPSG:4326
for storage and joins.
"""
from __future__ import annotations

import time

from .base import http_session, request_with_retry, save_raw, RawSnapshot

FOOTPRINTS_2021 = ("https://services.arcgis.com/UXmFoWC7yDHcDN5Q/arcgis/rest/services/"
                   "Orange_County_Building_Footprints_2021/FeatureServer/0")
# ~25 m in degrees at OC latitude; enough to catch a store whose OSM node sits
# in the parking lot just outside its building.
DEFAULT_PAD_DEG = 0.00025


def query_envelope(session, layer_url: str, lon: float, lat: float,
                   pad: float = DEFAULT_PAD_DEG) -> dict:
    params = {
        "geometry": f"{lon - pad},{lat - pad},{lon + pad},{lat + pad}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FID,OBJECTID,Address,City,Zipcode,MeanHeight,Shape__Area,capture_da",
        "outSR": 4326,
        "returnGeometry": "true",
        "f": "json",
    }
    r = request_with_retry(session, "GET", f"{layer_url}/query", params=params, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return data


def fetch_for_stores(stores: list[dict], layer_url: str = FOOTPRINTS_2021,
                     delay: float = 0.2) -> RawSnapshot:
    """One envelope query per store; the whole batch is saved as one raw snapshot."""
    session, results = http_session(), {}
    for s in stores:
        data = query_envelope(session, layer_url, s["lon"], s["lat"])
        results[s["source_record_id"]] = data.get("features", [])
        time.sleep(delay)  # be polite to a shared public service
    n = sum(len(v) for v in results.values())
    return save_raw("oc_building_footprints_2021", results, layer_url,
                    {"mode": "envelope_per_store", "pad_deg": DEFAULT_PAD_DEG}, n)


def feature_to_ring(feature: dict) -> list[tuple[float, float]] | None:
    rings = (feature.get("geometry") or {}).get("rings")
    return [tuple(p) for p in rings[0]] if rings else None
