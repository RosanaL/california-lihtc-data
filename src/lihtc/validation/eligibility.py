"""Store-level TCAC grocery-amenity classification.

Decides which store class (if any) a store can count as, and how sure we are.
It does NOT score a project: distance tiers, barriers and the 15-point cap
need a project site and are applied downstream.

Status values
  eligible     every requirement backed by authoritative/strong evidence
  likely       requirements met only by proxy evidence (e.g. footprint area)
  ineligible   a requirement is affirmatively failed with strong evidence
  unverified   evidence is missing or contradictory; needs human review
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parents[3] / "configs" / "eligibility_rules.yaml"


def load_rules(path: Path = RULES_PATH) -> dict:
    return yaml.safe_load(path.read_text())


@dataclass
class AreaEvidence:
    sqft: float
    source_type: str          # key from area_evidence_strength
    measurement_type: str     # gross_interior | leased | footprint | grocery_section


@dataclass
class StoreFacts:
    store_id: str
    is_multipurpose: bool = False
    # None = unknown; True/False = established by evidence
    sells_staples: bool | None = None
    sells_fresh_meat: bool | None = None
    sells_fresh_produce: bool | None = None
    area: list[AreaEvidence] = field(default_factory=list)
    operating: bool | None = None


@dataclass
class Classification:
    store_id: str
    store_class: str | None   # full_scale_supermarket | neighborhood_market | None
    status: str
    area_basis_sqft: float | None
    reasons: list[str]


def _strength(source_type: str, rules: dict) -> str:
    for level, types in rules["area_evidence_strength"].items():
        if source_type in types:
            return level
    return "proxy"


def _best_area(facts: StoreFacts, rules: dict) -> tuple[AreaEvidence | None, str | None, list[str]]:
    """Choose the area evidence to judge against, strongest first."""
    notes = []
    usable = facts.area
    if facts.is_multipurpose:
        # Regulation: only the documented grocery-section interior area counts.
        usable = [a for a in facts.area if a.measurement_type == "grocery_section"]
        if facts.area and not usable:
            notes.append("multipurpose store: only total-store area known, grocery section unmeasured")
    if not usable:
        return None, None, notes
    order = {"authoritative": 0, "strong": 1, "proxy": 2}
    ranked = sorted(usable, key=lambda a: order[_strength(a.source_type, rules)])
    best = ranked[0]
    best_level = _strength(best.source_type, rules)
    # conflict check among strong-or-better sources
    strong_vals = [a.sqft for a in usable if _strength(a.source_type, rules) != "proxy"]
    if len(strong_vals) > 1 and max(strong_vals) > 1.2 * min(strong_vals):
        notes.append(f"conflicting documented areas: {sorted(round(v) for v in strong_vals)}")
    return best, best_level, notes


def classify(facts: StoreFacts, rules: dict | None = None) -> Classification:
    rules = rules or load_rules()
    reasons: list[str] = []
    if facts.operating is False:
        return Classification(facts.store_id, None, "ineligible", None,
                              ["store not operating: amenity must be in place at application"])

    food = {"staples": facts.sells_staples, "fresh_meat": facts.sells_fresh_meat,
            "fresh_produce": facts.sells_fresh_produce}
    missing_food = [k for k, v in food.items() if v is False]
    unknown_food = [k for k, v in food.items() if v is None]
    if missing_food:
        return Classification(facts.store_id, None, "ineligible", None,
                              [f"does not sell required category: {', '.join(missing_food)}"])

    best, level, notes = _best_area(facts, rules)
    reasons += notes
    classes = rules["store_classes"]
    full_min = classes["full_scale_supermarket"]["min_gross_interior_sqft"]
    nbhd_min = classes["neighborhood_market"]["min_gross_interior_sqft"]

    if best is None:
        reasons.append("no usable area evidence")
        return Classification(facts.store_id, None, "unverified", None, reasons + _food_notes(unknown_food))

    sqft = best.sqft
    store_class = ("full_scale_supermarket" if sqft >= full_min
                   else "neighborhood_market" if sqft >= nbhd_min else None)
    if store_class is None:
        if level == "proxy":
            # A footprint can overstate area but almost never understates it much,
            # so a tiny footprint is decent evidence the store is too small.
            reasons.append(f"proxy area {sqft:,.0f} sqft below {nbhd_min:,} minimum")
            return Classification(facts.store_id, None, "ineligible" if sqft < 0.8 * nbhd_min else "unverified",
                                  sqft, reasons)
        reasons.append(f"documented area {sqft:,.0f} sqft below {nbhd_min:,} minimum")
        return Classification(facts.store_id, None, "ineligible", sqft, reasons)

    reasons.append(f"{best.measurement_type} area {sqft:,.0f} sqft from {best.source_type} ({level})")
    if best.measurement_type == "footprint":
        reasons.append("footprint is a proxy: includes walls, other tenants, back-of-house")
    if unknown_food:
        return Classification(facts.store_id, store_class, "unverified", sqft,
                              reasons + _food_notes(unknown_food))
    if any(n.startswith("conflicting") for n in notes):
        return Classification(facts.store_id, store_class, "unverified", sqft, reasons)
    status = "likely" if level == "proxy" else "eligible"
    return Classification(facts.store_id, store_class, status, sqft, reasons)


def _food_notes(unknown: list[str]) -> list[str]:
    return [f"unconfirmed food category: {', '.join(unknown)}"] if unknown else []
