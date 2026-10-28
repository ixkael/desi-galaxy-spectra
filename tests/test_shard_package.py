import h5py
import numpy as np
import pytest

from conftest import OBJS_A, make_shard
from desi_galaxy_spectra import config, manifest, package, shard, verify


def test_restack_matches_temp(tmp_path):
    sh, temp = make_shard(tmp_path, 11, OBJS_A)
    with h5py.File(sh) as f, h5py.File(temp) as t:
        assert f.attrs["healpix"] == 11 and f.attrs["kit_version"] == config.KIT_VERSION
        assert f.attrs["lockfile_sha256"] == config.lockfile_sha256()
        for c in ("BGS", "ELG", "LRG"):
            n = int(t[c].attrs["n_objects"])
            assert f[c]["spectra/flux"].shape == (n, 7781)
            assert f[c]["spectra/resolution"].shape == (n, 11, 7781)
            assert f[c]["spectra/flux"].chunks == (1, 7781)
            assert (f[c]["catalog/photsys"][()] == b"S").all()
            for i in range(n):
                o = t[c]["spectra"][f"obj_{i:05d}"]
                assert np.array_equal(f[c]["spectra/ivar"][i], o["ivar"][()])
                assert f[c]["spectra_attrs/targetid"][i] == o.attrs["targetid"]
        assert len(f["metadata/units"].attrs) == 11


def test_restack_records_permanent_raw_dir(tmp_path):
    from conftest import make_temp
    temp = tmp_path / "t.h5"
    make_temp(temp, 5, OBJS_A)
    dirs = {"bright": "/nas/bright/0/5", "dark": "/nas/dark/0/5"}
    shard.restack(temp, tmp_path / "healpix_5.h5", 5,
                  {c: np.full(n, b"S", "S1") for c, n in (("BGS", 3), ("ELG", 2), ("LRG", 1))},
                  [("x", "0" * 64)], log=lambda m: None, coadd_dirs=dirs)
    with h5py.File(tmp_path / "healpix_5.h5") as f:
        assert f["ELG/catalog/coadd_file"][0] == b"/nas/dark/0/5/coadd-main-dark-5.fits"
        assert f["BGS/catalog/coadd_file"][0] == b"/nas/bright/0/5/coadd-main-bright-5.fits"


def test_restack_rejects_failed_extraction(tmp_path):
    from conftest import make_temp
    temp = tmp_path / "t.h5"
    make_temp(temp, 5, OBJS_A)
    with h5py.File(temp, "r+") as f:
        f["BGS/quality/extraction_success"][1] = False
    with pytest.raises(shard.ShardError, match="failed extractions"):
        shard.restack(temp, tmp_path / "healpix_5.h5", 5,
                      {c: np.full(n, b"S", "S1") for c, n in (("BGS", 3), ("ELG", 2), ("LRG", 1))},
                      [("x", "0" * 64)], log=lambda m: None)
    assert not (tmp_path / "healpix_5.h5").exists()


def test_package_two_shards(shard_dir, tmp_path):
    out = tmp_path / "chunks"
    package.package(shard_dir, out, log=lambda m: None)
    names = sorted(p.name for p in out.glob("*.h5"))
    assert "desi_dr1_clean_galaxies_zlt0.10.h5" in names
    assert not list(out.glob("*.partial"))
    # BGS 100/bright (pixel 11) and BGS 100/dark (pixel 12): same targetid, different program
    f01 = out / "desi_dr1_clean_galaxies_z0.10-0.20.h5"
    with h5py.File(f01) as f:
        assert f.attrs["has_resolution"] == True  # noqa: E712
        assert f.attrs["n_BGS"] == 2
        assert list(f.attrs["source_shards"]) == [b"healpix_11.h5", b"healpix_12.h5"]
        hp = f["BGS/catalog/healpix"][()].tolist()
        assert hp == [11, 12]  # row 101 (clean, z=.15) then pixel 12 row (z=.16); 102 not clean
        assert f["BGS/source_row"][()].tolist() == [1, 0]
        assert f["BGS/spectra/resolution"].dtype == config.RESOLUTION_STORAGE_DTYPE
    with h5py.File(out / "desi_dr1_clean_galaxies_z1.60-inf.h5") as f:
        assert f.attrs["n_ELG"] == 1
    # spectra equal to the shard rows
    with h5py.File(f01) as f, h5py.File(shard_dir / "healpix_12.h5") as s:
        assert np.array_equal(f["BGS/spectra/flux"][1], s["BGS/spectra/flux"][0])
        assert np.array_equal(f["BGS/spectra/resolution"][1], s["BGS/spectra/resolution"][0])


def test_package_no_resolution_and_only(shard_dir, tmp_path):
    out = tmp_path / "nores"
    package.package(shard_dir, out, with_resolution=False, only=[1], log=lambda m: None)
    files = list(out.glob("*.h5"))
    assert len(files) == 1
    with h5py.File(files[0]) as f:
        assert f.attrs["has_resolution"] == False  # noqa: E712
        assert "resolution" not in f["BGS/spectra"]
        assert "resolution" not in f["units"].attrs


def test_package_refuses_stale_chunk(shard_dir, tmp_path):
    out = tmp_path / "c"
    package.package(shard_dir, out, only=[1], log=lambda m: None)
    package.package(shard_dir, out, only=[1], log=lambda m: None)    # idempotent skip
    with pytest.raises(package.PackageError, match="resolution setting"):
        package.package(shard_dir, out, with_resolution=False, only=[1], log=lambda m: None)


def test_manifest_roundtrip_and_verify(shard_dir, tmp_path):
    out = tmp_path / "chunks"
    package.package(shard_dir, out, log=lambda m: None)
    m = manifest.write_manifest(out, tmp_path / "m.json", log=lambda m: None)
    again = manifest.build_manifest(out, log=lambda m: None)
    assert again["files"] == m["files"]
    rep = verify.verify(out, tmp_path / "m.json", shard_dir=shard_dir, log=lambda m: None)
    assert rep.ok, rep.text()
    # since 0.2.0 skipping the shard checks needs the explicit opt-out (S7)
    rep0 = verify.verify(out, tmp_path / "m.json", no_shards=True, log=lambda m: None)
    assert rep0.ok and any(s for *_, s in rep0.checks)


def test_verify_detects_one_ulp(shard_dir, tmp_path):
    out = tmp_path / "chunks"
    package.package(shard_dir, out, log=lambda m: None)
    manifest.write_manifest(out, tmp_path / "m.json", log=lambda m: None)
    target = out / "desi_dr1_clean_galaxies_z0.10-0.20.h5"
    with h5py.File(target, "r+") as f:
        d = f["BGS/spectra/flux"]
        row = d[0]
        row[10] = np.nextafter(row[10], np.float32(np.inf))
        d[0] = row
    rep = verify.verify(out, tmp_path / "m.json", log=lambda m: None)
    assert not rep.ok
    txt = rep.text()
    assert target.name in txt and "BGS/spectra/flux" in txt


def test_verify_catches_bad_attrs(shard_dir, tmp_path):
    out = tmp_path / "chunks"
    package.package(shard_dir, out, log=lambda m: None)
    manifest.write_manifest(out, tmp_path / "m.json", log=lambda m: None)
    target = out / "desi_dr1_clean_galaxies_z0.10-0.20.h5"
    with h5py.File(target, "r+") as f:
        f.attrs["n_BGS"] = 99
        f["BGS/catalog/z_redrock"][0] = 0.5
    txt = verify.verify(out, tmp_path / "m.json", log=lambda m: None).text()
    assert "n_BGS=99" in txt and "outside [0.1,0.2)" in txt and "attributes differ" in txt


def test_manifest_ignores_timestamps(shard_dir, tmp_path):
    out = tmp_path / "c"
    package.package(shard_dir, out, only=[1], log=lambda m: None)
    p = next(out.glob("*.h5"))
    a = manifest.file_entry(p)
    with h5py.File(p, "r+") as f:
        f.attrs["created"] = "2000-01-01T00:00:00"
    assert manifest.file_entry(p) == a
    with h5py.File(p, "r+") as f:
        f.attrs["z_lo"] = 0.1000001
    assert manifest.file_entry(p) != a
