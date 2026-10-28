"""Fix session 2026-10-23: B2 (per-shard table), S1 (failed pixel stops a parallel run), S7."""
import json
import os
import time
from pathlib import Path

import h5py
import numpy as np
import pytest

from conftest import OBJS_A, OBJS_B, make_shard
from desi_galaxy_spectra import cli, manifest, package, verify

F01 = "desi_dr1_clean_galaxies_z0.10-0.20.h5"
OBJS_C = [("ELG", 400, "dark", 0.15, True), ("LRG", 410, "dark", 0.15, True)]     # no BGS


def _pkg(shard_dir, tmp_path, name="chunks"):
    out = tmp_path / name
    package.package(shard_dir, out, log=lambda m: None)
    manifest.write_manifest(out, tmp_path / (name + ".json"), log=lambda m: None)
    return out, tmp_path / (name + ".json")


# ------------------------------------------------------------------------------------- B2
def test_table_has_one_row_per_shard_in_every_chunk_file(shard_dir, tmp_path):
    out, _ = _pkg(shard_dir, tmp_path)
    files = sorted(out.glob("*.h5"))
    assert files
    for p in files:
        with h5py.File(p) as f:
            assert f["source_shard_table/healpix"][()].tolist() == [11, 12]
            assert len(f.attrs["source_shards"]) == 2


def test_table_values_equal_shard_attrs_and_chunk_keeps_only_common(shard_dir, tmp_path):
    out, _ = _pkg(shard_dir, tmp_path)
    with h5py.File(out / F01) as f, h5py.File(shard_dir / "healpix_11.h5") as s11, \
            h5py.File(shard_dir / "healpix_12.h5") as s12:
        t = f["source_shard_table"]
        for c in ("BGS", "ELG", "LRG"):
            for k in s11[c].attrs:
                col = t[c][k][()]
                assert col.dtype == np.asarray(s11[c].attrs[k]).dtype or col.dtype.kind == "O"
                assert t[c][k + "__valid"][()].all()
            assert t[c]["n_objects"][()].tolist() == [s11[c].attrs["n_objects"],
                                                      s12[c].attrs["n_objects"]]
            assert t[c]["_present"][()].all()
        # BGS n_objects differs between the shards (3 vs 1): only in the table
        assert "n_objects" not in f["BGS"].attrs
        # galaxy_type is identical: kept at chunk level, with its value
        assert f["BGS"].attrs["galaxy_type"] == "DESI_DR1_BGS"
        # source_metadata: n_total_objects differs (6 vs 4), script_version identical
        assert "n_total_objects" not in f["source_metadata"].attrs
        assert f["source_metadata"].attrs["script_version"] == "4.0.0"
        assert t["source_metadata/n_total_objects"][()].tolist() == [6, 4]
        assert t["source_metadata/n_total_objects"].dtype == np.asarray(
            s11["metadata"].attrs["n_total_objects"]).dtype
        assert [x.decode() for x in t["source_metadata/creation_date"][()]] == \
            [s11["metadata"].attrs["creation_date"], s12["metadata"].attrs["creation_date"]]


def test_single_shard_chunk_attrs_unchanged(tmp_path):
    make_shard(tmp_path, 11, OBJS_A)
    out, _ = _pkg(tmp_path / "shards", tmp_path)
    with h5py.File(out / F01) as f, h5py.File(tmp_path / "shards" / "healpix_11.h5") as s:
        assert dict(f["BGS"].attrs) == dict(s["BGS"].attrs)
        assert dict(f["source_metadata"].attrs) == dict(s["metadata"].attrs)
        assert f["source_shard_table/healpix"].shape == (1,)


def test_table_marks_missing_class_and_missing_attribute(tmp_path):
    for pix, objs in ((11, OBJS_A), (12, OBJS_B), (13, OBJS_C)):
        make_shard(tmp_path, pix, objs)
    sd = tmp_path / "shards"
    with h5py.File(sd / "healpix_11.h5", "r+") as f:           # median_snr only in shard 11
        f["ELG"].attrs["median_snr"] = 7.5
    out, man = _pkg(sd, tmp_path)
    with h5py.File(out / F01) as f:
        t = f["source_shard_table"]
        assert t["BGS/_present"][()].tolist() == [True, True, False]
        assert t["ELG/_present"][()].tolist() == [True, True, True]
        assert t["BGS/n_objects__valid"][()].tolist() == [True, True, False]
        assert t["BGS/n_objects"][()].tolist() == [3, 1, 0]                 # fill, ignore
        assert t["ELG/median_snr__valid"][()].tolist() == [True, False, False]
        assert t["ELG/median_snr"][()][0] == 7.5 and np.isnan(t["ELG/median_snr"][()][1:]).all()
        assert "median_snr" not in f["ELG"].attrs                           # not common
        # BGS group attrs: common over the shards that HAVE a BGS group
        assert f["BGS"].attrs["galaxy_type"] == "DESI_DR1_BGS"
    rep = verify.verify(out, man, shard_dir=sd, log=lambda m: None)
    assert rep.ok, rep.text()


def test_verify_checks_the_table(shard_dir, tmp_path):
    out, man = _pkg(shard_dir, tmp_path)
    assert verify.verify(out, man, shard_dir=shard_dir, log=lambda m: None).ok
    p = out / F01
    # (a) chunk-level attr that differs between shards
    with h5py.File(p, "r+") as f:
        f["BGS"].attrs["n_objects"] = 3
    txt = verify.verify(out, man, shard_dir=shard_dir, log=lambda m: None).text()
    assert "BGS@n_objects at chunk level but differs between shards" in txt
    with h5py.File(p, "r+") as f:
        del f["BGS"].attrs["n_objects"]
        f["source_shard_table/healpix"][0] = 99
    txt = verify.verify(out, man, shard_dir=shard_dir, log=lambda m: None).text()
    assert "table healpix set != source_shards" in txt
    with h5py.File(p, "r+") as f:
        f["source_shard_table/healpix"][0] = 11
        del f["source_shard_table"]
    txt = verify.verify(out, man, shard_dir=shard_dir, log=lambda m: None).text()
    assert "source_shard_table/ missing" in txt


def test_verify_flags_common_attr_missing_at_chunk_level(shard_dir, tmp_path):
    out, man = _pkg(shard_dir, tmp_path)
    with h5py.File(out / F01, "r+") as f:
        del f["BGS"].attrs["galaxy_type"]
    txt = verify.verify(out, man, shard_dir=shard_dir, log=lambda m: None).text()
    assert "identical in all shards but missing at chunk level" in txt


def test_old_layout_chunk_is_refused_on_rerun(shard_dir, tmp_path):
    out, _ = _pkg(shard_dir, tmp_path)
    with h5py.File(out / F01, "r+") as f:
        del f["source_shard_table"]
    with pytest.raises(package.PackageError, match="source_shard_table"):
        package.package(shard_dir, out, log=lambda m: None)


# ------------------------------------------------------------------------------------- S7
def test_verify_fails_without_shards_unless_opted_out(shard_dir, tmp_path):
    out, man = _pkg(shard_dir, tmp_path)
    rep = verify.verify(out, man, log=lambda m: None)
    assert not rep.ok and "--no-shards" in rep.text()
    rep = verify.verify(out, man, no_shards=True, log=lambda m: None)
    assert rep.ok and "--no-shards given" in rep.text()
    assert cli.main(["verify", "--target", str(out), "--manifest", str(man)]) == 1
    assert cli.main(["verify", "--target", str(out), "--manifest", str(man), "--no-shards"]) == 0
    assert cli.main(["verify", "--target", str(out), "--manifest", str(man), "--shard-dir",
                     str(shard_dir)]) == 0


# ------------------------------------------------------------------------------------- S1
def fake_runner(job):
    """Module-level so a process pool can pickle it.  Pixel 1 fails at once; others are slow."""
    Path(job["marks"], f"started_{job['pix']}").write_text("x")
    if job["pix"] == 1:
        raise RuntimeError("pixel 1 exploded")
    time.sleep(0.6)
    return {"healpix": job["pix"], "n_objects": 1, "seconds_total": 0.6}


def test_failed_pixel_stops_parallel_run_and_is_logged(tmp_path):
    marks = tmp_path / "marks"
    marks.mkdir()
    jobs = [dict(pix=p, marks=str(marks)) for p in range(1, 13)]
    logf = tmp_path / "extract_log.jsonl"
    failures = cli.run_jobs(jobs, 2, logf, fake_runner, mp_method="fork")
    assert failures and failures[0][0] == 1 and "exploded" in failures[0][1]
    recs = [json.loads(line) for line in logf.read_text().splitlines()]
    err = [r for r in recs if "error" in r]
    assert len(err) == 1 and err[0]["healpix"] == 1 and "traceback" in err[0]
    started = {int(p.name.split("_")[1]) for p in marks.iterdir()}
    assert 12 not in started and len(started) < 12                  # pending jobs were cancelled
    # every job that did start is accounted for in the log (result, error or cancellation)
    assert started <= {r["healpix"] for r in recs}


def test_serial_failure_is_recorded_and_stops(tmp_path):
    marks = tmp_path / "m"
    marks.mkdir()
    logf = tmp_path / "log.jsonl"
    jobs = [dict(pix=p, marks=str(marks)) for p in (1, 2, 3)]
    failures = cli.run_jobs(jobs, 1, logf, fake_runner)
    assert [p for p, _ in failures] == [1] and sorted(os.listdir(marks)) == ["started_1"]
    assert "error" in json.loads(logf.read_text().splitlines()[0])
