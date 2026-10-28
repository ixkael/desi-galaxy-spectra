"""Status table, index.csv and the cross-batch uniqueness check."""
import json

from desi_galaxy_spectra.driver import run_batch
from desi_galaxy_spectra.index import build_index, check_unique
from desi_galaxy_spectra.status import status_table, write_status

quiet = dict(log=lambda m: None)


def test_status_table_and_files(settings_tmp, batch_factory):
    a = batch_factory("910", [71, 72], tid_base=100000)
    b = batch_factory("911", [73], tid_base=200000, shards=False)
    c = batch_factory("912", [74], raw=False, shards=False)
    run_batch(settings_tmp, a, **quiet)
    _, out_b = (None, settings_tmp.batch_root / "batch_911")
    out_b.mkdir(parents=True)
    (out_b / "CLAIM").write_text(json.dumps({"user": "alice"}))
    rows = status_table(settings_tmp, ids=[a, b, c])
    by = {r["batch"]: r for r in rows}
    assert by[a]["stage"] == "verified" and by[a]["n_pixels"] == 2 and by[a]["n_clean"] == 28
    assert by[a]["bytes"] > 0 and by[a]["claimed_by"] == ""
    assert by[b]["stage"] == "downloaded" and by[b]["claimed_by"] == "alice"
    assert by[c]["stage"] == "queued" and by[c]["n_clean"] == 0
    tsv, md = write_status(settings_tmp, ids=[a, b, c])
    assert tsv == settings_tmp.batch_root.parent / "status.tsv" and md.name == "status.md"
    lines = tsv.read_text().splitlines()
    assert lines[0].startswith("# stage check: size-only") and "deep=True" in lines[0]
    assert lines[1].split("\t") == ["batch", "stage", "claimed_by", "n_pixels", "n_clean", "bytes"]
    assert len(lines) == 5 and "alice" in md.read_text()
    assert "not re-hashed" in md.read_text()


def test_status_tsv_states_a_deep_check(settings_tmp, batch_factory):
    a = batch_factory("913", [75], tid_base=150000)
    run_batch(settings_tmp, a, **quiet)
    tsv, md = write_status(settings_tmp, ids=[a], deep=True)
    assert tsv.read_text().splitlines()[0].startswith("# stage check: md5 recheck")
    assert "re-hashed" in md.read_text() and "not re-hashed" not in md.read_text()


def test_check_unique_flags_cross_batch_duplicate(settings_tmp, batch_factory):
    a = batch_factory("920", [81], tid_base=300000)
    b = batch_factory("921", [82], tid_base=400000)
    run_batch(settings_tmp, a, **quiet)
    run_batch(settings_tmp, b, **quiet)
    assert check_unique(settings_tmp) == []
    c = batch_factory("922", [83], tid_base=300000)       # same targetids and programmes as batch 920
    run_batch(settings_tmp, c, **quiet)
    dup = check_unique(settings_tmp)
    assert dup and any("300000" in m for m in dup)
    assert any("batch_920" in m and "batch_922" in m for m in dup)


def test_index_and_unique_cover_only_verified_batches(settings_tmp, batch_factory):
    good = batch_factory("940", [101], tid_base=600000)
    failed = batch_factory("941", [102], tid_base=700000)
    noverify = batch_factory("942", [103], tid_base=800000)
    dup = batch_factory("943", [104], tid_base=600000)      # duplicates batch 940, but FAILed
    for b in (good, failed, noverify, dup):
        run_batch(settings_tmp, b, **quiet)
    out = settings_tmp.batch_root
    (out / "batch_941" / "VERIFY.md").write_text("RESULT: FAIL\n")
    (out / "batch_942" / "VERIFY.md").unlink()
    (out / "batch_943" / "VERIFY.md").write_text("RESULT: FAIL\n")
    for b in (failed, noverify, dup):
        assert (out / f"batch_{b}" / "MD5SUMS.txt").is_file()
    rows = [r.split(",") for r in build_index(settings_tmp).read_text().splitlines()[1:]]
    assert rows and {r[0] for r in rows} == {"940"}
    assert check_unique(settings_tmp) == []                  # the duplicate sits in an unverified batch


def test_index_columns_match_md5sums(settings_tmp, batch_factory):
    a = batch_factory("930", [91], tid_base=500000)
    run_batch(settings_tmp, a, **quiet)
    rows = [r.split(",") for r in build_index(settings_tmp).read_text().splitlines()[1:]]
    sums = {ln.split("  ")[1]: ln.split("  ")[0]
            for ln in (settings_tmp.batch_root / "batch_930" / "MD5SUMS.txt").read_text().splitlines()}
    assert rows and all(r[0] == "930" and sums[r[1]] == r[6] for r in rows)
    assert sum(int(r[2]) + int(r[3]) + int(r[4]) for r in rows) == 14
