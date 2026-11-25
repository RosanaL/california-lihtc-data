"""OpenStreetMap candidate-store adapter (Overpass API).

Pulls only tags that can plausibly be a TCAC grocery amenity:
  shop=supermarket, shop=grocery, shop=wholesale (warehouse clubs), plus
  department_store/general stores of known big-box brands, which may be
  "large multipurpose stores containing a grocery section".
OSM cannot tell us grocery-section area, so multipurpose stores are only
candidates here. Ways come back with `out center geom` so we keep the
store outline when OSM mapped the store as a building.
"""
from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Polygon

from .base import http_session, request_with_retry, save_raw, RawSnapshot

OVERPASS_URLS = [  # the main instance is often at capacity; mirrors serve the same data
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
# Orange County, California: OSM relation 396466 -> area id 3600000000 + id
OC_AREA_ID = 3600396466

QUERY_TEMPLATE = """
[out:json][timeout:180];
area({area_id})->.a;
(
  nwr["shop"~"^(supermarket|grocery|wholesale)$"](area.a);
  nwr["shop"~"^(department_store|general)$"]["brand"~"Walmart|Target|Costco|Sam's Club",i](area.a);
);
out tags geom;
"""


def fetch(area_id: int = OC_AREA_ID) -> RawSnapshot:
    query = QUERY_TEMPLATE.format(area_id=area_id)
    session, errors = http_session(), []
    for url in OVERPASS_URLS:
        try:
            r = request_with_retry(session, "POST", url, retries=2, data={"data": query})
            data = r.json()
        except (RuntimeError, ValueError) as e:
            errors.append(f"{url}: {e}")
            continue
        if "remark" in data and "error" in data["remark"].lower():
            errors.append(f"{url}: {data['remark']}")  # e.g. query timed out server-side
            continue
        return save_raw("osm_overpass", data, url,
                        {"area_id": area_id, "query": query}, len(data.get("elements", [])))
    raise RuntimeError("all Overpass endpoints failed:\n" + "\n".join(errors))


def parse(raw_path: str | Path) -> list[dict]:
    """Flatten Overpass elements into candidate-store records."""
    data = json.loads(Path(raw_path).read_text())
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        outline = None
        if el["type"] == "node":
            lat, lon = el["lat"], el["lon"]
        elif el["type"] == "way" and el.get("geometry"):
            # `out geom` returns the full ring instead of a center
            outline = [(p["lon"], p["lat"]) for p in el["geometry"]]
            pt = Polygon(outline).representative_point() if len(outline) >= 4 else None
            lon, lat = (pt.x, pt.y) if pt else (outline[0][0], outline[0][1])
        elif el.get("bounds"):  # relations: fall back to bbox centre
            b = el["bounds"]
            lat, lon = (b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None:
            continue
        out.append({
            "source_record_id": f"{el['type']}/{el['id']}",
            "name": tags.get("name"),
            "brand": tags.get("brand"),
            "shop": tags.get("shop"),
            "housenumber": tags.get("addr:housenumber"),
            "street": tags.get("addr:street"),
            "city": tags.get("addr:city"),
            "postcode": tags.get("addr:postcode"),
            "lat": lat,
            "lon": lon,
            "osm_outline": outline,           # closed ring if mapped as a building
            "is_building": "building" in tags,
            "tags": tags,
        })
    return out
