"""The ``dgs`` command line: parser, defaults from the settings, shared-running commands."""
import os
from pathlib import Path

import h5py
import pytest

from desi_galaxy_spectra import claims, cli, config, dryrun
from desi_galaxy_spectra.driver import run_batch, stage_on_disk
from test_claims import write_settings

REF = Path(os.environ["DGS_REFERENCE_H5"]) if os.environ.get("DGS_REFERENCE_H5") else None
quiet = dict(log=lambda m: None)


@pytest.fixture()
def cfg(settings_tmp, tmp_path, monkeypatch):
    """settings.toml for settings_tmp, found through $DGS_SETTINGS."""
    p = write_settings(tmp_path / "settings.toml", settings_tmp)
    monkeypatch.setenv("DGS_SETTINGS", str(p))
    return p


def test_parser_subcommands():
    ap = cli.build_parser()
    a = ap.parse_args(["extract", "--pixel", "21852", "--shard-dir", "s", "--local-flat", "f",
                       "--redrock-dir", "r", "--keep-temp"])
    assert a.pixel == 21852 and a.keep_temp and a.workers is None      # workers: from settings
    assert ap.parse_args(["package", "--shard-dir", "s", "--out", "o", "--no-resolution",
                          "--only", "0,3"]).no_resolution
    assert ap.parse_args(["verify", "--target", "t", "--manifest", "m"]).cmd == "verify"
    with pytest.raises(SystemExit):
        ap.parse_args(["extract", "--shard-dir", "s"])          # needs --pixels or --pixel
    a = ap.parse_args(["run", "--batch", "007", "--claim", "--fetch", "--until", "packaged"])
    assert (a.batch, a.claim, a.fetch, a.until) == ("007", True, True, "packaged")
    assert ap.parse_args(["release", "--force", "--batch", "ref22"]).force
    for argv in (["next"], ["status", "--deep"], ["doctor"], ["index"], ["deliver", "--batch", "001"]):
        assert ap.parse_args(argv).cmd == argv[0]
    with pytest.raises(SystemExit):
        ap.parse_args(["claim", "--batch", "084"])


def test_keep_temp_is_gate_only():
    with pytest.raises(SystemExit):
        cli.main(["extract", "--pixel", "1", "--shard-dir", "/nonexistent-x", "--keep-temp"])


def test_resolution_dtype_single_source():
    assert config.RESOLUTION_STORAGE_DTYPE == "f4" and config.KIT_VERSION == "1.0.0"
    pkg = Path(config.__file__).parent
    src = "".join(p.read_text() for p in pkg.glob("*.py") if p.name != "config.py")
    assert "RESOLUTION_STORAGE_DTYPE =" not in src


@pytest.mark.skipif(REF is None or not REF.exists(),
                    reason="reference HDF5 not available (set DGS_REFERENCE_H5)")
def test_reference_layout_matches_kit_expectations():
    with h5py.File(REF) as f:
        assert {"BGS", "ELG", "LRG", "metadata"} <= set(f)
        assert len(f["metadata/units"].attrs) == 11
        assert f["BGS/spectra/obj_00000/resolution"].shape == (11, 7781)


def test_extract_defaults_come_from_settings(settings_tmp, cfg, tmp_path, monkeypatch):
    seen = {}

    def fake(jobs, workers, logf, runner, mp_method="spawn"):
        seen.update(jobs=jobs, workers=workers, logf=logf)
        return []

    monkeypatch.setattr(cli, "run_jobs", fake)
    s = settings_tmp
    assert cli.main(["extract", "--pixel", "5"]) == 0
    j = seen["jobs"][0]
    assert j["raw_root"] == str(s.raw_root) and j["shard_dir"] == str(s.shard_root)
    assert j["staging"] == str(s.staging) and seen["workers"] == s.workers
    r, sd, st = (str(tmp_path / n) for n in ("r", "s", "t"))
    assert cli.main(["extract", "--pixel", "5", "--raw-root", r, "--shard-dir", sd,
                     "--staging", st, "--workers", "3"]) == 0
    j = seen["jobs"][0]
    assert (j["raw_root"], j["shard_dir"], j["staging"], seen["workers"]) == (r, sd, st, 3)


def test_dry_run_never_defaults_to_provenance(settings_tmp, cfg, monkeypatch):
    got = []
    monkeypatch.setattr(dryrun, "run", lambda *a, **k: got.append((a, k)))
    assert cli.main(["extract", "--pixel", "5", "--dry-run"]) == 0
    out = Path(got[-1][1]["out_dir"])
    assert out == settings_tmp.batch_root.parent / "dry-run"
    assert out != config.DRY_RUN_DIR and config.DRY_RUN_DIR not in out.parents
    assert cli.main(["extract", "--pixel", "5", "--dry-run", "--dry-run-out", "/x/y"]) == 0
    assert Path(got[-1][1]["out_dir"]) == Path("/x/y")


def test_dry_run_without_settings_or_out_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("DGS_SETTINGS", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["extract", "--pixel", "5", "--dry-run", "--raw-root", "/r", "--shard-dir", "/s",
                  "--staging", "/t", "--workers", "1"])


def test_claim_release_next_roundtrip(settings_tmp, cfg, capsys):
    assert cli.main(["next"]) == 0 and capsys.readouterr().out.strip() == "000"
    assert cli.main(["claim", "--batch", "000"]) == 0
    assert cli.main(["claim", "--batch", "000"]) == 1
    assert "already claimed" in capsys.readouterr().err
    capsys.readouterr()
    assert cli.main(["next"]) == 0 and capsys.readouterr().out.strip() == "001"
    assert cli.main(["release", "--batch", "000"]) == 0
    assert claims.read_claim(settings_tmp, "000") is None


def test_run_refuses_batch_claimed_by_other_user(settings_tmp, cfg, fake_batch, monkeypatch,
                                                 capsys):
    monkeypatch.setattr(claims.getpass, "getuser", lambda: "a")
    claims.claim(settings_tmp, fake_batch)
    monkeypatch.setattr(claims.getpass, "getuser", lambda: "b")
    assert cli.main(["run", "--batch", fake_batch]) == 1
    assert "claimed by 'a'" in capsys.readouterr().err


def test_run_claim_then_resume_and_deliver(settings_tmp, cfg, fake_batch, capsys):
    assert cli.main(["run", "--batch", fake_batch, "--claim"]) == 0
    assert claims.read_claim(settings_tmp, fake_batch) is not None
    assert stage_on_disk(settings_tmp, fake_batch) == "verified"
    assert cli.main(["run", "--batch", fake_batch, "--claim"]) == 0       # own claim: resume
    assert cli.main(["deliver", "--batch", fake_batch]) == 0
    out = settings_tmp.batch_root / f"batch_{fake_batch}"
    d = (out / "DELIVERED").read_text()
    assert '"user"' in d and '"time"' in d
    assert stage_on_disk(settings_tmp, fake_batch) == "delivered"


def test_deliver_refused_unless_verified(settings_tmp, cfg, fake_batch, capsys):
    assert cli.main(["deliver", "--batch", fake_batch]) == 1       # only extracted
    assert "extracted" in capsys.readouterr().err
    out = settings_tmp.batch_root / f"batch_{fake_batch}"
    assert not (out / "DELIVERED").exists()
    run_batch(settings_tmp, fake_batch, until="verified", **quiet)
    with open(out / "desi_dr1_clean_galaxies_zlt0.10.h5", "r+b") as f:     # md5 no longer matches
        f.seek(-1, 2)
        last = f.read(1)
        f.seek(-1, 2)
        f.write(b"\x00" if last != b"\x00" else b"\x01")
    assert cli.main(["deliver", "--batch", fake_batch]) == 1
    assert not (out / "DELIVERED").exists()


def test_status_writes_tables(settings_tmp, cfg, capsys):
    assert cli.main(["status"]) == 0
    base = settings_tmp.batch_root.parent
    assert "queued 85" in (base / "status.md").read_text()
    assert (base / "status.tsv").read_text().startswith("# stage check: size-only")
    assert cli.main(["status", "--deep"]) == 0
    assert "md5 recheck" in (base / "status.tsv").read_text().splitlines()[0]


def test_doctor_exit_code(settings_tmp, cfg, capsys):
    assert cli.main(["doctor"]) == 0                       # nothing exists yet
    settings_tmp.raw_root.mkdir(parents=True)
    os.chmod(settings_tmp.raw_root, 0o755)
    assert cli.main(["doctor"]) == 1
    assert str(settings_tmp.raw_root) in capsys.readouterr().out
    assert os.stat(settings_tmp.raw_root).st_mode & 0o777 == 0o755      # not changed


def test_index_exit_code_on_duplicates(settings_tmp, cfg, batch_factory, capsys):
    batch_factory("901", [21, 22], tid_base=1000)
    batch_factory("902", [23, 24], tid_base=1000)           # same target ids: duplicates
    for b in ("901", "902"):
        run_batch(settings_tmp, b, until="verified", **quiet)
    assert cli.main(["index"]) == 1
    out = capsys.readouterr().out
    assert "is in both" in out and (settings_tmp.batch_root.parent / "index.csv").is_file()
