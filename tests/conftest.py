"""Shared fixtures: small synthetic v4-layout temp files and shards (no NAS, no network)."""
import os
import sys
from pathlib import Path

for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ.setdefault(k, "1")

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

import h5py          # noqa: E402
import numpy as np   # noqa: E402
import pytest        # noqa: E402

from desi_galaxy_spectra import shard as shard_mod   # noqa: E402,F401  (imports the frozen extractor)

NWAVE, NDIAG = 7781, 11
UNIT_KEYS = ["wavelength", "flux", "ivar", "mask", "photometry", "photometry_ivar", "fiberflux",
             "ebv", "mw_transmission", "coordinates", "tsnr2"]


def make_temp(path, pix, objs, seed=0):
    """Write a synthetic v4-layout file.  objs: list of (class, targetid, program, z, clean)."""
    rng = np.random.default_rng(seed + pix)
    wave = (3600 + 0.8 * np.arange(NWAVE)).astype("f4")
    with h5py.File(path, "w") as h:
        m = h.create_group("metadata")
        m.attrs["creation_date"] = "2026-01-01T00:00:00"
        m.attrs["script_version"] = "4.0.0"
        m.attrs["n_total_objects"] = len(objs)
        u = m.create_group("units")
        for k in UNIT_KEYS:
            u.attrs[k] = f"unit of {k}"
        for cls in ("BGS", "ELG", "LRG"):
            rs = [o for o in objs if o[0] == cls]
            if not rs:
                continue
            n = len(rs)
            g = h.create_group(cls)
            g.attrs["n_objects"] = n
            g.attrs["galaxy_type"] = f"DESI_DR1_{cls}"
            cat, ph, q = (g.create_group(x) for x in ("catalog", "photometry", "quality"))
            sp = g.create_group("spectra")
            cat["targetid"] = np.array([o[1] for o in rs], "i8")
            cat["healpix"] = np.full(n, pix, "i4")
            cat["program"] = np.array([o[2].encode() for o in rs], "S10")
            cat["fiber_index"] = np.arange(n, dtype="i4")
            cat["coadd_file"] = np.array(
                [f"/stage/{o[2]}/coadd-main-{o[2]}-{pix}.fits".encode() for o in rs], "S256")
            cat["z_redrock"] = np.array([o[3] for o in rs], "f8")
            clean = np.array([o[4] for o in rs], bool)
            cat["is_galaxy"] = np.ones(n, bool)
            cat["is_clean_galaxy"] = clean
            cat["coeff"] = rng.normal(size=(n, 10))
            ph["flux_g"] = rng.normal(size=n).astype("f4")
            q["extraction_success"] = np.ones(n, bool)
            q["snr_median"] = rng.normal(size=n).astype("f4")
            for i, o in enumerate(rs):
                sg = sp.create_group(f"obj_{i:05d}")
                for name, arr in (("wavelength", wave),
                                  ("flux", rng.normal(size=NWAVE).astype("f4")),
                                  ("ivar", rng.random(NWAVE).astype("f4")),
                                  ("mask", rng.integers(0, 4, NWAVE).astype("i2")),
                                  ("resolution", rng.random((NDIAG, NWAVE)).astype("f4"))):
                    sg.create_dataset(name, data=arr, compression="gzip", compression_opts=4)
                sg.attrs["targetid"] = o[1]
                sg.attrs["extraction_success"] = True
                sg.attrs["snr_median"] = float(i)
                sg.attrs["n_pixels"] = NWAVE
                sg.attrs["resolution_n_diag"] = NDIAG
                for a in "BRZ":
                    sg.attrs[f"arm_{a}_coverage"] = 0.9


def make_shard(tmp_path, pix, objs):
    temp = Path(tmp_path) / f"temp_{pix}.h5"
    make_temp(temp, pix, objs)
    ps = {}
    for cls in ("BGS", "ELG", "LRG"):
        n = sum(1 for o in objs if o[0] == cls)
        if n:
            ps[cls] = np.full(n, b"S", dtype="S1")
    d = Path(tmp_path) / "shards"
    d.mkdir(exist_ok=True)
    shard_mod.restack(temp, d / f"healpix_{pix}.h5", pix, ps,
                      [("coadd-main-bright-%d.fits" % pix, "0" * 64)], log=lambda m: None)
    return d / f"healpix_{pix}.h5", temp


OBJS_A = [("BGS", 100, "bright", 0.05, True), ("BGS", 101, "bright", 0.15, True),
          ("BGS", 102, "bright", 0.15, False), ("ELG", 200, "dark", 0.85, True),
          ("ELG", 201, "dark", 0.86, True), ("LRG", 300, "dark", 0.45, True)]
OBJS_B = [("BGS", 100, "dark", 0.16, True), ("ELG", 210, "dark", 1.7, True),
          ("LRG", 310, "dark", 0.46, True), ("LRG", 311, "dark", 0.47, True)]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests are offline: any real requests.Session call is an error."""
    import requests

    def boom(*a, **k):
        raise AssertionError("test attempted a real network request")

    monkeypatch.setattr(requests.sessions.Session, "request", boom)


@pytest.fixture()
def shard_dir(tmp_path):
    make_shard(tmp_path, 11, OBJS_A)
    make_shard(tmp_path, 12, OBJS_B)
    return tmp_path / "shards"
