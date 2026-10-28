"""Claims (exclusive, per batch), the permissions check, and the umask / thread caps of the CLI."""
import json
import os
import stat
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import pytest

from desi_galaxy_spectra import claims, cli
from desi_galaxy_spectra.claims import ClaimError
from desi_galaxy_spectra.driver import run_batch


def _try_claim(s, batch_id):
    """Module-level so a process pool can pickle it."""
    try:
        claims.claim(s, batch_id)
        return True
    except ClaimError:
        return False


def _as(monkeypatch, user):
    monkeypatch.setattr(claims.getpass, "getuser", lambda: user)


def write_settings(path, s):
    """A settings file for ``s`` (JSON strings are valid TOML strings)."""
    lines = [f"{k} = {json.dumps(str(getattr(s, k)))}"
             for k in ("raw_root", "shard_root", "batch_root", "staging")]
    lines.append(f"workers = {s.workers}")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_two_simultaneous_claims_one_wins(settings_tmp):
    with ProcessPoolExecutor(2) as ex:
        res = list(ex.map(_try_claim, [settings_tmp] * 2, ["001"] * 2))
    assert sorted(res) == [False, True]
    c = claims.read_claim(settings_tmp, "001")
    assert set(c) == {"user", "host", "slurm_job_id", "time"}


def test_claim_file_is_group_writable_json(settings_tmp, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "4242")
    old = os.umask(0o002)
    try:
        c = claims.claim(settings_tmp, "002")
    finally:
        os.umask(old)
    assert c["slurm_job_id"] == "4242"
    p = claims.claim_path(settings_tmp, "002")
    assert stat.S_IMODE(p.stat().st_mode) == 0o664
    with pytest.raises(ClaimError, match="already claimed by you"):
        claims.claim(settings_tmp, "002")


def test_claim_refused_for_other_user(settings_tmp, fake_batch, monkeypatch):
    _as(monkeypatch, "a")
    claims.claim(settings_tmp, fake_batch)
    _as(monkeypatch, "b")
    with pytest.raises(ClaimError, match="claimed by 'a'"):
        run_batch(settings_tmp, fake_batch, log=lambda m: None)
    with pytest.raises(ClaimError, match="already claimed by 'a'"):
        claims.claim(settings_tmp, fake_batch)
    _as(monkeypatch, "a")
    run_batch(settings_tmp, fake_batch, until="extracted", log=lambda m: None)   # the owner may


def test_release_force_logged(settings_tmp, monkeypatch):
    _as(monkeypatch, "a")
    claims.claim(settings_tmp, "003")
    _as(monkeypatch, "b")
    with pytest.raises(ClaimError, match="only they can release"):
        claims.release(settings_tmp, "003", log=lambda m: None)
    assert claims.read_claim(settings_tmp, "003")["user"] == "a"
    claims.release(settings_tmp, "003", force=True, log=lambda m: None)
    assert claims.read_claim(settings_tmp, "003") is None
    lines = (settings_tmp.batch_root / "claims.log").read_text().splitlines()
    assert len(lines) == 1
    assert "forced release by b" in lines[0] and '"user": "a"' in lines[0]
    assert lines[0].split("\t")[0][:4].isdigit()                 # time stamp first
    _as(monkeypatch, "a")
    claims.claim(settings_tmp, "003")                            # free again
    claims.release(settings_tmp, "003", log=lambda m: None)      # the owner needs no force
    assert len((settings_tmp.batch_root / "claims.log").read_text().splitlines()) == 1


def test_next_skips_claimed_and_delivered(settings_tmp):
    assert claims.next_unclaimed(settings_tmp) == "000"
    claims.claim(settings_tmp, "000")
    (settings_tmp.batch_root / "batch_001").mkdir(parents=True)
    (settings_tmp.batch_root / "batch_001" / "DELIVERED").write_text("x\n")
    claims.claim(settings_tmp, "002")
    assert claims.next_unclaimed(settings_tmp) == "003"
    for i in range(3, 84):
        claims.claim(settings_tmp, f"{i:03d}")
    with pytest.raises(ClaimError, match="every batch"):
        claims.next_unclaimed(settings_tmp)


def _tidy(s):
    """Directories 2775, files 664, in the user's own group."""
    for root in (s.raw_root, s.shard_root, s.batch_root):
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o2775)
        (root / "sub").mkdir(exist_ok=True)
        os.chmod(root / "sub", 0o2775)
        f = root / "sub" / "f.dat"
        f.write_text("x")
        os.chmod(f, 0o664)


def test_doctor_reports_not_group_writable(settings_tmp):
    _tidy(settings_tmp)
    assert claims.doctor(settings_tmp) == []
    bad = settings_tmp.shard_root / "sub" / "f.dat"
    os.chmod(bad, 0o644)
    found = claims.doctor(settings_tmp)
    assert len(found) == 1 and str(bad) in found[0] and "not group-writable" in found[0]
    assert stat.S_IMODE(bad.stat().st_mode) == 0o644             # listing only, nothing changed


def test_doctor_reports_missing_setgid_and_wrong_group(settings_tmp):
    _tidy(settings_tmp)
    d = settings_tmp.batch_root / "sub"
    os.chmod(d, 0o775)
    found = claims.doctor(settings_tmp)
    assert len(found) == 1 and str(d) in found[0] and "without setgid" in found[0]
    os.chmod(d, 0o2775)
    s = replace(settings_tmp, group="no-such-group-xyz")
    found = claims.doctor(s)
    assert len(found) == 9 and all("not 'no-such-group-xyz'" in m for m in found)


def test_cli_sets_umask_and_thread_caps(settings_tmp, tmp_path, monkeypatch, capsys):
    cfg = write_settings(tmp_path / "settings.toml", settings_tmp)
    monkeypatch.setenv("DGS_SETTINGS", str(cfg))
    for k in cli.THREAD_VARS:
        monkeypatch.setenv(k, "8")
    old = os.umask(0o022)
    try:
        assert cli.main(["next"]) == 0
        mask = os.umask(old)
    except BaseException:
        os.umask(old)
        raise
    assert mask == 0o002
    assert all(os.environ[k] == "1" for k in cli.THREAD_VARS)
    assert capsys.readouterr().out.strip() == "000"
