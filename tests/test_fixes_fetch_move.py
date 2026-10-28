"""Fix session 2026-10-23: B1 (crash-safe raw move), S2, S3, S4, S5.  Offline: mocked HTTP,
the extractor stubbed where a real FITS file would be needed."""
import hashlib
from pathlib import Path

import h5py
import pytest

from desi_galaxy_spectra import cli, config, fetch, shard
from desi_galaxy_spectra.fetch import FetchError, Fetcher, dest_verified
from test_fetch_dryrun import FakeSession, Resp, pixel_files

PIX = 7


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fake network + fake extraction; returns an object with a run() that executes one job."""
    class E:
        pass
    e = E()
    e.files = pixel_files(PIX)
    e.sess = FakeSession(e.files)
    sess = e.sess

    class F(Fetcher):
        def __init__(self):
            super().__init__(sess, sleep=lambda s: None)

    monkeypatch.setattr(fetch, "Fetcher", F)
    monkeypatch.setattr(config, "STAGING_MARGIN_BYTES", 0)

    def fake_process_pixel(pix, coadds, reds, shard_dir, scratch, **kw):
        # the real extractor needs FITS; here: the shard is just a file, as after os.replace
        Path(shard_dir).mkdir(parents=True, exist_ok=True)
        (Path(shard_dir) / config.shard_name(pix)).write_bytes(b"shard")
        e.process_kw = kw
        return {"healpix": pix, "n_objects": 1, "seconds_total": 0.0}

    monkeypatch.setattr(shard, "process_pixel", fake_process_pixel)
    e.shard_dir, e.staging, e.raw = tmp_path / "shards", tmp_path / "staging", tmp_path / "nas"
    e.job = dict(pix=PIX, shard_dir=str(e.shard_dir), staging=str(e.staging), keep_temp=False,
                 local_flat=None, redrock_dir=None, raw_root=str(e.raw), fetch=True)
    e.run = lambda: shard.run_pixel_job(dict(e.job))
    e.dest = lambda prog: config.raw_dir(e.raw, PIX, prog)
    e.staged = lambda prog: config.raw_dir(e.staging / "raw", PIX, prog)
    e.gets = lambda: [c[1] for c in e.sess.calls if c[0] == "GET"]
    return e


# ------------------------------------------------------------------------------------- B1
def test_move_rehashes_at_destination_and_writes_marker_last(env, monkeypatch):
    seen = []
    orig = Fetcher.verify_sha

    def spy(d, names):
        seen.append((Path(d), (Path(d) / config.SHA_MARKER).exists()))
        return orig(d, names)

    monkeypatch.setattr(Fetcher, "verify_sha", staticmethod(spy))
    res = env.run()
    for prog in config.PROGRAMS:
        dest_calls = [m for d, m in seen if d == env.dest(prog)]
        assert dest_calls == [False]         # hashed at the NAS, and no marker existed yet
        assert dest_verified(env.dest(prog), PIX, prog)
        assert not env.staged(prog).exists()
    assert res["raw_files_moved"] and env.process_kw["verify_sha"] is True


def test_page_cache_dropped_before_destination_rehash(env, monkeypatch):
    # review F2: the NAS is CIFS cache=strict, so each copied file is evicted before re-hashing
    order = []
    real_drop, real_sha = fetch.drop_page_cache, Fetcher.verify_sha

    def drop(p):
        order.append(("drop", Path(p).name))
        real_drop(p)

    def spy(d, names):
        if Path(d) in (env.dest(p) for p in config.PROGRAMS):
            order.append(("hash", sorted(names.values())))
        return real_sha(d, names)

    monkeypatch.setattr(fetch, "drop_page_cache", drop)
    monkeypatch.setattr(Fetcher, "verify_sha", staticmethod(spy))
    env.run()
    for prog in config.PROGRAMS:
        names = sorted(fetch.prog_names(PIX, prog).values())
        i = order.index(("hash", names))
        assert sorted(n for k, n in order[i - len(names):i] if k == "drop") == names


def test_crash_mid_copy_leaves_no_marker_and_rerun_finishes_move(env, monkeypatch):
    real, n = fetch.shutil.copyfile, {"k": 0}

    def flaky(a, b, *x, **k):
        n["k"] += 1
        if n["k"] == 2:                      # die while the second file is being copied
            Path(b).write_bytes(b"trunc")
            raise OSError("NAS hiccup")
        return real(a, b, *x, **k)

    monkeypatch.setattr(fetch.shutil, "copyfile", flaky)
    with pytest.raises(OSError):
        env.run()
    monkeypatch.setattr(fetch.shutil, "copyfile", real)
    assert (env.shard_dir / config.shard_name(PIX)).exists()          # shard already renamed
    assert not (env.dest("bright") / config.SHA_MARKER).exists()      # never a marker without proof
    assert env.staged("bright").exists()                              # originals kept
    res = env.run()                                                   # shard exists -> repair
    assert res["skipped"] is True and set(res["raw_archive"]) == set(config.PROGRAMS)
    for prog in config.PROGRAMS:
        assert dest_verified(env.dest(prog), PIX, prog) and not env.staged(prog).exists()


def test_shard_renamed_but_nothing_moved_is_repaired(env):
    """Review case 2: crash between the shard rename and the move (coadd_file points at the NAS)."""
    env.shard_dir.mkdir()
    (env.shard_dir / config.shard_name(PIX)).write_bytes(b"shard")
    fetch.Fetcher().fetch_pixel(PIX, env.staging / "raw", log=lambda m: None)   # mocked session
    assert not env.dest("dark").exists()
    res = env.run()
    assert res["skipped"] and res["raw_archive"] == {"bright": "moved", "dark": "moved"}
    assert all(dest_verified(env.dest(p), PIX, p) for p in config.PROGRAMS)


def test_marker_with_truncated_file_and_no_staged_copy_is_redone(env):
    env.run()
    bad = env.dest("bright") / config.coadd_name(PIX, "bright")
    bad.write_bytes(bad.read_bytes()[:50])           # marker still there, file truncated
    assert (env.dest("bright") / config.SHA_MARKER).exists()
    env.sess.calls.clear()
    res = env.run()
    assert res["raw_archive"] == {"bright": "refetched", "dark": "verified"}
    assert dest_verified(env.dest("bright"), PIX, "bright")
    assert all("/bright/" in u for u in env.gets())  # only the damaged programme was downloaded


def test_missing_destination_file_is_redone(env):
    env.run()
    (env.dest("dark") / config.redrock_name(PIX, "dark")).unlink()
    res = env.run()
    assert res["raw_archive"]["dark"] == "refetched" and dest_verified(env.dest("dark"), PIX, "dark")


def test_corrupt_staged_copy_is_refetched(env):
    env.shard_dir.mkdir()
    (env.shard_dir / config.shard_name(PIX)).write_bytes(b"shard")
    fetch.Fetcher().fetch_pixel(PIX, env.staging / "raw", log=lambda m: None)   # mocked session
    (env.staged("dark") / config.coadd_name(PIX, "dark")).write_bytes(b"garbage")
    res = env.run()
    assert res["raw_archive"]["dark"] == "refetched" and res["raw_archive"]["bright"] == "moved"
    assert dest_verified(env.dest("dark"), PIX, "dark")


def test_destination_hash_failure_is_an_error_not_a_success(env, monkeypatch):
    real = fetch.shutil.copyfile

    def corrupting(a, b, *x, **k):
        real(a, b, *x, **k)
        if str(a).endswith(".fits"):
            Path(b).write_bytes(Path(b).read_bytes()[:-1])       # silent truncation on the NAS

    monkeypatch.setattr(fetch.shutil, "copyfile", corrupting)
    with pytest.raises(FetchError, match="re-hash at the destination"):
        env.run()
    assert not (env.dest("bright") / config.SHA_MARKER).exists()
    assert not (env.dest("bright") / config.coadd_name(PIX, "bright")).exists()
    assert env.staged("bright").exists()
    monkeypatch.setattr(fetch.shutil, "copyfile", real)
    assert env.run()["skipped"]                                  # and a rerun then repairs it
    assert dest_verified(env.dest("bright"), PIX, "bright")


def test_raw_root_mode_does_not_trust_marker_alone(tmp_path):
    root = tmp_path / "raw"
    for prog in config.PROGRAMS:
        d = config.raw_dir(root, PIX, prog)
        d.mkdir(parents=True)
        (d / config.SHA_MARKER).write_text("ok\n")
        (d / config.coadd_name(PIX, prog)).write_bytes(b"x")
        (d / config.redrock_name(PIX, prog)).write_bytes(b"y")
    job = dict(pix=PIX, shard_dir=str(tmp_path / "s"), staging=str(tmp_path / "st"),
               raw_root=str(root), fetch=False, local_flat=None, redrock_dir=None, keep_temp=False)
    with pytest.raises(shard.ShardError, match="sha256sum missing"):
        shard.run_pixel_job(job)
    # hashes computed during extraction are compared with the DESI .sha256sum
    coadds = {p: config.raw_dir(root, PIX, p) / config.coadd_name(PIX, p) for p in config.PROGRAMS}
    reds = {p: config.raw_dir(root, PIX, p) / config.redrock_name(PIX, p) for p in config.PROGRAMS}
    hashes = {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
              for p in list(coadds.values()) + list(reds.values())}
    for prog in config.PROGRAMS:
        (config.raw_dir(root, PIX, prog) / config.sha_name(PIX, prog)).write_text(
            "".join(f"{hashes[str(p)]}  {Path(p).name}\n"
                    for p in (coadds[prog], reds[prog])))
    shard.check_inputs_against_sha256sum(PIX, coadds, reds, hashes)          # consistent: fine
    hashes[str(coadds["dark"])] = "0" * 64
    with pytest.raises(shard.ShardError, match="SHA256"):
        shard.check_inputs_against_sha256sum(PIX, coadds, reds, hashes)


# ------------------------------------------------------------------------------------- S2
def test_fetch_requires_raw_root(tmp_path, env, monkeypatch):
    monkeypatch.delenv("DGS_SETTINGS", raising=False)
    monkeypatch.chdir(tmp_path)                                  # no settings.toml here
    # no --raw-root and no usable settings: refused, naming the option
    with pytest.raises(SystemExit) as e:
        cli.main(["extract", "--pixel", "1", "--shard-dir", str(tmp_path / "s"), "--fetch"])
    assert "--raw-root" in str(e.value)
    # an empty --raw-root would mean the current directory: refused too
    with pytest.raises(SystemExit) as e:
        cli.main(["extract", "--pixel", "1", "--shard-dir", str(tmp_path / "s"), "--fetch",
                  "--raw-root", ""])
    assert "--raw-root" in str(e.value)
    env.job["raw_root"] = ""
    with pytest.raises(shard.ShardError, match="--raw-root"):
        env.run()


# ------------------------------------------------------------------------------------- S3
def _rows(progs):
    return [{"program": p} for p in progs]


def test_bgs_from_dark_guard(tmp_path, monkeypatch):
    written = {}

    def fake_write(rows, path, n_coadds, log, programs_processed, skipped=False):
        with h5py.File(path, "w") as h:
            h.create_group("metadata").attrs["bgs_group_skipped"] = skipped
        written["ok"] = True

    monkeypatch.setattr(shard.ext, "collect_rows", lambda *a, **k: _rows(["dark", "dark"]))
    monkeypatch.setattr(shard.ext, "write_hdf5", fake_write)
    with pytest.raises(shard.ShardError, match="expected"):          # programmes != {bright, dark}
        shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, tmp_path / "t.h5")
    assert "ok" not in written
    monkeypatch.setattr(shard.ext, "collect_rows", lambda *a, **k: _rows(["dark", "bright"]))
    monkeypatch.setattr(shard.ext, "write_hdf5",
                        lambda *a, **k: fake_write(*a, skipped=True, **k))
    with pytest.raises(shard.ShardError, match="bgs_group_skipped"):
        shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, tmp_path / "t.h5")
    monkeypatch.setattr(shard.ext, "write_hdf5", fake_write)
    assert shard.extract_to_temp([tmp_path / "a.fits"], tmp_path, tmp_path / "t2.h5") == 2


# ------------------------------------------------------------------------------------- S4
class ScriptedHead:
    def __init__(self, statuses):
        self.statuses, self.n = list(statuses), 0

    def head(self, url, **kw):
        self.n += 1
        s = self.statuses.pop(0) if self.statuses else self.last
        self.last = s
        return Resp(s, headers={"Content-Length": "5"})


def test_head_retries_5xx_and_429_with_backoff():
    sleeps = []
    s = ScriptedHead([503, 429, 502, 200])
    assert Fetcher(s, sleep=sleeps.append).head("u") == (200, 5)
    assert s.n == 4 and sleeps == [1, 2, 4]
    s = ScriptedHead([503])                                   # never recovers
    with pytest.raises(FetchError, match="HTTP 503"):
        Fetcher(s, sleep=lambda x: None, retries=3).head("u")
    assert s.n == 3


@pytest.mark.parametrize("code", [404, 403])
def test_head_fails_immediately_on_404_403(code):
    s = ScriptedHead([code, 200])
    assert Fetcher(s, sleep=lambda x: pytest.fail("slept")).head("u")[0] == code
    assert s.n == 1


# ------------------------------------------------------------------------------------- S5
def test_sha_mismatch_removes_bad_file_so_rerun_downloads_again(tmp_path):
    files = pixel_files(PIX)
    url = f"{config.remote_dir(PIX, 'bright')}/{config.coadd_name(PIX, 'bright')}"
    corrupted = dict(files)
    corrupted[url] = b"X" + files[url][1:]          # same size, wrong bytes (corrupt transfer)
    bad = FakeSession(corrupted)
    with pytest.raises(FetchError, match="SHA256 mismatch"):
        Fetcher(bad, sleep=lambda s: None).fetch_pixel(PIX, tmp_path, log=lambda m: None)
    d = config.raw_dir(tmp_path, PIX, "bright")
    assert not (d / config.coadd_name(PIX, "bright")).exists()
    good = FakeSession(files)
    Fetcher(good, sleep=lambda s: None).fetch_pixel(PIX, tmp_path, log=lambda m: None)
    assert any(c[0] == "GET" and "coadd-main-bright" in c[1] for c in good.calls)
    assert dest_verified(d, PIX, "bright")
