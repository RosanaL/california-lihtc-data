import json

from lihtc.acquisition import osm
from lihtc.normalization.address import (address_agreement, normalize_address,
                                         normalize_name, split_address_line, store_number)
from lihtc.spatial.footprints import area_sqft, match_store
from lihtc.validation.eligibility import AreaEvidence, StoreFacts, classify


# ---- normalization -------------------------------------------------------
def test_address_forms_converge():
    assert normalize_address("3750", "Barranca Parkway") == normalize_address(
        *split_address_line("3750 BARRANCA PKWY"))
    assert normalize_address("4225", "Campus Drive") == "4225 campus dr"
    assert normalize_address(*split_address_line("4213 CAMPUS DR 166A")) == "4213 campus dr"
    assert normalize_address("27702", "Crown Valley Parkway") == normalize_address(
        *split_address_line("27702 CROWN VALLEY PKWY UNIT B"))


def test_address_agreement_grades():
    assert address_agreement("21751 lake forest dr", "21751 lake forest dr") == "exact"
    assert address_agreement("21751 lake forest dr", "21761 lake forest dr") == "same_street"
    assert address_agreement("915 s brookhurst st", "2219 ball rd") == "different"
    assert address_agreement("305 w imperial hwy", "0") == "unknown"


def test_names():
    assert normalize_name("RALPHS GROCERY #78") == normalize_name("Ralphs") == "ralphs"
    assert normalize_name("Sprouts Farmer Market") == normalize_name("Sprouts Farmers Market")
    assert normalize_name("Trader Joe's") == "trader joes"
    assert store_number("RALPHS GROCERY #78") == "78"


# ---- OSM parsing -----------------------------------------------------------
def test_osm_parse_way_uses_outline(tmp_path):
    ring = [{"lat": 33.0, "lon": -117.0}, {"lat": 33.0, "lon": -116.999},
            {"lat": 33.001, "lon": -116.999}, {"lat": 33.001, "lon": -117.0},
            {"lat": 33.0, "lon": -117.0}]
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"elements": [
        {"type": "way", "id": 1, "geometry": ring, "tags": {"shop": "supermarket", "building": "retail"}},
        {"type": "node", "id": 2, "lat": 33.5, "lon": -117.5, "tags": {"shop": "supermarket"}},
    ]}))
    recs = osm.parse(p)
    assert len(recs) == 2  # regression: ways from `out geom` were once dropped
    way = recs[0]
    assert way["osm_outline"] and 33.0 < way["lat"] < 33.001


# ---- spatial ---------------------------------------------------------------
def _square(lon, lat, d):
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


def test_area_sqft_reasonable():
    # 0.001 deg square at 33.7N ~ 111m x 92.6m ~ 10,300 m^2 ~ 111k sqft
    a = area_sqft([tuple(p) for p in _square(-117.8, 33.7, 0.001)])
    assert 100_000 < a < 120_000


def test_match_prefers_osm_outline_when_footprint_much_bigger():
    store = {"lon": -117.7995, "lat": 33.7005, "housenumber": "1", "street": "Main St",
             "osm_outline": [tuple(p) for p in _square(-117.8, 33.7, 0.0005)]}
    big = {"attributes": {"FID": 9, "OBJECTID": 0, "Address": "1 MAIN ST"},
           "geometry": {"rings": [_square(-117.801, 33.699, 0.003)]}}
    m = match_store(store, [big])
    assert m.method == "osm_outline"
    assert "footprint_exceeds_osm_store_outline" in m.flags
    assert m.address_agrees == "exact"


def test_match_none():
    m = match_store({"lon": 0, "lat": 0}, [])
    assert m.method == "none" and m.footprint_sqft is None


# ---- eligibility -----------------------------------------------------------
FOOD_OK = dict(sells_staples=True, sells_fresh_meat=True, sells_fresh_produce=True)


def test_documented_large_store_is_eligible():
    c = classify(StoreFacts("a", **FOOD_OK, area=[AreaEvidence(48000, "permit_record", "gross_interior")]))
    assert (c.store_class, c.status) == ("full_scale_supermarket", "eligible")


def test_footprint_only_is_never_eligible():
    c = classify(StoreFacts("c", **FOOD_OK,
                            area=[AreaEvidence(42000, "building_footprint_single_tenant", "footprint")]))
    assert c.status == "likely"


def test_13k_store_is_neighborhood_market_not_ineligible():
    c = classify(StoreFacts("b", **FOOD_OK, area=[AreaEvidence(13000, "leasing_brochure_tenant_sf", "leased")]))
    assert (c.store_class, c.status) == ("neighborhood_market", "eligible")


def test_tiny_store_ineligible():
    c = classify(StoreFacts("t", **FOOD_OK, area=[AreaEvidence(2500, "assessor_building_area", "gross_interior")]))
    assert c.status == "ineligible"


def test_unknown_food_blocks_eligibility():
    c = classify(StoreFacts("d", area=[AreaEvidence(60000, "permit_record", "gross_interior")]))
    assert c.status == "unverified" and c.store_class == "full_scale_supermarket"


def test_no_fresh_meat_is_ineligible():
    c = classify(StoreFacts("e", sells_staples=True, sells_fresh_meat=False, sells_fresh_produce=True,
                            area=[AreaEvidence(60000, "permit_record", "gross_interior")]))
    assert c.status == "ineligible"


def test_multipurpose_needs_grocery_section_area():
    total_only = StoreFacts("w", **FOOD_OK, is_multipurpose=True,
                            area=[AreaEvidence(180000, "permit_record", "gross_interior")])
    c = classify(total_only)
    assert c.status == "unverified" and c.store_class is None
    with_section = StoreFacts("w", **FOOD_OK, is_multipurpose=True,
                              area=[AreaEvidence(180000, "permit_record", "gross_interior"),
                                    AreaEvidence(32000, "architect_measurement", "grocery_section")])
    assert classify(with_section).status == "eligible"


def test_conflicting_documents_need_review():
    c = classify(StoreFacts("x", **FOOD_OK, area=[
        AreaEvidence(42500, "leasing_brochure_tenant_sf", "leased"),
        AreaEvidence(23000, "planning_document", "gross_interior")]))
    assert c.status == "unverified"


def test_closed_store_ineligible():
    assert classify(StoreFacts("z", operating=False)).status == "ineligible"
