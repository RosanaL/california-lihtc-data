"""Evaluate the pipeline against the hand-verified benchmark.

Usage:  PYTHONPATH=src python evaluation/evaluate.py
Writes evaluation/results.md and prints the same summary.

Inputs
  evaluation/benchmark.csv              hand-verified truth (25 stores, stratified)
  data/processed/oc_grocery_candidates.csv   pipeline output (proxy area, flags)
"""
from __future__ import annotations

import csv
import statistics as st
from pathlib import Path

from lihtc.pipeline import FULL_LINE_BRANDS
from lihtc.validation.eligibility import AreaEvidence, StoreFacts, classify, load_rules

ROOT = Path(__file__).resolve().parents[1]
USABLE_CONF = {"high", "medium"}   # "low" = secondary citation only: excluded from error stats


def _b(v: str) -> bool | None:
    return None if v == "" else v == "1"


def store_class(sqft: float | None, rules: dict) -> str:
    if sqft is None:
        return "unknown"
    c = rules["store_classes"]
    if sqft >= c["full_scale_supermarket"]["min_gross_interior_sqft"]:
        return "full_scale"
    if sqft >= c["neighborhood_market"]["min_gross_interior_sqft"]:
        return "neighborhood"
    return "too_small"


def facts_for(b: dict, p: dict, staples_from_market_permit: bool) -> StoreFacts:
    area = []
    if b["truth_area_sqft"] and b["truth_area_confidence"] in USABLE_CONF:
        area.append(AreaEvidence(float(b["truth_area_sqft"]), "leasing_brochure_tenant_sf", "leased"))
    elif p["footprint_sqft"]:
        area.append(AreaEvidence(float(p["footprint_sqft"]),
                                 "building_footprint_multi_tenant" if p["footprint_flags"]
                                 else "building_footprint_single_tenant", "footprint"))
    matched = b["permit_matched"] == "1"
    # A Meat sub-permit proves a fresh-meat department. Its absence proves nothing,
    # except a permit the regulator itself names "PREPACKAGED FOOD ONLY".
    meat = True if b["fresh_meat_permit"] == "1" else (False if b["prepackaged_only"] == "1" else None)
    produce = True if b["produce_named"] == "1" else None
    staples = True if (staples_from_market_permit and matched) else None
    return StoreFacts(b["osm_id"], is_multipurpose=p["is_multipurpose"] == "True",
                      sells_staples=staples, sells_fresh_meat=meat, sells_fresh_produce=produce,
                      area=area)


def main() -> str:
    rules = load_rules()
    bench = list(csv.DictReader((ROOT / "evaluation" / "benchmark.csv").open()))
    proxy = {r["osm_id"]: r for r in csv.DictReader((ROOT / "data/processed/oc_grocery_candidates.csv").open())}
    n = len(bench)
    out: list[str] = ["# Benchmark evaluation", "",
                      f"{n} Orange County stores, stratified by footprint flag (see benchmark_selection.csv).", ""]

    # 1. Verification rate -------------------------------------------------
    matched = sum(b["permit_matched"] == "1" for b in bench)
    meat = sum(b["fresh_meat_permit"] == "1" for b in bench)
    produce = sum(b["produce_named"] == "1" for b in bench)
    conf = {c: sum(b["truth_area_confidence"] == c for b in bench) for c in ("high", "medium", "low")}
    out += ["## Verification rate", "",
            "| Evidence | Stores | Share |", "| --- | --- | --- |",
            f"| Health permit matched | {matched} | {matched / n:.0%} |",
            f"| Fresh-meat sub-permit | {meat} | {meat / n:.0%} |",
            f"| Produce named in a permit | {produce} | {produce / n:.0%} |",
            f"| Documented area, high confidence | {conf['high']} | {conf['high'] / n:.0%} |",
            f"| Documented area, medium confidence | {conf['medium']} | {conf['medium'] / n:.0%} |",
            f"| Documented area, low confidence (excluded) | {conf['low']} | {conf['low'] / n:.0%} |", ""]

    # 2. Area proxy error ----------------------------------------------------
    rows = []
    for b in bench:
        p = proxy[b["osm_id"]]
        if b["truth_area_sqft"] and b["truth_area_confidence"] in USABLE_CONF and p["footprint_sqft"]:
            t, e = float(b["truth_area_sqft"]), float(p["footprint_sqft"])
            rows.append((b, p, t, e, (e - t) / t))
    out += ["## Area proxy vs documented area", "",
            "| Store | Proxy method | Flags | Proxy sqft | Documented sqft | Error | Class (proxy / truth) |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    agree = 0
    for b, p, t, e, err in sorted(rows, key=lambda r: abs(r[4])):
        pc, tc = store_class(e, rules), store_class(t, rules)
        agree += pc == tc
        out.append(f"| {b['permit_facility']} | {p['footprint_method']} | {p['footprint_flags'] or 'none'} | "
                   f"{e:,.0f} | {t:,.0f} | {err:+.0%} | {pc} / {tc}{'' if pc == tc else ' **x**'} |")
    by_method: dict[str, list[float]] = {}
    for _, p, _, _, err in rows:
        by_method.setdefault(p["footprint_method"], []).append(abs(err))
    out += ["", f"Class agreement: {agree} of {len(rows)}.", "",
            "Absolute error by method (min / median / max): " + "; ".join(
                f"{m} {min(v):.0%} / {st.median(v):.0%} / {max(v):.0%} (n={len(v)})"
                for m, v in sorted(by_method.items())) + ".",
            "", "n is small; treat these as directional, not as rates. Documented figures are "
            "leased square feet from owner or broker rosters, used as a stand-in for gross interior area.", ""]

    # 3. Brand prior vs permits ------------------------------------------------
    prior = [b for b in bench if proxy[b["osm_id"]]["brand"] in FULL_LINE_BRANDS and b["permit_matched"] == "1"]
    prior_meat = sum(b["fresh_meat_permit"] == "1" for b in prior)
    out += ["## Brand prior check", "",
            f"Of {len(prior)} stores whose brand the pipeline treats as a full-line grocer, "
            f"{prior_meat} hold a fresh-meat sub-permit. The prior is a reasonable search hint, "
            "not evidence.", ""]

    # 4. Eligibility with benchmark evidence ---------------------------------------
    out += ["## Eligibility with benchmark evidence", "",
            "| Scenario | eligible | likely | unverified | ineligible |", "| --- | --- | --- | --- | --- |"]
    detail = {}
    for label, assume in (("Strict: staples need direct evidence", False),
                          ("Assume any matched retail-food MARKET permit sells staples", True)):
        res = [classify(facts_for(b, proxy[b["osm_id"]], assume), rules) for b in bench]
        cnt = {s: sum(r.status == s for r in res) for s in ("eligible", "likely", "unverified", "ineligible")}
        out.append(f"| {label} | {cnt['eligible']} | {cnt['likely']} | {cnt['unverified']} | {cnt['ineligible']} |")
        detail[label] = list(zip(bench, res))
    out += ["", "Under the assumption, the stores that reach a firm status:", ""]
    for b, r in detail["Assume any matched retail-food MARKET permit sells staples"]:
        if r.status in ("eligible", "ineligible", "likely"):
            out.append(f"- **{b['permit_facility'] or b['name']}**: {r.status}"
                       f"{' (' + r.store_class + ')' if r.store_class else ''}; {r.reasons[-1]}")
    out.append("")

    text = "\n".join(out)
    (ROOT / "evaluation" / "results.md").write_text(text)
    return text


if __name__ == "__main__":
    print(main())
