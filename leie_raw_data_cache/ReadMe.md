# leie_raw_data_cache/

`download_leie.py` writes the HHS-OIG LEIE full database (`UPDATED.csv`) and its
download-metadata sidecar (`.leie_meta.json`) here.

This directory is intentionally kept empty in version control (only this ReadMe
is tracked). The downloaded file is **fetched source data, not repository
content**, and is `.gitignore`d — it lives in the pipeline's S3 raw bucket, which
is the versioned source of truth.
