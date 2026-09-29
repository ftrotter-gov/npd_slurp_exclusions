#!/usr/bin/env python3
"""Download the HHS-OIG LEIE (List of Excluded Individuals/Entities) full database.

The LEIE "Updated" file is the complete, authoritative snapshot of every exclusion
currently in effect. OIG replaces it in full every month and *removes* reinstated
parties from it, so the single full file is sufficient on its own -- the monthly
"supplement" (exclusion/reinstatement delta) files are deliberately NOT fetched
(OIG warns against applying both).

The download is a conditional GET: the source ETag / Last-Modified from the prior
run is stored in a sidecar meta file, so an unchanged monthly file is skipped
(HTTP 304) instead of re-downloaded -- the same "only real changes" contract the
rest of the pipeline keeps against S3.

Usage:
    python download_leie.py --cache-dir ./leie_raw_data_cache
    python download_leie.py --cache-dir /work/raw --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

# The full, current LEIE database (CSV). Stable URL, replaced monthly by OIG.
DEFAULT_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"

CSV_NAME = "UPDATED.csv"
META_NAME = ".leie_meta.json"

# A browser-ish UA; some CMS/OIG edges 403 the default python-requests UA.
USER_AGENT = "npd_slurp_exclusions/1.0 (+https://github.com/ftrotter-gov/npd_slurp_exclusions)"


def _load_meta(cache_dir: str) -> dict:
    path = os.path.join(cache_dir, META_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def _save_meta(cache_dir: str, meta: dict) -> None:
    path = os.path.join(cache_dir, META_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def download(cache_dir: str, url: str = DEFAULT_URL, force: bool = False,
            timeout: int = 120) -> dict:
    """Fetch UPDATED.csv into cache_dir if it changed. Returns a result dict."""
    os.makedirs(cache_dir, exist_ok=True)
    csv_path = os.path.join(cache_dir, CSV_NAME)
    meta = {} if force else _load_meta(cache_dir)

    headers = {"User-Agent": USER_AGENT}
    # Only send validators if we still hold the file they describe.
    if os.path.exists(csv_path) and not force:
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

    with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
        if resp.status_code == 304 and os.path.exists(csv_path):
            print(f"[leie] unchanged (HTTP 304) -> keeping {csv_path}")
            return {"changed": False, "status": 304, "csv_path": csv_path,
                    "bytes": os.path.getsize(csv_path)}
        resp.raise_for_status()

        tmp = csv_path + ".tmp"
        total = 0
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
        os.replace(tmp, csv_path)

        new_meta = {
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "content_length": resp.headers.get("Content-Length"),
            "url": url,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "bytes": total,
        }
        _save_meta(cache_dir, new_meta)
        print(f"[leie] downloaded {total:,} bytes -> {csv_path}")
        return {"changed": True, "status": resp.status_code, "csv_path": csv_path,
                "bytes": total}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Download the HHS-OIG LEIE full database CSV.")
    ap.add_argument("--cache-dir", default="./leie_raw_data_cache",
                    help="Directory to store UPDATED.csv and the download meta.")
    ap.add_argument("--url", default=DEFAULT_URL, help="Override the source URL.")
    ap.add_argument("--force", action="store_true",
                    help="Ignore stored validators and re-download unconditionally.")
    args = ap.parse_args(argv)

    try:
        download(args.cache_dir, url=args.url, force=args.force)
    except requests.RequestException as err:
        print(f"[leie] download failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
