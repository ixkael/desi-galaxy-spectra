"""Per-batch driver: stages derived from markers on disk, resumable, one failure stops one batch."""
import json

import pytest

from desi_galaxy_spectra import config
from desi_galaxy_spectra.driver import BatchError, batch_dirs, run_batch, stage_on_disk

quiet = dict(log=lambda m: None)
# strings that must never reach the public tree (built in pieces so that this file is clean too)
PRIVATE = ("/home" + "/bl", "NAS" + "/Four", "claude" + "-scratch", "bl" + "desktop")


def test_run_batch_reaches_verified_on_synthetic_batch(settings_tmp, fake_batch):
    assert stage_on_disk(settings_tmp, fake_batch) == "extracted"
    assert run_batch(settings_tmp, fake_batch, until="verified", **quiet) == "verified"
    out = settings_tmp.batch_root / f"batch_{fake_batch}"
    assert (out / "RUN_SUMMARY.md").exists() and (out / "run_summary.json").exists()
    st = json.loads((out / "status.json").read_text())
    assert st["stage"] == "verified"
    for k in ("timestamps", "kit_version", "lockfile_sha256", "counts", "bytes",
              "raw_retention_action", "run_by", "host", "slurm_job_id"):
        assert k in st
    assert st["kit_version"] == config.KIT_VERSION
    assert st["lockfile_sha256"] == config.lockfile_sha256()
    assert st["counts"]["n_pixels"] == 2 and st["counts"]["n_clean"] == 28
    # MD5SUMS.txt is in `md5sum -c` format: sorted, two spaces, every chunk file and the manifest
    lines = (out / "MD5SUMS.txt").read_text().splitlines()
    names = [ln.split("  ", 1)[1] for ln in lines]
    assert names == sorted(names) and "manifest.json" in names and len(names) == 15
    assert (out / "README.md").exists()
    assert any(ln == "RESULT: PASS" for ln in (out / "VERIFY.md").read_text().splitlines())
    assert stage_on_disk(settings_tmp, fake_batch) == "verified"


def test_status_derived_from_markers_not_status_json(settings_tmp, fake_batch):
    run_batch(settings_tmp, fake_batch, until="verified", **quiet)
    shard_dir, out = batch_dirs(settings_tmp, fake_batch)
    (out / "status.json").unlink()
    assert stage_on_disk(settings_tmp, fake_batch) == "verified"
    (out / "DELIVERED").write_text("x\n")
    assert stage_on_disk(settings_tmp, fake_batch) == "delivered"
    (out / "DELIVERED").unlink()
    (out / "VERIFY.md").write_text("RESULT: FAIL\n")
    assert stage_on_disk(settings_tmp, fake_batch) == "packaged"
    (out / "manifest.json").unlink()
    assert stage_on_disk(settings_tmp, fake_batch) == "extracted"
    (shard_dir / config.shard_name(21)).unlink()
    assert stage_on_disk(settings_tmp, fake_batch) == "downloaded"
    (settings_tmp.raw_root / "bright" / "0" / "21" / config.SHA_MARKER).unlink()
    assert stage_on_disk(settings_tmp, fake_batch) == "queued"


def test_md5_recheck_catches_a_modified_chunk(settings_tmp, fake_batch):
    run_batch(settings_tmp, fake_batch, until="verified", **quiet)
    _, out = batch_dirs(settings_tmp, fake_batch)
    chunk = out / "desi_dr1_clean_galaxies_zlt0.10.h5"
    with open(chunk, "r+b") as f:
        f.seek(-1, 2)
        f.write(b"\x00" if chunk.read_bytes()[-1:] != b"\x00" else b"\x01")
    assert stage_on_disk(settings_tmp, fake_batch) == "packaged"


def test_run_batch_resumes_after_partial(settings_tmp, fake_batch):
    shard_dir, out = batch_dirs(settings_tmp, fake_batch)
    out.mkdir(parents=True)
    (out / "desi_dr1_clean_galaxies_z0.30-0.40.h5.partial").write_bytes(b"junk")
    (shard_dir / "healpix_21.h5.partial").write_bytes(b"junk")
    assert run_batch(settings_tmp, fake_batch, **quiet) == "verified"
    assert not list(out.glob("*.partial")) and not list(shard_dir.glob("*.partial"))
    # a second call is a no-op that leaves the batch verified
    assert run_batch(settings_tmp, fake_batch, **quiet) == "verified"


def test_failed_pixel_stops_only_its_batch(settings_tmp, batch_factory):
    a = batch_factory("902", [31, 32], tid_base=2000)
    b = batch_factory("903", [41, 42], tid_base=5000)
    shard_dir_a, _ = batch_dirs(settings_tmp, a)
    (shard_dir_a / config.shard_name(32)).write_bytes(b"not an hdf5 file")
    with pytest.raises(BatchError, match="902"):
        run_batch(settings_tmp, a, **quiet)
    assert stage_on_disk(settings_tmp, a) == "extracted"
    assert run_batch(settings_tmp, b, **quiet) == "verified"
    assert stage_on_disk(settings_tmp, b) == "verified"


def test_extraction_failure_is_logged_and_raises(settings_tmp, batch_factory):
    bid = batch_factory("904", [51], tid_base=7000, shards=False)
    d = config.raw_dir(settings_tmp.raw_root, 51, "bright")
    (d / config.coadd_name(51, "bright")).unlink()        # marker present, coadd file gone
    with pytest.raises(BatchError, match="extract"):
        run_batch(settings_tmp, bid, **quiet)
    shard_dir, _ = batch_dirs(settings_tmp, bid)
    rec = [json.loads(ln) for ln in (shard_dir / "extract_log.jsonl").read_text().splitlines()]
    assert rec and rec[0]["healpix"] == 51 and "error" in rec[0]
    assert stage_on_disk(settings_tmp, bid) == "downloaded"


def test_queued_batch_needs_fetch(settings_tmp, batch_factory):
    bid = batch_factory("905", [61], raw=False, shards=False)
    assert stage_on_disk(settings_tmp, bid) == "queued"
    with pytest.raises(BatchError, match="fetch"):
        run_batch(settings_tmp, bid, fetch=False, **quiet)


def test_corrupt_status_json_is_a_hard_error(settings_tmp, fake_batch):
    run_batch(settings_tmp, fake_batch, **quiet)
    _, out = batch_dirs(settings_tmp, fake_batch)
    (out / "status.json").write_text("{not json")
    with pytest.raises(BatchError, match="status.json"):
        run_batch(settings_tmp, fake_batch, **quiet)
    (out / "status.json").unlink()                       # an absent file is fine
    assert run_batch(settings_tmp, fake_batch, **quiet) == "verified"


def test_fetch_that_leaves_no_marker_is_a_hard_error(settings_tmp, batch_factory, monkeypatch):
    bid = batch_factory("906", [62], raw=False, shards=False)
    calls = []

    class SilentFetcher:
        def fetch_pixel(self, pix, root, log=print, progs=None):
            calls.append(pix)          # returns as if done, but writes no SHA256_OK marker

    monkeypatch.setattr("desi_galaxy_spectra.driver.Fetcher", SilentFetcher)
    with pytest.raises(BatchError, match="fetch finished.*62.*SHA256_OK"):
        run_batch(settings_tmp, bid, fetch=True, **quiet)
    assert calls == [62] and stage_on_disk(settings_tmp, bid) == "queued"


def test_fetch_missing_one_programme_marker_is_a_hard_error(settings_tmp, batch_factory,
                                                           monkeypatch):
    bid = batch_factory("907", [63], raw=False, shards=False)

    class HalfFetcher:
        def fetch_pixel(self, pix, root, log=print, progs=None):
            d = config.raw_dir(root, pix, "bright")
            d.mkdir(parents=True)
            (d / config.SHA_MARKER).write_text("ok\n")     # the dark programme gets nothing

    monkeypatch.setattr("desi_galaxy_spectra.driver.Fetcher", HalfFetcher)
    with pytest.raises(BatchError, match="fetch finished.*63.*dark"):
        run_batch(settings_tmp, bid, fetch=True, **quiet)


def test_raw_deleted_only_after_verify_when_switch_off(settings_tmp, fake_batch):
    import dataclasses
    s = dataclasses.replace(settings_tmp, keep_raw_after_verify=False,
                            keep_shards_after_verify=False)
    raw = config.raw_dir(s.raw_root, 21, "bright")
    shard_dir, out = batch_dirs(s, fake_batch)
    assert run_batch(s, fake_batch, until="packaged", **quiet) == "packaged"
    assert raw.exists() and (shard_dir / config.shard_name(21)).exists()
    assert run_batch(s, fake_batch, until="verified", **quiet) == "verified"
    assert not raw.exists() and not (shard_dir / config.shard_name(21)).exists()
    assert not config.raw_dir(s.raw_root, 22, "dark").exists()
    assert json.loads((out / "status.json").read_text())["raw_retention_action"] == "deleted"
    # the verified stage survives the removal of raw files and shards
    assert stage_on_disk(s, fake_batch) == "verified"


def test_raw_kept_when_switch_on(settings_tmp, fake_batch):
    run_batch(settings_tmp, fake_batch, **quiet)
    assert config.raw_dir(settings_tmp.raw_root, 21, "bright").exists()
    _, out = batch_dirs(settings_tmp, fake_batch)
    assert json.loads((out / "status.json").read_text())["raw_retention_action"] == "kept"


def test_verify_and_index_tolerate_absent_empty_chunk(settings_tmp, fake_batch):
    from desi_galaxy_spectra.index import build_index
    assert run_batch(settings_tmp, fake_batch, **quiet) == "verified"     # no z >= 1.6 objects
    _, out = batch_dirs(settings_tmp, fake_batch)
    assert len(list(out.glob("*.h5"))) == 14
    assert not (out / "desi_dr1_clean_galaxies_z1.60-inf.h5").exists()
    idx = build_index(settings_tmp)
    rows = idx.read_text().splitlines()
    assert rows[0] == "batch,file,n_BGS,n_ELG,n_LRG,bytes,md5" and len(rows) == 15


def test_claim_by_another_user_blocks_the_run(settings_tmp, fake_batch):
    from desi_galaxy_spectra.claims import ClaimError
    _, out = batch_dirs(settings_tmp, fake_batch)
    out.mkdir(parents=True)
    (out / "CLAIM").write_text(json.dumps({"user": "someone-else-entirely"}))
    with pytest.raises(ClaimError, match="someone-else-entirely"):
        run_batch(settings_tmp, fake_batch, **quiet)
    assert stage_on_disk(settings_tmp, fake_batch) == "extracted"


def test_run_summary_is_complete_and_honest(settings_tmp, fake_batch):
    from desi_galaxy_spectra.runsummary import write_run_summary
    run_batch(settings_tmp, fake_batch, **quiet)
    _, out = batch_dirs(settings_tmp, fake_batch)
    md, js = write_run_summary(settings_tmp, fake_batch, {"extract": 1.0, "package": 2.0})
    d = json.loads(js.read_text())
    for k in ("run_id", "provenance", "model", "parameters", "diagnostics", "outputs", "status"):
        assert k in d
    import re
    assert re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+$", d["run_id"])
    assert d["diagnostics"]["chi2_reduced"].startswith("not produced")
    assert d["status"]["gates_failed"] == [] and len(d["status"]["gates_passed"]) == 10
    text = md.read_text()
    assert "not produced" in text and "VERIFY PASSED" in text
    # (the command line and host name are runtime facts of whoever ran it, so only the strings
    # that could come from the source tree are checked here; the sources are scanned below)
    for bad in PRIVATE[:3]:
        assert bad not in text
    # fields the run cannot supply are labelled, not invented: this batch was built from ready
    # shards, so the extraction log has no record of its pixels
    assert "extract_log.jsonl has no record of the shards of this batch" in text
    assert d["model"]["selection"] == []


def test_new_sources_and_templates_carry_no_private_paths():
    from pathlib import Path
    import desi_galaxy_spectra
    pkg = Path(desi_galaxy_spectra.__file__).parent
    files = [pkg / f"{n}.py" for n in ("driver", "status", "runsummary", "index", "claims")]
    files += list((pkg / "templates").iterdir())
    for f in files:
        text = f.read_text()
        for bad in PRIVATE + ("/Users" + "/",):
            assert bad not in text, f"{f.name} contains {bad}"


def test_package_templates_are_found():
    from pathlib import Path
    import desi_galaxy_spectra
    t = Path(desi_galaxy_spectra.__file__).parent / "templates"
    for n in ("RUN_SUMMARY.md", "run_summary.schema.json", "BATCH_README.md"):
        assert (t / n).is_file()
    json.loads((t / "run_summary.schema.json").read_text())
