import json

from lihtc.acquisition import base
from lihtc.refresh.diff import (diff_count, diff_results, diff_schema, diff_stores, worst)


def _store(i, name="Ralphs", lat=33.70, lon=-117.80, **kw):
    s = {"source_record_id": f"way/{i}", "name": name, "brand": name, "shop": "supermarket",
         "housenumber": "1", "street": "Main St", "city": "Irvine", "lat": lat, "lon": lon}
    s.update(kw)
    return s


# ---- source level --------------------------------------------------------------
def test_count_thresholds():
    assert diff_count("osm", 100, 98) == []
    assert diff_count("osm", 100, 85)[0].severity == "warn"
    assert diff_count("osm", 100, 60)[0].severity == "error"   # a broken query, not 40 closures
    assert diff_count("osm", 100, 120)[0].severity == "info"
    assert diff_count("osm", 0, 50) == []


def test_schema_required_field_missing_is_error():
    ch = diff_schema("fp", ["FID", "Address", "Shape__Area", "City"], ["FID", "Shape__Area", "City", "Zip"],
                     {"FID", "Address", "Shape__Area"})
    kinds = {c.kind: c.severity for c in ch}
    assert kinds == {"required_field_missing": "error", "fields_added": "info"}


def test_schema_optional_field_removed_is_warn():
    ch = diff_schema("fp", ["FID", "Address", "Shape__Area", "MeanHeight"], ["FID", "Address", "Shape__Area"],
                     {"FID", "Address", "Shape__Area"})
    assert [(c.kind, c.severity) for c in ch] == [("fields_removed", "warn")]


# ---- store level ---------------------------------------------------------------
def test_store_added_removed_renamed_moved():
    old = [_store(1), _store(2, "Albertsons"), _store(3, "Vons")]
    new = [_store(1), _store(2, "Grocery Outlet"), _store(3, "Vons", lat=33.701), _store(4, "Sprouts")]
    old.append(_store(5, "Stater Bros."))
    ch = {(c.kind, c.key): c for c in diff_stores("osm", old, new)}
    assert ch[("store_added", "way/4")].severity == "info"
    assert ch[("store_removed", "way/5")].severity == "warn"     # possible closure
    assert ch[("store_renamed", "way/2")].severity == "warn"     # e.g. ownership change
    assert ch[("store_moved", "way/3")].detail["meters"] > 100   # 0.001 deg lat ~ 111 m
    assert ("store_moved", "way/1") not in ch


def test_small_jitter_is_not_a_move():
    ch = diff_stores("osm", [_store(1)], [_store(1, lat=33.70002)])  # ~2 m
    assert ch == []


# ---- result level --------------------------------------------------------------
def _row(i, cls="full_scale_supermarket", status="unverified", sqft="40000", bid="7"):
    return {"osm_id": f"way/{i}", "name": "X", "store_class": cls, "status": status,
            "footprint_sqft": sqft, "building_id": bid}


def test_results_classification_and_area():
    old = [_row(1), _row(2), _row(3)]
    new = [_row(1), _row(2, cls="neighborhood_market", sqft="20000"), _row(3, sqft="41000", bid="9")]
    kinds = {(c.kind, c.key) for c in diff_results(old, new)}
    assert ("classification_changed", "way/2") in kinds
    assert ("area_changed", "way/2") in kinds
    assert ("area_changed", "way/3") not in kinds            # 2.5% is noise
    assert ("building_changed", "way/3") in kinds


def test_worst():
    assert worst([]) == "info"
    assert worst(diff_count("o", 100, 60) + diff_count("o", 100, 85)) == "error"


# ---- LAST_GOOD pointer ---------------------------------------------------------
def test_last_good_pointer(tmp_path):
    src = tmp_path / "osm"
    src.mkdir()
    (src / "20260101T000000Z.json").write_text(json.dumps({"elements": []}))
    (src / "20260102T000000Z.json").write_text(json.dumps({"elements": []}))
    # before any refresh: falls back to the newest snapshot
    assert base.last_good("osm", root=tmp_path).name == "20260102T000000Z.json"
    # a gated newer snapshot must not be picked up
    base.mark_good("osm", src / "20260101T000000Z.json", root=tmp_path)
    (src / "20260103T000000Z.json").write_text(json.dumps({"elements": []}))
    assert base.last_good("osm", root=tmp_path).name == "20260101T000000Z.json"
