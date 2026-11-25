<!-- Example output of a gated refresh. Produced by injecting a synthetic OSM snapshot with half its records removed and one store renamed (see README, Refresh). -->
# Refresh 20261125T183423Z

**Status: gated**

1 errors · 194 warnings · 0 info

## Snapshots

- `schema`: 20261125T183049Z.json
- `osm_overpass`: 29991231T000000Z.json
- `oc_building_footprints_2021`: 20261125T183408Z.json

## Error

| Source | Change | Key | Detail |
| --- | --- | --- | --- |
| osm_overpass | record_count_drop |  | `{"old": 387, "new": 194, "change": -0.4987}` |

## Warn

| Source | Change | Key | Detail |
| --- | --- | --- | --- |
| osm_overpass | store_renamed | node/471293427 | `{"old": ["Ralphs", "Ralphs"], "new": ["Ralphs Fresh Fare", "Ralphs"]}` |
| osm_overpass | store_removed | … | 193 rows, one per vanished store (name, brand, city) |

Result: exit code 2. The published CSV and every source's LAST_GOOD pointer were left unchanged.
