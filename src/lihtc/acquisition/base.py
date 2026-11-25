"""Raw-data storage shared by every acquisition adapter.

Each fetch is saved untouched as data/raw/<source_id>/<UTC timestamp>.json
alongside a manifest entry (URL, params, SHA-256, record count), so any
downstream value can be traced back to the exact payload it came from.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

RAW_ROOT = Path(__file__).resolve().parents[3] / "data" / "raw"
USER_AGENT = "california-lihtc-data/0.1 (research prototype)"


@dataclass
class RawSnapshot:
    source_id: str
    path: str
    url: str
    params: dict
    sha256: str
    record_count: int
    retrieved_at: str


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def request_with_retry(session: requests.Session, method: str, url: str,
                       retries: int = 3, backoff: float = 5.0, **kw) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = session.request(method, url, timeout=kw.pop("timeout", 180), **kw)
            if r.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} from {url}", response=r)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # network or retryable status
            last = e
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"{method} {url} failed after {retries} attempts") from last


def save_raw(source_id: str, payload: dict | list, url: str, params: dict,
             record_count: int, root: Path = RAW_ROOT) -> RawSnapshot:
    now = datetime.now(timezone.utc)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    out_dir = root / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now:%Y%m%dT%H%M%SZ}.json"
    path.write_bytes(body)
    snap = RawSnapshot(source_id, str(path), url, params,
                       hashlib.sha256(body).hexdigest(), record_count, now.isoformat())
    with (out_dir / "manifest.jsonl").open("a") as f:
        f.write(json.dumps(asdict(snap)) + "\n")
    return snap


def latest_raw(source_id: str, root: Path = RAW_ROOT) -> Path | None:
    files = sorted((root / source_id).glob("*.json"))
    return files[-1] if files else None


# --- last-known-good pointer ---------------------------------------------------
# A refresh writes new snapshots first and checks them second. Downstream code
# reads the LAST_GOOD snapshot, so a snapshot that failed its checks is kept on
# disk for inspection but never feeds the published dataset.

def last_good(source_id: str, root: Path = RAW_ROOT) -> Path | None:
    ptr = root / source_id / "LAST_GOOD"
    if ptr.exists():
        p = root / source_id / ptr.read_text().strip()
        if p.exists():
            return p
    return latest_raw(source_id, root)  # bootstrap: before any refresh has run


def mark_good(source_id: str, snapshot: str | Path, root: Path = RAW_ROOT) -> None:
    (root / source_id / "LAST_GOOD").write_text(Path(snapshot).name + "\n")
