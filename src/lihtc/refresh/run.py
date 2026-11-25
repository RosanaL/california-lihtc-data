"""Refresh all sources, detect changes, and publish only if checks pass.

Usage:  PYTHONPATH=src python -m lihtc.refresh.run [--load] [--offline]

  --load     after publishing, reload PostGIS and record the run and its changes
  --offline  skip fetching; re-check the newest snapshots on disk against LAST_GOOD
             (useful to re-evaluate a gated run after a fix)

Steps
  1. fetch the footprint layer's schema, OSM candidates, and footprints
  2. diff each new snapshot against its LAST_GOOD snapshot
  3. stage a candidate pipeline run from the new snapshots; diff its results
  4. if nothing is error-severity: publish (move LAST_GOOD, replace the CSV)
     otherwise: keep the previous dataset and exit non-zero
  5. write data/runs/<run_id>/report.md + changes.json either way

Exit codes: 0 published, 1 published with warnings, 2 gated (not published).
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..acquisition import arcgis, osm
from ..acquisition.base import (http_session, last_good, latest_raw, mark_good,
                                request_with_retry, save_raw)
from ..pipeline import PUBLISHED
from ..pipeline import run as run_pipeline
from .diff import Change, diff_count, diff_results, diff_schema, diff_stores, worst

ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / "data" / "runs"
FP_SOURCE = "oc_building_footprints_2021"
SCHEMA_SOURCE = "oc_building_footprints_2021_schema"
# Fields the pipeline cannot work without (FID is the unique key; see README Finding 3)
FP_REQUIRED = {"FID", "Address", "Shape__Area"}


def fetch_layer_schema() -> Path:
    r = request_with_retry(http_session(), "GET", arcgis.FOOTPRINTS_2021, params={"f": "json"}, timeout=60)
    meta = r.json()
    if "error" in meta:
        raise RuntimeError(f"ArcGIS error: {meta['error']}")
    keep = {k: meta.get(k) for k in ("name", "objectIdField", "maxRecordCount", "geometryType")}
    keep["fields"] = [f["name"] for f in meta.get("fields", [])]
    keep["extent_wkid"] = (meta.get("extent") or {}).get("spatialReference", {}).get("wkid")
    return Path(save_raw(SCHEMA_SOURCE, keep, arcgis.FOOTPRINTS_2021, {"f": "json"}, len(keep["fields"])).path)


def _same_file(a: Path | None, b: Path | None) -> bool:
    return a is not None and b is not None and a.name == b.name


def check(new_schema: Path | None, new_osm: Path, new_fp: Path) -> tuple[list[Change], list[dict], dict]:
    """Diff new snapshots against LAST_GOOD. Returns changes and the parsed new inputs."""
    changes: list[Change] = []

    # schema
    if new_schema:
        old_schema = last_good(SCHEMA_SOURCE)
        new_meta = json.loads(new_schema.read_text())
        old_meta = json.loads(old_schema.read_text()) if old_schema and not _same_file(old_schema, new_schema) else None
        if old_meta:
            changes += diff_schema(SCHEMA_SOURCE, old_meta["fields"], new_meta["fields"], FP_REQUIRED)
            if old_meta.get("objectIdField") != new_meta.get("objectIdField"):
                changes.append(Change(SCHEMA_SOURCE, "object_id_field_changed", "error",
                                      detail={"old": old_meta.get("objectIdField"), "new": new_meta.get("objectIdField")}))
        else:  # first schema snapshot: nothing to compare, but required fields must exist
            changes += diff_schema(SCHEMA_SOURCE, new_meta["fields"], new_meta["fields"], FP_REQUIRED)

    # OSM candidates
    new_stores = osm.parse(new_osm)
    old_osm = last_good("osm_overpass")
    if old_osm and not _same_file(old_osm, new_osm):
        old_stores = osm.parse(old_osm)
        changes += diff_count("osm_overpass", len(old_stores), len(new_stores))
        changes += diff_stores("osm_overpass", old_stores, new_stores)

    # footprints: a response with no features for most stores means the query broke
    new_fp_data = json.loads(new_fp.read_text())
    hit = sum(1 for s in new_stores if new_fp_data.get(s["source_record_id"]))
    old_fp = last_good(FP_SOURCE)
    if old_fp and not _same_file(old_fp, new_fp):
        old_fp_data = json.loads(old_fp.read_text())
        old_hit = sum(1 for v in old_fp_data.values() if v)
        changes += diff_count(FP_SOURCE, old_hit, hit)
    if new_stores and hit / len(new_stores) < 0.5:
        changes.append(Change(FP_SOURCE, "low_footprint_coverage", "error",
                              detail={"stores_with_footprints": hit, "stores": len(new_stores)}))
    return changes, new_stores, new_fp_data


def write_report(run_dir: Path, run_id: str, status: str, changes: list[Change], snapshots: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "changes.json").write_text(json.dumps(
        {"run_id": run_id, "status": status, "snapshots": snapshots,
         "changes": [c.as_dict() for c in changes]}, indent=2))
    by_sev = {s: [c for c in changes if c.severity == s] for s in ("error", "warn", "info")}
    lines = [f"# Refresh {run_id}", "", f"**Status: {status}**", "",
             f"{len(by_sev['error'])} errors · {len(by_sev['warn'])} warnings · {len(by_sev['info'])} info", "",
             "## Snapshots", ""] + [f"- `{k}`: {v}" for k, v in snapshots.items()] + [""]
    for sev in ("error", "warn", "info"):
        if not by_sev[sev]:
            continue
        lines += [f"## {sev.title()}", "", "| Source | Change | Key | Detail |", "| --- | --- | --- | --- |"]
        for c in by_sev[sev][:200]:
            lines.append(f"| {c.source_id} | {c.kind} | {c.key} | `{json.dumps(c.detail, ensure_ascii=False)}` |")
        if len(by_sev[sev]) > 200:
            lines.append(f"| … | {len(by_sev[sev]) - 200} more in changes.json | | |")
        lines.append("")
    path = run_dir / "report.md"
    path.write_text("\n".join(lines))
    return path


def record_in_db(run_id: str, status: str, changes: list[Change], snapshots: dict) -> None:
    import psycopg
    from psycopg.types.json import Jsonb
    from ..load import DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO refresh_runs (run_id, status, snapshots, n_error, n_warn, n_info) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (run_id, status, Jsonb(snapshots),
                     *(sum(c.severity == s for c in changes) for s in ("error", "warn", "info"))))
        for c in changes:
            cur.execute("INSERT INTO change_events (run_id, source_id, kind, severity, record_key, detail) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (run_id, c.source_id, c.kind, c.severity, c.key or None, Jsonb(c.detail)))


def refresh(offline: bool = False, load: bool = False) -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    changes: list[Change] = []
    snapshots: dict = {}

    # 1. acquire ------------------------------------------------------------------
    try:
        if offline:
            new_schema, new_osm, new_fp = (latest_raw(SCHEMA_SOURCE), latest_raw("osm_overpass"),
                                           latest_raw(FP_SOURCE))
        else:
            new_schema = fetch_layer_schema()
            new_osm = Path(osm.fetch().path)
            new_fp = Path(arcgis.fetch_for_stores(osm.parse(new_osm)).path)
    except Exception as e:  # a source that cannot be fetched blocks publishing
        changes.append(Change("acquisition", "fetch_failed", "error", detail={"error": str(e)[:500]}))
        write_report(run_dir, run_id, "gated", changes, snapshots)
        if load:
            record_in_db(run_id, "gated", changes, snapshots)
        print(f"GATED: fetch failed; see {run_dir / 'report.md'}")
        return 2
    snapshots = {"schema": new_schema.name if new_schema else None,
                 "osm_overpass": new_osm.name, FP_SOURCE: new_fp.name}

    # 2. source checks ------------------------------------------------------------
    src_changes, new_stores, new_fp_data = check(new_schema, new_osm, new_fp)
    changes += src_changes

    # 3. staged candidate run ------------------------------------------------------
    candidate = run_dir / "candidate.csv"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_pipeline(out=candidate, inputs=(new_stores, new_fp_data))
    if PUBLISHED.exists():
        old_rows = list(csv.DictReader(PUBLISHED.open()))
        new_rows = list(csv.DictReader(candidate.open()))
        changes += diff_results(old_rows, new_rows)

    # 4. gate --------------------------------------------------------------------
    severity = worst(changes)
    if severity == "error":
        status, code = "gated", 2
    else:
        if PUBLISHED.exists():
            shutil.copy2(PUBLISHED, run_dir / "previous.csv")
        shutil.copy2(candidate, PUBLISHED)
        if new_schema:
            mark_good(SCHEMA_SOURCE, new_schema)
        mark_good("osm_overpass", new_osm)
        mark_good(FP_SOURCE, new_fp)
        status, code = ("published_with_warnings", 1) if severity == "warn" else ("published", 0)

    report = write_report(run_dir, run_id, status, changes, snapshots)
    if load:
        if status != "gated":
            from ..load import load as load_db
            load_db()
        record_in_db(run_id, status, changes, snapshots)
    print(f"{status.upper()}: {sum(c.severity == 'error' for c in changes)} errors, "
          f"{sum(c.severity == 'warn' for c in changes)} warnings, "
          f"{sum(c.severity == 'info' for c in changes)} info; report {report}")
    return code


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--load", action="store_true")
    a = ap.parse_args()
    sys.exit(refresh(offline=a.offline, load=a.load))
