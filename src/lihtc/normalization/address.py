"""Address and store-name normalization for cross-source matching."""
from __future__ import annotations

import re

SUFFIXES = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave", "av": "ave",
    "boulevard": "blvd", "blvd": "blvd", "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr", "parkway": "pkwy", "pkwy": "pkwy", "pky": "pkwy",
    "highway": "hwy", "hwy": "hwy", "lane": "ln", "ln": "ln", "way": "way",
    "court": "ct", "ct": "ct", "circle": "cir", "cir": "cir", "place": "pl",
    "pl": "pl", "center": "ctr", "ctr": "ctr", "plaza": "plz", "plz": "plz",
    "terrace": "ter", "trail": "trl", "square": "sq", "camino": "camino",
}
DIRECTIONS = {"north": "n", "south": "s", "east": "e", "west": "w",
              "n": "n", "s": "s", "e": "e", "w": "w"}
UNIT_RE = re.compile(r"\b(suite|ste|unit|bldg|building|#|space|spc)\b\.?\s*[\w-]*", re.I)

# Words that carry no identity in store names ("RALPHS GROCERY #78" -> "ralphs")
NAME_NOISE = {"grocery", "groceries", "market", "supermarket", "store", "inc", "llc",
              "co", "company", "the", "food", "foods", "and", "of"}
BRAND_ALIASES = {
    "stater bros": "stater bros", "stater brothers": "stater bros",
    "sprouts farmer market": "sprouts", "sprouts farmers market": "sprouts",
    "trader joes": "trader joes", "trader joe s": "trader joes",
    "whole foods market": "whole foods", "wholefoods": "whole foods",
}


def normalize_street(street: str | None) -> str:
    if not street:
        return ""
    s = UNIT_RE.sub(" ", street.lower())
    s = re.sub(r"[^\w\s]", " ", s)
    toks = [DIRECTIONS.get(t, SUFFIXES.get(t, t)) for t in s.split()]
    # drop trailing unit designators after the street suffix: "campus dr 166a", "el toro rd a"
    suffix_vals = set(SUFFIXES.values())
    last_suffix = max((i for i, t in enumerate(toks) if t in suffix_vals), default=None)
    if last_suffix is not None:
        toks = toks[:last_suffix + 1]
    return " ".join(toks)


def address_agreement(a: str, b: str, max_number_gap: int = 200) -> str:
    """Compare two normalized addresses.

    exact        same number and street
    same_street  same street, numbers within max_number_gap (typical of one
                 shopping-center building carrying a single site address)
    different    otherwise; unknown if either side is empty or unnumbered
    """
    if not a or not b:
        return "unknown"
    na, _, sa = a.partition(" ")
    nb, _, sb = b.partition(" ")
    if not (na.isdigit() and nb.isdigit()) or int(na) == 0 or int(nb) == 0:
        return "unknown"
    if sa != sb:
        return "different"
    if na == nb:
        return "exact"
    return "same_street" if abs(int(na) - int(nb)) <= max_number_gap else "different"


def normalize_address(housenumber: str | None, street: str | None) -> str:
    hn = re.sub(r"[^\d]", "", (housenumber or "").split("-")[0])
    return f"{hn} {normalize_street(street)}".strip()


def split_address_line(line: str | None) -> tuple[str, str]:
    """'31481 Santa Margarita Pkwy' -> ('31481', 'Santa Margarita Pkwy')."""
    m = re.match(r"\s*(\d+)[A-Za-z-]*\s+(.*)", line or "")
    return (m.group(1), m.group(2)) if m else ("", line or "")


def store_number(name: str | None) -> str | None:
    m = re.search(r"#\s*(\d+)", name or "")
    return m.group(1) if m else None


def normalize_name(name: str | None) -> str:
    s = (name or "").lower().replace("’", "'").replace("'", "")
    s = re.sub(r"#\s*\d+", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = " ".join(s.split())
    for k, v in BRAND_ALIASES.items():
        if s.startswith(k):
            return v
    toks = [t for t in s.split() if t not in NAME_NOISE]
    return " ".join(toks) or s
