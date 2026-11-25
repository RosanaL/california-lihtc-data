# Benchmark evaluation

25 Orange County stores, stratified by footprint flag (see benchmark_selection.csv).

## Verification rate

| Evidence | Stores | Share |
| --- | --- | --- |
| Health permit matched | 24 | 96% |
| Fresh-meat sub-permit | 11 | 44% |
| Produce named in a permit | 4 | 16% |
| Documented area, high confidence | 4 | 16% |
| Documented area, medium confidence | 1 | 4% |
| Documented area, low confidence (excluded) | 1 | 4% |

## Area proxy vs documented area

| Store | Proxy method | Flags | Proxy sqft | Documented sqft | Error | Class (proxy / truth) |
| --- | --- | --- | --- | --- | --- | --- |
| Traders Joes #257 | osm_outline | shared_site_address;footprint_exceeds_osm_store_outline | 15,096 | 15,000 | +1% | neighborhood / neighborhood |
| RALPHS GROCERY #650 | osm_outline | address_mismatch;footprint_exceeds_osm_store_outline | 51,179 | 51,581 | -1% | full_scale / full_scale |
| La Habra Grocery Outlet #299 | point_in_polygon | none | 15,824 | 14,844 | +7% | neighborhood / neighborhood |
| Stater Bros Market #198 | point_in_polygon | none | 63,014 | 43,830 | +44% | full_scale / full_scale |
| TRADER JOES #113 | osm_outline | shared_site_address;footprint_exceeds_osm_store_outline | 29,358 | 12,160 | +141% | full_scale / neighborhood **x** |

Class agreement: 4 of 5.

Absolute error by method (min / median / max): osm_outline 1% / 1% / 141% (n=3); point_in_polygon 7% / 25% / 44% (n=2).

n is small; treat these as directional, not as rates. Documented figures are leased square feet from owner or broker rosters, used as a stand-in for gross interior area.

## Brand prior check

Of 8 stores whose brand the pipeline treats as a full-line grocer, 8 hold a fresh-meat sub-permit. The prior is a reasonable search hint, not evidence.

## Eligibility with benchmark evidence

| Scenario | eligible | likely | unverified | ineligible |
| --- | --- | --- | --- | --- |
| Strict: staples need direct evidence | 0 | 0 | 24 | 1 |
| Assume any matched retail-food MARKET permit sells staples | 1 | 3 | 20 | 1 |

Under the assumption, the stores that reach a firm status:

- **ABC SUPERMARKET (1216 S Magnolia Ave)**: likely (full_scale_supermarket); footprint is a proxy: includes walls, other tenants, back-of-house
- **Stater Bros Market #198**: eligible (full_scale_supermarket); leased area 43,830 sqft from leasing_brochure_tenant_sf (strong)
- **Stater Bros Market #28**: likely (full_scale_supermarket); footprint is a proxy: includes walls, other tenants, back-of-house
- **STATER BROS MARKET #124**: likely (full_scale_supermarket); footprint is a proxy: includes walls, other tenants, back-of-house
- **WAL-MART #2546**: ineligible; does not sell required category: fresh_meat
