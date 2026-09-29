#!/usr/bin/env python3
"""Split the HHS-OIG LEIE full database CSV into one JSON document per excluded party.

Output layout mirrors the other npd_slurp_* splitters:

    <json-dir>/<STATE>/<key>.json     one excluded party per file
    <json-dir>/_manifest.json         index of every key in THIS snapshot

Key strategy
------------
Each excluded party needs a stable, collision-free key:

  * NPI when it is present and real (LEIE stores ``0000000000`` when absent).
  * Otherwise a deterministic content hash of the identity fields -- BUSNAME for
    entities, LASTNAME|FIRSTNAME|MIDNAME|DOB for individuals -- salted with
    EXCLDATE so two same-named parties excluded on different dates stay distinct.
    A short suffix disambiguates the rare exact-collision.

Why the manifest matters (reinstatement correctness)
----------------------------------------------------
OIG replaces UPDATED.csv in full every month and *removes* reinstated parties.
The downstream cross-account sync is additive-only, so a reinstated party's JSON
can linger in the destination after it has left the source. ``_manifest.json``
lists exactly the keys present in the current snapshot, so downstream consumers
can filter to the authoritative current set and ignore stale stragglers.

Usage:
    python split_leie_to_json.py --csv-path ./leie_raw_data_cache/UPDATED.csv \
                                 --json-dir ./leie_split_data
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone

# The OIG record layout, in file order. Used to validate the header we get.
EXPECTED_FIELDS = [
    "LASTNAME", "FIRSTNAME", "MIDNAME", "BUSNAME", "GENERAL", "SPECIALTY",
    "UPIN", "NPI", "DOB", "ADDRESS", "CITY", "STATE", "ZIP", "EXCLTYPE",
    "EXCLDATE", "REINDATE", "WAIVERDATE", "WVRSTATE",
]

# LEIE stores this literal when an NPI is not on record.
NPI_ABSENT = "0000000000"

# Names carry Windows-1252 bytes (curly quotes, accented letters). cp1252 is a
# superset of latin-1 and decodes every byte, so it never raises.
CSV_ENCODING = "cp1252"


def _clean(value: str) -> str:
    return (value or "").strip()


def _valid_npi(npi: str) -> bool:
    npi = _clean(npi)
    return len(npi) == 10 and npi.isdigit() and npi != NPI_ABSENT


def _identity_hash(row: dict) -> str:
    """Deterministic 16-hex-char digest of a party's identity fields."""
    busname = _clean(row.get("BUSNAME"))
    if busname:
        basis = f"E|{busname}|{_clean(row.get('EXCLDATE'))}"
    else:
        basis = "|".join([
            "I",
            _clean(row.get("LASTNAME")),
            _clean(row.get("FIRSTNAME")),
            _clean(row.get("MIDNAME")),
            _clean(row.get("DOB")),
            _clean(row.get("EXCLDATE")),
        ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _shard(row: dict) -> str:
    """State sub-directory; parties with no state land in XX/."""
    state = _clean(row.get("STATE")).upper()
    # Keep it a safe single path segment: letters only, else the catch-all.
    if len(state) == 2 and state.isalpha():
        return state
    return "XX"


def _record_key(row: dict, seen: set) -> str:
    """A collision-free key for this row within the current run."""
    npi = _clean(row.get("NPI"))
    if _valid_npi(npi):
        base = npi
    else:
        base = f"h{_identity_hash(row)}"

    key = base
    # Two rows can share an NPI (e.g. an entity excluded twice) or a hash basis;
    # suffix to keep every file distinct.
    suffix = 1
    while key in seen:
        suffix += 1
        key = f"{base}-{suffix}"
    seen.add(key)
    return key


def split(csv_path: str, json_dir: str) -> dict:
    """Explode the LEIE CSV into per-party JSON files + a manifest."""
    if os.path.isdir(json_dir):
        # Rebuild from scratch each run so the local tree matches the snapshot;
        # s3_push.py --delete then prunes reinstated parties in S3.
        shutil.rmtree(json_dir)
    os.makedirs(json_dir, exist_ok=True)

    seen: set = set()
    keys: list = []
    row_count = 0

    with open(csv_path, newline="", encoding=CSV_ENCODING) as fh:
        reader = csv.DictReader(fh)
        header = [h.strip().upper() for h in (reader.fieldnames or [])]
        if header != EXPECTED_FIELDS:
            raise ValueError(
                "Unexpected LEIE header.\n"
                f"  expected: {EXPECTED_FIELDS}\n"
                f"  got:      {header}"
            )

        for row in reader:
            # DictReader with a bad line count can yield None values; normalize.
            row = {k: _clean(v) for k, v in row.items() if k}
            if not any(row.values()):
                continue  # skip fully blank trailing lines
            row_count += 1

            key = _record_key(row, seen)
            shard = _shard(row)
            rel_key = f"{shard}/{key}"

            record = dict(row)
            record["_key"] = key
            record["_npi_present"] = _valid_npi(row.get("NPI", ""))

            out_dir = os.path.join(json_dir, shard)
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, f"{key}.json"), "w",
                      encoding="utf-8") as out:
                json.dump(record, out, ensure_ascii=False, sort_keys=True,
                          indent=2)
            keys.append(rel_key)

    manifest = {
        "source": "hhs-oig-leie",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": row_count,
        "key_count": len(keys),
        "keys": sorted(keys),
    }
    with open(os.path.join(json_dir, "_manifest.json"), "w",
              encoding="utf-8") as out:
        json.dump(manifest, out, ensure_ascii=False, indent=2)

    print(f"[leie] wrote {len(keys):,} JSON records "
          f"from {row_count:,} rows -> {json_dir}")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Split the LEIE full CSV into per-party JSON documents.")
    ap.add_argument("--csv-path", required=True,
                    help="Path to UPDATED.csv (from download_leie.py).")
    ap.add_argument("--json-dir", required=True,
                    help="Output directory for the split JSON tree.")
    args = ap.parse_args(argv)

    try:
        split(args.csv_path, args.json_dir)
    except (OSError, ValueError) as err:
        print(f"[leie] split failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
