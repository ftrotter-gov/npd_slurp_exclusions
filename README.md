# NPD Slurp Exclusions

Download and process public healthcare **provider-exclusion** data for the National Provider Directory (NPD).

## Overview

This project mirrors the HHS-OIG **LEIE** (List of Excluded Individuals/Entities) — the authoritative
federal list of providers excluded from participating in Medicare, Medicaid, and all other federal
health care programs — and splits it into one JSON document per excluded party for downstream entity
resolution.

It is a small, resumable two-step pipeline:

- **`download_leie.py`** — conditional GET (`If-None-Match` / `If-Modified-Since`) of the OIG
  **full** database file `UPDATED.csv`, into a local cache. The prior run's ETag/Last-Modified is
  stored in a sidecar so an unchanged monthly file returns HTTP 304 and is not re-downloaded.
- **`split_leie_to_json.py`** — explode `UPDATED.csv` into `<STATE>/<key>.json`, one file per
  excluded party, plus a `_manifest.json` listing every key in the current snapshot.

### Why the full file only (no supplements)

OIG publishes the full `UPDATED.csv` and separate monthly *supplement* delta files. The full file is
a complete monthly replacement that **already incorporates new exclusions and removes reinstated
parties** — OIG explicitly warns against applying the supplements on top of it. This pipeline
therefore consumes the full file exclusively.

### Reinstatement correctness (`_manifest.json`)

Because OIG *removes* reinstated parties from each month's file, a party can disappear between
snapshots. The split step rebuilds the output tree from scratch each run and emits
`_manifest.json` — the exact set of keys present in the current snapshot. Downstream consumers should
treat the raw (versioned) `UPDATED.csv` as the source of truth and use the manifest to filter the
split set to the current snapshot, so a reinstated party's stale JSON in an additive-only destination
is ignored.

## Record layout

The LEIE record layout (per the OIG
[record-layout reference](https://oig.hhs.gov/exclusions/files/leie_record_layout.pdf)):

```
LASTNAME, FIRSTNAME, MIDNAME, BUSNAME, GENERAL, SPECIALTY, UPIN, NPI, DOB,
ADDRESS, CITY, STATE, ZIP, EXCLTYPE, EXCLDATE, REINDATE, WAIVERDATE, WVRSTATE
```

Individuals populate the name fields; entities populate `BUSNAME`. `NPI` is `0000000000` when
absent. `REINDATE` is `00000000` for active exclusions. **The file contains no SSN**, so it is
cleanly public.

### Key strategy

Each excluded party gets a stable, collision-free key:

- **`NPI`** when it is present and real (not `0000000000`).
- Otherwise a deterministic 16-hex content hash of the identity fields — `BUSNAME` for entities,
  `LASTNAME|FIRSTNAME|MIDNAME|DOB` for individuals — salted with `EXCLDATE`. A numeric suffix
  disambiguates the rare exact collision.

Files are sharded by two-letter `STATE`; parties with no valid state land under `XX/`.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Download the full LEIE file (skips download if unchanged since last run):
python download_leie.py --cache-dir ./leie_raw_data_cache

# Split it into per-party JSON + a manifest:
python split_leie_to_json.py \
    --csv-path ./leie_raw_data_cache/UPDATED.csv \
    --json-dir ./leie_split_data
```

The downloaded CSV and the split JSON tree are **fetched/derived data, not repository content**, and
are `.gitignore`d — in production they live in the pipeline's versioned S3 buckets.

## Tests

```bash
python -m pytest tests/ -q
```

## Scope

v1 covers **OIG LEIE only**. The pipeline is structured so additional exclusion sources (e.g. state
Medicaid exclusion lists) can be added as sibling modules later — LEIE is the authoritative federal
core but is not a superset of the state lists.

## Policies

### Open Source Policy

We adhere to the [CMS Open Source Policy](https://github.com/CMSGov/cms-open-source-policy). If you have any questions, just [shoot us an email](mailto:opensource@cms.hhs.gov).

### Security and Responsible Disclosure Policy

_Submit a vulnerability:_ Vulnerability reports can be submitted through [Bugcrowd](https://bugcrowd.com/cms-vdp). Reports may be submitted anonymously. If you share contact information, we will acknowledge receipt of your report within 3 business days.

### Software Bill of Materials (SBOM)

A Software Bill of Materials (SBOM) is a formal record containing the details and supply chain relationships of various components used in building software.

In the spirit of [Executive Order 14028 - Improving the Nation's Cyber Security](https://www.gsa.gov/technology/it-contract-vehicles-and-purchasing-programs/information-technology-category/it-security/executive-order-14028), a SBOM for this repository is provided here: https://github.com/ftrotter-gov/npd_slurp_exclusions/network/dependencies.

For more information and resources about SBOMs, visit: https://www.cisa.gov/sbom.

## Public domain

This project is in the public domain within the United States, and copyright and related rights in the work worldwide are waived through the [CC0 1.0 Universal public domain dedication](https://creativecommons.org/publicdomain/zero/1.0/) as indicated in [LICENSE](LICENSE).

All contributions to this project will be released under the CC0 dedication. By submitting a pull request or issue, you are agreeing to comply with this waiver of copyright interest.
