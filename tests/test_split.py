"""Unit tests for split_leie_to_json.py.

Exercises the LEIE record layout, key strategy, state sharding, encoding,
manifest correctness, and the reinstatement contract (a party absent from the
current CSV is absent from the current manifest).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import split_leie_to_json as sp  # noqa: E402

HEADER = ",".join(sp.EXPECTED_FIELDS)

# A real-shape individual (has NPI), an entity (BUSNAME, NPI absent), and an
# individual with no NPI (must fall back to a content hash).
ROW_INDIVIDUAL = ("SMITH,JOHN,Q,,PHYSICIAN (MD/DO),INTERNAL MEDICINE,,"
                  "1972902351,19700101,1 MAIN ST,AUSTIN,TX,78701,1128a1,"
                  "20200115,00000000,00000000,")
ROW_ENTITY = (",,,ACME HOME HEALTH LLC,BUSINESS,HOME HEALTH AGENCY,,"
              "0000000000,00000000,5 OAK AVE,MIAMI,FL,33101,1128b8,"
              "20210310,00000000,00000000,")
ROW_NO_NPI = ("DOE,JANE,,,NURSE,NURSING,,0000000000,19850505,"
              "9 ELM RD,,,,1128a2,20190701,00000000,00000000,")


def _write_csv(path, rows):
    with open(path, "w", encoding=sp.CSV_ENCODING, newline="") as fh:
        fh.write(HEADER + "\n")
        for row in rows:
            fh.write(row + "\n")


def test_header_validation(tmp_path):
    csv_path = tmp_path / "bad.csv"
    with open(csv_path, "w", encoding="utf-8") as fh:
        fh.write("FOO,BAR\n1,2\n")
    with pytest.raises(ValueError, match="Unexpected LEIE header"):
        sp.split(str(csv_path), str(tmp_path / "out"))


def test_counts_and_manifest(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"
    _write_csv(csv_path, [ROW_INDIVIDUAL, ROW_ENTITY, ROW_NO_NPI])

    manifest = sp.split(str(csv_path), str(out_dir))

    assert manifest["record_count"] == 3
    assert manifest["key_count"] == 3
    # One JSON file per key, plus _manifest.json, across the state shards.
    written = []
    for root, _dirs, files in os.walk(out_dir):
        written += [f for f in files if f.endswith(".json")]
    assert len(written) == 3 + 1  # 3 records + manifest

    # Manifest key set == the split key set on disk.
    on_disk = set()
    for root, _dirs, files in os.walk(out_dir):
        shard = os.path.basename(root)
        for f in files:
            if f == "_manifest.json":
                continue
            on_disk.add(f"{shard}/{f[:-len('.json')]}")
    assert set(manifest["keys"]) == on_disk


def test_npi_key_when_present(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"
    _write_csv(csv_path, [ROW_INDIVIDUAL])
    sp.split(str(csv_path), str(out_dir))
    # Real NPI -> keyed by NPI, sharded under its state.
    assert (out_dir / "TX" / "1972902351.json").exists()


def test_hash_key_when_npi_absent(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"
    _write_csv(csv_path, [ROW_ENTITY])
    manifest = sp.split(str(csv_path), str(out_dir))
    # Entity has no NPI -> hash key (h-prefixed), sharded under FL.
    key = manifest["keys"][0]
    assert key.startswith("FL/h")


def test_missing_state_lands_in_xx(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"
    _write_csv(csv_path, [ROW_NO_NPI])  # STATE column blank
    manifest = sp.split(str(csv_path), str(out_dir))
    assert manifest["keys"][0].startswith("XX/")


def test_duplicate_npi_gets_distinct_keys(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"
    # Same entity excluded twice (same identity) -> two distinct files.
    _write_csv(csv_path, [ROW_INDIVIDUAL, ROW_INDIVIDUAL])
    manifest = sp.split(str(csv_path), str(out_dir))
    assert manifest["key_count"] == 2
    assert len(set(manifest["keys"])) == 2


def test_hash_is_deterministic(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    _write_csv(csv_path, [ROW_ENTITY])
    m1 = sp.split(str(csv_path), str(tmp_path / "o1"))
    m2 = sp.split(str(csv_path), str(tmp_path / "o2"))
    assert m1["keys"] == m2["keys"]


def test_cp1252_names_decode(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"
    # 0xF1 is 'ñ' in cp1252 -- a byte that is NOT valid standalone UTF-8.
    row = ("PE\xf1A,LU\xcdS,,,PHYSICIAN,FAMILY,,1234567893,19750202,"
           "2 RIO ST,EL PASO,TX,79901,1128a1,20220101,00000000,00000000,")
    _write_csv(csv_path, [row])
    sp.split(str(csv_path), str(out_dir))
    with open(out_dir / "TX" / "1234567893.json", encoding="utf-8") as fh:
        rec = json.load(fh)
    assert rec["LASTNAME"] == "PE\xf1A"


def test_reinstatement_drops_from_manifest(tmp_path):
    """A party present one month and gone the next must leave the manifest."""
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"

    # Month 1: two excluded parties.
    _write_csv(csv_path, [ROW_INDIVIDUAL, ROW_ENTITY])
    m1 = sp.split(str(csv_path), str(out_dir))
    assert m1["key_count"] == 2
    assert any(k.endswith("1972902351") for k in m1["keys"])

    # Month 2: the individual was reinstated -> removed from UPDATED.csv.
    _write_csv(csv_path, [ROW_ENTITY])
    m2 = sp.split(str(csv_path), str(out_dir))
    assert m2["key_count"] == 1
    assert not any(k.endswith("1972902351") for k in m2["keys"])
    # And the stale JSON is gone from the rebuilt local tree.
    assert not (out_dir / "TX" / "1972902351.json").exists()


def test_blank_trailing_lines_ignored(tmp_path):
    csv_path = tmp_path / "UPDATED.csv"
    out_dir = tmp_path / "out"
    with open(csv_path, "w", encoding=sp.CSV_ENCODING, newline="") as fh:
        fh.write(HEADER + "\n")
        fh.write(ROW_INDIVIDUAL + "\n")
        fh.write("\n")  # blank line
    manifest = sp.split(str(csv_path), str(out_dir))
    assert manifest["record_count"] == 1
