"""Regression manifest and the 1-ULP comparator (regress.py)."""
import json
import shutil
from pathlib import Path

import h5py
import numpy as np
import pytest
from conftest import OBJS_A, OBJS_B, make_shard

from desi_galaxy_spectra import cli, package, regress
from desi_galaxy_spectra.regress import (
    RegressError,
    RegressReport,
    compare,
    regression_manifest,
    ulp_distance,
)

quiet = {"log": lambda m: None}


# ------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def synthetic_base(tmp_path_factory):
    """Two synthetic shards and their chunk files: <root>/shards/*.h5, <root>/chunks/*.h5."""
    base = tmp_path_factory.mktemp("regress_base")
    root = base / "ref"
    (root / "shards").mkdir(parents=True)
    for pix, objs in ((11, OBJS_A), (12, OBJS_B)):
        work = base / f"work_{pix}"
        work.mkdir()
        sh, _ = make_shard(work, pix, objs)
        shutil.move(str(sh), root / "shards" / sh.name)
    package.package(root / "shards", root / "chunks", **quiet)
    return root


@pytest.fixture()
def synthetic_chunks(synthetic_base, tmp_path):
    """(reference arrays dir, reference manifest, fresh target copy)."""
    ref = tmp_path / "ref"
    shutil.copytree(synthetic_base, ref)
    tgt = tmp_path / "tgt"
    shutil.copytree(synthetic_base, tgt)
    return ref, regression_manifest(ref), tgt


def _flux(root):
    """(rel, h5 path) of the first chunk flux dataset with at least one row."""
    for p in sorted((Path(root) / "chunks").glob("*.h5")):
        with h5py.File(p, "r") as f:
            for c in ("BGS", "ELG", "LRG"):
                if c in f and f[c]["spectra/flux"].shape[0] > 0:
                    return str(p.relative_to(root)), f"/{c}/spectra/flux"
    raise AssertionError("no flux rows in the synthetic chunks")


def _nudge(root, rel, dset, steps, row=0, col=None):
    """Move one element `steps` ULPs up with nextafter; returns the column changed."""
    with h5py.File(Path(root) / rel, "r+") as f:
        d = f[dset]
        r = d[row]
        j = int(np.argmax(np.isfinite(r) & (r != 0))) if col is None else col
        for _ in range(steps):
            r[j] = np.nextafter(r[j], np.asarray(np.inf, r.dtype))
        d[row] = r
    return j


def _set_bits(root, rel, dset, row, col, bits):
    with h5py.File(Path(root) / rel, "r+") as f:
        d = f[dset]
        r = d[row]
        r.view(np.uint32)[col] = bits
        d[row] = r


def _n_compared(man):
    return sum(1 for fe in man["files"].values() for e in fe["datasets"].values()
               if e["kind"] != "provenance")


# ------------------------------------------------------------------------------- ULP maths
def test_ulp_distance_basic():
    one, two = np.float32(1), np.float32(2)
    up1 = np.nextafter(one, two)
    up2 = np.nextafter(up1, two)
    a = np.array([1, 1, 1, -1], np.float32)
    b = np.array([one, up1, up2, -up1], np.float32)
    assert ulp_distance(a, b).tolist() == [0, 1, 2, 1]
    assert ulp_distance(a, b).dtype == np.int64


def test_ulp_distance_signed_zero_and_nan(synthetic_chunks):
    assert ulp_distance(np.float32([0.0]), np.float32([-0.0])).tolist() == [1]
    # smallest denormals either side of zero: -d -> -2, -0 -> -1, +0 -> 0, +d -> 1
    d = np.float32(1e-45)
    assert ulp_distance(np.float32([-d]), np.float32([d])).tolist() == [3]
    # NaN with different payloads -> compare() reports a failure (bit distance is only 1)
    ref, _, tgt = synthetic_chunks
    rel, ds = _flux(ref)
    _set_bits(ref, rel, ds, 0, 5, 0x7FC00000)
    man = regression_manifest(ref)
    shutil.rmtree(tgt)
    shutil.copytree(ref, tgt)
    _set_bits(tgt, rel, ds, 0, 5, 0x7FC00001)
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any("NaN" in m and ds in m for m in rep.failures), rep.failures
    assert rep.ulp_diffs == []


def test_identical_nan_bits_with_1ulp_elsewhere_passes(synthetic_chunks):
    ref, _, tgt = synthetic_chunks
    rel, ds = _flux(ref)
    for root in (ref, tgt):
        _set_bits(root, rel, ds, 0, 5, 0x7FC00007)
    man = regression_manifest(ref)
    _nudge(tgt, rel, ds, 1, col=9)
    rep = compare(man, tgt, ref)
    assert rep.passed, rep.failures
    assert rep.ulp_diffs == [{"file": rel, "dataset": ds, "n_differ": 1, "max_ulp": 1}]


def test_ulp_distance_float64_and_float16():
    f8 = np.float64
    big = np.finfo(f8).max
    a = np.array([1.0, 0.0, 5e-324, 1.0, -1.0], f8)
    b = np.array([np.nextafter(1.0, 2.0), -0.0, -5e-324, 2.0, np.nextafter(-1.0, -2.0)], f8)
    assert ulp_distance(a, b).tolist() == [1, 1, 3, 2**52, 1]
    # the full float64 line does not fit int64: saturates instead of wrapping
    assert ulp_distance(np.array([-big]), np.array([big])).tolist() == [np.iinfo(np.int64).max]
    h = np.float16(1)
    assert ulp_distance(np.array([h]), np.array([np.nextafter(h, np.float16(2))])).tolist() == [1]


def test_ulp_distance_rejects_dtype_mismatch_and_non_float():
    with pytest.raises(TypeError):
        ulp_distance(np.float32([1.0]), np.float64([1.0]))
    with pytest.raises(TypeError):
        ulp_distance(np.int32([1]), np.int32([1]))
    with pytest.raises(ValueError):
        ulp_distance(np.float32([1.0, 2.0]), np.float32([1.0]))


# ---------------------------------------------------------------------------- manifest
def test_regression_manifest_is_deterministic_and_complete(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    again = regression_manifest(tgt)
    assert json.dumps(man, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert list(man["files"]) == sorted(man["files"])
    assert any(r.startswith("shards/") for r in man["files"])
    assert any(r.startswith("chunks/") for r in man["files"])
    fe = man["files"]["shards/healpix_11.h5"]
    # provenance values are hashed too (the report says whether they differed), never compared
    assert isinstance(fe["attrs"]["/@kit_version"], str)
    assert isinstance(fe["attrs"]["/@healpix"], str)
    assert fe["datasets"]["/BGS/catalog/coadd_file"]["kind"] == "exact"
    ce = man["files"][_flux(ref)[0]]
    assert ce["datasets"]["/source_shard_table/source_metadata/creation_date"]["kind"] == \
        "provenance"
    assert fe["datasets"]["/BGS/spectra/flux"]["kind"] == "float"
    assert fe["datasets"]["/BGS/catalog/targetid"]["kind"] == "exact"
    assert "/@kit_version" in man["excluded"]["attrs"]
    assert "/*/catalog/coadd_file" in man["excluded"]["path_datasets"]


def test_regression_manifest_by_role_mapping(synthetic_chunks):
    ref, man, _ = synthetic_chunks
    by_role = regression_manifest({"shards": ref / "shards", "chunks": ref / "chunks"})
    assert by_role == man


# ----------------------------------------------------------------------------- compare
def test_regress_identical_copy_passes(synthetic_chunks):
    _, man, tgt = synthetic_chunks
    rep = compare(man, tgt, None)
    assert isinstance(rep, RegressReport)
    assert rep.passed, rep.failures
    assert rep.n_datasets_exact == _n_compared(man)
    assert rep.ulp_diffs == [] and rep.failures == []
    assert "PASS" in rep.to_markdown()


def test_regress_1ulp_flux_reported_and_passes(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, ds = _flux(tgt)
    _nudge(tgt, rel, ds, 1)
    rep = compare(man, tgt, ref)
    assert rep.passed, rep.failures
    assert rep.ulp_diffs == [{"file": rel, "dataset": ds, "n_differ": 1, "max_ulp": 1}]
    assert rep.n_datasets_exact == _n_compared(man) - 1
    md = rep.to_markdown()
    assert rel in md and ds in md and "1 ULP" in md


def test_regress_2ulp_flux_fails(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, ds = _flux(tgt)
    _nudge(tgt, rel, ds, 2)
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert rep.ulp_diffs == []
    assert any(rel in m and ds in m and "max 2 ULP" in m for m in rep.failures), rep.failures


def test_regress_integer_change_fails(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, ds = _flux(tgt)
    cls = ds.split("/")[1]
    with h5py.File(tgt / rel, "r+") as f:
        t = f[cls]["catalog/targetid"]
        t[0] = t[0] + 1
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any(f"/{cls}/catalog/targetid" in m for m in rep.failures), rep.failures


def test_regress_attribute_change_fails_unless_provenance(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, _ = _flux(tgt)
    with h5py.File(tgt / "shards/healpix_11.h5", "r+") as f:
        f.attrs["kit_version"] = "0.2.0"
    rep = compare(man, tgt, ref)
    assert rep.passed, rep.failures
    assert any("/@kit_version" in e and "differs in 1" in e for e in rep.excluded), rep.excluded
    with h5py.File(tgt / rel, "r+") as f:
        f.attrs["z_lo"] = f.attrs["z_lo"] + 1e-9
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any(rel in m and "/@z_lo" in m for m in rep.failures), rep.failures


def test_regress_path_dataset_basename_only(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, ds = _flux(tgt)
    cf = f"/{ds.split('/')[1]}/catalog/coadd_file"
    with h5py.File(tgt / rel, "r+") as f:
        d = f[cf]
        d[...] = np.array([v.replace(b"/stage/", b"/elsewhere/deeper/") for v in d[()]], d.dtype)
    with h5py.File(tgt / "shards/healpix_11.h5", "r+") as f:
        f.attrs["input_files"] = np.array([b"/some/dir/" + v for v in f.attrs["input_files"]])
    rep = compare(man, tgt, ref)
    assert rep.passed, rep.failures
    with h5py.File(tgt / rel, "r+") as f:
        d = f[cf]
        v = d[()]
        v[0] = v[0].replace(b"coadd-main", b"coadd-other")
        d[...] = v
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any(rel in m and cf in m for m in rep.failures), rep.failures


def test_regress_path_attr_basename_change_fails(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    with h5py.File(tgt / "shards/healpix_11.h5", "r+") as f:
        f.attrs["input_files"] = np.array([b"x" + v for v in f.attrs["input_files"]])
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any("/@input_files" in m for m in rep.failures), rep.failures


def test_regress_missing_arrays_fails_with_message(synthetic_chunks, tmp_path):
    _, man, tgt = synthetic_chunks
    rel, ds = _flux(tgt)
    _nudge(tgt, rel, ds, 1)
    rep = compare(man, tgt, None)
    assert not rep.passed
    assert any("arrays needed for ULP comparison" in m and rel in m for m in rep.failures)
    rep = compare(man, tgt, tmp_path / "no_such_arrays")
    assert not rep.passed
    assert any("arrays needed for ULP comparison" in m for m in rep.failures), rep.failures


def test_regress_reference_arrays_must_match_reference_manifest(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, ds = _flux(tgt)
    _nudge(tgt, rel, ds, 1)
    _nudge(ref, rel, ds, 1, col=3)          # the arrays are no longer the reference
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any("reference arrays" in m and rel in m for m in rep.failures), rep.failures


def test_regress_file_set_difference_fails(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, _ = _flux(tgt)
    (tgt / rel).unlink()
    shutil.copy(tgt / "shards/healpix_11.h5", tgt / "shards/healpix_13.h5")
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any(rel in m and "missing" in m for m in rep.failures), rep.failures
    assert any("shards/healpix_13.h5" in m for m in rep.failures), rep.failures


def test_regress_dataset_set_and_dtype_differences_fail(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    rel, ds = _flux(tgt)
    cls = ds.split("/")[1]
    with h5py.File(tgt / rel, "r+") as f:
        del f[cls]["photometry/flux_g"]
        f[cls]["photometry"].create_dataset("extra", data=np.zeros(3))
        z = f[cls]["catalog/z_redrock"][()]
        del f[cls]["catalog/z_redrock"]
        f[cls]["catalog"].create_dataset("z_redrock", data=z.astype("f4"))
    rep = compare(man, tgt, ref)
    assert not rep.passed
    txt = "\n".join(rep.failures)
    assert f"/{cls}/photometry/flux_g" in txt and f"/{cls}/photometry/extra" in txt
    assert f"/{cls}/catalog/z_redrock" in txt


def test_compare_refuses_reference_with_other_exclusion_rules(synthetic_chunks):
    ref, man, tgt = synthetic_chunks
    man = json.loads(json.dumps(man))
    man["excluded"]["attrs"]["/@healpix"] = "not a provenance attribute"
    rep = compare(man, tgt, ref)
    assert not rep.passed
    assert any("exclusion rules" in m for m in rep.failures), rep.failures


# --------------------------------------------------------------------------- run_regress
def test_run_regress_missing_reference_json_is_a_hard_error(settings_tmp, tmp_path, monkeypatch):
    monkeypatch.setattr(regress, "REGRESS_DIR", tmp_path / "no_regress")
    for name, task in (("21852", "Task 12"), ("batch000", "Task 13"), ("ref22", "Task 14")):
        with pytest.raises(RegressError, match=task):
            regress.run_regress(settings_tmp, name, tmp_path / f"scratch_{name}", **quiet)
    with pytest.raises(ValueError):
        regress.run_regress(settings_tmp, "pixel21852", tmp_path / "s", **quiet)


def _install_reference(monkeypatch, tmp_path, stem, man):
    rdir = tmp_path / "regress_refs"
    rdir.mkdir(exist_ok=True)
    (rdir / f"{stem}.json").write_text(json.dumps(man, sort_keys=True))
    monkeypatch.setattr(regress, "REGRESS_DIR", rdir)


def test_run_regress_batch_uses_scratch_and_keeps_raw(synthetic_chunks, settings_tmp, tmp_path,
                                                      monkeypatch):
    import dataclasses
    ref, man, tgt = synthetic_chunks
    _install_reference(monkeypatch, tmp_path, "batch000", man)
    s = dataclasses.replace(settings_tmp, reference_root=tmp_path / "arrays",
                            keep_raw_after_verify=False, keep_shards_after_verify=False)
    shutil.copytree(ref, tmp_path / "arrays" / "batch000")
    seen = {}

    def fake_run_batch(s2, bid, fetch=False, until="verified", log=print):
        seen.update(s=s2, bid=bid, fetch=fetch, until=until)
        shutil.copytree(tgt / "shards", Path(s2.shard_root) / f"batch_{bid}")
        shutil.copytree(tgt / "chunks", Path(s2.batch_root) / f"batch_{bid}")
        _nudge(Path(s2.batch_root) / f"batch_{bid}", Path(rel).name, ds, 1)
        return "verified"

    rel, ds = _flux(tgt)
    monkeypatch.setattr(regress, "run_batch", fake_run_batch)
    scratch = tmp_path / "scratch"
    rep = regress.run_regress(s, "batch000", scratch, **quiet)
    assert seen["bid"] == "000" and seen["fetch"] is False and seen["until"] == "verified"
    s2 = seen["s"]
    assert s2.raw_root == s.raw_root
    assert s2.keep_raw_after_verify and s2.keep_shards_after_verify
    for k in ("shard_root", "batch_root", "staging"):
        assert Path(getattr(s2, k)).is_relative_to(scratch)
    assert rep.passed, rep.failures
    assert rep.ulp_diffs == [{"file": rel, "dataset": ds, "n_differ": 1, "max_ulp": 1}]
    assert "PASS" in (scratch / "REGRESS.md").read_text()
    js = json.loads((scratch / "regress.json").read_text())
    assert js["passed"] is True and js["reference"] == "batch000"


def test_run_regress_refuses_non_empty_scratch(synthetic_chunks, settings_tmp, tmp_path,
                                               monkeypatch):
    _, man, _ = synthetic_chunks
    _install_reference(monkeypatch, tmp_path, "ref22", man)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "old.h5").write_text("stale")
    with pytest.raises(RegressError, match="empty"):
        regress.run_regress(settings_tmp, "ref22", scratch, **quiet)


def test_cli_regress_exit_code(settings_tmp, tmp_path, monkeypatch, capsys):
    from test_claims import write_settings
    cfgp = write_settings(tmp_path / "settings.toml", settings_tmp)
    calls = []

    def fake(s, reference, scratch, log=print):
        calls.append((reference, Path(scratch)))
        return RegressReport(passed=reference == "ref22", n_datasets_exact=3, ulp_diffs=[],
                             failures=[] if reference == "ref22" else ["x differs"], excluded=[])

    monkeypatch.setattr(regress, "run_regress", fake)
    assert cli.main(["--settings", str(cfgp), "regress", "--reference", "21852",
                     "--scratch", str(tmp_path / "s1")]) == 1
    assert cli.main(["--settings", str(cfgp), "regress", "--reference", "ref22",
                     "--scratch", str(tmp_path / "s2")]) == 0
    assert calls == [("21852", tmp_path / "s1"), ("ref22", tmp_path / "s2")]
    with pytest.raises(SystemExit):
        cli.main(["--settings", str(cfgp), "regress", "--reference", "batch001",
                  "--scratch", str(tmp_path / "s3")])

    def boom(s, reference, scratch, log=print):
        raise RegressError("reference/regress/pixel21852.json is missing (Task 12)")

    monkeypatch.setattr(regress, "run_regress", boom)
    assert cli.main(["--settings", str(cfgp), "regress", "--reference", "21852",
                     "--scratch", str(tmp_path / "s4")]) == 1
    assert "Task 12" in capsys.readouterr().err
